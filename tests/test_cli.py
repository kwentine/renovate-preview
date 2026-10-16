import contextlib
import io
import os
import unittest
from unittest import mock

from renovate_preview.cli import main, parse_args


def run(*argv, env=None):
    err, out = io.StringIO(), io.StringIO()
    with (
        mock.patch.dict(os.environ, env or {}, clear=True),
        contextlib.redirect_stderr(err),
        contextlib.redirect_stdout(out),
    ):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ArgumentTests(unittest.TestCase):
    def test_everything_after_double_dash_is_passed_through(self):
        opts = parse_args(["repo", "--", "--foo", "--", "bar"])
        self.assertEqual(opts.repository, "repo")
        self.assertEqual(opts.passthrough, ["--foo", "--", "bar"])

    def test_contradictions_are_rejected(self):
        for argv in (
            ["--silent", "--verbose"],
            ["--raw", "ndjson", "-o", "json"],
            ["--open"],
            ["--run", "--just-extract", "@o/r"],
            ["--run", "."],
            ["--validate", "@o/r"],
            ["--config", "renovate.json", "."],
            ["--config", "/no/such/file", "@o/r"],
            ["/no/such/directory"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(run(*argv, "--just-print")[0], 2)

    def test_remote_needs_a_token(self):
        code, _, err = run("@o/r", "--runtime", "local")
        self.assertEqual(code, 2)
        self.assertIn("RENOVATE_TOKEN", err)

    def test_just_print_needs_no_runtime(self):
        with mock.patch("shutil.which", return_value=None):
            code, out, _ = run(".", "--just-print", "--tag", "44.115.2")
        self.assertEqual(code, 0)
        self.assertIn("ghcr.io/renovatebot/renovate:44.115.2", out)
