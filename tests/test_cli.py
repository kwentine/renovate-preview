import io
import json
from pathlib import Path
from unittest import mock

import pytest

from renovate_preview.cli import check, column_names, last_run, parse_args
from renovate_preview.render import COLUMNS

from .conftest import INVALID_CONFIG, RELEASE_AGE

REPORT = '{"level":30,"msg":"Printing report","report":{"problems":[],"repositories":{}}}'
LINES = (
    '{"level":30,"msg":"Renovate started","renovateVersion":"44.209.0"}',
    REPORT,
)


# Arguments


def test_everything_after_double_dash_is_passed_through():
    opts, theirs = parse_args(["repo", "--", "--foo", "--", "bar"], {})
    assert opts.repository == "repo"
    assert theirs == ["--foo", "--", "bar"]


def test_the_repository_is_resolved_once():
    opts, _ = parse_args(["@o/r"], {})
    assert (opts.remote, opts.directory) == ("o/r", None)
    opts, _ = parse_args([], {})
    assert (opts.remote, opts.directory) == (None, str(Path.cwd()))


@pytest.mark.parametrize(
    "argv",
    [
        ["--silent", "--verbose"],
        ["--host", "ghe.example.com", "."],
        ["--columns", "dependency,nope"],
        ["--columns", ","],
        ["--global-config", "/no/such/file"],
        ["/no/such/directory"],
        [".", "--", "--dry-run=full"],
        [".", "--", "--dry-run", "full"],
        ["--env", "TOKEN=secret"],
    ],
)
def test_contradictions_are_rejected(run, argv):
    assert run(*argv, "--just-print")[0] == 2


def test_unknown_column_points_to_help(run):
    _, _, err = run("--columns", "dep", "--just-print")
    assert "unknown column(s) dep: see --columns=help" in err


def test_columns_help(run):
    status, out, _ = run("--columns=help")
    lines = out.splitlines()
    assert status == 0
    assert lines[0].split() == ["dependency", "DEPENDENCY"]
    assert "pr-title    PR TITLE" in lines
    assert lines[-1].split()[0] == "all"


def test_columns_all():
    assert column_names("all") == list(COLUMNS)
    assert column_names(" new, file,") == ["new", "file"]


def test_default_columns_depend_on_the_run(run):
    def header(*argv):
        return run(*argv, "--from", str(RELEASE_AGE))[1].splitlines()[0].split()

    assert header() == ["DEPENDENCY", "CURRENT", "NEW", "FILE"]
    assert header("--extract") == ["DEPENDENCY", "CURRENT", "FILE"]
    assert header("@o/r") == ["DEPENDENCY", "CURRENT", "NEW", "FILE", "PR", "TITLE"]


def test_unknown_columns_in_a_replay(run):
    status, _, err = run("--from", str(RELEASE_AGE), "--columns", "dep")
    assert status == 2
    assert "--columns: unknown column(s) dep" in err


def test_full_dry_run_is_fine_remotely(run):
    assert run("@o/r", "--just-print", "--", "--dry-run=full")[0] == 0


def test_remote_needs_a_token(run):
    status, _, err = run("@o/r")
    assert status == 2
    assert "RENOVATE_TOKEN" in err


def test_check_returns_errors_and_warnings():
    opts, theirs = parse_args(["@o/r", "--env", "X"], {})
    errors, warnings = check(opts, theirs, {})
    assert errors == ["RENOVATE_TOKEN must be set to reach a remote repository (see --pat-url)"]
    assert warnings[0] == "--env X: not set"
    assert "GitHub rate-limits" in warnings[1]


def test_every_error_is_reported(run):
    _, _, err = run("/no/such/directory", "--host", "ghe.example.com", "--just-print")
    assert "no such directory" in err
    assert "--host goes with a remote repository" in err


def test_global_config(run, tmp_path):
    config = tmp_path / "global.json"
    config.write_text("{}")
    _, out, _ = run(".", "--just-print", "--global-config", str(config))
    assert f"{config}:/renovate-preview/global.json:ro,z" in out
    assert "RENOVATE_CONFIG_FILE=/renovate-preview/global.json" in out


