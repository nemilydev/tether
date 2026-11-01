"""Tests for the manifest, HACS metadata and translation parity."""
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "tether"
REPO_URL = "https://github.com/nemilydev/tether"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    return {key for name, item in value.items() for key in _keys(item, f"{prefix}/{name}")}


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = _load(INTEGRATION / "manifest.json")

    def test_http_is_a_required_dependency(self) -> None:
        self.assertIn("http", self.manifest["dependencies"])

    def test_hassfest_key_order(self) -> None:
        keys = list(self.manifest)
        self.assertEqual(keys[:2], ["domain", "name"])
        self.assertEqual(keys[2:], sorted(keys[2:]))

    def test_release_metadata(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["domain"], "tether")
        self.assertEqual(manifest["codeowners"], ["@nemilydev"])
        self.assertIs(manifest["config_flow"], True)
        self.assertEqual(manifest["documentation"], REPO_URL)
        self.assertEqual(manifest["issue_tracker"], f"{REPO_URL}/issues")
        self.assertIn(manifest["integration_type"], {"hub", "device"})
        self.assertIn(manifest["iot_class"], {"local_push", "local_polling"})
        self.assertIsInstance(manifest["requirements"], list)
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")


class HacsTests(unittest.TestCase):
    def test_hacs_metadata(self) -> None:
        hacs = _load(ROOT / "hacs.json")
        self.assertEqual(hacs["name"], "Tether")
        self.assertRegex(hacs["homeassistant"], r"^\d{4}\.\d+\.\d+$")
        self.assertIs(hacs["zip_release"], True)
        self.assertEqual(hacs["filename"], "tether.zip")
        self.assertTrue((ROOT / "README.md").is_file())
        # Keys HACS no longer accepts for integrations.
        for key in ("domains", "iot_class", "render_readme", "content_in_root"):
            self.assertNotIn(key, hacs)

    def test_download_badge_counts_this_repository(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://img.shields.io/github/downloads/nemilydev/tether/total", readme)
        self.assertIn("owner=nemilydev&repository=tether&category=integration", readme)

    def test_release_workflow_uploads_the_hacs_asset(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        self.assertEqual(workflow["on"]["release"]["types"], ["published"])
        steps = workflow["jobs"]["release"]["steps"]
        checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ github.event.release.tag_name }}")
        package = next(step for step in steps if step.get("name") == "Package integration")
        self.assertEqual(package["working-directory"], "custom_components/tether")
        self.assertIn('zip -r "${RUNNER_TEMP}/tether.zip" .', package["run"])
        upload = next(step for step in steps if step.get("name") == "Upload release asset")
        self.assertIn('gh release upload "$RELEASE_TAG" "${RUNNER_TEMP}/tether.zip"', upload["run"])


class TranslationTests(unittest.TestCase):
    def test_english_translation_matches_strings(self) -> None:
        strings = _load(INTEGRATION / "strings.json")
        english = _load(INTEGRATION / "translations" / "en.json")
        self.assertEqual(_keys(strings), _keys(english))

    def test_every_service_is_translated(self) -> None:
        services = yaml.safe_load((INTEGRATION / "services.yaml").read_text(encoding="utf-8"))
        translations = _load(INTEGRATION / "strings.json")["services"]
        self.assertEqual(set(services), set(translations))
        for service, definition in services.items():
            fields = set((definition or {}).get("fields", {}))
            self.assertEqual(fields, set(translations[service].get("fields", {})), service)
            self.assertTrue(
                all(re.fullmatch(r"[a-z0-9_-]+", field) for field in fields),
                f"{service} contains a field key invalid for Hassfest translations",
            )

    def test_registered_services_are_documented(self) -> None:
        source = (INTEGRATION / "services.py").read_text(encoding="utf-8")
        registered = set(re.findall(r'^SERVICE_[A-Z_]+ = "([a-z_]+)"$', source, re.M))
        services = yaml.safe_load((INTEGRATION / "services.yaml").read_text(encoding="utf-8"))
        self.assertEqual(registered, set(services))
