# Dot Telegram Bridge — Developed by Revothin Yacht Electrical System.
import tempfile
import unittest
from pathlib import Path

from bridge import Store
from setup_telegram import pairing_candidate


def update(number, user=123, chat=123, kind='private'):
    return {'update_id': number, 'message': {'message_id': number, 'date': 100,
            'from': {'id': user, 'is_bot': False},
            'chat': {'id': chat, 'type': kind}, 'text': 'Gmail kontrol et'}}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'queue.sqlite3'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    def test_only_owner_private_chat_is_accepted(self):
        self.store.ingest([update(1), update(2, user=456), update(3, chat=456),
                           update(4, kind='group'), {'update_id': 5}], 123, 123)
        self.assertEqual([item['update_id'] for item in self.store.pending()], [1])
        self.assertEqual(self.store.offset(), 6)

    def test_replays_do_not_duplicate_or_regress_cursor(self):
        self.store.ingest([update(20), update(10)], 123, 123)
        self.store.ingest([update(10)], 123, 123)
        self.assertEqual(len(self.store.pending()), 2)
        self.assertEqual(self.store.offset(), 21)

    def test_pending_messages_survive_restart_and_require_explicit_ack(self):
        self.store.ingest([update(1)], 123, 123)
        self.store.db.close()
        self.store = Store(self.path)
        self.assertEqual(len(self.store.pending()), 1)
        self.store.mark_done(1)
        self.assertEqual(self.store.pending(), [])
        with self.assertRaises(ValueError):
            self.store.mark_done(999)

    def test_invalid_batch_rolls_back_cursor_and_messages(self):
        with self.assertRaises(KeyError):
            self.store.ingest([update(1), {'message': {}}], 123, 123)
        self.assertEqual(self.store.pending(), [])
        self.assertIsNone(self.store.offset())


class PairingTests(unittest.TestCase):
    def test_pairing_requires_nonce_and_private_human_sender(self):
        challenge = '/start revo_test'
        wrong = update(1)
        group = update(2, kind='group')
        group['message']['text'] = challenge
        bot = update(3)
        bot['message']['text'] = challenge
        bot['message']['from']['is_bot'] = True
        self.assertIsNone(pairing_candidate([wrong, group, bot], challenge))
        valid = update(4)
        valid['message']['text'] = challenge
        self.assertEqual(pairing_candidate([valid], challenge)['id'], 123)


if __name__ == '__main__':
    unittest.main()

