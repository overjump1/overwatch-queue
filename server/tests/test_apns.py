"""Config loading and JWT signing, without ever talking to Apple's servers."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import jwt                                                         # noqa: E402
from cryptography.hazmat.primitives import serialization           # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec           # noqa: E402

from owqserver.apns import APNsClient, APNsConfig                  # noqa: E402


def _generate_p8() -> str:
    """A throwaway EC P-256 key in the same PEM shape as Apple's `.p8` download."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(encoding=serialization.Encoding.PEM,
                            format=serialization.PrivateFormat.PKCS8,
                            encryption_algorithm=serialization.NoEncryption())
    return pem.decode(), key


class _FakeResponse:
    def __init__(self, status_code, reason=None):
        self.status_code = status_code
        self._reason = reason
        self.text = "{}"

    def json(self):
        return {"reason": self._reason} if self._reason else {}


class APNsConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.dir, "apns.json")
        self.pem, _ = _generate_p8()
        with open(os.path.join(self.dir, "AuthKey_ABC123.p8"), "w") as handle:
            handle.write(self.pem)

    def _write_config(self, **overrides):
        data = {"team_id": "TEAM1234", "key_id": "ABC123",
                "bundle_id_ios": "com.tomerady.OverwatchQueue",
                "bundle_id_watch": "com.tomerady.OverwatchQueue.watchkitapp"}
        data.update(overrides)
        with open(self.config_path, "w") as handle:
            json.dump(data, handle)

    def test_loads_a_complete_config(self):
        self._write_config()
        config = APNsConfig.load(self.config_path)
        self.assertIsNotNone(config)
        self.assertEqual(config.team_id, "TEAM1234")
        self.assertEqual(config.bundle_id("phone"), "com.tomerady.OverwatchQueue")
        self.assertEqual(config.bundle_id("watch"), "com.tomerady.OverwatchQueue.watchkitapp")

    def test_missing_file_is_not_an_error(self):
        self.assertIsNone(APNsConfig.load(os.path.join(self.dir, "nope.json")))

    def test_incomplete_config_is_rejected(self):
        self._write_config(team_id="")
        self.assertIsNone(APNsConfig.load(self.config_path))

    def test_missing_key_file_is_rejected(self):
        self._write_config(key_id="NOSUCHKEY")
        self.assertIsNone(APNsConfig.load(self.config_path))


class APNsClientTests(unittest.TestCase):
    def setUp(self):
        self.pem, self.key = _generate_p8()
        self.config = APNsConfig("TEAM1234", "ABC123", "com.tomerady.OverwatchQueue",
                                 "com.tomerady.OverwatchQueue.watchkitapp", self.pem)
        self.client = APNsClient(self.config)

    def test_signs_a_verifiable_token(self):
        token = self.client._signed_auth_token()
        public_pem = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        header = jwt.get_unverified_header(token)
        self.assertEqual(header["kid"], "ABC123")
        claims = jwt.decode(token, public_pem, algorithms=["ES256"])
        self.assertEqual(claims["iss"], "TEAM1234")

    def test_token_is_cached_between_calls(self):
        first = self.client._signed_auth_token()
        second = self.client._signed_auth_token()
        self.assertEqual(first, second)

    def test_bad_device_token_is_invalid(self):
        self.assertTrue(APNsClient.token_is_invalid(_FakeResponse(400, "BadDeviceToken")))

    def test_unregistered_is_invalid(self):
        self.assertTrue(APNsClient.token_is_invalid(_FakeResponse(410, "Unregistered")))

    def test_other_errors_are_not_treated_as_an_invalid_token(self):
        self.assertFalse(APNsClient.token_is_invalid(_FakeResponse(500)))
        self.assertFalse(APNsClient.token_is_invalid(_FakeResponse(400, "PayloadTooLarge")))

    def test_no_response_is_not_treated_as_an_invalid_token(self):
        self.assertFalse(APNsClient.token_is_invalid(None))


if __name__ == "__main__":
    unittest.main()
