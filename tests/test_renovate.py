import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from renovate_preview.cli import parse_args
from renovate_preview.renovate import (
    Target,
    build_command,
    build_validate_command,
    ignore_paths,
    include_paths,
)


def command(*argv, runtime="podman", env=None):
    opts = parse_args(list(argv))
    opts.image = "img:tag"
    with mock.patch.dict(os.environ, env or {}, clear=True):
        return build_command(opts, Target.parse(opts.repository), runtime)


class TargetTests(unittest.TestCase):
    def test_remote(self):
        self.assertEqual(Target.parse("@org/repo"), Target(True, "org/repo"))

    def test_local_defaults_to_the_current_directory(self):
        self.assertEqual(Target.parse(None), Target(False, str(Path.cwd())))


class PathFilterTests(unittest.TestCase):
    def test_bare_directory_also_matches_its_subtree(self):
        self.assertEqual(include_paths("base/, a/*.yaml"), ["base", "base/**", "a/*.yaml"])

    def test_except_restates_defaults_and_the_repository_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "renovate.json").write_text('{"ignorePaths": ["vendor/**"]}')
            globs = ignore_paths("overlays/**", Target(False, tmp))
        self.assertEqual(
            globs, ["**/node_modules/**", "**/bower_components/**", "vendor/**", "overlays/**"]
        )

    def test_except_survives_an_unreadable_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "renovate.json").write_text("// json5")
            self.assertIn("x", ignore_paths("x", Target(False, tmp)))


class CommandTests(unittest.TestCase):
    def test_container_mounts_the_repository_read_only(self):
        argv = command("/tmp").argv
        self.assertEqual(argv[:3], ["podman", "run", "--rm"])
        self.assertIn("/tmp:/usr/src/app:ro,z", argv)
        self.assertIn("img:tag", argv)
        self.assertEqual(argv[-2:], ["--platform", "local"])

    def test_scope(self):
        self.assertIn("--dry-run=lookup", command("/tmp").argv)
        self.assertIn("--dry-run=full", command("@o/r").argv)
        self.assertIn("--dry-run=extract", command("--just-extract", "/tmp").argv)
        self.assertFalse([a for a in command("--run", "@o/r").argv if "dry-run" in a])

    def test_remote_is_not_mounted(self):
        argv = command("@org/repo").argv
        self.assertEqual(argv[-3:], ["--platform", "github", "org/repo"])
        self.assertFalse([a for a in argv if "/usr/src/app" in a])

    def test_passthrough_goes_before_the_platform(self):
        argv = command("/tmp", "--", "--ignore-paths=x").argv
        self.assertEqual(argv[-3:], ["--ignore-paths=x", "--platform", "local"])

    def test_credentials_are_forwarded_by_name_only(self):
        argv = command("/tmp", env={"RENOVATE_TOKEN": "secret", "RENOVATE_PREVIEW_RUNTIME": "x"})
        self.assertIn("RENOVATE_TOKEN", argv.argv)
        self.assertFalse([a for a in argv.argv if "secret" in a or "PREVIEW" in a])

    def test_local_runtime_runs_in_the_repository(self):
        cmd = command("/tmp", runtime="local")
        self.assertEqual(cmd.argv[0], "renovate")
        self.assertEqual(cmd.cwd, "/tmp")
        self.assertEqual(cmd.env["LOG_FORMAT"], "json")
        self.assertNotIn("GIT_CONFIG_COUNT", cmd.env)

    def test_printable_keeps_flags_with_their_values(self):
        text = command("/tmp", "--managers", "kustomize").printable()
        self.assertTrue(text.startswith("podman run --rm \\\n"))
        self.assertIn("\n    --enabled-managers kustomize \\\n", text)
        self.assertIn("\n    -e LOG_LEVEL=debug \\\n", text)

    def test_printable_local_command(self):
        text = command("/tmp", runtime="local").printable()
        self.assertTrue(text.startswith("cd /tmp && \\\n  NODE_OPTIONS=--no-deprecation"))

    def test_validate(self):
        opts = parse_args(["/tmp"])
        opts.image = "img:tag"
        config = Path("/tmp/renovate.json")
        cmd = build_validate_command(opts, "local", config)
        self.assertEqual(cmd.argv, ["renovate-config-validator", "--no-global", str(config)])
        argv = build_validate_command(opts, "podman", config).argv
        self.assertIn("/tmp/renovate.json:/renovate-preview/renovate.json:ro,z", argv)
        self.assertEqual(argv[-2:], ["--no-global", "/renovate-preview/renovate.json"])


class ConfigTests(unittest.TestCase):
    """--config shadows the remote repository's config; RENOVATE_CONFIG_FILE is global config."""

    def test_config_shadows_the_repository_config(self):
        with tempfile.NamedTemporaryFile(suffix=".json5") as config:
            argv = command("@o/r", "--config", config.name).argv
            self.assertIn(f"{config.name}:/renovate-preview/config.json5:ro,z", argv)
        self.assertIn("RENOVATE_ADDITIONAL_CONFIG_FILE=/renovate-preview/config.json5", argv)
        self.assertIn("RENOVATE_REQUIRE_CONFIG=ignored", argv)
        self.assertIn("RENOVATE_DELETE_ADDITIONAL_CONFIG_FILE=false", argv)

    def test_repository_config_is_read_without_config(self):
        self.assertIn("RENOVATE_REQUIRE_CONFIG=optional", command("@o/r").argv)

    def test_global_config_is_mounted(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as config:
            argv = command("/tmp", env={"RENOVATE_CONFIG_FILE": config.name}).argv
            self.assertIn(f"{config.name}:/renovate-preview/global.json:ro,z", argv)
        self.assertIn("RENOVATE_CONFIG_FILE=/renovate-preview/global.json", argv)
        self.assertNotIn("RENOVATE_CONFIG_FILE", argv)  # not also forwarded as-is

    def test_local_runtime_gets_absolute_paths(self):
        cmd = command("@o/r", "--config", "renovate.json", runtime="local")
        self.assertEqual(
            cmd.env["RENOVATE_ADDITIONAL_CONFIG_FILE"], str(Path.cwd() / "renovate.json")
        )

    def test_other_platforms(self):
        argv = command("@o/r", env={"RENOVATE_PLATFORM": "gitlab"}).argv
        self.assertEqual(argv[-3:], ["--platform", "gitlab", "o/r"])
