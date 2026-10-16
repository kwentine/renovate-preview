import json
import unittest

from renovate_preview.render import html_page, table, to_json
from renovate_preview.report import summarize

from .test_report import RELEASE_AGE, records


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.document = summarize("examples/x", records())

    def test_table(self):
        lines = table(self.document).splitlines()
        self.assertEqual(
            lines[0].split(), ["DEPENDENCY", "CURRENT", "NEW", "TYPE", "AGE", "FILE", "BRANCH"]
        )
        self.assertEqual(len(lines), 2 + 4)
        self.assertNotIn("PR TITLE", lines[0])

    def test_empty_table(self):
        self.assertEqual(table(summarize("x", [])), "No dependencies found.")

    def test_json_round_trips(self):
        self.assertEqual(json.loads(to_json(self.document)), self.document)

    def test_html_escapes(self):
        self.document["target"] = "<script>"
        page = html_page(self.document)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("v0.25.0", page)
        self.assertNotIn("<script>alert", page)


class ReleaseAgeTests(unittest.TestCase):
    """Against a real run with minimumReleaseAge: see tests/test_report.py."""

    def setUp(self):
        self.document = summarize("x", records(RELEASE_AGE))

    def test_table_columns(self):
        lines = table(self.document).splitlines()
        self.assertEqual(lines[0].split()[3:6], ["TYPE", "AGE", "PENDING"])
        cert_manager = next(line for line in lines if line.startswith("cert-manager"))
        self.assertEqual(cert_manager.split()[2:6], ["v1.21.1", "minor", "78", "days"])
        self.assertIn(" v1.21.2 ", cert_manager)

    def test_html_highlights(self):
        page = html_page(self.document)
        # Two images without a release date; three pending updates and one held back.
        self.assertEqual(page.count('<td class="mono warn"'), 2)
        self.assertEqual(page.count('<td class="mono pending"'), 4)
        self.assertIn("no release date", page)

    def test_no_age_columns_without_updates(self):
        for row in self.document["updates"]:
            row.update(new=None, age_days=None, pending_checks=None, pending_versions=None)
        header = table(self.document).splitlines()[0]
        self.assertNotIn("AGE", header)
        self.assertNotIn("PENDING", header)
        self.assertNotIn("warn", html_page(self.document).split("</style>")[1])

    def test_age_column_stays_when_no_update_has_a_date(self):
        for row in self.document["updates"]:
            row.update(age_days=None, release_timestamp=None)
        self.assertIn("AGE", table(self.document).splitlines()[0])
