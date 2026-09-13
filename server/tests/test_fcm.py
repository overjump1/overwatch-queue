"""What Firebase would actually receive, without ever talking to Google's servers.

`FCMClient._send` reaches for `requests` and `google.auth` lazily, from inside the call,
which is what makes this testable without the extras installed: a fake `requests` module
in `sys.modules` and a stubbed access token are enough to inspect the exact JSON body.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from owqserver.fcm import FCMClient, FCMConfig                      # noqa: E402

DEVICE = "fGhIjKlMnOp:APA91bH-registration-token"
START = "80-hex-ish-push-to-start-token"
UPDATE = "80-hex-ish-per-activity-token"
ATTRIBUTES = {"sessionID": "s-1"}
STATE = {"phase": "searching", "role": "tank"}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.text = json.dumps(payload or {})
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeRequests(types.ModuleType):
    """Stands in for the real `requests`, recording every call in full."""

    def __init__(self, response=None):
        super().__init__("requests")
        self.calls = []
        self.response = response or _FakeResponse()
        self.raises = None

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(data),
                           "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return self.response


class _Client(FCMClient):
    """The real client with only the OAuth round-trip removed."""

    def _access_token(self):
        return "ya29.fake-access-token"


class FCMConfigTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _write(self, payload):
        with open(self.path, "w") as handle:
            handle.write(payload if isinstance(payload, str) else json.dumps(payload))

    def test_a_missing_file_is_not_configured(self):
        os.remove(self.path)
        self.assertIsNone(FCMConfig.load(self.path))

    def test_a_corrupt_file_is_not_configured(self):
        self._write("{not json")
        self.assertIsNone(FCMConfig.load(self.path))

    def test_a_service_account_without_a_project_is_not_configured(self):
        self._write({"type": "service_account", "client_email": "a@b.c"})
        self.assertIsNone(FCMConfig.load(self.path))

    def test_the_project_comes_off_the_service_account(self):
        self._write({"type": "service_account", "project_id": "overwatch-queue"})
        config = FCMConfig.load(self.path)
        self.assertEqual(config.project_id, "overwatch-queue")
        self.assertEqual(config.service_account_path, self.path)


class FCMSendTests(unittest.TestCase):
    def setUp(self):
        self.requests = _FakeRequests()
        self._saved = sys.modules.get("requests")
        sys.modules["requests"] = self.requests
        self.logged = []
        self.client = _Client(FCMConfig("overwatch-queue", os.devnull), self.logged.append)
        self.client.device_token = DEVICE

    def tearDown(self):
        if self._saved is None:
            del sys.modules["requests"]
        else:
            sys.modules["requests"] = self._saved

    def only_call(self):
        self.assertEqual(len(self.requests.calls), 1)
        return self.requests.calls[0]

    def only_aps(self):
        return self.only_call()["body"]["message"]["apns"]["payload"]["aps"]

    # ------------------------------------------------------------ addressing

    def test_a_start_addresses_the_device_and_the_card_separately(self):
        # The trap this pins down: `message.token` is the *device's* FCM registration
        # token, `apns.live_activity_token` the ActivityKit one. Neither substitutes for
        # the other, and swapping them is accepted with a 200 that never arrives.
        self.client.send_activity_start(START, ATTRIBUTES, STATE, 1700)
        message = self.only_call()["body"]["message"]
        self.assertEqual(message["token"], DEVICE)
        self.assertEqual(message["apns"]["live_activity_token"], START)

    def test_it_posts_to_the_project_send_endpoint(self):
        self.client.send_activity_start(START, ATTRIBUTES, STATE, 1700)
        self.assertEqual(self.only_call()["url"],
                         "https://fcm.googleapis.com/v1/projects/overwatch-queue/messages:send")

    def test_it_carries_the_bearer_token(self):
        self.client.send_activity_start(START, ATTRIBUTES, STATE, 1700)
        headers = self.only_call()["headers"]
        self.assertEqual(headers["Authorization"], "Bearer ya29.fake-access-token")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_every_push_is_an_immediate_live_activity(self):
        # Apple rejects a Live Activity push sent at any other priority or push type.
        self.client.send_activity_update(UPDATE, STATE, 1700)
        headers = self.only_call()["body"]["message"]["apns"]["headers"]
        self.assertEqual(headers["apns-priority"], "10")
        self.assertEqual(headers["apns-push-type"], "liveactivity")

    def test_no_device_token_means_no_push(self):
        self.client.device_token = None
        self.assertIsNone(self.client.send_activity_start(START, ATTRIBUTES, STATE, 1700))
        self.assertEqual(self.requests.calls, [])

    def test_no_activity_token_means_no_push(self):
        self.assertIsNone(self.client.send_activity_update(None, STATE, 1700))
        self.assertEqual(self.requests.calls, [])

    # ------------------------------------------------------------ the three events

    def test_a_start_carries_the_attributes_its_type_needs(self):
        self.client.send_activity_start(START, ATTRIBUTES, STATE, 1700)
        aps = self.only_aps()
        self.assertEqual(aps["event"], "start")
        self.assertEqual(aps["timestamp"], 1700)
        self.assertEqual(aps["attributes-type"], "QueueActivityAttributes")
        self.assertEqual(aps["attributes"], ATTRIBUTES)
        self.assertEqual(aps["content-state"], STATE)

    def test_an_update_carries_no_attributes(self):
        # The card already exists; `attributes` is only for the one that creates it.
        self.client.send_activity_update(UPDATE, STATE, 1700)
        aps = self.only_aps()
        self.assertEqual(aps["event"], "update")
        self.assertNotIn("attributes", aps)
        self.assertNotIn("attributes-type", aps)
        self.assertNotIn("stale-date", aps)

    def test_an_update_can_say_when_it_goes_stale(self):
        self.client.send_activity_update(UPDATE, STATE, 1700, stale_date=1900)
        self.assertEqual(self.only_aps()["stale-date"], 1900)

    def test_an_end_can_say_when_to_dismiss(self):
        self.client.send_activity_end(UPDATE, STATE, 1700, dismissal_date=1760)
        aps = self.only_aps()
        self.assertEqual(aps["event"], "end")
        self.assertEqual(aps["dismissal-date"], 1760)

    def test_an_end_without_a_dismissal_date_leaves_the_key_out(self):
        self.client.send_activity_end(UPDATE, STATE, 1700)
        self.assertNotIn("dismissal-date", self.only_aps())

    # ------------------------------------------------------------ alerts

    def test_an_alert_rides_along_when_there_is_one(self):
        alert = {"title": "Match found", "body": "Tank", "sound": "default"}
        self.client.send_activity_update(UPDATE, STATE, 1700, alert=alert)
        self.assertEqual(self.only_aps()["alert"], alert)

    def test_no_alert_means_a_silent_update(self):
        self.client.send_activity_update(UPDATE, STATE, 1700)
        self.assertNotIn("alert", self.only_aps())

    # ------------------------------------------------------------ failures

    def test_a_transport_failure_is_logged_not_raised(self):
        self.requests.raises = OSError("network down")
        self.assertIsNone(self.client.send_activity_update(UPDATE, STATE, 1700))
        self.assertTrue(any("network down" in line for line in self.logged))

    def test_a_refusal_is_logged_and_still_returned(self):
        self.requests.response = _FakeResponse(403, {"error": {"status": "PERMISSION_DENIED"}})
        response = self.client.send_activity_update(UPDATE, STATE, 1700)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(any("refused" in line for line in self.logged))


class FCMTokenInvalidTests(unittest.TestCase):
    """`token_is_invalid` is what makes the server drop a dead registration, so it has to
    be exact: too eager and a working phone stops getting pushes."""

    def test_nothing_sent_is_not_an_invalid_token(self):
        self.assertFalse(FCMClient.token_is_invalid(None))

    def test_success_is_not_an_invalid_token(self):
        self.assertFalse(FCMClient.token_is_invalid(_FakeResponse(200)))

    def test_a_server_error_is_not_an_invalid_token(self):
        # Firebase being down must not make us forget the phone.
        self.assertFalse(FCMClient.token_is_invalid(_FakeResponse(503, {})))

    def test_unregistered_means_the_app_is_gone(self):
        self.assertTrue(FCMClient.token_is_invalid(
            _FakeResponse(404, {"error": {"status": "UNREGISTERED"}})))

    def test_a_malformed_token_is_invalid(self):
        self.assertTrue(FCMClient.token_is_invalid(
            _FakeResponse(400, {"error": {"status": "INVALID_ARGUMENT"}})))

    def test_a_bad_request_for_another_reason_is_not(self):
        self.assertFalse(FCMClient.token_is_invalid(
            _FakeResponse(400, {"error": {"status": "QUOTA_EXCEEDED"}})))

    def test_an_unparseable_body_is_not(self):
        self.assertFalse(FCMClient.token_is_invalid(_FakeResponse(400)))


if __name__ == "__main__":
    unittest.main()
