"""Tests for HACS custom repository validation settings."""
import re
import unittest
from pathlib import Path


class WorkflowTests(unittest.TestCase):
    def test_hacs_ignores_only_publication_checks(self) -> None:
        workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "validate.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        hacs_step = re.search(
            r"      - uses: hacs/action@main\n((?:        .*\n)*)", workflow
        )
        self.assertIsNotNone(hacs_step)
        settings = hacs_step.group(1)
        self.assertRegex(settings, r"(?m)^          category: integration$")
        ignored_checks = re.search(r"(?m)^          ignore: (.+)$", settings)
        self.assertIsNotNone(ignored_checks)
        self.assertCountEqual(ignored_checks.group(1).split(), ["brands", "topics"])

    def test_hassfest_and_pytest_run(self) -> None:
        workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "validate.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("uses: home-assistant/actions/hassfest@master", workflow)
        self.assertRegex(workflow, r"(?m)^  push:$")
        self.assertRegex(workflow, r"(?m)^  pull_request:$")
        self.assertIn("pip install -r requirements_test.txt", workflow)
        self.assertRegex(workflow, r"(?m)^      - run: python -m pytest")
