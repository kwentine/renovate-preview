import json
import unittest

from renovate_preview.render import html_page, table, to_json
from renovate_preview.report import summarize

from .test_report import records


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.document = summarize("examples/x", records())

    def test_table(self):
        lines = table(self.document).splitlines()
        self.assertEqual(
            lines[0].split(), ["DEPENDENCY", "CURRENT", "NEW", "TYPE", "FILE", "BRANCH"]
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
