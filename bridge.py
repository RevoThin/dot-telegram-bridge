# Dot Telegram Bridge — Developed by Revothin Yacht Electrical System.
"""Telegram transport foundation. Does not independently invoke a ChatGPT dot."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Telegram:
    def __init__(self, token):
        if not token:
            raise ValueError('TELEGRAM_BOT_TOKEN gerekli. .env.example dosyasını .env olarak kopyalayın.')
        self.token = token

    def call(self, method, payload=None):
        request = urllib.request.Request(
            f'https://api.telegram.org/bot{self.token}/{method}',
            data=json.dumps(payload or {}).encode(),
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            # Never expose request URLs: they contain the bot token.
            raise RuntimeError(f'Telegram HTTP hatası: {exc.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError('Telegram bağlantısı kurulamadı.') from None
        if not result.get('ok'):
            raise RuntimeError('Telegram isteği başarısız oldu.')
        return result['result']

    def send(self, chat_id, text):
        if not text.strip() or len(text) > 4096:
            raise ValueError('Mesaj 1–4096 karakter arasında olmalı.')
        return self.call('sendMessage', {'chat_id': chat_id, 'text': text})


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER);
            CREATE TABLE IF NOT EXISTS inbox (
                update_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL,
                text TEXT NOT NULL, received_at INTEGER NOT NULL,
                done INTEGER NOT NULL DEFAULT 0
            );
        ''')

    def offset(self):
        row = self.db.execute("SELECT value FROM state WHERE key='offset'").fetchone()
        return row[0] if row else None

    def ingest(self, updates, user_id, chat_id):
        # Save messages and their Telegram cursor together before acknowledging them.
        with self.db:
            for update in sorted(updates, key=lambda item: item['update_id']):
                message = update.get('message', {})
                chat = message.get('chat', {})
                sender = message.get('from', {})
                if (chat.get('type') == 'private' and chat.get('id') == chat_id
                        and sender.get('id') == user_id and not sender.get('is_bot')
                        and isinstance(message.get('text'), str)):
                    self.db.execute(
                        'INSERT OR IGNORE INTO inbox(update_id,message_id,text,received_at) VALUES(?,?,?,?)',
                        (update['update_id'], message['message_id'], message['text'], message['date']),
                    )
                cursor = update['update_id'] + 1
                self.db.execute("INSERT INTO state VALUES('offset',?) ON CONFLICT(key) DO UPDATE SET value=MAX(value,excluded.value)", (cursor,))

    def pending(self):
        return [dict(zip(('update_id', 'message_id', 'text', 'received_at'), row))
                for row in self.db.execute('SELECT update_id,message_id,text,received_at FROM inbox WHERE done=0 ORDER BY update_id LIMIT 100')]

    def mark_done(self, update_id):
        with self.db:
            result = self.db.execute('UPDATE inbox SET done=1 WHERE update_id=? AND done=0', (update_id,))
            if result.rowcount != 1:
                raise ValueError('Bekleyen mesaj bulunamadı.')


def owner():
    try:
        user_id = int(os.environ['TELEGRAM_OWNER_USER_ID'])
        chat_id = int(os.environ['TELEGRAM_OWNER_CHAT_ID'])
        if user_id <= 0 or chat_id <= 0 or user_id != chat_id:
            raise ValueError()
        return user_id, chat_id
    except (KeyError, ValueError):
        raise ValueError('Pozitif ve eşit OWNER_USER_ID / OWNER_CHAT_ID değerleri gerekli; yalnızca özel sohbet desteklenir.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor')
    commands.add_parser('poll')
    commands.add_parser('inbox')
    commands.add_parser('identify', help='Bota gönderilen /start mesajından kullanıcı kimliğini gösterir; eşleştirme yapmaz.')
    send = commands.add_parser('send')
    send.add_argument('--text-file', required=True, help='Gönderilecek UTF-8 metin dosyası')
    done = commands.add_parser('done')
    done.add_argument('update_id', type=int)
    args = parser.parse_args()
    load_env()
    if args.command in ('inbox', 'done'):
        store = Store(ROOT / 'data' / 'bridge.sqlite3')
        if args.command == 'inbox':
            print(json.dumps(store.pending(), ensure_ascii=False))
        else:
            store.mark_done(args.update_id)
        return
    telegram = Telegram(os.environ.get('TELEGRAM_BOT_TOKEN'))
    if args.command == 'doctor':
        bot = telegram.call('getMe')
        hook = telegram.call('getWebhookInfo')
        print(json.dumps({'bot': bot['username'], 'webhook_active': bool(hook.get('url')), 'dot_connected': False}, ensure_ascii=False))
        return
    if args.command == 'identify':
        updates = telegram.call('getUpdates', {'timeout': 0, 'allowed_updates': ['message']})
        candidates = []
        for update in updates:
            message = update.get('message', {})
            if message.get('chat', {}).get('type') == 'private' and message.get('text') == '/start':
                candidates.append({'user_id': message['from']['id'], 'chat_id': message['chat']['id']})
        print(json.dumps(candidates, ensure_ascii=False))
        return
    user_id, chat_id = owner()
    if args.command == 'send':
        result = telegram.send(chat_id, Path(args.text_file).read_text(encoding='utf-8-sig'))
        print(json.dumps({'message_id': result['message_id']}))
        return
    if telegram.call('getWebhookInfo').get('url'):
        raise ValueError('Botta webhook etkin; polling başlatılamaz. Ayrı bir bot kullanın veya mevcut bağlantıyı inceleyin.')
    store = Store(ROOT / 'data' / 'bridge.sqlite3')
    print('Telegram dinleniyor. Durdurmak için Ctrl+C.', flush=True)
    while True:
        try:
            payload = {'timeout': 25, 'allowed_updates': ['message']}
            offset = store.offset()
            if offset is not None:
                payload['offset'] = offset
            store.ingest(telegram.call('getUpdates', payload), user_id, chat_id)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

