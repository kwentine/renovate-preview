"""The output formats: a table to read, JSON to pipe, a page to open in a browser."""

from __future__ import annotations

import html
import json

# The columns --columns can name, in this order by default: {name: (title, row key)}.
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


def default_columns(extract: bool) -> list[str]:
    """With --extract there are no versions to look up, so no NEW."""
    return ["dependency", "current", *([] if extract else ["new"]), "file"]


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
    """Like the Pending column of Renovate's PRs: "yes" when this update itself waits for
    its checks (e.g. minimumReleaseAge), else the newest version held back, if any."""
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


STYLE = """
:root { color-scheme: light dark; --bg: #fbfbfa; --panel: #fff; --ink: #1c1c1a;
  --muted: #6a6a64; --line: #e3e3de; --major: #b3261e; --minor: #9a6700; --patch: #2f6f4f;
  --other: #5a5a8c; --warn-bg: #fbe3c2; --warn-ink: #7a4400; --pending-bg: #e6e8f6; }
@media (prefers-color-scheme: dark) { :root { --bg: #17171a; --panel: #1f1f23;
  --ink: #e9e9e4; --muted: #9a9a92; --line: #33333a; --major: #ff8a80; --minor: #e3b341;
  --patch: #7fc0a0; --other: #a5a5e0; --warn-bg: #4a3212; --warn-ink: #f2c27a;
  --pending-bg: #2a2c42; } }
body { margin: 0; padding: 2rem 1rem; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, sans-serif; }
main { max-width: 80rem; margin: 0 auto; }
h1 { font-size: 1.4rem; margin: 0; } h2 { font-size: 1rem; margin: 2rem 0 .5rem; }
.sub { color: var(--muted); font-size: .9rem; }
input { width: 100%; box-sizing: border-box; padding: .5rem .75rem; margin: 1rem 0 .5rem;
  font-size: .95rem; background: var(--panel); color: var(--ink);
  border: 1px solid var(--line); border-radius: .4rem; }
table { width: 100%; border-collapse: collapse; background: var(--panel);
  border: 1px solid var(--line); }
th, td { text-align: left; padding: .4rem .7rem; border-bottom: 1px solid var(--line);
  font-size: .85rem; vertical-align: top; }
th { font-size: .7rem; text-transform: uppercase; color: var(--muted); position: sticky;
  top: 0; background: var(--bg); }
code, .mono { font-family: ui-monospace, monospace; font-size: .8rem; }
.badge { font-size: .7rem; font-weight: 650; text-transform: uppercase; color: var(--other); }
.major { color: var(--major); } .minor { color: var(--minor); } .patch { color: var(--patch); }
.warn { background: var(--warn-bg); color: var(--warn-ink); font-weight: 650; }
.pending { background: var(--pending-bg); }
span.warn, span.pending { padding: 0 .3rem; border-radius: .2rem; }
pre { white-space: pre-wrap; color: var(--muted); font-size: .8rem; }
"""

SCRIPT = """
const box = document.getElementById('filter');
const rows = [...document.querySelectorAll('tbody tr')];
box?.addEventListener('input', () => {
  const q = box.value.toLowerCase();
  for (const row of rows) row.hidden = q && !row.textContent.toLowerCase().includes(q);
});
"""


MONO = ("current", "new", "age_days", "pending", "versioning", "file", "branch")


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def html_page(document: dict, names: list[str]) -> str:
    """The whole result as one self-contained page: no network, opens straight off disk."""
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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>renovate-preview: {esc(document["target"])}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
{"".join(sections)}
</main>
<script>{SCRIPT}</script>
</body>
</html>
"""
