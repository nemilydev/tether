"""Unit tests for Standard API pairing helpers."""
import unittest

from custom_components.tether.utils import (
    connection_info_from_callback,
    extract_pairing_code,
    extract_qr_image_url,
    normalize_toys,
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

    def test_ignores_malformed_qr_values_before_falling_back(self) -> None:
        for value in (
            None, [], {}, 123, "", "https://", "https:///qr.jpg",
            "https://example.com:bad/qr.jpg", "https://example.com:65536/qr.jpg",
            "https://[invalid/qr.jpg", "https://user:secret@example.com/qr.jpg",
            "https://example.com/\nqr.jpg", "https://bad host/qr.jpg",
        ):
            with self.subTest(value=value):
                self.assertEqual(extract_qr_image_url({"qr": value}), "")
                self.assertEqual(extract_qr_image_url({"qr": value, "qrCodeUrl": QR_URL}), QR_URL)

    def test_qr_url_is_trimmed_and_accepts_https_case_insensitively(self) -> None:
        url = "HTTPS://example.com/qr.jpg"
        self.assertEqual(extract_qr_image_url({"qr": f" {url} "}), url)

    def test_pairing_code_must_be_alphanumeric(self) -> None:
        self.assertEqual(extract_pairing_code({"code": 123456}), "123456")
        self.assertEqual(extract_pairing_code({"code": "**bold**"}), "")
        self.assertEqual(extract_pairing_code({}), "")

    def test_pairing_code_rejects_wrong_types_and_preserves_strings(self) -> None:
        for value in (None, True, False, [], {}, 1.5, "", " ", "AB-12", "A\nB"):
            with self.subTest(value=value):
                self.assertEqual(extract_pairing_code({"code": value}), "")
        self.assertEqual(extract_pairing_code({"code": " 0012AB "}), "0012AB")
        self.assertEqual(extract_pairing_code({"code": 0}), "0")

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


class CallbackToyTests(unittest.TestCase):
    def test_strict_normalization_accepts_empty_snapshots(self) -> None:
        for payload in ({}, "{}", {"toys": {}}, '{"toys": "{}"}'):
            with self.subTest(payload=payload):
                self.assertEqual(normalize_toys(payload, strict=True), {})

    def test_strict_normalization_rejects_partial_maps(self) -> None:
        payload = {"good": {"name": "lush"}, "bad": None}
        self.assertEqual(normalize_toys(payload), {"good": {"name": "lush"}})
        with self.assertRaises(ValueError):
            normalize_toys(payload, strict=True)

    def test_nested_toy_envelopes_do_not_use_python_recursion(self) -> None:
        payload = {"toy": {"name": "lush"}}
        for _ in range(2000):
            payload = {"toys": payload}
        self.assertEqual(normalize_toys(payload, strict=True), {"toy": {"name": "lush"}})


if __name__ == "__main__":
    unittest.main()
