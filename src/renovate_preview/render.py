"""The output formats: a table to read, JSON to pipe, a page to open in a browser."""

from __future__ import annotations

import html
import json

COLUMNS = (
    ("DEPENDENCY", "dependency"),
    ("CURRENT", "current"),
    ("NEW", "new"),
    ("TYPE", "type"),
    ("FILE", "file"),
    ("BRANCH", "branch"),
    ("PR TITLE", "pr_title"),
)


def columns(rows: list[dict]) -> list[tuple[str, str]]:
    """The columns with something in them (e.g. --just-extract has no NEW)."""
    return [(title, key) for title, key in COLUMNS if any(cell(row, key) != "-" for row in rows)]


def cell(row: dict, key: str) -> str:
    if key == "new" and not row["new"] and row["skip_reason"]:
        return f"(skipped: {row['skip_reason']})"
    return str(row[key] or "-")


def table(document: dict) -> str:
    rows = document["updates"]
    if not rows:
        return "No dependencies found."
    cols = columns(rows)
    data = [[title for title, _ in cols]] + [[cell(row, key) for _, key in cols] for row in rows]
    widths = [max(len(line[i]) for line in data) for i in range(len(cols))]
    data.insert(1, ["-" * w for w in widths])
    return "\n".join(
        "  ".join(c.ljust(w) for c, w in zip(line, widths, strict=True)).rstrip() for line in data
    )


def to_json(document: dict) -> str:
    return json.dumps(document, indent=2)


STYLE = """
:root { color-scheme: light dark; --bg: #fbfbfa; --panel: #fff; --ink: #1c1c1a;
  --muted: #6a6a64; --line: #e3e3de; --major: #b3261e; --minor: #9a6700; --patch: #2f6f4f;
  --other: #5a5a8c; }
@media (prefers-color-scheme: dark) { :root { --bg: #17171a; --panel: #1f1f23;
  --ink: #e9e9e4; --muted: #9a9a92; --line: #33333a; --major: #ff8a80; --minor: #e3b341;
  --patch: #7fc0a0; --other: #a5a5e0; } }
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


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def html_page(document: dict) -> str:
    """The whole result as one self-contained page: no network, opens straight off disk."""
    rows = document["updates"]
    cols = [(title, key) for title, key in columns(rows) if key != "type"] if rows else []
    head = "".join(f"<th>{esc(title)}</th>" for title, _ in cols)
    body = []
    for row in rows:
        cells = []
        for _, key in cols:
            value = esc(cell(row, key))
            if key == "new" and row["type"]:
                value += f' <span class="badge {esc(row["type"])}">{esc(row["type"])}</span>'
            mono = key in ("current", "new", "file", "branch")
            cells.append(f'<td class="mono">{value}</td>' if mono else f"<td>{value}</td>")
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
