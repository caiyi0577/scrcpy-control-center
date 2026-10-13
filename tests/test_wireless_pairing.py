"""Pairing details must come from the active dialog or a matching mDNS service."""

import unittest

from wireless_pairing import (
    choose_pairing_endpoint,
    pairing_service_endpoints,
    parse_pairing_dialog,
)


class WirelessPairingTests(unittest.TestCase):
    def test_aosp_dialog_prefers_pairing_endpoint_over_connect_endpoint(self):
        hierarchy = (
            '<hierarchy><node package="com.android.settings" text="192.168.1.50:37123" '
            'resource-id="com.android.settings:id/connection_address"/>'
            '<node package="com.android.settings" text="515109" '
            'resource-id="com.android.settings:id/pairing_code"/>'
            '<node package="com.android.settings" text="192.168.1.50:43811" '
            'resource-id="com.android.settings:id/ip_addr"/></hierarchy>'
        )
        self.assertEqual(parse_pairing_dialog(hierarchy), ("515109", "192.168.1.50:43811"))

    def test_unlabeled_vendor_dialog_uses_visible_pairing_context(self):
        hierarchy = (
            '<hierarchy><node package="com.oplus.settings" text="使用配对码配对设备"/>'
            '<node package="com.oplus.settings" text="123456"/>'
            '<node package="com.oplus.settings" text="10.0.0.8:45213"/></hierarchy>'
        )
        self.assertEqual(parse_pairing_dialog(hierarchy), ("123456", "10.0.0.8:45213"))

    def test_unrelated_six_digit_text_is_not_a_pairing_code(self):
        hierarchy = (
            '<hierarchy><node package="com.example.app" text="配对码"/>'
            '<node package="com.example.app" text="123456"/></hierarchy>'
        )
        self.assertEqual(parse_pairing_dialog(hierarchy), ("", ""))

    def test_mdns_ignores_qr_service_and_does_not_guess_other_phone(self):
        output = (
            "List of discovered mdns services\n"
            "adb-phone-a _adb-tls-pairing._tcp 192.168.1.50:43811\n"
            "adb-phone-b _adb-tls-pairing._tcp 192.168.1.51:41414\n"
            "studio-random _adb-tls-pairing._tcp 192.168.1.52:50000\n"
            "adb-phone-a _adb-tls-connect._tcp 192.168.1.50:37123\n"
        )
        endpoints = pairing_service_endpoints(output)
        self.assertEqual(endpoints, ["192.168.1.50:43811", "192.168.1.51:41414"])
        self.assertEqual(choose_pairing_endpoint(endpoints, "192.168.1.51"), "192.168.1.51:41414")
        self.assertEqual(choose_pairing_endpoint(endpoints), "")
        self.assertEqual(choose_pairing_endpoint(endpoints, "192.168.1.52"), "")


if __name__ == "__main__":
    unittest.main()
