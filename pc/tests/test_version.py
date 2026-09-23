"""The stamps in version.py, as the source has to leave them, and the app they make."""
import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import channel  # noqa: E402
import version  # noqa: E402


class SourceTests(unittest.TestCase):
    """CI stamps main's release as the real app just before building, after the tests have run. A
    stamp that got committed instead would make running from source, and every dev build, the real
    app -- on the real worker, with the real pairing."""

    def test_the_source_is_overqueue_dev(self):
        self.assertIs(version.DEV, True)

    def test_the_real_app_talks_to_the_real_worker(self):
        self.assertEqual(channel.REAL.worker_url, "https://overwatch-queue-push-relay.tomerady.workers.dev")

    def test_the_real_app_updates_from_the_real_releases(self):
        self.assertEqual(channel.REAL.release_api,
                         "https://api.github.com/repos/overjump1/overwatch-queue/releases/latest")


class ChannelTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(channel)

    def load(self, dev, worker_url=None):
        env = {"OVERQUEUE_WORKER_URL": worker_url} if worker_url else {}
        with mock.patch.object(version, "DEV", dev), mock.patch.dict(os.environ, env):
            if not worker_url:
                os.environ.pop("OVERQUEUE_WORKER_URL", None)
            return importlib.reload(channel)

    def test_mains_release_is_the_real_app(self):
        loaded = self.load(dev=False)
        self.assertEqual(loaded.CURRENT, loaded.REAL)
        self.assertEqual(loaded.WORKER_URL, loaded.REAL.worker_url)

    def test_everything_else_is_overqueue_dev(self):
        loaded = self.load(dev=True)
        self.assertEqual(loaded.CURRENT, loaded.DEV)
        self.assertEqual(loaded.WORKER_URL, loaded.DEV.worker_url)

    def test_the_two_never_share_a_pairing_or_a_link(self):
        self.assertNotEqual(channel.REAL.data_dir, channel.DEV.data_dir)
        self.assertNotEqual(channel.REAL.pair_scheme, channel.DEV.pair_scheme)
        self.assertNotEqual(channel.REAL.worker_url, channel.DEV.worker_url)

    def test_a_worker_url_from_the_environment_wins(self):
        self.assertEqual(self.load(dev=False, worker_url="http://localhost:8787/").WORKER_URL,
                         "http://localhost:8787")


if __name__ == "__main__":
    unittest.main()