def test_global_config_is_not_read_from_the_environment(run, tmp_path):
    config = tmp_path / "global.json"
    config.write_text("{}")
    _, out, _ = run(".", "--just-print", env={"RENOVATE_CONFIG_FILE": str(config)})
    assert "global.json" not in out


def test_only_tokens_are_forwarded_and_env_adds_more(run):
    env = {"RENOVATE_TOKEN": "t", "RENOVATE_PLATFORM": "gitlab", "REGISTRY_PASSWORD": "p"}
    _, out, _ = run(".", "--just-print", env=env)
    _, more, err = run(".", "--just-print", "--env", "REGISTRY_PASSWORD", "--env", "X", env=env)
    assert "-e RENOVATE_TOKEN \\" in out
    assert "RENOVATE_PLATFORM" not in out
    assert "REGISTRY_PASSWORD" not in out
    assert "-e REGISTRY_PASSWORD \\" in more
    assert "--env X: not set" in err


# Defaults from RENOVATE_PREVIEW_* variables


def test_defaults_from_the_environment(run, tmp_path):
    g, c = tmp_path / "g.json", tmp_path / "c.json"
    g.write_text("{}")
    c.write_text("{}")
    env = {
        "RENOVATE_PREVIEW_GLOBAL_CONFIG": str(g),
        "RENOVATE_PREVIEW_CONFIG": str(c),
        "RENOVATE_PREVIEW_HOST": "ghe.example.com",
        "RENOVATE_TOKEN": "t",
    }
    _, out, _ = run("@o/r", "--just-print", env=env)
    assert "RENOVATE_CONFIG_FILE=/renovate-preview/global.json" in out
    assert "RENOVATE_ADDITIONAL_CONFIG_FILE=/renovate-preview/config.json" in out
    assert "--endpoint=https://ghe.example.com/api/v3/" in out


def test_options_win_over_the_environment():
    environ = {"RENOVATE_PREVIEW_HOST": "ghe.example.com", "RENOVATE_PREVIEW_COLUMNS": "file"}
    opts, _ = parse_args(["--host", "github.com", "--columns", "new"], environ)
    assert (opts.host, opts.columns) == ("github.com", "new")
    opts, _ = parse_args([], environ)
    assert (opts.host, opts.columns) == ("ghe.example.com", "file")


def test_a_variable_that_does_not_apply_is_ignored(run):
    # It doesn't apply to a local repository: ignored.
    env = {"RENOVATE_PREVIEW_HOST": "ghe.example.com"}
    status, out, err = run(".", "--just-print", env=env)
    assert (status, err) == (0, "")
    assert "--endpoint" not in out
    # Typed, it is one.
    status, _, err = run(".", "--just-print", "--host", "ghe.example.com", env=env)
    assert status == 2
    assert "--host goes with a remote repository" in err


def test_a_value_names_its_origin(run, tmp_path):
    env = {"RENOVATE_PREVIEW_CONFIG": str(tmp_path / "missing.json")}
    _, _, err = run(".", "--just-print", env=env)
    assert f"RENOVATE_PREVIEW_CONFIG: no such config file: {tmp_path}/missing.json" in err
    _, _, err = run(".", "--just-print", "--config", "nope.json", env=env)
    assert "--config: no such config file: nope.json" in err
    opts, _ = parse_args(["--config", "c.json"], {"RENOVATE_PREVIEW_HOST": "h"})
    assert opts.origin == {
        "global_config": "default",
        "config": "cli",
        "columns": "default",
        "host": "env",
    }


def test_columns_from_the_environment_are_checked(run):
    status, _, err = run("--just-print", env={"RENOVATE_PREVIEW_COLUMNS": "nope"})
    assert status == 2
    assert "RENOVATE_PREVIEW_COLUMNS: unknown column(s) nope" in err


def test_columns_from_the_environment_apply_to_a_replay(run):
    env = {
        "RENOVATE_PREVIEW_COLUMNS": "dependency,new",
        "RENOVATE_PREVIEW_HOST": "ghe.example.com",
    }
    status, out, _ = run("--from", str(RELEASE_AGE), env=env)
    assert status == 0
    assert out.splitlines()[0].split() == ["DEPENDENCY", "NEW"]


# --config


