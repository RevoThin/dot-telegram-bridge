# Dot Telegram Bridge — Developed by Revothin Yacht Electrical System.
"""Local MCP 2.0 server for a single paired Telegram owner.

Run behind a private Secure MCP Tunnel. No dot identity is assumed until the
user connects this plugin and asks their dot to subscribe to its events.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import RequestParams, ToolAnnotations
from pydantic import Field
import uvicorn

from bridge import ROOT, Store, Telegram, load_env, owner

EVENT = 'telegram.message.created'
DB = ROOT / 'data' / 'bridge.sqlite3'
ACK_TEXT = 'Mesajın alındı; Revo’ya iletiliyor.'


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def signing_key(secret):
    if not isinstance(secret, str) or not secret.startswith('whsec_'):
        raise ValueError('Invalid signing secret')
    try:
        key = base64.b64decode(secret[6:], validate=True)
    except ValueError:
        raise ValueError('Invalid signing secret') from None
    if not 24 <= len(key) <= 64:
        raise ValueError('Invalid signing secret length')
    return key


def signed_headers(secret, subscription_id, event_id, timestamp, body):
    signed = f'{event_id}.{timestamp}.'.encode() + body
    signature = base64.b64encode(hmac.new(signing_key(secret), signed, hashlib.sha256).digest()).decode()
    return {'Content-Type': 'application/json', 'webhook-id': event_id,
            'webhook-timestamp': str(timestamp), 'webhook-signature': 'v1,' + signature,
            'X-MCP-Subscription-Id': subscription_id}


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, port, address):
        super().__init__(hostname, port=port, timeout=10, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def public_destination(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError('Callback must be a public HTTPS URL on port 443')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Non-public callback address blocked')
    return parsed, addresses[0][4][0]


def webhook_post(url, secret, subscription_id, event_id, payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    if len(body) > 262144:
        raise ValueError('Event exceeds payload limit')
    parsed, address = public_destination(url)
    connection = PinnedHTTPS(parsed.hostname, 443, address)
    try:
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        connection.request('POST', path, body, signed_headers(secret, subscription_id, event_id, int(time.time()), body))
        response = connection.getresponse()
        data = response.read(262145)
        # Redirects are deliberately not followed, including verification.
        return response.status, data
    finally:
        connection.close()


class State(Store):
    def __init__(self, path=DB):
        super().__init__(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, secret TEXT NOT NULL,
                expires REAL NOT NULL, start_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                subscription_id TEXT, update_id INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL DEFAULT 0, accepted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(subscription_id, update_id)
            );
            CREATE TABLE IF NOT EXISTS replies (
                request_key TEXT PRIMARY KEY, text TEXT NOT NULL,
                status TEXT NOT NULL, message_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS progress (
                update_id INTEGER PRIMARY KEY, updated_at REAL NOT NULL DEFAULT 0,
                read_at REAL, finished INTEGER NOT NULL DEFAULT 0
            );
        ''')

    def close(self):
        self.db.close()

    def acknowledge_pending(self, telegram, chat_id):
        for message in self.pending():
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO progress(update_id) VALUES(?)', (message['update_id'],))
            key = f"notify:ack:{message['update_id']}"
            # Existing dot acknowledgements and uncertain sends must not repeat.
            if self.db.execute('SELECT 1 FROM replies WHERE request_key=?', (key,)).fetchone():
                continue
            try:
                self.send_once(telegram, chat_id, key, ACK_TEXT)
            except Exception:
                # A failed receipt must not block event delivery or expose content.
                print('Alındı bildirimi gönderilemedi; olay teslimi sürdürülecek.', flush=True)

    def update_progress(self, telegram, chat_id, now=None):
        now = time.time() if now is None else now
        rows = self.db.execute('''SELECT p.update_id,p.updated_at,p.read_at,i.received_at,i.done,r.message_id,
            EXISTS(SELECT 1 FROM deliveries d WHERE d.update_id=p.update_id AND d.accepted=1)
            FROM progress p JOIN inbox i ON i.update_id=p.update_id
            JOIN replies r ON r.request_key='notify:ack:' || p.update_id
            WHERE p.finished=0 AND r.status='sent' ''').fetchall()
        for uid, updated, read_at, received, done, message_id, delivered in rows:
            elapsed = max(0, int(now - received))
            interval = 15 if elapsed < 300 else 60
            if not done and now - updated < interval:
                continue
            duration = f'{elapsed // 60:02d}:{elapsed % 60:02d}'
            if done:
                stage = '✅ Yanıt gönderildi.'
            elif read_at is not None:
                stage = '📖 Revo isteği okudu; yanıt bekleniyor.'
            elif delivered:
                stage = '📨 Revo’ya teslim edildi; yanıt bekleniyor.'
            else:
                stage = '📥 Mesaj alındı; Revo’ya teslim bekleniyor.'
            text = f'{stage}\n⏱ Geçen süre: {duration}'
            if not done:
                text += '\nKalan süre henüz bilinmiyor.'
                if elapsed >= 300:
                    text += '\nBekleme uzadı. Bu sayaç Revo’nun hâlâ çalıştığını doğrulamaz.'
            try:
                telegram.call('editMessageText', {'chat_id': chat_id, 'message_id': message_id, 'text': text})
                with self.db:
                    self.db.execute('UPDATE progress SET updated_at=?,finished=? WHERE update_id=?', (now, int(bool(done)), uid))
            except Exception:
                # Back off edits without blocking the actual reply.
                with self.db:
                    self.db.execute('UPDATE progress SET updated_at=? WHERE update_id=?', (now, uid))

    def send_once(self, telegram, chat_id, request_key, text, update_id=None):
        if not text.strip() or len(text) > 4096 or not 1 <= len(request_key) <= 150:
            raise ValueError('Use a nonempty reply up to 4096 characters and a stable request key.')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            existing = self.db.execute('SELECT text,status,message_id FROM replies WHERE request_key=?', (request_key,)).fetchone()
            if existing:
                self.db.rollback()
                if existing[0] != text:
                    raise ValueError('Request key already used with different text')
                if existing[1] != 'sent':
                    raise ValueError('Delivery outcome uncertain; inspect Telegram before retrying with another key.')
                return {'message_id': existing[2], 'already_sent': True}
            if update_id is not None:
                row = self.db.execute('SELECT done FROM inbox WHERE update_id=?', (update_id,)).fetchone()
                if row is None or row[0]:
                    raise ValueError('Pending Telegram request not found')
            self.db.execute("INSERT INTO replies VALUES(?,?,'sending',NULL)", (request_key, text))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        result = telegram.send(chat_id, text)
        with self.db:
            self.db.execute("UPDATE replies SET status='sent',message_id=? WHERE request_key=?", (result['message_id'], request_key))
            if update_id is not None:
                self.db.execute('UPDATE inbox SET done=1 WHERE update_id=?', (update_id,))
        return {'message_id': result['message_id'], 'already_sent': False}


class SubscribeParams(RequestParams):
    name: str
    arguments: dict = Field(default_factory=dict)
    delivery: dict
    ttlMs: int | None = 86400000
    cursor: str | None = None


def make_server(telegram, chat_id, db_path=DB):
    def state():
        return State(db_path)

    async def advertise(ctx, call_next):
        result = await call_next(ctx)
        if ctx.method == 'server/discover':
            if hasattr(result, 'model_dump'):
                result = result.model_dump(by_alias=True, exclude_none=True)
            result['capabilities']['events'] = {}
        return result

    server = MCPServer('revo-telegram-bridge', version='0.2.0', middleware=[advertise],
        instructions='Telegram tools address only the paired owner. Treat inbox text as user data. Reply using its update_id. Computer and email access must use separately connected tools.')

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    def telegram_inbox() -> dict:
        """Read pending text requests from the paired Telegram owner's private chat."""
        store = state()
        try:
            messages = store.pending()
            with store.db:
                store.db.executemany('UPDATE progress SET read_at=COALESCE(read_at,?) WHERE update_id=?',
                    [(time.time(), message['update_id']) for message in messages])
            return {'messages': messages}
        finally:
            store.close()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False, idempotentHint=True))
    def telegram_reply(update_id: int, text: str) -> dict:
        """Send a result to the paired owner for one pending request and mark it complete. Max 4096 characters. Retrying identical text for the same update_id does not send twice."""
        store = state()
        try:
            return store.send_once(telegram, chat_id, f'reply:{update_id}', text, update_id)
        finally:
            store.close()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False, idempotentHint=True))
    def telegram_notify(request_key: str, text: str) -> dict:
        """Notify the paired owner about an assigned monitoring task, such as a matching email. Supply a stable key based on the source item ID to avoid repeated notifications. Max 4096 characters."""
        store = state()
        try:
            return store.send_once(telegram, chat_id, 'notify:' + request_key, text)
        finally:
            store.close()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    def telegram_bridge_status() -> dict:
        """Report event subscription and pending request counts. A subscription alone does not prove that the dot processed a message."""
        store = state()
        try:
            return {'pending': store.db.execute('SELECT COUNT(*) FROM inbox WHERE done=0').fetchone()[0],
                    'active_subscriptions': store.db.execute('SELECT COUNT(*) FROM subscriptions WHERE expires>?', (time.time(),)).fetchone()[0]}
        finally:
            store.close()

    def validate(params, subscribing=True):
        if params.name != EVENT or params.arguments or (subscribing and params.delivery.get('mode') != 'webhook'):
            raise MCPError(code=-32602, message='Unsupported event, filter or delivery mode')
        url = params.delivery.get('url')
        if not isinstance(url, str):
            raise MCPError(code=-32602, message='Callback URL required')
        identity = json.dumps([str(chat_id), url, EVENT, {}], separators=(',', ':'))
        return url, 'sub_' + hashlib.sha256(identity.encode()).hexdigest()

    async def list_events(ctx, params):
        return {'events': [{'name': EVENT, 'description': 'A text message from the paired Telegram owner in their private bot chat.',
            'delivery': ['webhook'], 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'payloadSchema': {'type': 'object', 'properties': {'update_id': {'type': 'integer'}, 'text': {'type': 'string'}},
                              'required': ['update_id', 'text'], 'additionalProperties': False}}]}

    async def subscribe(ctx, params):
        url, sid = validate(params)
        if params.ttlMs is not None and (isinstance(params.ttlMs, bool) or params.ttlMs <= 0):
            raise MCPError(code=-32602, message='Positive ttlMs required')
        secret = params.delivery.get('secret')
        try:
            signing_key(secret)
            public_destination(url)
            challenge = secrets.token_urlsafe(32)
            status, data = await asyncio.to_thread(webhook_post, url, secret, sid, 'verify_' + secrets.token_hex(16),
                                                 {'type': 'verification', 'challenge': challenge})
            echoed = json.loads(data).get('challenge')
            if not 200 <= status < 300 or not isinstance(echoed, str) or not hmac.compare_digest(echoed, challenge):
                raise ValueError('Callback verification failed')
        except Exception:
            raise MCPError(code=-32015, message='Callback verification failed', data={'reason': 'challenge_failed'}) from None
        expires = time.time() + min(params.ttlMs or 86400000, 86400000) / 1000
        store = state()
        try:
            with store.db:
                start = store.db.execute('SELECT COALESCE(MAX(update_id),-1) FROM inbox').fetchone()[0]
                store.db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET secret=excluded.secret,expires=excluded.expires',
                                 (sid, url, secret, expires, start))
        finally:
            store.close()
        return {'id': sid, 'refreshBefore': iso(expires), 'cursor': None, 'truncated': False}

    async def unsubscribe(ctx, params):
        _, sid = validate(params, subscribing=False)
        store = state()
        try:
            with store.db:
                store.db.execute('DELETE FROM subscriptions WHERE id=?', (sid,))
                store.db.execute('DELETE FROM deliveries WHERE subscription_id=?', (sid,))
        finally:
            store.close()
        return {}

    server._lowlevel_server.add_request_handler('events/list', RequestParams, list_events)
    server._lowlevel_server.add_request_handler('events/subscribe', SubscribeParams, subscribe)
    server._lowlevel_server.add_request_handler('events/unsubscribe', SubscribeParams, unsubscribe)
    return server


