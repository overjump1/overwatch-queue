"""`activity_tokens.json` round-trips, same style as `test_pushtokens.py`."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from owqserver.activitytokens import ActivityTokens              # noqa: E402


class ActivityTokensTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp()
        os.close(handle)
        os.remove(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_starts_empty(self):
        tokens = ActivityTokens(self.path)
        self.assertIsNone(tokens.start_token())
        self.assertIsNone(tokens.update_token("session-1"))

    def test_register_and_read_the_start_token(self):
        tokens = ActivityTokens(self.path)
        tokens.register_start("start-token", "sandbox")
        self.assertEqual(tokens.start_token(), ("start-token", "sandbox"))

    def test_the_update_token_is_only_valid_for_the_session_it_was_registered_for(self):
        tokens = ActivityTokens(self.path)
        tokens.register_update("session-1", "activity-token", "sandbox")
        self.assertEqual(tokens.update_token("session-1"), ("activity-token", "sandbox"))
        self.assertIsNone(tokens.update_token("session-2"))

    def test_registering_a_new_session_replaces_the_old_update_token(self):
        tokens = ActivityTokens(self.path)
        tokens.register_update("session-1", "old-token", "sandbox")
        tokens.register_update("session-2", "new-token", "sandbox")
        self.assertIsNone(tokens.update_token("session-1"))
        self.assertEqual(tokens.update_token("session-2"), ("new-token", "sandbox"))

    def test_survives_a_reload(self):
        tokens = ActivityTokens(self.path)
        tokens.register_start("start-token", "production")
        tokens.register_update("session-1", "activity-token", "production")
        reloaded = ActivityTokens(self.path)
        self.assertEqual(reloaded.start_token(), ("start-token", "production"))
        self.assertEqual(reloaded.update_token("session-1"), ("activity-token", "production"))

    def test_forget_update_drops_only_the_update_token(self):
        tokens = ActivityTokens(self.path)
        tokens.register_start("start-token", "sandbox")
        tokens.register_update("session-1", "activity-token", "sandbox")
        tokens.forget_update()
        self.assertIsNone(tokens.update_token("session-1"))
        self.assertEqual(tokens.start_token(), ("start-token", "sandbox"))

    def test_clear_drops_both(self):
        tokens = ActivityTokens(self.path)
        tokens.register_start("start-token", "sandbox")
        tokens.register_update("session-1", "activity-token", "sandbox")
        tokens.clear()
        self.assertIsNone(tokens.start_token())
        self.assertIsNone(tokens.update_token("session-1"))

    def test_rejects_an_unknown_environment(self):
        tokens = ActivityTokens(self.path)
        tokens.register_start("start-token", "staging")
        tokens.register_update("session-1", "activity-token", "staging")
        self.assertIsNone(tokens.start_token())
        self.assertIsNone(tokens.update_token("session-1"))

    def test_ignores_a_corrupt_file(self):
        with open(self.path, "w") as handle:
            handle.write("not json")
        tokens = ActivityTokens(self.path)
        self.assertIsNone(tokens.start_token())


if __name__ == "__main__":
    unittest.main()
