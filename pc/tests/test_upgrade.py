"""An install upgraded from OverQueue keeps its pairing, and knows the old phones are on it."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402

PAIR_ID = "c" * 32


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.old = os.path.join(self.root, "OverQueue")
        self.new = os.path.join(self.root, "QueueFox")
        self.pairing = os.path.join(self.new, "pairing.json")

    def adopt(self):
        app.adopt_old_data_dir(self.old, self.new, self.pairing)

    def write_old_pairing(self):
        os.makedirs(self.old)
        with open(os.path.join(self.old, "pairing.json"), "w", encoding="utf-8") as handle:
            json.dump({"id": PAIR_ID}, handle)

    def test_the_old_folder_moves_over_with_its_pairing(self):
        self.write_old_pairing()
        self.adopt()
        self.assertFalse(os.path.exists(self.old))
        with open(self.pairing, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["id"], PAIR_ID)
        self.assertTrue(app.pairing_uses_old_worker(self.pairing))

    def test_an_existing_folder_is_left_alone(self):
        self.write_old_pairing()
        os.makedirs(self.new)
        self.adopt()
        self.assertTrue(os.path.exists(self.old))
        self.assertFalse(app.pairing_uses_old_worker(self.pairing))

    def test_a_fresh_install_starts_without_the_old_worker(self):
        self.adopt()
        self.assertFalse(os.path.exists(self.new))
        self.assertFalse(app.pairing_uses_old_worker(self.pairing))

    def test_a_new_code_drops_the_old_worker(self):
        os.makedirs(self.new)
        with open(self.pairing, "w", encoding="utf-8") as handle:
            json.dump({"id": PAIR_ID, "oldWorker": True}, handle)
        app._save_pairing({"id": "d" * 32}, self.pairing)
        self.assertFalse(app.pairing_uses_old_worker(self.pairing))


if __name__ == "__main__":
    unittest.main()
