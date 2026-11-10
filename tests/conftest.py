"""Fixtures shared by the tests: Renovate logs of real runs, and the command line."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest import mock

import pytest

from renovate_preview import container
from renovate_preview.cli import main
from renovate_preview.report import parse_record, summarize

DATA = Path(__file__).parent / "data"
# A real Renovate 44 run of examples/separate-multiple-minor.
LOOKUP = DATA / "lookup.ndjson"
# A real run with minimumReleaseAge (Renovate 44.164.10, October 2026).
RELEASE_AGE = DATA / "release-age.ndjson"


def records(path: Path) -> list[dict]:
    return [r for r in map(parse_record, path.read_text().splitlines()) if r is not None]


@pytest.fixture
def lookup() -> dict:
    return summarize("examples/x", records(LOOKUP))


@pytest.fixture
def release_age() -> dict:
    return summarize("x", records(RELEASE_AGE))


@pytest.fixture(autouse=True)
def fresh_logger():
    """main() sets our logger up, on the stderr of its time: undo it after each test."""
    yield
    logger = logging.getLogger("renovate_preview")
    logger.handlers, logger.propagate = [], True
    logger.setLevel(logging.NOTSET)


@pytest.fixture
def run(capsys, tmp_path):
    """renovate-preview's command line: run(*argv, env={...}) -> (status, stdout, stderr).

    In an environment holding only `env`, and with the last run kept under tmp_path rather
    than in your home directory."""

    def run(*argv: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        environ = {"XDG_STATE_HOME": str(tmp_path / "state"), **(env or {})}
        with mock.patch.dict(os.environ, environ, clear=True):
            status = main(list(argv))
        out, err = capsys.readouterr()
        return status, out, err

    return run


@pytest.fixture
def renovate(monkeypatch):
    """Stands in for the container: renovate(*lines, status=0) makes it output `lines`."""

    def renovate(*lines: str, status: int = 0) -> None:
        def fake(argv, on_line):
            for line in lines:
                on_line(line)
            return status, list(lines)

        monkeypatch.setattr(container, "run", fake)
        monkeypatch.setattr(container, "detect_engine", lambda: "podman")

    return renovate
