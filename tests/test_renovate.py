import os
from pathlib import Path
from unittest import mock

from renovate_preview.cli import parse_args
from renovate_preview.renovate import (
    Target,
    arguments,
    environment,
    forwarded_environment,
    passthrough_value,
)


def args(*argv, env=None):
    opts = parse_args(list(argv))
    with mock.patch.dict(os.environ, env or {}, clear=True):
        return arguments(opts, Target.parse(opts.repository))


# Target


def test_remote_target():
    assert Target.parse("@org/repo") == Target(True, "org/repo")


def test_local_target_defaults_to_the_current_directory():
    assert Target.parse(None) == Target(False, str(Path.cwd()))


# Arguments


def test_local():
    assert args("/tmp") == ["--report-type=logging", "--dry-run=lookup", "--platform=local"]


def test_scope():
    assert "--dry-run=full" in args("@o/r")
    assert "--dry-run=extract" in args("--extract", "/tmp")


def test_remote():
    assert args("@org/repo")[-2:] == ["--platform=github", "org/repo"]
    assert args("@o/r", env={"RENOVATE_PLATFORM": "gitlab"})[-2:] == ["--platform=gitlab", "o/r"]


def test_github_enterprise_server():
    assert "--endpoint=https://ghe.example.com/api/v3/" in args(
        "@o/r", "--host", "ghe.example.com"
    )
    assert not [a for a in args("@o/r") if a.startswith("--endpoint")]


# Arguments after `--` win: over ours, and (with --force-cli) over the repository config.


def test_passthrough_comes_after_ours_with_force_cli():
    assert args("/tmp", "--", "--ignore-paths=x") == [
        "--report-type=logging",
        "--dry-run=lookup",
        "--force-cli=true",
        "--platform=local",
        "--ignore-paths=x",
    ]


def test_no_force_cli_without_passthrough():
    assert "--force-cli=true" not in args("/tmp")


def test_ours_are_dropped_when_given_again():
    argv = args("/tmp", "--", "--dry-run", "extract", "--platform=local")
    assert "--dry-run=lookup" not in argv
    assert argv.count("--platform=local") == 1  # theirs, not ours too
    assert argv[-3:] == ["--dry-run", "extract", "--platform=local"]


def test_remote_repository_stays_last():
    assert args("@o/r", "--", "--foo")[-2:] == ["--foo", "o/r"]


def test_passthrough_value():
    assert passthrough_value(["--dry-run=full"], "--dry-run") == "full"
    assert passthrough_value(["--dry-run", "full"], "--dry-run") == "full"
    assert passthrough_value(["--dry-run", "--x"], "--dry-run") == ""
    assert passthrough_value(["--dry-runs=1"], "--dry-run") is None


# Environment


def test_renovate_defaults_for_require_config():
    assert "RENOVATE_REQUIRE_CONFIG" not in environment(None, None, remote=True)


def test_global_config():
    assert "RENOVATE_CONFIG_FILE" not in environment(None, None, False)
    assert environment("/g.json", None, False)["RENOVATE_CONFIG_FILE"] == "/g.json"


def test_config_replaces_the_repository_config_remotely():
    env = environment(None, "/c.json", remote=True)
    assert env["RENOVATE_ADDITIONAL_CONFIG_FILE"] == "/c.json"
    assert env["RENOVATE_REQUIRE_CONFIG"] == "ignored"


def test_config_locally():
    env = environment(None, "/c.json", remote=False)
    assert env["RENOVATE_ADDITIONAL_CONFIG_FILE"] == "/c.json"
    assert "RENOVATE_REQUIRE_CONFIG" not in env


def test_forwarded_by_name():
    env = {"RENOVATE_TOKEN": "x", "GITHUB_COM_TOKEN": "y", "RENOVATE_PREVIEW_RUNTIME": "z"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert forwarded_environment() == ["GITHUB_COM_TOKEN", "RENOVATE_TOKEN"]
