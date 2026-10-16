"""Each example still extracts what its expected.json says.

These run Renovate for real (offline, with --just-extract), so they only run on request:
    make examples                       # or RENOVATE_PREVIEW_E2E=1 make test
"""

import contextlib
import io
import json
import os
import unittest
from pathlib import Path

from renovate_preview.cli import main

EXAMPLES = Path(__file__).parent.parent / "examples"
FIELDS = ("dependency", "current", "manager", "file")


def extract(example: Path) -> list[dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["--just-extract", "--silent", "-o", "json", str(example)])
    if code != 0:
        raise AssertionError(f"renovate-preview exited {code}")
    rows = json.loads(out.getvalue())["updates"]
    return sorted({tuple(row[f] for f in FIELDS) for row in rows})


@unittest.skipUnless(os.environ.get("RENOVATE_PREVIEW_E2E"), "set RENOVATE_PREVIEW_E2E=1")
class ExampleTests(unittest.TestCase):
    def test_examples(self):
        for example in sorted(p.parent for p in EXAMPLES.glob("*/expected.json")):
            with self.subTest(example=example.name):
                expected = json.loads((example / "expected.json").read_text())["dependencies"]
                want = sorted(tuple(dep[f] for f in FIELDS) for dep in expected)
                self.assertEqual(extract(example), want)


class ExampleLayoutTests(unittest.TestCase):
    def test_every_example_is_complete(self):
        for example in (p for p in EXAMPLES.iterdir() if p.is_dir()):
            for name in ("README.md", "renovate.json", "expected.json"):
                self.assertTrue((example / name).is_file(), f"{example.name}/{name}")
