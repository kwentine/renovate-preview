from renovate_preview.cli import parse_args
from renovate_preview.renovate import (
    container_args,
    credentials_warning,
    environment,
    forwarded,
    inside,
    their_flags,
)


def args(*argv):
    return container_args(*parse_args(list(argv), {}))


def opts(*argv):
    return parse_args(list(argv), {})[0]


# Arguments


def test_local():
    assert args("/tmp") == ["--report-type=logging", "--dry-run=lookup", "--platform=local"]


def test_scope():
    assert "--dry-run=full" in args("@o/r")
    assert "--dry-run=extract" in args("--extract", "/tmp")


def test_remote():
    assert args("@org/repo")[-2:] == ["--platform=github", "org/repo"]


def test_other_platforms_are_arguments():
    argv = args("@o/r", "--", "--platform=gitlab", "--endpoint=https://gitlab.example.com/")
    assert "--platform=github" not in argv
    assert argv[-3:] == ["--platform=gitlab", "--endpoint=https://gitlab.example.com/", "o/r"]


def test_github_enterprise_server():
    assert "--endpoint=https://ghe.example.com/api/v3/" in args(
        "@o/r", "--host", "ghe.example.com"
    )
    assert not [a for a in args("@o/r") if a.startswith("--endpoint")]


# Arguments after `--` win: over ours, and (with --force-cli) over the repository config.


def test_theirs_come_after_ours_with_force_cli():
    assert args("/tmp", "--", "--ignore-paths=x") == [
        "--report-type=logging",
        "--dry-run=lookup",
        "--force-cli=true",
        "--platform=local",
        "--ignore-paths=x",
    ]


def test_no_force_cli_without_theirs():
    assert "--force-cli=true" not in args("/tmp")


def test_ours_are_dropped_when_given_again():
    argv = args("/tmp", "--", "--dry-run", "extract", "--platform=local")
    assert "--dry-run=lookup" not in argv
    assert argv.count("--platform=local") == 1  # theirs, not ours too
    assert argv[-3:] == ["--dry-run", "extract", "--platform=local"]


def test_remote_repository_stays_last():
    assert args("@o/r", "--", "--foo")[-2:] == ["--foo", "o/r"]


def test_their_flags():
    assert their_flags(["--dry-run=full"]).dry_run == "full"
    assert their_flags(["--x", "--dry-run", "full", "y"]).dry_run == "full"
    assert their_flags(["--dry-run"]).dry_run is None  # Renovate rejects it
    assert their_flags(["--dry-runs=1", "--dry"]).dry_run is None


# Environment


def test_inside_keeps_the_extension():
    assert inside("global", "/a/b.json5") == "/renovate-preview/global.json5"
    assert inside("config", "c") == "/renovate-preview/config"


def test_renovate_defaults_for_require_config():
    assert "RENOVATE_REQUIRE_CONFIG" not in environment(opts("@o/r"))


def test_global_config():
    assert "RENOVATE_CONFIG_FILE" not in environment(opts())
    env = environment(opts("--global-config", "g.json"))
    assert env["RENOVATE_CONFIG_FILE"] == "/renovate-preview/global.json"


def test_config_replaces_the_repository_config_remotely():
    env = environment(opts("@o/r", "--config", "c.json"))
    assert env["RENOVATE_ADDITIONAL_CONFIG_FILE"] == "/renovate-preview/config.json"
    assert env["RENOVATE_REQUIRE_CONFIG"] == "ignored"


def test_config_locally():
    env = environment(opts("--config", "c.json"))
    assert env["RENOVATE_ADDITIONAL_CONFIG_FILE"] == "/renovate-preview/config.json"
    assert "RENOVATE_REQUIRE_CONFIG" not in env


def test_forwarded_tokens_and_env_when_set():
    environ = {"RENOVATE_TOKEN": "x", "GITHUB_COM_TOKEN": "y", "RENOVATE_PLATFORM": "z", "A": ""}
    assert forwarded(opts(), environ) == ["RENOVATE_TOKEN", "GITHUB_COM_TOKEN"]
    assert forwarded(opts("--env", "A", "--env", "B"), environ)[-1] == "A"


def test_credentials_warning():
    assert "GitHub rate-limits" in credentials_warning({})
    assert credentials_warning({"GITHUB_COM_TOKEN": "y"}) is None