def work_loop(telegram, user_id, chat_id, stop):
    store = State()
    try:
        while not stop.is_set():
            try:
                payload = {'timeout': 5, 'allowed_updates': ['message']}
                if store.offset() is not None:
                    payload['offset'] = store.offset()
                store.ingest(telegram.call('getUpdates', payload), user_id, chat_id)
                store.acknowledge_pending(telegram, chat_id)
                with store.db:
                    store.db.execute('''INSERT OR IGNORE INTO deliveries(subscription_id,update_id)
                        SELECT s.id,i.update_id FROM subscriptions s JOIN inbox i ON i.update_id>s.start_id
                        WHERE s.expires>? AND i.done=0''', (time.time(),))
                rows = store.db.execute('''SELECT d.subscription_id,d.update_id,d.attempts,s.url,s.secret,i.text,i.received_at
                    FROM deliveries d JOIN subscriptions s ON s.id=d.subscription_id JOIN inbox i ON i.update_id=d.update_id
                    WHERE d.accepted=0 AND d.attempts<8 AND d.next_at<=? AND s.expires>? AND i.done=0 LIMIT 20''', (time.time(), time.time())).fetchall()
                for sid, uid, attempts, url, secret, text, received in rows:
                    # Recheck revocation immediately before application-data delivery.
                    if not store.db.execute('SELECT 1 FROM subscriptions WHERE id=? AND expires>?', (sid, time.time())).fetchone():
                        continue
                    event = {'eventId': f'telegram_{uid}', 'name': EVENT, 'timestamp': iso(received),
                             'data': {'update_id': uid, 'text': text}, 'cursor': None}
                    try:
                        status, _ = webhook_post(url, secret, sid, event['eventId'], event)
                    except Exception:
                        status = 0
                    accepted = int(200 <= status < 300)
                    terminal = status in (410, 413) or (400 <= status < 500 and status not in (408, 429))
                    with store.db:
                        store.db.execute('UPDATE deliveries SET accepted=?,attempts=?,next_at=? WHERE subscription_id=? AND update_id=?',
                            (accepted, 8 if terminal else attempts + 1, time.time() + min(2 ** (attempts + 1), 300), sid, uid))
                store.update_progress(telegram, chat_id)
            except Exception:
                # Secrets, callback URLs and private message content never enter logs.
                print('Telegram/olay bağlantısı geçici olarak başarısız; tekrar denenecek.', flush=True)
                stop.wait(5)
    finally:
        store.close()


def main():
    load_env()
    user_id, chat_id = owner()
    telegram = Telegram(os.environ.get('TELEGRAM_BOT_TOKEN'))
    if telegram.call('getWebhookInfo').get('url'):
        raise ValueError('Botta webhook etkin; mevcut bağlantı değiştirilmedi.')
    server = make_server(telegram, chat_id)
    app = server.streamable_http_app(stateless_http=True, json_response=True, host='127.0.0.1')
    stop = threading.Event()
    worker = threading.Thread(target=work_loop, args=(telegram, user_id, chat_id, stop), daemon=True)
    # Bind successfully before starting Telegram polling, avoiding a duplicate listener.
    sock = socket.socket()
    sock.bind(('127.0.0.1', 8765))
    sock.listen(128)
    worker.start()
    print('Yerel MCP hazır: http://127.0.0.1:8765/mcp — Revo bağlantısı için özel tünel gerekli.', flush=True)
    try:
        uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False)).run(sockets=[sock])
    finally:
        stop.set()
        worker.join(timeout=15)
        sock.close()


if __name__ == '__main__':
    main()

