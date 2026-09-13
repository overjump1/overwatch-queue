"""What the push relay would actually receive, without ever talking to it.

`FCMClient._post` is the one method that touches the network, so replacing it is enough
to inspect the exact JSON body. `_post` itself is exercised against a local HTTP server.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import threading
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from owqserver import fcm                                           # noqa: E402
from owqserver.fcm import FCMClient, _Response                      # noqa: E402

RELAY = "https://relay.example"
DEVICE = "fGhIjKlMnOp:APA91bH-registration-token"
START = "80-hex-ish-push-to-start-token"
UPDATE = "80-hex-ish-per-activity-token"
ATTRIBUTES = {"sessionID": "s-1"}
STATE = {"phase": "searching", "role": "tank"}


def _response(status_code=200, payload=None):
    return _Response(status_code, b"" if payload is None else json.dumps(payload).encode())


class _Client(FCMClient):
    """The real client with only the network call replaced, recording every call in full."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self.response = _response()
        self.raises = None

    def _post(self, url, body):
        self.calls.append({"url": url, "body": json.loads(json.dumps(body))})
        if self.raises is not None:
            raise self.raises
        return self.response


class RelayURLTests(unittest.TestCase):
    def test_the_built_in_relay_is_used_by_default(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(fcm.RELAY_URL_ENV, None)
            self.assertEqual(fcm.relay_url(), fcm.RELAY_URL)

    def test_another_relay_can_be_named(self):
        with mock.patch.dict(os.environ, {fcm.RELAY_URL_ENV: "http://127.0.0.1:8787/"}):
            self.assertEqual(fcm.relay_url(), "http://127.0.0.1:8787")

    def test_an_empty_url_turns_pushing_off(self):
        with mock.patch.dict(os.environ, {fcm.RELAY_URL_ENV: ""}):
            self.assertIsNone(fcm.relay_url())


class FCMSendTests(unittest.TestCase):
    def setUp(self):
        self.logged = []
        self.client = _Client(RELAY + "/", self.logged.append)
        self.client.device_token = DEVICE

    def only_call(self):
        self.assertEqual(len(self.client.calls), 1)
        return self.client.calls[0]

    def only_aps(self):
        return self.only_call()["body"]["aps"]

    # ------------------------------------------------------------ addressing

    def test_a_start_addresses_the_device_and_the_card_separately(self):
        # The trap this pins down: the device's FCM registration token and the ActivityKit
        # one are different things. Neither substitutes for the other, and swapping them
        # is accepted with a 200 that never arrives.
        self.client.send_activity_start(START, ATTRIBUTES, STATE, 1700)
        body = self.only_call()["body"]
        self.assertEqual(body["deviceToken"], DEVICE)
        self.assertEqual(body["liveActivityToken"], START)

    def test_it_posts_to_the_relay(self):
        self.client.send_activity_start(START, ATTRIBUTES, STATE, 1700)
        self.assertEqual(self.only_call()["url"], "https://relay.example/v1/push")

    def test_it_sends_no_credential(self):
        # The whole point of the relay: nothing on this PC can push to anyone else.
        self.client.send_activity_update(UPDATE, STATE, 1700)
        self.assertEqual(set(self.only_call()["body"]), {"deviceToken", "liveActivityToken", "aps"})

    def test_no_device_token_means_no_push(self):
        self.client.device_token = None
        self.assertIsNone(self.client.send_activity_start(START, ATTRIBUTES, STATE, 1700))
        self.assertEqual(self.client.calls, [])

    def test_no_activity_token_means_no_push(self):
        self.assertIsNone(self.client.send_activity_update(None, STATE, 1700))
        self.assertEqual(self.client.calls, [])

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
        self.client.raises = urllib.error.URLError("network down")
        self.assertIsNone(self.client.send_activity_update(UPDATE, STATE, 1700))
        self.assertTrue(any("network down" in line for line in self.logged))

    def test_a_refusal_is_logged_and_still_returned(self):
        self.client.response = _response(403, {"error": {"status": "PERMISSION_DENIED"}})
        response = self.client.send_activity_update(UPDATE, STATE, 1700)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(any("refused" in line for line in self.logged))


class _RelayHandler(http.server.BaseHTTPRequestHandler):
    received = []
    status, reply = 200, b'{"name": "projects/p/messages/1"}'

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        type(self).received.append({"path": self.path, "headers": dict(self.headers),
                                    "body": json.loads(self.rfile.read(length))})
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.reply)

    def log_message(self, *args):
        pass


