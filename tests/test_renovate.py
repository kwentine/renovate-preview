import os
import unittest
from pathlib import Path
from unittest import mock

from renovate_preview.cli import parse_args
from renovate_preview.renovate import (
    Target,
    arguments,
    environment,
    forwarded_environment,
    include_paths,
    passthrough_value,
)


def args(*argv, env=None):
    opts = parse_args(list(argv))
    with mock.patch.dict(os.environ, env or {}, clear=True):
        return arguments(opts, Target.parse(opts.repository))


class TargetTests(unittest.TestCase):
    def test_remote(self):
        self.assertEqual(Target.parse("@org/repo"), Target(True, "org/repo"))

    def test_local_defaults_to_the_current_directory(self):
        self.assertEqual(Target.parse(None), Target(False, str(Path.cwd())))


class PathFilterTests(unittest.TestCase):
    def test_bare_directory_also_matches_its_subtree(self):
        self.assertEqual(include_paths("base/, a/*.yaml"), ["base", "base/**", "a/*.yaml"])


class ArgumentTests(unittest.TestCase):
    def test_local(self):
        self.assertEqual(
            args("/tmp"), ["--report-type=logging", "--dry-run=lookup", "--platform=local"]
        )

    def test_scope(self):
        self.assertIn("--dry-run=full", args("@o/r"))
        self.assertIn("--dry-run=extract", args("--extract", "/tmp"))

    def test_remote(self):
        self.assertEqual(args("@org/repo")[-2:], ["--platform=github", "org/repo"])
        other = args("@o/r", env={"RENOVATE_PLATFORM": "gitlab"})
        self.assertEqual(other[-2:], ["--platform=gitlab", "o/r"])

    def test_github_enterprise_server(self):
        argv = args("@o/r", "--host", "ghe.example.com")
        self.assertIn("--endpoint=https://ghe.example.com/api/v3/", argv)
        self.assertFalse([a for a in args("@o/r") if a.startswith("--endpoint")])

    def test_except_is_passed_as_given(self):
        self.assertIn("--ignore-paths=manifests,a/**", args("/tmp", "--except", "manifests,a/**"))

    def test_filters(self):
        argv = args("/tmp", "--only", "base", "--managers", "kustomize")
        self.assertIn("--include-paths=base,base/**", argv)
        self.assertIn("--enabled-managers=kustomize", argv)


class PassthroughTests(unittest.TestCase):
    """Arguments after `--` win: over ours, and (with --force-cli) over the repo config."""

    def test_passthrough_comes_after_ours_with_force_cli(self):
        self.assertEqual(
            args("/tmp", "--", "--ignore-paths=x"),
            [
                "--report-type=logging",
                "--dry-run=lookup",
                "--force-cli=true",
                "--platform=local",
                "--ignore-paths=x",
            ],
        )

    def test_no_force_cli_without_passthrough(self):
        self.assertNotIn("--force-cli=true", args("/tmp"))

    def test_ours_are_dropped_when_given_again(self):
        argv = args("/tmp", "--only", "base", "--", "--include-paths", "x", "--dry-run=extract")
        self.assertFalse([a for a in argv if a.startswith("--include-paths=")])
        self.assertNotIn("--dry-run=lookup", argv)
        self.assertEqual(argv[-3:], ["--include-paths", "x", "--dry-run=extract"])

    def test_remote_repository_stays_last(self):
        self.assertEqual(args("@o/r", "--", "--foo")[-2:], ["--foo", "o/r"])

    def test_passthrough_value(self):
        self.assertEqual(passthrough_value(["--dry-run=full"], "--dry-run"), "full")
        self.assertEqual(passthrough_value(["--dry-run", "full"], "--dry-run"), "full")
        self.assertEqual(passthrough_value(["--dry-run", "--x"], "--dry-run"), "")
        self.assertIsNone(passthrough_value(["--dry-runs=1"], "--dry-run"))


class EnvironmentTests(unittest.TestCase):
    def test_renovate_defaults_for_require_config(self):
        self.assertNotIn("RENOVATE_REQUIRE_CONFIG", environment(None))

    def test_global_config(self):
        self.assertNotIn("RENOVATE_CONFIG_FILE", environment(None))
        self.assertEqual(environment("/g.json")["RENOVATE_CONFIG_FILE"], "/g.json")

    def test_forwarded_by_name(self):
        env = {"RENOVATE_TOKEN": "x", "GITHUB_COM_TOKEN": "y", "RENOVATE_PREVIEW_RUNTIME": "z"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(forwarded_environment(), ["GITHUB_COM_TOKEN", "RENOVATE_TOKEN"])
