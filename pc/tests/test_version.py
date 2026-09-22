"""The stamps in version.py, as the source has to leave them."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import version  # noqa: E402


class SourceTests(unittest.TestCase):
    """CI stamps a dev build's addresses in just before building, after the tests have run. A
    stamp that got committed instead would ship every build made from here -- main's included --
    talking to the dev worker and updating from dev builds."""

    def test_it_talks_to_the_real_worker(self):
        self.assertEqual(version.WORKER_URL, "https://overwatch-queue-push-relay.tomerady.workers.dev")

    def test_it_updates_from_the_real_releases(self):
        self.assertEqual(version.RELEASE_API,
                         "https://api.github.com/repos/overjump1/overwatch-queue/releases/latest")


if __name__ == "__main__":
    unittest.main()
