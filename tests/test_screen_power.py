"""Offline regression tests for Android display-state handling."""
from __future__ import annotations

import unittest

from screen_power import physical_screen_on, power_commands


def panel(state="ON", logical="ON", kind="INTERNAL", default=True):
    default_flag = "FLAG_ALLOWED_TO_BE_DEFAULT_DISPLAY" if default else ""
    return (
        f"Display State={logical}\nDisplay Devices: size=1\n"
        f'  DisplayDeviceInfo{{"panel": type {kind}, {default_flag}}}\n'
        f"    mState={state}\n    mCommittedState={state}\n"
        f"Display Power State:\n  mScreenState={logical}\n"
    )


class ScreenPowerParsingTests(unittest.TestCase):
    def test_physical_state_is_not_logical_state(self):
        self.assertFalse(physical_screen_on(panel("OFF", "ON")))
        self.assertTrue(physical_screen_on(panel("ON", "OFF")))
        self.assertIsNone(physical_screen_on("Display State=ON\n mScreenState=ON"))

    def test_default_internal_display_is_selected(self):
        output = panel("OFF") + panel("ON", kind="EXTERNAL", default=False)
        self.assertFalse(physical_screen_on(output))
        self.assertIsNone(physical_screen_on(panel("ON", default=False) + panel("OFF", default=False)))

    def test_unknown_state_is_safe(self):
        self.assertIsNone(physical_screen_on(panel("UNKNOWN")))

    def test_power_command_variants(self):
        self.assertEqual(
            power_commands("  power-off DISPLAY_ID\n  power-reset DISPLAY_ID\n"),
            ("power-off", "power-reset"),
        )
        self.assertEqual(
            power_commands("  power-off DISPLAY_ID\n  power-on DISPLAY_ID\n"),
            ("power-off", "power-on"),
        )
        self.assertIsNone(power_commands("  power-off DISPLAY_ID\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
