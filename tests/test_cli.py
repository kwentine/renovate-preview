import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from renovate_preview.cli import columns, main, parse_args
from renovate_preview.render import COLUMNS


def run(*argv, env=None):
    err, out = io.StringIO(), io.StringIO()
    with (
        mock.patch.dict(os.environ, env or {}, clear=True),
        contextlib.redirect_stderr(err),
        contextlib.redirect_stdout(out),
    ):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def fake_renovate(*lines, status=0):
    """Stands in for the container: its output is `lines`."""

    def fake(argv, on_line):
        for line in lines:
            on_line(line)
        return status, list(lines)

    return mock.patch.multiple(
        "renovate_preview.container",
        run=fake,
        detect_engine=mock.Mock(return_value="podman"),
    )


REPORT = '{"level":30,"msg":"Printing report","report":{"problems":[],"repositories":{}}}'
LINES = (
    '{"level":30,"msg":"Renovate started","renovateVersion":"44.209.0"}',
    REPORT,
)


class ArgumentTests(unittest.TestCase):
    def test_everything_after_double_dash_is_passed_through(self):
        opts = parse_args(["repo", "--", "--foo", "--", "bar"])
        self.assertEqual(opts.repository, "repo")
        self.assertEqual(opts.passthrough, ["--foo", "--", "bar"])

    def test_contradictions_are_rejected(self):
        for argv in (
            ["--silent", "--verbose"],
            ["--host", "ghe.example.com", "."],
            ["--columns", "dependency,nope"],
            ["--columns", ","],
            ["--validate", "@o/r"],
            ["--global-config", "/no/such/file"],
            ["/no/such/directory"],
            [".", "--", "--dry-run=full"],
            [".", "--", "--dry-run"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(run(*argv, "--just-print")[0], 2)

    def test_unknown_column_points_to_help(self):
        _, _, err = run("--columns", "dep", "--just-print")
        self.assertIn("unknown column(s) dep: see --columns=help", err)

    def test_columns_help(self):
        code, out, _ = run("--columns=help")
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0].split(), ["dependency", "DEPENDENCY", "(default)"])
        self.assertIn("new         NEW         (default, unless --extract)", lines)
        self.assertEqual(lines[-1].split()[0], "all")

    def test_columns_all(self):
        opts = parse_args(["--columns=all"])
        self.assertEqual(columns(opts), list(COLUMNS))

    def test_full_dry_run_is_fine_remotely(self):
        self.assertEqual(run("@o/r", "--just-print", "--", "--dry-run=full")[0], 0)

    def test_remote_needs_a_token(self):
        code, _, err = run("@o/r")
        self.assertEqual(code, 2)
        self.assertIn("RENOVATE_TOKEN", err)

    def test_global_config_defaults_to_renovate_config_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as config:
            _, out, _ = run(".", "--just-print", env={"RENOVATE_CONFIG_FILE": config.name})
        self.assertIn(f"{config.name}:/renovate-preview/global.json:ro,z", out)
        self.assertIn("RENOVATE_CONFIG_FILE=/renovate-preview/global.json", out)


class ConfigTests(unittest.TestCase):
    def test_remote(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as config:
            code, out, err = run("@o/r", "--config", config.name, "--just-print")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"{config.name}:/renovate-preview/config.json:ro,z", out)
        self.assertIn("RENOVATE_ADDITIONAL_CONFIG_FILE=/renovate-preview/config.json", out)
        self.assertIn("RENOVATE_REQUIRE_CONFIG=ignored", out)

    def test_local_warns_that_the_repository_config_wins(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.NamedTemporaryFile() as config:
            Path(repo, "renovate.json").write_text("{}")
            code, _, err = run(repo, "--config", config.name, "--just-print")
            _, _, without_own = run(tempfile.gettempdir(), "--config", config.name, "--just-print")
        self.assertEqual(code, 0)
        self.assertIn("renovate.json is read too, and wins over --config", err)
        self.assertNotIn("wins over --config", without_own)

    def test_validate_shows_the_file_name(self):
        with tempfile.NamedTemporaryFile(suffix=".json5") as config:
            _, out, _ = run("@o/r", "--validate", "--config", config.name, "--just-print")
        name = Path(config.name).name
        self.assertIn(f"{config.name}:/usr/src/app/{name}:ro,z", out)
        self.assertTrue(out.rstrip().endswith(f"--no-global {name}"))


class JustPrintTests(unittest.TestCase):
    def test_needs_no_engine(self):
        with mock.patch("shutil.which", return_value=None):
            code, out, _ = run(".", "--just-print", "--tag", "44.115.2")
        self.assertEqual(code, 0)
        self.assertIn("ghcr.io/renovatebot/renovate:44.115.2", out)

    def test_shell(self):
        with mock.patch("shutil.which", return_value=None):
            _, out, _ = run(".", "--just-print", "--shell", "--name", "rp")
        self.assertIn("\n    -it \\\n", out)
        self.assertIn("\n    --name rp \\\n", out)
        self.assertTrue(out.rstrip().endswith("ghcr.io/renovatebot/renovate:latest \\\n    bash"))


class OutputTests(unittest.TestCase):
    def test_raw_and_report(self):
        with fake_renovate(*LINES):
            _, raw, _ = run(".", "-o", "raw")
            _, report, _ = run(".", "-o", "report")
        self.assertEqual(raw.splitlines(), list(LINES))
        self.assertEqual(report.split(), ["{", '"problems":', "[],", '"repositories":', "{}", "}"])

    def test_write(self):
        with fake_renovate(*LINES), tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "out.html")
            _, out, _ = run(".", "-o", "html", "-w", str(path))
            self.assertTrue(path.read_text().startswith("<!DOCTYPE html>"))
        self.assertEqual(out, "")

    def test_version_is_shown(self):
        with fake_renovate(*LINES):
            _, _, err = run(".")
            _, _, silent = run(".", "--silent")
        self.assertIn("Renovate 44.209.0 (podman, ghcr.io/renovatebot/renovate:latest)", err)
        self.assertEqual(silent, "")

    def test_missing_image(self):
        engine_error = "Error: ghcr.io/renovatebot/renovate:latest: image not known"
        with fake_renovate(engine_error, status=125):
            code, out, err = run(".", "--silent")
        self.assertEqual((code, out), (125, ""))
        self.assertIn(f"the container did not start: {engine_error}", err)
        self.assertIn("podman pull ghcr.io/renovatebot/renovate:latest", err)

    def test_failure_points_to_the_raw_log(self):
        with fake_renovate(*LINES, status=1):
            code, _, err = run(".")
        self.assertEqual(code, 1)
        self.assertIn("-o raw", err)


class PatUrlTests(unittest.TestCase):
    def test_pat_url(self):
        code, out, _ = run("--pat-url")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("https://github.com/settings/personal-access-tokens/new?"))
        self.assertIn("&contents=read&", out)
        self.assertNotIn("write", out)

    def test_pat_url_on_github_enterprise_server(self):
        _, out, _ = run("--pat-url", "--host", "ghe.example.com")
        self.assertTrue(out.startswith("https://ghe.example.com/settings/"))
