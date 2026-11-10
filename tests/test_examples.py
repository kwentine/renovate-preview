"""Each example still extracts what its expected.json says.

These run Renovate for real (offline, with --extract), so they only run on request:
    make examples                       # or RENOVATE_PREVIEW_E2E=1 make test
"""

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

from renovate_preview.cli import main

EXAMPLES = Path(__file__).parent.parent / "examples"
FIELDS = ("dependency", "current", "manager", "file")


def extract(example: Path) -> list[tuple]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = main(["--extract", "--silent", "-o", "json", str(example)])
    assert status == 0, f"renovate-preview exited {status}"
    rows = json.loads(out.getvalue())["updates"]
    return sorted({tuple(row[f] for f in FIELDS) for row in rows})


@pytest.mark.skipif(
    not os.environ.get("RENOVATE_PREVIEW_E2E"), reason="set RENOVATE_PREVIEW_E2E=1"
)
@pytest.mark.parametrize(
    "example", sorted(p.parent for p in EXAMPLES.glob("*/expected.json")), ids=lambda p: p.name
)
def test_example(example):
    expected = json.loads((example / "expected.json").read_text())["dependencies"]
    assert extract(example) == sorted(tuple(dep[f] for f in FIELDS) for dep in expected)


@pytest.mark.parametrize(
    "example", [p for p in EXAMPLES.iterdir() if p.is_dir()], ids=lambda p: p.name
)
@pytest.mark.parametrize("name", ["README.md", "renovate.json", "expected.json"])
def test_every_example_is_complete(example, name):
    assert (example / name).is_file()