def test_config_remote(run, tmp_path):
    config = tmp_path / "pr.json"
    config.write_text("{}")
    status, out, err = run("@o/r", "--config", str(config), "--just-print")
    assert (status, err) == (0, "")
    assert f"{config}:/renovate-preview/config.json:ro,z" in out
    assert "RENOVATE_ADDITIONAL_CONFIG_FILE=/renovate-preview/config.json" in out
    assert "RENOVATE_REQUIRE_CONFIG=ignored" in out


def test_config_locally_warns_that_the_repository_config_wins(run, tmp_path):
    repo, other, config = tmp_path / "repo", tmp_path / "other", tmp_path / "config.json"
    repo.mkdir()
    other.mkdir()
    (repo / "renovate.json").write_text("{}")
    config.write_text("{}")
    status, _, err = run(str(repo), "--config", str(config), "--just-print")
    _, _, without_own = run(str(other), "--config", str(config), "--just-print")
    assert status == 0
    assert "renovate.json is read too, and wins over --config" in err
    assert "wins over --config" not in without_own


def test_an_invalid_config_is_shown(run, renovate):
    renovate(*INVALID_CONFIG.read_text().splitlines())
    _, _, err = run()
    assert (
        "WARN: Repository has invalid config: renovate.json: Invalid configuration option: "
        "foo, Invalid configuration option: packageRules[0].bogus"
    ) in err


# --just-print


def test_just_print_needs_no_engine(run):
    with mock.patch("shutil.which", return_value=None):
        status, out, _ = run(".", "--just-print", "--tag", "44.115.2")
    assert status == 0
    assert "ghcr.io/renovatebot/renovate:44.115.2" in out


def test_just_print_shell(run):
    with mock.patch("shutil.which", return_value=None):
        _, out, _ = run(".", "--just-print", "--shell", "--name", "rp")
    assert "\n    -it \\\n" in out
    assert "\n    --name rp \\\n" in out
    assert out.rstrip().endswith("ghcr.io/renovatebot/renovate:latest \\\n    bash")


# Output


def test_raw_and_report(run, renovate):
    renovate(*LINES)
    _, raw, _ = run(".", "-o", "raw")
    _, report, _ = run(".", "-o", "report")
    assert raw.splitlines() == list(LINES)
    assert json.loads(report) == {"problems": [], "repositories": {}}


def test_write(run, renovate, tmp_path):
    renovate(*LINES)
    path = tmp_path / "out.html"
    _, out, _ = run(".", "-o", "html", "-w", str(path))
    assert path.read_text().startswith("<!DOCTYPE html>")
    assert out == ""


def test_version_is_shown(run, renovate):
    renovate(*LINES)
    _, _, err = run(".")
    _, _, silent = run(".", "--silent")
    assert "Renovate 44.209.0 (podman, ghcr.io/renovatebot/renovate:latest)" in err
    assert silent == ""


def test_missing_image(run, renovate):
    # The engine's message goes to the terminal, not through us: only its status does.
    renovate(status=125)
    status, out, err = run(".", "--silent")
    assert (status, out) == (125, "")
    assert "podman could not run Renovate (status 125)" in err
    assert "podman pull ghcr.io/renovatebot/renovate:latest" in err


def test_renovate_stops_before_its_report(run, renovate):
    # E.g. an unknown option: Renovate logs that it started, then the error goes to stderr.
    renovate(LINES[0], status=1)
    status, out, err = run(".", "--", "--x")
    assert (status, out) == (1, "")
    assert "Renovate stopped before its report (status 1)" in err


def test_raw_and_json_output_need_no_report(run, renovate):
    renovate(LINES[0], status=1)
    assert run(".", "-o", "raw")[1] == LINES[0] + "\n"
    status, out, _ = run(".", "-o", "json")
    assert (status, json.loads(out)["renovate_version"]) == (1, None)


def test_failure_points_to_the_raw_log(run, renovate):
    renovate(*LINES, status=1)
    status, _, err = run(".")
    assert status == 1
    assert "-o raw" in err


# --show-config

CONFIG = json.dumps(
    {
        "level": 30,
        "msg": "Full resolved config and hostRules including presets",
        "config": {"branchPrefix": "renovate/", "ignorePaths": ["a/**"]},
        "hostRules": [{"hostType": "local"}],
    }
)


