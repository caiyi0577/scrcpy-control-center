"""Offline regression tests for Windows audio-output identifiers."""
from __future__ import annotations

import unittest

from audio_output import _unpack_endpoint_id


class AudioOutputTests(unittest.TestCase):
    def test_unpacks_render_endpoint_id_for_windows_policy_api(self):
        endpoint = (
            r"\\?\SWD#MMDEVAPI#"
            "{0.0.0.00000000}.{12345678-1234-1234-1234-123456789abc}"
            "#{e6327cad-dcec-4949-ae8a-991e976a79d2}"
        )
        self.assertEqual(
            _unpack_endpoint_id(endpoint),
            "{0.0.0.00000000}.{12345678-1234-1234-1234-123456789abc}",
        )

    def test_keeps_already_unpacked_endpoint_id(self):
        endpoint = "{0.0.0.00000000}.{12345678-1234-1234-1234-123456789abc}"
        self.assertEqual(_unpack_endpoint_id(endpoint), endpoint)


if __name__ == "__main__":
    unittest.main(verbosity=2)
