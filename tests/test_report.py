import pytest

from renovate_preview.report import dependency_rows, parse_record, sort_key, summarize

from .conftest import INVALID_CONFIG, records

# The report of a real Renovate 44 run of examples/separate-multiple-minor: `lookup`.


def test_one_row_per_update(lookup):
    rows = lookup["updates"]
    assert [r["new"] for r in rows] == ["v0.22.1", "v0.23.0", "v0.24.0", "v0.25.0"]
    assert rows[0]["branch"] == "renovate/trust-manager-0.x"
    assert rows[0]["type"] == "patch"
    assert rows[0]["file"] == "kustomization.yaml"


def test_run_facts(lookup):
    assert lookup["renovate_version"] == "44.115.10"
    assert lookup["files_scanned"] == 1
    assert lookup["problems"] == ["WARN: Package lookup failures"]


def test_invalid_config_says_why():
    assert summarize("x", records(INVALID_CONFIG))["problems"] == [
        "WARN: Repository has invalid config: renovate.json: Invalid configuration option: foo, "
        "Invalid configuration option: packageRules[0].bogus"
    ]


def test_resolved_config():
    record = {"msg": "Full resolved config and hostRules including presets", "config": {"a": 1}}
    assert summarize("x", [record])["config"] == {"a": 1}
    assert summarize("x", [])["config"] is None


def test_no_report():
    assert summarize("x", [])["updates"] == []


def test_pr_titles_come_from_branches():
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
    assert document["updates"][0]["pr_title"] == "Update a to 2"
    assert document["branches"][0]["upgrades"] == ["a"]


# minimumReleaseAge of 14 days; 36 for cert-manager, 3650 for trust-manager: `release_age`.


@pytest.fixture
def rows(release_age):
    """Return the rows by the dependency's short name."""
    return {row["dependency"].rsplit("/", 1)[-1]: row for row in release_age["updates"]}


def test_age(rows):
    assert rows["busybox"]["age_days"] == 23
    assert rows["busybox"]["release_timestamp"] == "2026-09-23T07:57:56.056Z"


def test_no_release_date(rows):
    # Not on the registry; and minimumReleaseAgeBehaviour=timestamp-required by default.
    assert rows["pause"]["age_days"] is None
    assert rows["pause"]["pending_checks"]


def test_too_young(rows):
    assert rows["trust-manager"]["age_days"] == 34
    assert rows["trust-manager"]["pending_checks"]


def test_newer_version_held_back(rows):
    row = rows["cert-manager"]
    assert (row["new"], row["pending_versions"]) == ("v1.21.1", ["v1.21.2"])
    assert not row["pending_checks"]


# Sorting


def test_sorted_by_dependency_ignoring_case(release_age):
    names = [row["dependency"] for row in release_age["updates"]]
    assert names == sorted(names, key=str.lower)
    assert names[:2] == ["cert-manager", "Deployment"]


def test_youngest_first_then_unknown_age_in_renovate_order():
    def row(new, age):
        return {"dependency": "a", "new": new, "current": "1", "age_days": age, "file": "f"}

    rows = [row("v3", 10), row("v1", None), row("v2", 200), row("v0", None)]
    assert [r["new"] for r in sorted(rows, key=sort_key)] == ["v3", "v2", "v1", "v0"]


# Rows


def test_dependency_without_updates_still_shows():
    rows = dependency_rows("m", {"packageFile": "f", "deps": [{"depName": "a"}]})
    assert rows[0]["new"] is None


def test_digest_update_shows_the_digest():
    dep = {
        "depName": "a",
        "currentValue": "1.38",
        "updates": [{"newValue": "1.38", "newDigest": "sha256:0123456789abcdef"}],
    }
    rows = dependency_rows("m", {"packageFile": "f", "deps": [dep]})
    assert rows[0]["new"] == "1.38@0123456789ab"


def test_plain_text_is_not_a_record():
    assert parse_record("npm notice") is None
    assert parse_record("{broken") is None
    assert parse_record('{"msg": "x"}') == {"msg": "x"}
