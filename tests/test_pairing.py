"""Unit tests for Standard API pairing helpers."""
import unittest

from custom_components.tether.utils import (
    connection_info_from_callback,
    extract_pairing_code,
    extract_qr_image_url,
    qr_markdown,
)

QR_URL = "https://test2.lovense.com/UploadFiles/qr/20220106/xxx.jpg"


class QrCodeTests(unittest.TestCase):
    def test_extracts_documented_getqrcode_response(self) -> None:
        data = {"qr": QR_URL, "code": "AB12CD"}
        self.assertEqual(extract_qr_image_url(data), QR_URL)
        self.assertEqual(extract_pairing_code(data), "AB12CD")

    def test_rejects_non_https_qr_sources(self) -> None:
        for value in ("http://example.com/qr.jpg", "javascript:alert(1)", "", None):
            with self.subTest(value=value):
                self.assertEqual(extract_qr_image_url({"qr": value}), "")

    def test_falls_back_to_alternate_qr_keys(self) -> None:
        self.assertEqual(extract_qr_image_url({"qrcodeUrl": QR_URL}), QR_URL)

    def test_pairing_code_must_be_alphanumeric(self) -> None:
        self.assertEqual(extract_pairing_code({"code": 123456}), "123456")
        self.assertEqual(extract_pairing_code({"code": "**bold**"}), "")
        self.assertEqual(extract_pairing_code({}), "")

    def test_markdown_embeds_image_and_link(self) -> None:
        markdown = qr_markdown(QR_URL)
        self.assertIn(f"![Lovense pairing QR code]({QR_URL})", markdown)
        self.assertIn(f"]({QR_URL})", markdown.split("\n\n")[1])

    def test_markdown_escapes_link_breaking_characters(self) -> None:
        markdown = qr_markdown("https://example.com/a (b).jpg")
        self.assertIn("https://example.com/a%20%28b%29.jpg", markdown)
        self.assertEqual(qr_markdown(""), "")


class ConnectionInfoTests(unittest.TestCase):
    def test_keeps_only_reconnect_fields(self) -> None:
        callback = {
            "uid": "user",
            "utoken": "secret",
            "toys": {"a": {"name": "lush"}},
            "domain": "192-0-2-44.lovense.club",
            "httpPort": "34567",
            "httpsPort": "34568",
            "wsPort": "34567",
            "wssPort": "34568",
            "platform": "android",
            "appType": "remote",
            "appVersion": "4.0.3",
            "version": "101",
        }
        info = connection_info_from_callback(callback)
        self.assertNotIn("utoken", info)
        self.assertNotIn("uid", info)
        self.assertNotIn("toys", info)
        self.assertEqual(info["domain"], "192-0-2-44.lovense.club")
        self.assertEqual(info["httpsPort"], "34568")

    def test_drops_empty_values(self) -> None:
        self.assertEqual(connection_info_from_callback({"domain": "", "httpPort": None}), {})


if __name__ == "__main__":
    unittest.main()
