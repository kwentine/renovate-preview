import unittest
from pathlib import Path

from renovate_preview.renovate import parse_record
from renovate_preview.report import dependency_rows, summarize

LOOKUP = Path(__file__).parent / "data" / "lookup.ndjson"
# A real run with minimumReleaseAge (Renovate 44.164.10, October 2026).
RELEASE_AGE = Path(__file__).parent / "data" / "release-age.ndjson"


def records(path=LOOKUP):
    return [r for r in map(parse_record, path.read_text().splitlines()) if r is not None]


class SummarizeTests(unittest.TestCase):
    """Against the report of a real Renovate 44 run of examples/separate-multiple-minor."""

    def setUp(self):
        self.document = summarize("examples/x", records())

    def test_one_row_per_update(self):
        rows = self.document["updates"]
        self.assertEqual([r["new"] for r in rows], ["v0.22.1", "v0.23.0", "v0.24.0", "v0.25.0"])
        self.assertEqual(rows[0]["branch"], "renovate/trust-manager-0.x")
        self.assertEqual(rows[0]["type"], "patch")
        self.assertEqual(rows[0]["file"], "kustomization.yaml")

    def test_run_facts(self):
        self.assertEqual(self.document["renovate_version"], "44.115.10")
        self.assertEqual(self.document["files_scanned"], 1)
        self.assertEqual(self.document["problems"], ["WARN: Package lookup failures"])

    def test_no_report(self):
        document = summarize("x", [])
        self.assertEqual(document["updates"], [])
        self.assertFalse(document["report_found"])

    def test_pr_titles_come_from_branches(self):
        report = {
            "repositories": {
                "o/r": {
                    "packageFiles": {
                        "kustomize": [
                            {
                                "packageFile": "k.yaml",
                                "deps": [
                                    {
                                        "depName": "a",
                                        "currentValue": "1",
                                        "updates": [{"newValue": "2", "branchName": "renovate/a"}],
                                    }
                                ],
                            }
                        ]
                    },
                    "branches": [
                        {
                            "branchName": "renovate/a",
                            "prTitle": "Update a to 2",
                            "upgrades": [{"depName": "a"}],
                        }
                    ],
                }
            }
        }
        document = summarize("@o/r", [{"msg": "Printing report", "report": report}])
        self.assertEqual(document["updates"][0]["pr_title"], "Update a to 2")
        self.assertEqual(document["branches"][0]["upgrades"], ["a"])


class ReleaseAgeTests(unittest.TestCase):
    """minimumReleaseAge of 14 days; 36 for cert-manager, 3650 for trust-manager."""

    def setUp(self):
        rows = summarize("x", records(RELEASE_AGE))["updates"]
        self.rows = {row["dependency"].rsplit("/", 1)[-1]: row for row in rows}

    def test_age(self):
        self.assertEqual(self.rows["busybox"]["age_days"], 23)
        self.assertEqual(self.rows["busybox"]["release_timestamp"], "2026-09-23T07:57:56.056Z")

    def test_no_release_date(self):
        # Not on the registry; and minimumReleaseAgeBehaviour=timestamp-required by default.
        self.assertIsNone(self.rows["pause"]["age_days"])
        self.assertTrue(self.rows["pause"]["pending_checks"])

    def test_too_young(self):
        self.assertEqual(self.rows["trust-manager"]["age_days"], 34)
        self.assertTrue(self.rows["trust-manager"]["pending_checks"])

    def test_newer_version_held_back(self):
        row = self.rows["cert-manager"]
        self.assertEqual((row["new"], row["pending_versions"]), ("v1.21.1", ["v1.21.2"]))
        self.assertFalse(row["pending_checks"])


class DependencyRowTests(unittest.TestCase):
    def test_dependency_without_updates_still_shows(self):
        rows = dependency_rows("m", {"packageFile": "f", "deps": [{"depName": "a"}]})
        self.assertEqual(rows[0]["new"], None)

    def test_digest_update_shows_the_digest(self):
        dep = {
            "depName": "a",
            "currentValue": "1.38",
            "updates": [{"newValue": "1.38", "newDigest": "sha256:0123456789abcdef"}],
        }
        rows = dependency_rows("m", {"packageFile": "f", "deps": [dep]})
        self.assertEqual(rows[0]["new"], "1.38@0123456789ab")


class ParseRecordTests(unittest.TestCase):
    def test_plain_text_is_not_a_record(self):
        self.assertIsNone(parse_record("npm notice"))
        self.assertIsNone(parse_record("{broken"))
        self.assertEqual(parse_record('{"msg": "x"}'), {"msg": "x"})
