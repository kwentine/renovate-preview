import json
import re

from renovate_preview.render import COLUMNS, default_columns, html_page, table, to_json
from renovate_preview.report import summarize

ALL = list(COLUMNS)


def header(text: str) -> list[str]:
    """Column titles, which can contain a single space ("PR TITLE")."""
    return re.split(r"\s{2,}", text.splitlines()[0].strip())


def test_default_columns(lookup):
    assert default_columns(extract=True, remote=False) == ["dependency", "current", "file"]
    assert default_columns(extract=False, remote=False) == ["dependency", "current", "new", "file"]
    assert default_columns(extract=False, remote=True)[-1] == "pr-title"
    assert "pr-title" not in default_columns(extract=True, remote=True)
    lines = table(lookup, default_columns(extract=False, remote=False)).splitlines()
    assert lines[0].split() == ["DEPENDENCY", "CURRENT", "NEW", "FILE"]
    assert len(lines) == 2 + 4


def test_chosen_columns_in_their_order(lookup):
    text = table(lookup, ["file", "pr-title", "dependency"])
    assert header(text) == ["FILE", "PR TITLE", "DEPENDENCY"]
    assert text.splitlines()[2].startswith("kustomization.yaml  -")


def test_empty_table():
    assert table(summarize("x", []), ALL) == "No dependencies found."


def test_json_round_trips(lookup):
    assert json.loads(to_json(lookup)) == lookup


def test_html_escapes(lookup):
    lookup["target"] = "<script>"
    page = html_page(lookup, ALL)
    assert "&lt;script&gt;" in page
    assert "v0.25.0" in page
    assert "<script>alert" not in page


# Against a real run with minimumReleaseAge: see tests/test_report.py.


def test_age_and_pending(release_age):
    lines = table(release_age, ["dependency", "new", "type", "age", "pending"]).splitlines()
    cert_manager = next(line for line in lines if line.startswith("cert-manager"))
    assert cert_manager.split() == ["cert-manager", "v1.21.1", "minor", "78", "days", "v1.21.2"]


def test_breaking_and_versioning(release_age):
    lines = table(release_age, ["dependency", "breaking", "versioning"]).splitlines()
    rows = {line.split()[0]: line.split()[1:] for line in lines[2:]}
    assert rows["cert-manager"] == ["no", "helm"]
    assert rows["trust-manager"] == ["yes", "helm"]  # 0.x: breaking under semver
    assert rows["Deployment"] == ["-", "kubernetes-api"]  # no update


def test_html_highlights(release_age):
    page = html_page(release_age, ALL)
    # Two images without a release date; three pending updates and one held back.
    assert page.count('<td class="mono warn"') == 2
    assert page.count('<td class="mono pending"') == 4
    assert "no release date" in page
    assert page.count('<td class="major">yes</td>') == 1


def test_no_highlight_without_its_column(release_age):
    page = html_page(release_age, default_columns(False, False)).split("</style>")[1]
    assert "warn" not in page
    assert "pending" not in page
