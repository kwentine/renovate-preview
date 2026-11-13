"""The output formats: a table to read, JSON to pipe, a page to open in a browser."""

from __future__ import annotations

import html
import json
from importlib.resources import files
from string import Template

# The columns --columns can name, in the order of --columns=all: {name: (title, row key)}.
COLUMNS = {
    "dependency": ("DEPENDENCY", "dependency"),
    "current": ("CURRENT", "current"),
    "new": ("NEW", "new"),
    "type": ("TYPE", "type"),
    "breaking": ("BREAKING", "breaking"),
    "age": ("AGE", "age_days"),
    "pending": ("PENDING", "pending"),
    "manager": ("MANAGER", "manager"),
    "datasource": ("DATASOURCE", "datasource"),
    "versioning": ("VERSIONING", "versioning"),
    "file": ("FILE", "file"),
    "branch": ("BRANCH", "branch"),
    "pr-title": ("PR TITLE", "pr_title"),
}


def default_columns(extract: bool, remote: bool) -> list[str]:
    """Return the columns shown by default: NEW needs lookups, PR TITLE a remote run."""
    names = ["dependency", "current"]
    if not extract:
        names.append("new")
    names.append("file")
    if remote and not extract:
        names.append("pr-title")
    return names


def cell(row: dict, key: str) -> str:
    if key == "new" and not row["new"] and row["skip_reason"]:
        return f"(skipped: {row['skip_reason']})"
    if key == "age_days" and row["age_days"] is not None:
        return f"{row['age_days']} day{'' if row['age_days'] == 1 else 's'}"
    if key == "pending":
        return pending(row)
    if key == "breaking":
        return {True: "yes", False: "no"}.get(row["breaking"], "-")
    return str(row[key] or "-")


def pending(row: dict) -> str:
    """Return the PENDING cell, like the Pending column of Renovate's PRs.

    "yes" when this update itself waits for its checks (e.g. minimumReleaseAge), else the
    newest version held back, if any.
    """
    if row["pending_checks"]:
        return "yes"
    held = row["pending_versions"] or []
    if not held:
        return "-"
    return held[-1] + (f" (+{len(held) - 1})" if len(held) > 1 else "")


def table(document: dict, names: list[str]) -> str:
    rows = document["updates"]
    if not rows:
        return "No dependencies found."
    cols = [COLUMNS[name] for name in names]
    data = [[title for title, _ in cols]] + [[cell(row, key) for _, key in cols] for row in rows]
    widths = [max(len(line[i]) for line in data) for i in range(len(cols))]
    data.insert(1, ["-" * w for w in widths])
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(line, widths)).rstrip() for line in data)


def to_json(document: dict) -> str:
    return json.dumps(document, indent=2)


MONO = ("current", "new", "age_days", "pending", "versioning", "file", "branch")


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def html_page(document: dict, names: list[str], show_config: bool = False) -> str:
    """Return the whole result as one self-contained page: no network, opens off disk."""
    rows = document["updates"]
    cols = [COLUMNS[name] for name in names]
    head = "".join(f"<th>{esc(title)}</th>" for title, _ in cols)
    body = []
    for row in rows:
        cells = []
        for _, key in cols:
            value = esc(cell(row, key))
            classes = ["mono"] if key in MONO else []
            hint = ""
            if key == "type" and row["type"]:
                classes += ["badge", row["type"]]
            elif key == "breaking" and row["breaking"]:
                classes.append("major")
            elif key == "age_days" and row["new"] and row["age_days"] is None:
                classes.append("warn")
                hint = "No release date: Renovate can't check minimumReleaseAge"
            elif key == "age_days" and row["release_timestamp"]:
                hint = f"Released {row['release_timestamp']}"
            elif key == "pending" and value != "-":
                classes.append("pending")
                hint = (
                    "This update waits for its checks, such as minimumReleaseAge"
                    if row["pending_checks"]
                    else "Newer versions wait for checks: " + ", ".join(row["pending_versions"])
                )
            attrs = f' class="{esc(" ".join(classes))}"' if classes else ""
            attrs += f' title="{esc(hint)}"' if hint else ""
            cells.append(f"<td{attrs}>{value}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")

    updates = sum(1 for row in rows if row["new"])
    summary = (
        f"{len(rows)} row(s), {updates} update(s), {document['files_scanned'] or 0} file(s) "
        f"scanned, Renovate {document['renovate_version'] or '?'}"
    )
    sections = [f"<h1>{esc(document['target'])}</h1>", f'<p class="sub">{esc(summary)}</p>']
    if show_config and document["config"] is not None:
        config = esc(json.dumps(document["config"], indent=2))
        sections.append(
            "<details><summary>Renovate's resolved config, presets included</summary>"
            f'<pre class="config">{config}</pre></details>'
        )
    if rows:
        sections.append('<input id="filter" type="search" placeholder="Filter…">')
        sections.append(
            f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        )
        legend = []
        keys = {key for _, key in cols}
        if "age_days" in keys and any(row["new"] and row["age_days"] is None for row in rows):
            legend.append(
                '<span class="warn">AGE -</span> no release date: Renovate can\'t check'
                " minimumReleaseAge"
            )
        if "pending" in keys and any(pending(row) != "-" for row in rows):
            legend.append(
                '<span class="pending">PENDING</span> waiting for checks such as'
                " minimumReleaseAge: yes for this update, or the newest version held back"
            )
        if legend:
            sections.append(f'<p class="sub">{"<br>".join(legend)}</p>')
    else:
        sections.append("<p>No dependencies found.</p>")
    if document["problems"]:
        problems = "\n".join(esc(p) for p in document["problems"])
        sections.append(f"<h2>Renovate reported</h2><pre>{problems}</pre>")

    # index.html holds the rest of the page: styles, and the filter's script. A literal $
    # there is written $$ (string.Template).
    page = Template(files(__package__).joinpath("index.html").read_text(encoding="utf-8"))
    return page.substitute(title=esc(document["target"]), main="".join(sections))
