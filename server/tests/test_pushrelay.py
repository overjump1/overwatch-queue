"""`PushRelayClient` against a tiny fake relay — a real HTTP round trip, no mocking
library needed, mirroring how `test_server.py` uses a real socket."""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from owqserver.pushrelay import PushRelayClient, PushRelayConfig       # noqa: E402


class _FakeRelay(http.server.BaseHTTPRequestHandler):
    """Records the last request it received, and answers however the test asked."""

    status = 200
    body = b"{}"
    last_path = None
    last_headers = None
    last_body = None

    def do_POST(self):                                                 # noqa: N802
        type(self).last_path = self.path
        type(self).last_headers = dict(self.headers)
        length = int(self.headers.get("Content-Length", 0))
        type(self).last_body = json.loads(self.rfile.read(length) or b"{}")

        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *args):
        pass                                                            # quiet tests


class PushRelayClientTests(unittest.TestCase):
    def setUp(self):
        _FakeRelay.status, _FakeRelay.body = 200, b"{}"
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _FakeRelay)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.config = PushRelayConfig(url="http://127.0.0.1:%d" % port, api_key="secret-key")
        self.client = PushRelayClient(self.config)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_sends_a_background_push_with_the_bearer_token(self):
        self.client.send_background("phone", "device-token", "sandbox", "session-1", 5)
        self.assertEqual(_FakeRelay.last_path, "/v1/push")
        self.assertEqual(_FakeRelay.last_headers["Authorization"], "Bearer secret-key")
        # `urllib`'s default User-Agent ("Python-urllib/x.y") gets a 403 from
        # Cloudflare's bot protection before the request ever reaches the Worker —
        # caught in the field once already, so it's pinned here.
        self.assertNotIn("python-urllib", _FakeRelay.last_headers["User-Agent"].lower())
        self.assertEqual(_FakeRelay.last_body, {
            "kind": "phone", "token": "device-token", "environment": "sandbox",
            "pushType": "background", "sessionId": "session-1", "sequence": 5,
        })

    def test_sends_an_alert_push_with_title_and_body(self):
        self.client.send_alert("watch", "device-token", "production",
                               "Match Found", "Get back to your PC.", "session-2", 9)
        self.assertEqual(_FakeRelay.last_body, {
            "kind": "watch", "token": "device-token", "environment": "production",
            "pushType": "alert", "sessionId": "session-2", "sequence": 9,
            "title": "Match Found", "body": "Get back to your PC.",
        })

    def test_sends_a_live_activity_start_with_attributes(self):
        self.client.send_activity_start(
            "start-token", "sandbox",
            attributes={"sessionID": "abc", "startedAt": 721692800.0},
            content_state={"phase": {"type": "idle"}, "sequence": 0},
            timestamp=1700000000)
        self.assertEqual(_FakeRelay.last_body, {
            "pushType": "liveActivityStart", "token": "start-token", "environment": "sandbox",
            "timestamp": 1700000000,
            "attributes": {"sessionID": "abc", "startedAt": 721692800.0},
            "contentState": {"phase": {"type": "idle"}, "sequence": 0},
        })

    def test_live_activity_start_includes_an_alert_when_given_one(self):
        self.client.send_activity_start(
            "start-token", "sandbox", attributes={}, content_state={}, timestamp=1,
            alert={"title": "Match Found", "body": "Get back to your PC."})
        self.assertEqual(_FakeRelay.last_body["alert"],
                         {"title": "Match Found", "body": "Get back to your PC."})

    def test_sends_a_live_activity_update(self):
        self.client.send_activity_update(
            "activity-token", "production",
            content_state={"phase": {"type": "searching"}, "sequence": 3}, timestamp=5)
        self.assertEqual(_FakeRelay.last_body, {
            "pushType": "liveActivityUpdate", "token": "activity-token", "environment": "production",
            "timestamp": 5, "contentState": {"phase": {"type": "searching"}, "sequence": 3},
        })

    def test_live_activity_update_includes_a_stale_date_when_given_one(self):
        self.client.send_activity_update(
            "activity-token", "production", content_state={}, timestamp=1, stale_date=1700000500)
        self.assertEqual(_FakeRelay.last_body["staleDate"], 1700000500)

    def test_sends_a_live_activity_end(self):
        self.client.send_activity_end(
            "activity-token", "production", content_state={"phase": {"type": "idle"}}, timestamp=9)
        self.assertEqual(_FakeRelay.last_body, {
            "pushType": "liveActivityEnd", "token": "activity-token", "environment": "production",
            "timestamp": 9, "contentState": {"phase": {"type": "idle"}},
        })

    def test_a_successful_response_is_not_treated_as_an_invalid_token(self):
        response = self.client.send_background("phone", "t", "sandbox", "s", 1)
        self.assertFalse(PushRelayClient.token_is_invalid(response))

    def test_a_bad_device_token_response_is_recognised(self):
        _FakeRelay.status, _FakeRelay.body = 400, b'{"reason":"BadDeviceToken"}'
        response = self.client.send_background("phone", "t", "sandbox", "s", 1)
        self.assertTrue(PushRelayClient.token_is_invalid(response))

    def test_an_unregistered_response_is_recognised(self):
        _FakeRelay.status, _FakeRelay.body = 410, b'{"reason":"Unregistered"}'
        response = self.client.send_background("phone", "t", "sandbox", "s", 1)
        self.assertTrue(PushRelayClient.token_is_invalid(response))

    def test_an_unrelated_error_is_not_treated_as_an_invalid_token(self):
        _FakeRelay.status, _FakeRelay.body = 500, b"{}"
        response = self.client.send_background("phone", "t", "sandbox", "s", 1)
        self.assertFalse(PushRelayClient.token_is_invalid(response))


class PushRelayConfigTests(unittest.TestCase):
    def test_missing_file_loads_as_none(self):
        self.assertIsNone(PushRelayConfig.load("/no/such/file.json"))

    def test_incomplete_config_loads_as_none(self):
        import tempfile
        handle, path = tempfile.mkstemp()
        try:
            with os.fdopen(handle, "w") as f:
                json.dump({"url": "https://relay.example"}, f)          # no api_key
            self.assertIsNone(PushRelayConfig.load(path))
        finally:
            os.remove(path)

    def test_a_complete_config_loads(self):
        import tempfile
        handle, path = tempfile.mkstemp()
        try:
            with os.fdopen(handle, "w") as f:
                json.dump({"url": "https://relay.example/", "api_key": "k"}, f)
            config = PushRelayConfig.load(path)
            self.assertEqual(config.url, "https://relay.example")       # trailing slash trimmed
            self.assertEqual(config.api_key, "k")
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
