# Dot Telegram Bridge — Developed by Revothin Yacht Electrical System.
import asyncio
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx2

from revo_mcp import ACK_TEXT, EVENT, State, make_server, public_destination, signed_headers, signing_key
from test_bridge import update


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state = State(Path(self.directory.name) / 'test.sqlite3')
        self.state.ingest([update(1)], 123, 123)
        self.telegram = Mock()
        self.telegram.send.return_value = {'message_id': 77}

    def tearDown(self):
        self.state.close()
        self.directory.cleanup()

    def test_same_reply_is_not_sent_twice(self):
        self.state.send_once(self.telegram, 123, 'reply:1', 'Yanıt', 1)
        result = self.state.send_once(self.telegram, 123, 'reply:1', 'Yanıt', 1)
        self.assertTrue(result['already_sent'])
        self.telegram.send.assert_called_once_with(123, 'Yanıt')
        self.assertEqual(self.state.pending(), [])

    def test_receipt_is_once_and_does_not_complete_request(self):
        self.state.acknowledge_pending(self.telegram, 123)
        self.state.acknowledge_pending(self.telegram, 123)
        self.telegram.send.assert_called_once_with(123, ACK_TEXT)
        self.assertEqual(len(self.state.pending()), 1)

    def test_existing_dot_receipt_is_not_repeated(self):
        self.state.send_once(self.telegram, 123, 'notify:ack:1', 'İsteğin alındı, inceliyorum.')
        self.state.acknowledge_pending(self.telegram, 123)
        self.assertEqual(self.telegram.send.call_count, 1)

    def test_progress_edits_receipt_throttles_and_stops_on_reply(self):
        self.state.db.execute('UPDATE inbox SET received_at=100 WHERE update_id=1')
        self.state.db.commit()
        self.state.acknowledge_pending(self.telegram, 123)
        self.state.update_progress(self.telegram, 123, now=130)
        method, payload = self.telegram.call.call_args.args
        self.assertEqual(method, 'editMessageText')
        self.assertEqual(payload['message_id'], 77)
        self.assertIn('00:30', payload['text'])
        self.assertIn('teslim bekleniyor', payload['text'])
        self.state.update_progress(self.telegram, 123, now=135)
        self.assertEqual(self.telegram.call.call_count, 1)
        self.state.send_once(self.telegram, 123, 'reply:1', 'Sonuç', 1)
        self.state.update_progress(self.telegram, 123, now=140)
        self.assertIn('Yanıt gönderildi', self.telegram.call.call_args.args[1]['text'])
        self.state.update_progress(self.telegram, 123, now=200)
        self.assertEqual(self.telegram.call.call_count, 2)

    def test_read_receipt_does_not_claim_active_work_or_eta(self):
        self.state.acknowledge_pending(self.telegram, 123)
        self.state.db.execute('UPDATE progress SET read_at=110 WHERE update_id=1')
        self.state.db.commit()
        self.state.update_progress(self.telegram, 123, now=500)
        text = self.telegram.call.call_args.args[1]['text']
        self.assertIn('isteği okudu', text)
        self.assertIn('Kalan süre henüz bilinmiyor', text)

    def test_unknown_delivery_is_not_blindly_retried(self):
        self.telegram.send.side_effect = RuntimeError('timeout')
        with self.assertRaises(RuntimeError):
            self.state.send_once(self.telegram, 123, 'reply:1', 'Yanıt', 1)
        with self.assertRaises(ValueError):
            self.state.send_once(self.telegram, 123, 'reply:1', 'Yanıt', 1)
        self.assertEqual(self.telegram.send.call_count, 1)
        self.assertEqual(len(self.state.pending()), 1)

    def test_unknown_request_does_not_send(self):
        with self.assertRaises(ValueError):
            self.state.send_once(self.telegram, 123, 'reply:999', 'Yanıt', 999)
        self.telegram.send.assert_not_called()


class CallbackTests(unittest.TestCase):
    def test_private_and_mixed_dns_addresses_blocked(self):
        for addresses in [('127.0.0.1',), ('8.8.8.8', '10.0.0.1')]:
            records = [(2, 1, 6, '', (address, 443)) for address in addresses]
            with patch('revo_mcp.socket.getaddrinfo', return_value=records):
                with self.assertRaises(ValueError):
                    public_destination('https://callback.example/event')

    def test_non_https_and_userinfo_blocked(self):
        for url in ['http://example.com', 'https://u:p@example.com', 'https://example.com:8443']:
            with self.assertRaises(ValueError):
                public_destination(url)

    def test_signed_body_and_identity_are_bound(self):
        secret = 'whsec_' + base64.b64encode(b'k' * 32).decode()
        first = signed_headers(secret, 'sub_1', 'evt_1', 123, b'{}')
        second = signed_headers(secret, 'sub_1', 'evt_1', 123, b'{"x":1}')
        self.assertTrue(first['webhook-signature'].startswith('v1,'))
        self.assertNotEqual(first['webhook-signature'], second['webhook-signature'])
        with self.assertRaises(ValueError):
            signing_key('whsec_bad')


class ProtocolTests(unittest.TestCase):
    def test_discovery_tools_and_events_over_http(self):
        async def check():
            with tempfile.TemporaryDirectory() as directory:
                app = make_server(Mock(), 123, Path(directory) / 'test.sqlite3').streamable_http_app(
                    stateless_http=True, json_response=True, host='127.0.0.1')
                async with app.router.lifespan_context(app):
                    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url='http://127.0.0.1:8765') as client:
                        async def rpc(method, params=None):
                            arguments = dict(params or {})
                            arguments['_meta'] = {'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientCapabilities': {}}
                            response = await client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': arguments},
                                headers={'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2026-07-28', 'mcp-method': method})
                            self.assertEqual(response.status_code, 200, response.text)
                            value = response.json()
                            self.assertNotIn('error', value, value)
                            return value['result']
                        discovery = await rpc('server/discover')
                        self.assertIn('events', discovery['capabilities'])
                        tools = await rpc('tools/list')
                        self.assertEqual(len(tools['tools']), 4)
                        events = await rpc('events/list')
                        self.assertEqual(events['events'][0]['name'], EVENT)
                        secret = 'whsec_' + base64.b64encode(b'k' * 32).decode()
                        params = {'name': EVENT, 'arguments': {}, 'delivery': {
                            'mode': 'webhook', 'url': 'https://callback.example/event', 'secret': secret}}

                        def verified(url, key, sid, eid, payload):
                            return 200, json.dumps({'challenge': payload['challenge']}).encode()

                        with patch('revo_mcp.public_destination'), patch('revo_mcp.webhook_post', side_effect=verified):
                            first = await rpc('events/subscribe', params)
                            second = await rpc('events/subscribe', params)
                        self.assertEqual(first['id'], second['id'])
                        state = State(Path(directory) / 'test.sqlite3')
                        self.assertEqual(state.db.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)
                        removal = {'name': EVENT, 'arguments': {}, 'delivery': {'url': params['delivery']['url']}}
                        await rpc('events/unsubscribe', removal)
                        await rpc('events/unsubscribe', removal)
                        self.assertEqual(state.db.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 0)
                        state.close()
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()