class FCMPostTests(unittest.TestCase):
    """`_post` against a real (local) HTTP server, so the urllib half is covered too."""

    def setUp(self):
        _RelayHandler.received = []
        _RelayHandler.status, _RelayHandler.reply = 200, b'{"name": "projects/p/messages/1"}'
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), _RelayHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = FCMClient("http://127.0.0.1:%d" % self.httpd.server_address[1])
        self.client.device_token = DEVICE

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_it_posts_json_under_a_real_user_agent(self):
        response = self.client.send_activity_update(UPDATE, STATE, 1700)
        self.assertEqual(response.status_code, 200)
        request = _RelayHandler.received[0]
        self.assertEqual(request["path"], "/v1/push")
        self.assertEqual(request["headers"]["User-Agent"], "OverwatchQueueServer/1.0")
        self.assertEqual(request["body"]["aps"]["event"], "update")

    def test_an_error_status_comes_back_as_a_response_not_an_exception(self):
        _RelayHandler.status, _RelayHandler.reply = 404, b'{"error": {"status": "UNREGISTERED"}}'
        response = self.client.send_activity_update(UPDATE, STATE, 1700)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(FCMClient.token_is_invalid(response))

    def test_an_unreachable_relay_is_logged(self):
        with socket.socket() as closed:                 # a port nothing is listening on
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
        logged = []
        client = FCMClient("http://127.0.0.1:%d" % port, logged.append)
        client.device_token = DEVICE
        self.assertIsNone(client.send_activity_update(UPDATE, STATE, 1700))
        self.assertTrue(any("failed" in line for line in logged))


class FCMTokenInvalidTests(unittest.TestCase):
    """`token_is_invalid` is what makes the server drop a dead registration, so it has to
    be exact: too eager and a working phone stops getting pushes."""

    def test_nothing_sent_is_not_an_invalid_token(self):
        self.assertFalse(FCMClient.token_is_invalid(None))

    def test_success_is_not_an_invalid_token(self):
        self.assertFalse(FCMClient.token_is_invalid(_response(200)))

    def test_a_server_error_is_not_an_invalid_token(self):
        # Firebase — or the relay — being down must not make us forget the phone.
        self.assertFalse(FCMClient.token_is_invalid(_response(503, {})))
        self.assertFalse(FCMClient.token_is_invalid(_response(502, {"error": "firebase_unreachable"})))

    def test_unregistered_means_the_app_is_gone(self):
        self.assertTrue(FCMClient.token_is_invalid(
            _response(404, {"error": {"status": "UNREGISTERED"}})))

    def test_a_malformed_token_is_invalid(self):
        self.assertTrue(FCMClient.token_is_invalid(
            _response(400, {"error": {"status": "INVALID_ARGUMENT"}})))

    def test_a_bad_request_for_another_reason_is_not(self):
        self.assertFalse(FCMClient.token_is_invalid(
            _response(400, {"error": {"status": "QUOTA_EXCEEDED"}})))

    def test_the_relay_refusing_a_request_is_not(self):
        self.assertFalse(FCMClient.token_is_invalid(
            _response(400, {"error": "aps.timestamp must be a non-negative integer"})))

    def test_an_unparseable_body_is_not(self):
        self.assertFalse(FCMClient.token_is_invalid(_response(400)))


if __name__ == "__main__":
    unittest.main()
