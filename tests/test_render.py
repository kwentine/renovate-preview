import json
import re
import unittest

from renovate_preview.render import COLUMNS, default_columns, html_page, table, to_json
from renovate_preview.report import summarize

from .test_report import RELEASE_AGE, records

ALL = list(COLUMNS)


def header(text: str) -> list[str]:
    """Column titles, which can contain a single space ("PR TITLE")."""
    return re.split(r"\s{2,}", text.splitlines()[0].strip())


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.document = summarize("examples/x", records())

    def test_default_columns(self):
        self.assertEqual(default_columns(extract=False), ["dependency", "current", "new", "file"])
        self.assertEqual(default_columns(extract=True), ["dependency", "current", "file"])
        lines = table(self.document, default_columns(False)).splitlines()
        self.assertEqual(lines[0].split(), ["DEPENDENCY", "CURRENT", "NEW", "FILE"])
        self.assertEqual(len(lines), 2 + 4)

    def test_chosen_columns_in_their_order(self):
        text = table(self.document, ["file", "pr-title", "dependency"])
        self.assertEqual(header(text), ["FILE", "PR TITLE", "DEPENDENCY"])
        self.assertTrue(text.splitlines()[2].startswith("kustomization.yaml  -"))

    def test_empty_table(self):
        self.assertEqual(table(summarize("x", []), ALL), "No dependencies found.")

    def test_json_round_trips(self):
        self.assertEqual(json.loads(to_json(self.document)), self.document)

    def test_html_escapes(self):
        self.document["target"] = "<script>"
        page = html_page(self.document, ALL)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("v0.25.0", page)
        self.assertNotIn("<script>alert", page)


class ReleaseAgeTests(unittest.TestCase):
    """Against a real run with minimumReleaseAge: see tests/test_report.py."""

    def setUp(self):
        self.document = summarize("x", records(RELEASE_AGE))

    def test_table_columns(self):
        lines = table(self.document, ["dependency", "new", "type", "age", "pending"]).splitlines()
        cert_manager = next(line for line in lines if line.startswith("cert-manager"))
        self.assertEqual(
            cert_manager.split(), ["cert-manager", "v1.21.1", "minor", "78", "days", "v1.21.2"]
        )

    def test_breaking_and_versioning(self):
        lines = table(self.document, ["dependency", "breaking", "versioning"]).splitlines()
        rows = {line.split()[0]: line.split()[1:] for line in lines[2:]}
        self.assertEqual(rows["cert-manager"], ["no", "helm"])
        self.assertEqual(rows["trust-manager"], ["yes", "helm"])  # 0.x: breaking under semver
        self.assertEqual(rows["Deployment"], ["-", "kubernetes-api"])  # no update

    def test_html_highlights(self):
        page = html_page(self.document, ALL)
        # Two images without a release date; three pending updates and one held back.
        self.assertEqual(page.count('<td class="mono warn"'), 2)
        self.assertEqual(page.count('<td class="mono pending"'), 4)
        self.assertIn("no release date", page)
        self.assertEqual(page.count('<td class="major">yes</td>'), 1)

    def test_no_highlight_without_its_column(self):
        page = html_page(self.document, default_columns(False)).split("</style>")[1]
        self.assertNotIn("warn", page)
        self.assertNotIn("pending", page)