def test_show_config_asks_renovate_to_print_it(run):
    assert "--print-config=true" in run(".", "--just-print", "--show-config")[1]
    assert "--print-config" not in run(".", "--just-print")[1]


def test_show_config_before_the_table(run, renovate):
    renovate(LINES[0], CONFIG, REPORT)
    _, out, _ = run(".", "--show-config")
    config, rest = out.split("\n\n", 1)
    assert json.loads(config) == {"branchPrefix": "renovate/", "ignorePaths": ["a/**"]}
    assert "hostType" not in out
    assert rest.startswith("No dependencies found")
    assert "branchPrefix" not in run(".")[1]


def test_show_config_in_a_page(run, renovate):
    renovate(LINES[0], CONFIG, REPORT)
    _, out, _ = run(".", "--show-config", "-o", "html")
    assert "<details><summary>Renovate's resolved config" in out
    assert "&quot;branchPrefix&quot;: &quot;renovate/&quot;" in out


def test_show_config_without_config_in_the_log(run):
    status, _, err = run("--from", str(RELEASE_AGE), "--show-config")
    assert status == 0
    assert "no config in Renovate's log" in err


# --pat-url


def test_pat_url(run):
    status, out, _ = run("--pat-url")
    assert status == 0
    assert out.startswith("https://github.com/settings/personal-access-tokens/new?")
    assert "&contents=read&" in out
    assert "write" not in out


def test_pat_url_on_github_enterprise_server(run):
    _, out, _ = run("--pat-url", "--host", "ghe.example.com")
    assert out.startswith("https://ghe.example.com/settings/")


# --from: a log instead of a run


def test_from_file(run):
    status, out, err = run("--from", str(RELEASE_AGE), "--columns", "dependency,new")
    assert status == 0
    assert ["cert-manager", "v1.21.1"] in [line.split() for line in out.splitlines()]
    assert f"Renovate 44.164.10, replayed from {RELEASE_AGE}" in err


def test_from_stdin(run):
    with mock.patch("sys.stdin", io.StringIO(RELEASE_AGE.read_text())):
        status, out, _ = run("--from", "-", "-o", "json")
    assert status == 0
    assert json.loads(out)["target"] == "stdin"


def test_a_run_is_kept_for_from_last(run, renovate, tmp_path):
    renovate(*LINES)
    run(".")
    directory = tmp_path / "state" / "renovate-preview"
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "last.ndjson").stat().st_mode & 0o777 == 0o600
    status, out, _ = run("--from", "last", "-o", "raw")
    assert (status, out.splitlines()) == (0, list(LINES))


def test_last_run_is_xdg_state():
    assert last_run({"XDG_STATE_HOME": "/state"}) == Path("/state/renovate-preview/last.ndjson")
    # Relative values are to be ignored, says the spec.
    environ = {"XDG_STATE_HOME": "relative", "HOME": "/home/me"}
    assert last_run(environ) == Path("/home/me/.local/state/renovate-preview/last.ndjson")


def test_no_last_run(run):
    status, _, err = run("--from", "last")
    assert status == 2
    assert "(no run yet?)" in err


def test_a_log_without_report(run, tmp_path):
    log = tmp_path / "log.ndjson"
    log.write_text(LINES[0] + "\n")
    status, _, err = run("--from", str(log))
    assert status == 1
    assert "Renovate stopped before its report;" in err


def test_not_a_renovate_log(run, tmp_path):
    log = tmp_path / "log.txt"
    log.write_text("hello\n")
    status, _, err = run("--from", str(log))
    assert status == 2
    assert "no Renovate JSON log records" in err


def test_run_only_options_are_ignored_with_from(run):
    # A run's command line, with --from added, shows that run again. Before `--`: after
    # it, --from would be one of Renovate's arguments.
    ours = [".", "--host", "ghe.example.com", "--tag", "1", "--env", "X", "--extract"]
    replay = ["--from", str(RELEASE_AGE), "--columns", "dependency"]
    status, out, _ = run(*ours, *replay, "--", "--foo")
    assert status == 0
    assert out.startswith("DEPENDENCY")
