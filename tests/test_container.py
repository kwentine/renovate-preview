import logging
import os
from unittest import mock

import pytest

from renovate_preview.container import Filesystem, Progress, command, filesystem, printable
from renovate_preview.renovate import Target


def run_command(target="/tmp", engine="podman", image="img:tag", env=None, **kw):
    fs = filesystem(Target.parse(target), kw.pop("global_config", None), None)
    with mock.patch.dict(os.environ, env or {}, clear=True):
        return command(engine, image, fs, {"LOG_LEVEL": "debug"}, ["--a"], **kw)


# Filesystem


def test_local_repository_is_the_working_directory():
    fs = filesystem(Target(False, "/repo"), None, None)
    assert fs == Filesystem([("/repo", "/usr/src/app")], "/usr/src/app")


def test_remote_mounts_nothing():
    assert filesystem(Target(True, "o/r"), None, None) == Filesystem()


def test_global_config_keeps_its_extension(tmp_path):
    config = tmp_path / "global.json5"
    fs = filesystem(Target(True, "o/r"), str(config), None)
    assert fs.global_config == "/renovate-preview/global.json5"
    assert fs.mounts == [(str(config), "/renovate-preview/global.json5")]


def test_config(tmp_path):
    fs = filesystem(Target(True, "o/r"), str(tmp_path / "g.json"), str(tmp_path / "c"))
    assert (fs.global_config, fs.config) == (
        "/renovate-preview/global.json",
        "/renovate-preview/config",
    )
    assert len(fs.mounts) == 2


# Command


def test_mounts():
    argv = run_command()
    assert argv[:4] == ["podman", "run", "--rm", "--pull=never"]
    assert "/tmp:/usr/src/app:ro,z" in argv
    assert "renovate-preview:/tmp/renovate" in argv
    assert argv[-2:] == ["img:tag", "--a"]


def test_environment():
    argv = run_command(env={"RENOVATE_TOKEN": "secret"})
    assert "LOG_LEVEL=debug" in argv
    assert "GIT_CONFIG_VALUE_0=*" in argv
    assert "RENOVATE_TOKEN" in argv  # by name: the value stays out of the command
    assert not [a for a in argv if "secret" in a]


def test_program_name_and_shell():
    argv = run_command(program="bash", name="rp", interactive=True)
    assert argv[argv.index("--name") + 1] == "rp"
    assert "-it" in argv
    assert argv[-3:] == ["img:tag", "bash", "--a"]


def test_printable_keeps_flags_with_their_values():
    text = printable(run_command(name="rp"))
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
    """The messages Progress logs at `level`, for LINES."""

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
