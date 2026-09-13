"""The guard `conftest.py` sets up, checked from where it matters: a real test module
that has already imported `owqserver`."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from owqserver import activitytokens, fcm, fcmtokens, pushtokens      # noqa: E402


class IsolationTests(unittest.TestCase):
    def test_every_push_config_lives_under_a_temporary_home(self):
        temp = os.path.realpath(tempfile.gettempdir())
        for module in (activitytokens, fcmtokens, pushtokens):
            with self.subTest(module=module.__name__):
                self.assertTrue(os.path.realpath(module.CONFIG_PATH).startswith(temp),
                                module.CONFIG_PATH)

    def test_pushing_is_turned_off(self):
        self.assertIsNone(fcm.relay_url())


if __name__ == "__main__":
    unittest.main()
