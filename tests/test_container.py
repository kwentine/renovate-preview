import logging

import pytest

from renovate_preview.cli import parse_args
from renovate_preview.container import Progress, mounts, printable, runtime_args


def opts(*argv):
    return parse_args(list(argv))[0]


def command(*argv, environ=None):
    """Return a preview's whole command, as cli.preview() builds it, "--a" for Renovate."""
    return ["podman", "run", *runtime_args(opts(*argv), environ or {}), "img:tag", "--a"]


# Mounts


def test_local_repository_is_the_working_directory():
    assert mounts(opts("/repo")) == [("/repo", "/usr/src/app")]
    argv = command("/repo")
    assert argv[argv.index("-w") + 1] == "/usr/src/app"


def test_remote_mounts_nothing():
    assert mounts(opts("@o/r")) == []
    assert "-w" not in command("@o/r")


def test_config_files(tmp_path):
    g, c = tmp_path / "g.json5", tmp_path / "c"
    assert mounts(opts("@o/r", "--global-config", str(g), "--config", str(c))) == [
        (str(g), "/renovate-preview/global.json5"),
        (str(c), "/renovate-preview/config"),
    ]


# Runtime arguments


def test_mounts():
    argv = command("/tmp")
    assert argv[:4] == ["podman", "run", "--rm", "--pull=never"]
    assert "/tmp:/usr/src/app:ro,z" in argv
    assert "renovate-preview:/tmp/renovate" in argv
    assert argv[-2:] == ["img:tag", "--a"]


def test_environment():
    argv = command(environ={"RENOVATE_TOKEN": "secret"})
    assert "LOG_LEVEL=debug" in argv
    assert "GIT_CONFIG_VALUE_0=*" in argv
    assert "RENOVATE_TOKEN" in argv  # by name: the value stays out of the command
    assert not [a for a in argv if "secret" in a]


def test_ours_win_over_env():
    argv = command("--env", "LOG_LEVEL", environ={"LOG_LEVEL": "info"})
    assert "LOG_LEVEL" not in argv


def test_name_and_shell():
    argv = command("--name", "rp", "--shell")
    assert argv[argv.index("--name") + 1] == "rp"
    assert "-it" in argv


def test_printable_keeps_flags_with_their_values():
    text = printable(command("--name", "rp"))
    assert text.startswith("podman run --rm \\\n")
    assert "\n    --name rp \\\n" in text
    assert "\n    -e LOG_LEVEL=debug \\\n" in text


# Progress

LINES = (
    "Trying to pull ghcr.io/renovatebot/renovate:latest...",
    '{"level":30,"msg":"Renovate started","renovateVersion":"44.209.0"}',
    '{"level":30,"msg":"Repository started","renovateVersion":"44.209.0"}',
    '{"level":20,"msg":"extract()"}',
    "some later plain text",
)


@pytest.fixture
def shown(caplog):
    """Return a function that returns the messages Progress logs for LINES at a level."""

    def shown(level: int) -> list[str]:
        progress = Progress("podman, img:latest")
        with caplog.at_level(level, logger="renovate_preview"):
            for line in LINES:
                progress(line)
        return caplog.messages

    return shown


def test_progress(shown):
    assert shown(logging.INFO) == [
        "Renovate 44.209.0 (podman, img:latest)",
        "reading configuration...",
        "extracting dependencies...",
    ]


def test_verbose_progress_shows_every_record(shown):
    messages = shown(logging.DEBUG)
    assert "extracting dependencies..." not in messages
    assert "DEBUG extract()" in messages
