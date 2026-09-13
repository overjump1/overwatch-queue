"""`fcm_tokens.json` round-trips, same style as `test_activitytokens.py`."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from owqserver.fcmtokens import FCMTokens                           # noqa: E402

TOKEN = "fGhIjKlMnOp:APA91bHxQ-token-shaped-string"


class FCMTokensTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp()
        os.close(handle)
        os.remove(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_starts_empty(self):
        self.assertIsNone(FCMTokens(self.path).get())

    def test_register_and_read_it_back(self):
        tokens = FCMTokens(self.path)
        tokens.register(TOKEN)
        self.assertEqual(tokens.get(), TOKEN)

    def test_it_survives_a_restart(self):
        FCMTokens(self.path).register(TOKEN)
        self.assertEqual(FCMTokens(self.path).get(), TOKEN)

    def test_a_new_token_replaces_the_old_one(self):
        # One phone per pairing, so there is nothing to merge — Firebase rotates the
        # registration token on reinstall and the previous one is simply dead.
        tokens = FCMTokens(self.path)
        tokens.register(TOKEN)
        tokens.register("second-token")
        self.assertEqual(tokens.get(), "second-token")
        with open(self.path) as handle:
            self.assertEqual(json.load(handle), {"phone": "second-token"})

    def test_an_empty_token_is_ignored(self):
        tokens = FCMTokens(self.path)
        tokens.register(TOKEN)
        tokens.register("")
        self.assertEqual(tokens.get(), TOKEN)

    def test_registering_the_same_token_does_not_rewrite_the_file(self):
        tokens = FCMTokens(self.path)
        tokens.register(TOKEN)
        before = os.stat(self.path).st_mtime_ns
        tokens.register(TOKEN)
        self.assertEqual(os.stat(self.path).st_mtime_ns, before)

    def test_forget_clears_it(self):
        tokens = FCMTokens(self.path)
        tokens.register(TOKEN)
        tokens.forget()
        self.assertIsNone(tokens.get())
        self.assertIsNone(FCMTokens(self.path).get())

    def test_forgetting_nothing_writes_nothing(self):
        FCMTokens(self.path).forget()
        self.assertFalse(os.path.exists(self.path))

    def test_the_file_is_owner_only(self):
        # It addresses the user's phone; no reason for anyone else on the machine to read it.
        FCMTokens(self.path).register(TOKEN)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode),
                         stat.S_IRUSR | stat.S_IWUSR)

    def test_a_corrupt_file_reads_as_empty(self):
        with open(self.path, "w") as handle:
            handle.write("{not json")
        self.assertIsNone(FCMTokens(self.path).get())


if __name__ == "__main__":
    unittest.main()
