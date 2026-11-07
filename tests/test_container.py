import logging
import os
import tempfile
import unittest
from unittest import mock

from renovate_preview.container import (
    Filesystem,
    Progress,
    command,
    filesystem,
    printable,
)
from renovate_preview.renovate import Target


def run_command(target="/tmp", engine="podman", image="img:tag", env=None, **kw):
    fs = filesystem(Target.parse(target), kw.pop("global_config", None))
    with mock.patch.dict(os.environ, env or {}, clear=True):
        return command(engine, image, fs, {"LOG_LEVEL": "debug"}, ["--a"], **kw)


class FilesystemTests(unittest.TestCase):
    def test_local_repository_is_the_working_directory(self):
        fs = filesystem(Target(False, "/repo"), None)
        self.assertEqual(fs, Filesystem([("/repo", "/usr/src/app")], "/usr/src/app"))

    def test_remote_mounts_nothing(self):
        self.assertEqual(filesystem(Target(True, "o/r"), None), Filesystem())

    def test_global_config_keeps_its_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".json5") as config:
            fs = filesystem(Target(True, "o/r"), config.name)
        self.assertEqual(fs.global_config, "/renovate-preview/global.json5")
        self.assertEqual(fs.mounts, [(config.name, "/renovate-preview/global.json5")])


class CommandTests(unittest.TestCase):
    def test_mounts(self):
        argv = run_command()
        self.assertEqual(argv[:4], ["podman", "run", "--rm", "--pull=never"])
        self.assertIn("/tmp:/usr/src/app:ro,z", argv)
        self.assertIn("renovate-preview:/tmp/renovate", argv)
        self.assertEqual(argv[-2:], ["img:tag", "--a"])

    def test_environment(self):
        argv = run_command(env={"RENOVATE_TOKEN": "secret"})
        self.assertIn("LOG_LEVEL=debug", argv)
        self.assertIn("GIT_CONFIG_VALUE_0=*", argv)
        self.assertIn("RENOVATE_TOKEN", argv)  # by name: the value stays out of the command
        self.assertFalse([a for a in argv if "secret" in a])

    def test_program_name_and_shell(self):
        argv = run_command(program="bash", name="rp", interactive=True)
        self.assertEqual(argv[argv.index("--name") + 1], "rp")
        self.assertIn("-it", argv)
        self.assertEqual(argv[-3:], ["img:tag", "bash", "--a"])

    def test_printable_keeps_flags_with_their_values(self):
        text = printable(run_command(name="rp"))
        self.assertTrue(text.startswith("podman run --rm \\\n"))
        self.assertIn("\n    --name rp \\\n", text)
        self.assertIn("\n    -e LOG_LEVEL=debug \\\n", text)


class ProgressTests(unittest.TestCase):
    LINES = (
        "Trying to pull ghcr.io/renovatebot/renovate:latest...",
        '{"level":30,"msg":"Renovate started","renovateVersion":"44.209.0"}',
        '{"level":30,"msg":"Repository started","renovateVersion":"44.209.0"}',
        '{"level":20,"msg":"extract()"}',
        "some later plain text",
    )

    def shown(self, level):
        logger = logging.getLogger("renovate_preview")
        logger.setLevel(level)
        progress = Progress("podman, img:latest")
        with self.assertLogs(logger, level) as logs:
            for line in self.LINES:
                progress(line)
        return [record.getMessage() for record in logs.records]

    def test_default(self):
        messages = self.shown(logging.INFO)
        self.assertEqual(
            messages,
            [
                "Renovate 44.209.0 (podman, img:latest)",
                "reading configuration...",
                "extracting dependencies...",
            ],
        )

    def test_verbose_shows_every_record(self):
        messages = self.shown(logging.DEBUG)
        self.assertNotIn("extracting dependencies...", messages)
        self.assertIn("DEBUG extract()", messages)
