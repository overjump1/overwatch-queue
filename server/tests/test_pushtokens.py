"""`push_tokens.json` round-trips, same as `test_pairing.py` does for `pairing.json`."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from owqserver.pushtokens import PushTokens                       # noqa: E402


class PushTokensTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp()
        os.close(handle)
        os.remove(self.path)               # PushTokens should tolerate a missing file

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_starts_empty(self):
        tokens = PushTokens(self.path)
        self.assertIsNone(tokens.get("phone"))
        self.assertEqual(list(tokens.items()), [])

    def test_register_and_get(self):
        tokens = PushTokens(self.path)
        tokens.register("phone", "abc123", "sandbox")
        self.assertEqual(tokens.get("phone"), ("abc123", "sandbox"))

    def test_survives_a_reload(self):
        PushTokens(self.path).register("watch", "def456", "production")
        reloaded = PushTokens(self.path)
        self.assertEqual(reloaded.get("watch"), ("def456", "production"))

    def test_registering_again_overwrites(self):
        tokens = PushTokens(self.path)
        tokens.register("phone", "old", "sandbox")
        tokens.register("phone", "new", "production")
        self.assertEqual(tokens.get("phone"), ("new", "production"))

    def test_forget_one_kind_leaves_the_other(self):
        tokens = PushTokens(self.path)
        tokens.register("phone", "abc", "sandbox")
        tokens.register("watch", "def", "sandbox")
        tokens.forget("phone")
        self.assertIsNone(tokens.get("phone"))
        self.assertEqual(tokens.get("watch"), ("def", "sandbox"))

    def test_clear_drops_everything(self):
        tokens = PushTokens(self.path)
        tokens.register("phone", "abc", "sandbox")
        tokens.register("watch", "def", "sandbox")
        tokens.clear()
        self.assertEqual(list(tokens.items()), [])

    def test_rejects_an_unknown_kind_or_environment(self):
        tokens = PushTokens(self.path)
        tokens.register("tablet", "abc", "sandbox")
        tokens.register("phone", "abc", "staging")
        self.assertEqual(list(tokens.items()), [])

    def test_ignores_a_corrupt_file(self):
        with open(self.path, "w") as handle:
            handle.write("not json")
        tokens = PushTokens(self.path)
        self.assertEqual(list(tokens.items()), [])


if __name__ == "__main__":
    unittest.main()
