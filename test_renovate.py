#!/usr/bin/env python3
"""Unit tests for renovate.py.

    $ make test          # or: python3 helpers/renovate/test_renovate.py

These cover the pure logic - command construction, log/report parsing, diagnostics - and
never invoke Renovate. For the end-to-end checks, run the fixtures instead:

    $ make examples      # or: ./helpers/renovate/renovate.py --examples [--with-network]
"""

import argparse
import contextlib
import io
import json
import posixpath
import subprocess
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import renovate  # noqa: E402


# --- fixtures ----------------------------------------------------------------

EXTRACT_REPORT = {
    "problems": [],
    "repositories": {
        "local": {
            "problems": [],
            "branches": [],
            "packageFiles": {
                "argocd": [
                    {
                        "packageFile": "manifests/grafana/application.yaml",
                        "deps": [
                            {
                                "depName": "grafana",
                                "currentValue": "8.1.0",
                                "datasource": "helm",
                            }
                        ],
                    }
                ],
                "kubernetes": [
                    {
                        "packageFile": "manifests/sysctl-config/daemonset.yaml",
                        "deps": [{"depName": "busybox", "currentValue": "1.36"}],
                    }
                ],
            },
        }
    },
}

FULL_REPORT = {
    "problems": [],
    "repositories": {
        "local": {
            "problems": [],
            "branches": [
                {
                    "branchName": "renovate/grafana-8.x",
                    "prNo": None,
                    "prTitle": "chore(deps): update helmchart grafana/grafana to v8.2.0",
                    "result": "error",
                    "upgrades": [
                        {
                            "depName": "grafana",
                            "currentValue": "8.1.0",
                            "newValue": "8.2.0",
                            "packageFile": "manifests/grafana/application.yaml",
                            "updateType": "minor",
                        }
                    ],
                }
            ],
            "packageFiles": {
                "argocd": [
                    {
                        "packageFile": "manifests/grafana/application.yaml",
                        "deps": [
                            {
                                "depName": "grafana",
                                "currentValue": "8.1.0",
                                "datasource": "helm",
                                "updates": [{"newValue": "8.2.0", "updateType": "minor"}],
                            },
                            {
                                # a dep with no available update must not vanish or crash
                                "depName": "no-update-available",
                                "currentValue": "1.0.0",
                                "updates": [],
                            },
                        ],
                    }
                ]
            },
        }
    },
}

REPORT_LOG = {"level": 30, "msg": "Printing report", "report": EXTRACT_REPORT}
EXTRACTED_DEPENDENCIES_LOG = {
    "level": 30,
    "msg": "Extracted dependencies",
    "packageFiles": EXTRACT_REPORT["repositories"]["local"]["packageFiles"],
}
PACKAGE_FILES_WITH_UPDATES_LOG = {
    "level": 20,
    "msg": "packageFiles with updates",
    "config": FULL_REPORT["repositories"]["local"]["packageFiles"],
}
EXTRACTION_COMPLETE_LOG = {
    "level": 30,
    "msg": "Dependency extraction complete",
    "stats": {
        "managers": {"argocd": {"fileCount": 30, "depCount": 38}},
        "total": {"fileCount": 1784, "depCount": 6997},
    },
}
GIT_FALLBACK_LOG = {"level": 20, "msg": "Could not get file list using git, using glob instead"}
GIT_OK_LOG = {"level": 20, "msg": "Got file list using git"}
TOKEN_WARNING_LOG = {"level": 40, "msg": "GitHub token is required for some dependencies"}
UNRELATED_LOG = {"level": 20, "msg": "Resolving branchName"}


def make_args(**overrides):
    """A Namespace shaped like a parsed command line, so tests exercise the real parser."""
    parser = renovate.build_arg_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        assert hasattr(args, key), f"unknown option {key}"
        setattr(args, key, value)
    return args


def visible(written: str) -> str:
    """What a terminal would still be showing after honouring the \\r characters."""
    line = ""
    for piece in written.split("\r"):
        line = piece + line[len(piece):]
    return line.rstrip()


def make_runtime(kind="podman"):
    return renovate.Runtime(kind, "latest", "renovate-tmp")


def run_cmd(**overrides):
    runtime = make_runtime(overrides.pop("runtime_kind", "podman"))
    scope = overrides.pop("scope_value", "lookup")
    args = make_args(**overrides)
    cmd, decisions = renovate.build_run_command(args, runtime, scope)
    return cmd, decisions, runtime


def value_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


# --- path globs --------------------------------------------------------------


class ExpandBarePathsTests(unittest.TestCase):
    """includePaths are matched with equality-or-minimatch, so a bare directory needs help."""

    def test_bare_path_gains_subtree_glob(self):
        self.assertEqual(
            renovate.expand_bare_paths("manifests/cert-manager"),
            ["manifests/cert-manager", "manifests/cert-manager/**"],
        )

    def test_trailing_slash_does_not_produce_a_double_slash(self):
        self.assertEqual(
            renovate.expand_bare_paths("manifests/cert-manager/"),
            ["manifests/cert-manager", "manifests/cert-manager/**"],
        )

    def test_path_with_glob_char_is_left_alone(self):
        self.assertEqual(renovate.expand_bare_paths("manifests/*.yaml"), ["manifests/*.yaml"])

    def test_multiple_comma_separated_entries(self):
        self.assertEqual(
            renovate.expand_bare_paths("a/b, c/d/**"), ["a/b", "a/b/**", "c/d/**"]
        )

    def test_blank_entries_are_skipped(self):
        self.assertEqual(renovate.expand_bare_paths("a/b,,  "), ["a/b", "a/b/**"])


class SplitGlobsTests(unittest.TestCase):
    def test_kept_verbatim(self):
        self.assertEqual(renovate.split_globs("a/b, **/c/**"), ["a/b", "**/c/**"])


class GitignoreChartGlobsTests(unittest.TestCase):
    def test_extracts_charts_patterns(self):
        with tempfile.TemporaryDirectory() as d:
            local_dir = Path(d)
            (local_dir / ".gitignore").write_text(
                "/manifests/**/charts/\n/argocd/**/charts/\n# comment\nnode_modules/\n"
            )
            globs = renovate.gitignore_chart_globs(local_dir)
        self.assertEqual(globs, ["manifests/**/charts/**", "argocd/**/charts/**"])

    def test_no_gitignore_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(renovate.gitignore_chart_globs(Path(d)), [])


class ResolveIgnorePathsTests(unittest.TestCase):
    def test_restates_renovate_defaults_because_a_cli_value_replaces_them(self):
        globs = renovate.resolve_ignore_paths(make_args(), "remote", "", None)
        for default in renovate.RENOVATE_DEFAULT_IGNORE_PATHS:
            self.assertIn(default, globs)

    def test_charts_are_always_excluded(self):
        globs = renovate.resolve_ignore_paths(make_args(), "remote", "", None)
        self.assertIn("**/charts/**", globs)

    def test_target_own_ignore_paths_are_preserved(self):
        globs = renovate.resolve_ignore_paths(
            make_args(), "remote", "", {"ignorePaths": ["keep/me/**"]}
        )
        self.assertIn("keep/me/**", globs)

    def test_preset_ignore_paths_added_only_when_config_extends_something(self):
        without = renovate.resolve_ignore_paths(make_args(), "remote", "", {})
        with_extends = renovate.resolve_ignore_paths(
            make_args(), "remote", "", {"extends": ["config:best-practices"]}
        )
        self.assertNotIn("**/tests/**", without)
        self.assertIn("**/tests/**", with_extends)

    def test_no_default_ignores_drops_everything_when_user_passes_nothing(self):
        self.assertEqual(
            renovate.resolve_ignore_paths(
                make_args(no_default_ignores=True), "remote", "", None
            ),
            [],
        )

    def test_no_default_ignores_still_honours_an_explicit_value(self):
        globs = renovate.resolve_ignore_paths(
            make_args(no_default_ignores=True, ignore_paths="only/this/**"), "remote", "", None
        )
        self.assertIn("only/this/**", globs)
        self.assertNotIn("**/charts/**", globs)

    def test_result_is_deduplicated(self):
        globs = renovate.resolve_ignore_paths(
            make_args(ignore_paths="**/charts/**"), "remote", "", None
        )
        self.assertEqual(len(globs), len(set(globs)))


# --- target resolution -------------------------------------------------------


class ResolveTargetTests(unittest.TestCase):
    def test_default_is_local_cwd(self):
        self.assertEqual(renovate.resolve_target(None), ("local", "."))

    def test_local_path(self):
        self.assertEqual(
            renovate.resolve_target("renovate-examples/kustomize"),
            ("local", "renovate-examples/kustomize"),
        )

    def test_remote_repo(self):
        self.assertEqual(
            renovate.resolve_target("@ScilifeLabDataCentre/argocd-matomo"),
            ("remote", "ScilifeLabDataCentre/argocd-matomo"),
        )


# --- effective config --------------------------------------------------------


class BuildEffectiveConfigTests(unittest.TestCase):
    """A plain --config is overridden by the target's own renovate.json; `force` is not."""

    def _write(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, handle)
        handle.close()
        return Path(handle.name)

    def test_repo_level_keys_are_nested_under_force(self):
        path, note = renovate.build_effective_config(
            self._write({"kubernetes": {"managerFilePatterns": ["/x/"]}}), "remote"
        )
        generated = json.loads(path.read_text())
        self.assertEqual(
            generated["force"], {"kubernetes": {"managerFilePatterns": ["/x/"]}}
        )
        self.assertIn("force", note)

    def test_global_only_keys_stay_at_the_top_level(self):
        path, _ = renovate.build_effective_config(
            self._write({"$schema": "https://x", "platform": "github", "extends": ["a"]}),
            "remote",
        )
        generated = json.loads(path.read_text())
        self.assertEqual(generated["platform"], "github")
        self.assertEqual(generated["$schema"], "https://x")
        self.assertEqual(generated["force"], {"extends": ["a"]})

    def test_an_existing_force_block_wins_over_the_synthesized_one(self):
        path, _ = renovate.build_effective_config(
            self._write({"extends": ["a"], "force": {"extends": ["b"]}}), "remote"
        )
        self.assertEqual(json.loads(path.read_text())["force"]["extends"], ["b"])

    def test_a_config_with_only_global_keys_is_passed_through_untouched(self):
        source = self._write({"platform": "github"})
        path, note = renovate.build_effective_config(source, "remote")
        self.assertEqual(path, source)
        self.assertIsNone(note)

    def test_unparseable_config_is_passed_through_with_a_warning(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        handle.write("module.exports = {};")
        handle.close()
        path, note = renovate.build_effective_config(Path(handle.name), "remote")
        self.assertEqual(path, Path(handle.name))
        self.assertIn("not plain JSON", note)


# --- command construction ----------------------------------------------------


class BuildRunCommandTests(unittest.TestCase):
    def test_local_target_is_mounted_read_only_and_becomes_the_workdir(self):
        cmd, decisions, _ = run_cmd()
        self.assertEqual(decisions["mode"], "local")
        self.assertTrue(any(part.endswith(f"{renovate.REPO_MOUNT}:ro,z") for part in cmd))
        self.assertEqual(value_after(cmd, "-w"), renovate.REPO_MOUNT)

    def test_a_real_run_needs_a_writable_mount(self):
        cmd, _, _ = run_cmd(run=True)
        self.assertTrue(any(part.endswith(f"{renovate.REPO_MOUNT}:rw,z") for part in cmd))

    def test_dry_run_is_a_cli_flag_not_an_env_var(self):
        """RENOVATE_DRY_RUN never reaches GlobalConfig, so 'extract' silently does a lookup."""
        cmd, _, _ = run_cmd(scope_value="extract")
        self.assertIn("--dry-run=extract", cmd)
        self.assertFalse(any("RENOVATE_DRY_RUN" in part for part in cmd))

    def test_run_flag_omits_dry_run_entirely(self):
        cmd, _, _ = run_cmd(run=True)
        self.assertFalse(any(part.startswith("--dry-run") for part in cmd))

    def test_git_safe_directory_is_set_so_gitignore_is_honoured(self):
        """Without it git rejects the bind mount and Renovate globs every vendored chart."""
        cmd, _, _ = run_cmd()
        self.assertIn("GIT_CONFIG_KEY_0=safe.directory", cmd)
        self.assertIn("GIT_CONFIG_VALUE_0=*", cmd)

    def test_the_structured_report_is_requested(self):
        cmd, _, _ = run_cmd()
        self.assertIn("--report-type=logging", cmd)

    def test_remote_target_uses_platform_github_and_mounts_nothing(self):
        cmd, decisions, _ = run_cmd(repository="@ScilifeLabDataCentre/argocd-matomo")
        self.assertEqual(decisions["mode"], "remote")
        self.assertEqual(decisions["local_dir"], "")
        idx = cmd.index("--platform")
        self.assertEqual(cmd[idx + 1 : idx + 3], ["github", "ScilifeLabDataCentre/argocd-matomo"])
        self.assertFalse(any(renovate.REPO_MOUNT in part for part in cmd))

    def test_gh_host_sets_the_enterprise_endpoint(self):
        cmd, _, _ = run_cmd(repository="@org/repo", gh_host="github.example.com")
        self.assertIn("RENOVATE_ENDPOINT=https://github.example.com/api/v3", cmd)

    def test_managers_and_include_paths_are_passed_through(self):
        cmd, _, _ = run_cmd(managers="npm,dockerfile", include_paths="a/*")
        self.assertEqual(value_after(cmd, "--enabled-managers"), "npm,dockerfile")
        self.assertEqual(value_after(cmd, "--include-paths"), "a/*")

    def test_bare_include_path_is_expanded_to_also_match_the_subtree(self):
        cmd, _, _ = run_cmd(include_paths="manifests/cert-manager")
        self.assertEqual(
            value_after(cmd, "--include-paths").split(","),
            ["manifests/cert-manager", "manifests/cert-manager/**"],
        )

    def test_the_cache_volume_is_mounted(self):
        cmd, _, _ = run_cmd()
        self.assertIn(f"renovate-tmp:{renovate.CACHE_MOUNT}", cmd)

    def test_local_runtime_produces_a_bare_renovate_invocation(self):
        cmd, _, runtime = run_cmd(runtime_kind="local")
        self.assertEqual(cmd[0], "renovate")
        self.assertNotIn("podman", cmd)
        self.assertEqual(runtime.mounts, [])

    def test_local_runtime_passes_configuration_through_the_environment(self):
        _, _, runtime = run_cmd(runtime_kind="local")
        env = runtime.subprocess_env()
        self.assertEqual(env["LOG_FORMAT"], "json")
        self.assertEqual(env["LOG_LEVEL"], "debug")

    def test_containerized_runtime_inherits_our_environment_unchanged(self):
        _, _, runtime = run_cmd()
        self.assertIsNone(runtime.subprocess_env())


class BuildValidateCommandTests(unittest.TestCase):
    def test_file_is_mounted_at_the_path_the_user_typed(self):
        runtime = make_runtime()
        cmd = renovate.build_validate_command(runtime, "helpers/renovate.json", "repo")
        self.assertEqual(cmd[-1], "helpers/renovate.json")
        self.assertEqual(value_after(cmd, "-w"), renovate.VALIDATE_ROOT)
        self.assertTrue(
            any(f"{renovate.VALIDATE_ROOT}/helpers/renovate.json" in part for part in cmd)
        )

    def test_an_absolute_path_is_mounted_at_that_same_absolute_path(self):
        """Otherwise the validator is told a path that is not where the file was mounted."""
        runtime = make_runtime()
        cmd = renovate.build_validate_command(runtime, "/etc/renovate.json", "repo")
        self.assertEqual(cmd[-1], "/etc/renovate.json")
        self.assertEqual(runtime.mounts[0][1], "/etc/renovate.json")

    def test_a_relative_path_resolves_against_the_scratch_workdir(self):
        runtime = make_runtime()
        cmd = renovate.build_validate_command(runtime, "../renovate.json", "repo")
        self.assertEqual(cmd[-1], "../renovate.json")
        # Both the mount target and workdir+argument normalize to the same place.
        self.assertEqual(
            posixpath.normpath(runtime.mounts[0][1]),
            posixpath.normpath(posixpath.join(renovate.VALIDATE_ROOT, "../renovate.json")),
        )

    def test_repo_flavour_passes_no_global(self):
        cmd = renovate.build_validate_command(make_runtime(), "renovate.json", "repo")
        self.assertIn("--no-global", cmd)

    def test_global_flavour_omits_no_global(self):
        cmd = renovate.build_validate_command(make_runtime(), "config.js", "global")
        self.assertNotIn("--no-global", cmd)

    def test_output_is_left_human_readable(self):
        cmd = renovate.build_validate_command(make_runtime(), "renovate.json", "repo")
        self.assertNotIn("LOG_FORMAT=json", cmd)


# --- report and log parsing --------------------------------------------------


class FindReportTests(unittest.TestCase):
    def test_finds_the_report_record(self):
        self.assertEqual(renovate.find_report([UNRELATED_LOG, REPORT_LOG]), EXTRACT_REPORT)

    def test_none_when_absent(self):
        self.assertIsNone(renovate.find_report([UNRELATED_LOG]))


class RowsFromReportTests(unittest.TestCase):
    def test_extract_report_has_no_new_versions(self):
        rows = renovate.rows_from_report(EXTRACT_REPORT)
        self.assertEqual(len(rows), 2)
        grafana = next(r for r in rows if r["dependency"] == "grafana")
        self.assertEqual(grafana["current"], "8.1.0")
        self.assertEqual(grafana["manager"], "argocd")
        self.assertEqual(grafana["datasource"], "helm")
        self.assertIsNone(grafana["new"])

    def test_one_row_per_update(self):
        rows = renovate.rows_from_report(FULL_REPORT)
        grafana = [r for r in rows if r["dependency"] == "grafana"]
        self.assertEqual([r["new"] for r in grafana], ["8.2.0"])

    def test_a_dep_with_no_update_still_appears(self):
        rows = renovate.rows_from_report(FULL_REPORT)
        idle = next(r for r in rows if r["dependency"] == "no-update-available")
        self.assertIsNone(idle["new"])

    def test_a_digest_only_update_shows_the_digest_not_1_38_to_1_38(self):
        report = {
            "repositories": {
                "local": {
                    "branches": [],
                    "packageFiles": {
                        "kubernetes": [
                            {
                                "packageFile": "ds.yaml",
                                "deps": [
                                    {
                                        "depName": "busybox",
                                        "currentValue": "1.38",
                                        "updates": [
                                            {
                                                "newValue": "1.38",
                                                "newDigest": "sha256:dc2d74b1234567890",
                                                "updateType": "digest",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                }
            }
        }
        row = renovate.rows_from_report(report)[0]
        self.assertTrue(row["new"].startswith("1.38@sha256:"))
        self.assertEqual(row["update_type"], "digest")

    def test_branch_and_pr_title_are_attached_to_the_matching_row(self):
        rows = renovate.rows_from_report(FULL_REPORT)
        grafana = next(r for r in rows if r["new"] == "8.2.0")
        self.assertEqual(grafana["branch"], "renovate/grafana-8.x")
        self.assertIn("update helmchart grafana/grafana", grafana["pr_title"])

    def test_rows_without_a_branch_are_left_blank(self):
        rows = renovate.rows_from_report(FULL_REPORT)
        idle = next(r for r in rows if r["dependency"] == "no-update-available")
        self.assertEqual(idle["branch"], "")


class RowsFromLogsTests(unittest.TestCase):
    """The fallback for a Renovate build without the report facility."""

    def test_extracted_dependencies_record(self):
        rows = renovate.rows_from_logs([EXTRACTED_DEPENDENCIES_LOG, UNRELATED_LOG], "extract")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["new"] is None for r in rows))

    def test_package_files_with_updates_record(self):
        rows = renovate.rows_from_logs([PACKAGE_FILES_WITH_UPDATES_LOG], "lookup")
        self.assertIn("8.2.0", [r["new"] for r in rows])

    def test_build_rows_prefers_the_report(self):
        rows = renovate.build_rows([EXTRACTED_DEPENDENCIES_LOG], FULL_REPORT, "full")
        self.assertIn("8.2.0", [r["new"] for r in rows])

    def test_build_rows_falls_back_to_the_log(self):
        rows = renovate.build_rows([EXTRACTED_DEPENDENCIES_LOG], None, "extract")
        self.assertEqual(len(rows), 2)


class PlannedBranchesTests(unittest.TestCase):
    def test_from_report(self):
        planned = renovate.planned_branches(FULL_REPORT, [])
        self.assertEqual(planned[0]["branch"], "renovate/grafana-8.x")
        self.assertEqual(planned[0]["upgrades"], 1)

    def test_falls_back_to_dry_run_lines_and_deduplicates(self):
        line = {"level": 30, "msg": "DRY-RUN: Would create PR: chore(deps): update x"}
        planned = renovate.planned_branches(None, [line, line, UNRELATED_LOG])
        self.assertEqual(len(planned), 1)


class RunFactsTests(unittest.TestCase):
    def _facts(self, records):
        facts = renovate.RunFacts()
        for rec in records:
            facts.observe(rec)
        return facts

    def test_stats_are_captured(self):
        self.assertEqual(
            self._facts([EXTRACTION_COMPLETE_LOG]).stats["total"]["depCount"], 6997
        )

    def test_git_fallback_is_noticed(self):
        self.assertIs(self._facts([GIT_FALLBACK_LOG]).git_listing, False)
        self.assertIs(self._facts([GIT_OK_LOG]).git_listing, True)
        self.assertIsNone(self._facts([UNRELATED_LOG]).git_listing)

    def test_problems_are_deduplicated(self):
        facts = self._facts([TOKEN_WARNING_LOG, TOKEN_WARNING_LOG, UNRELATED_LOG])
        self.assertEqual(facts.problems, ["WARN: GitHub token is required for some dependencies"])

    def test_repo_ignore_paths_are_recorded(self):
        facts = self._facts(
            [{"level": 20, "msg": "Found repo ignorePaths", "ignorePaths": ["a/**"]}]
        )
        self.assertEqual(facts.repo_ignore_paths, ["a/**"])


class SplitProblemsTests(unittest.TestCase):
    def test_local_full_scope_noise_is_suppressed(self):
        kept, suppressed = renovate.split_problems(
            ["WARN: Error updating branch", "WARN: real problem"], "local", "full"
        )
        self.assertEqual(kept, ["WARN: real problem"])
        self.assertTrue(suppressed)

    def test_kept_for_a_remote_target(self):
        kept, suppressed = renovate.split_problems(
            ["WARN: Error updating branch"], "remote", "full"
        )
        self.assertEqual(len(kept), 1)
        self.assertFalse(suppressed)

    def test_kept_at_other_scopes(self):
        kept, _ = renovate.split_problems(["WARN: Error updating branch"], "local", "lookup")
        self.assertEqual(len(kept), 1)


class FormatStatsLineTests(unittest.TestCase):
    def test_formats_totals(self):
        line = renovate.format_stats_line(EXTRACTION_COMPLETE_LOG["stats"])
        self.assertIn("1784 file(s)", line)
        self.assertIn("6997 dependency(ies)", line)
        self.assertIn("1 manager(s)", line)

    def test_none_when_no_stats(self):
        self.assertIsNone(renovate.format_stats_line(None))


class IterJsonRecordsTests(unittest.TestCase):
    def test_skips_non_json_lines(self):
        raw = ["npm warn deprecated something", '{"level":30,"msg":"hi"}', "", "not json {"]
        self.assertEqual(
            list(renovate.iter_json_records(raw)), [{"level": 30, "msg": "hi"}]
        )


class RecordLevelNameTests(unittest.TestCase):
    def test_numeric_pino_level(self):
        self.assertEqual(renovate.record_level_name({"level": 50}), "error")

    def test_string_level(self):
        self.assertEqual(renovate.record_level_name({"level": "WARN"}), "warn")

    def test_missing_level(self):
        self.assertEqual(renovate.record_level_name({}), "")


# --- output ------------------------------------------------------------------


class FormatTableTests(unittest.TestCase):
    def test_absent_values_render_as_a_dash(self):
        rows = renovate.rows_from_report(EXTRACT_REPORT)
        table = renovate.format_table(rows, renovate.summary_columns(rows, wide=False))
        self.assertRegex(table.splitlines()[2], r"\s-\s")

    def test_columns_are_aligned_and_headed(self):
        rows = renovate.rows_from_report(FULL_REPORT)
        table = renovate.format_table(rows, renovate.summary_columns(rows, wide=False))
        lines = table.splitlines()
        self.assertTrue(lines[0].startswith("DEPENDENCY"))
        self.assertTrue(set(lines[1]) <= {"-", " "})

    def test_wide_adds_manager_and_datasource(self):
        rows = renovate.rows_from_report(EXTRACT_REPORT)
        headers = [title for title, _ in renovate.summary_columns(rows, wide=True)]
        self.assertIn("MANAGER", headers)
        self.assertIn("DATASOURCE", headers)

    def test_pr_columns_appear_only_when_there_is_pr_data(self):
        with_pr = renovate.summary_columns(renovate.rows_from_report(FULL_REPORT), False)
        without = renovate.summary_columns(renovate.rows_from_report(EXTRACT_REPORT), False)
        self.assertIn("PR TITLE", [t for t, _ in with_pr])
        self.assertNotIn("PR TITLE", [t for t, _ in without])


class PrintSummaryTests(unittest.TestCase):
    """The default output is the table and nothing else; --verbose adds the story."""

    def _render(self, rows, *, verbose, diagnostics=(), problems=(), planned=(), scope="lookup"):
        facts = renovate.RunFacts()
        facts.stats = EXTRACTION_COMPLETE_LOG["stats"]
        facts.problems = list(problems)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            renovate.print_summary(
                rows, list(planned), facts, make_decisions(notes=["a note about config"]),
                scope, False, list(diagnostics), Path("/tmp/x.ndjson"), verbose,
            )
        return out.getvalue(), err.getvalue()

    def test_default_prints_the_table_and_nothing_else(self):
        out, err = self._render(renovate.rows_from_report(FULL_REPORT), verbose=False)
        self.assertIn("DEPENDENCY", out)
        self.assertIn("grafana", out)
        self.assertNotIn("file(s)", out)          # no stats line
        self.assertNotIn("a note about config", out)
        self.assertEqual(err, "")                 # nothing to hint at

    def test_verbose_adds_stats_notes_and_the_log_path(self):
        out, err = self._render(renovate.rows_from_report(FULL_REPORT), verbose=True)
        self.assertIn("6997 dependency(ies)", out)
        self.assertIn("a note about config", out)
        self.assertIn("/tmp/x.ndjson", err)

    def test_default_hints_at_problems_on_stderr_in_one_line(self):
        out, err = self._render(
            renovate.rows_from_report(FULL_REPORT), verbose=False,
            problems=["WARN: something"], diagnostics=["a note"],
        )
        self.assertNotIn("WARN", out)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("1 problem(s)", err)
        self.assertIn("1 note(s)", err)
        self.assertIn("-v", err)

    def test_verbose_spells_the_problems_out(self):
        out, _ = self._render(
            renovate.rows_from_report(FULL_REPORT), verbose=True, problems=["WARN: something"]
        )
        self.assertIn("WARN: something", out)

    def test_an_empty_result_always_explains_itself(self):
        out, _ = self._render([], verbose=False, diagnostics=["nothing matched because X"])
        self.assertIn("nothing matched because X", out)

    def test_an_empty_result_with_no_diagnosis_still_says_so(self):
        out, _ = self._render([], verbose=False)
        self.assertIn("No dependencies found.", out)

    def test_planned_branches_are_verbose_only(self):
        planned = [{"branch": "renovate/x", "pr_title": "chore(deps): x", "upgrades": 1}]
        quiet, _ = self._render([], verbose=False, planned=planned, scope="full")
        loud, _ = self._render([], verbose=True, planned=planned, scope="full")
        self.assertNotIn("renovate/x", quiet)
        self.assertIn("renovate/x", loud)


class HintAtTheRestTests(unittest.TestCase):
    def test_silent_when_there_is_nothing_to_add(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            renovate.hint_at_the_rest([{"new": "1"}], [], [], Path("/tmp/x"))
        self.assertEqual(err.getvalue(), "")

    def test_notes_are_not_advertised_when_the_table_is_empty(self):
        """An empty result prints its diagnosis in full, so pointing at -v would be noise."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            renovate.hint_at_the_rest([], ["already printed"], [], Path("/tmp/x"))
        self.assertEqual(err.getvalue(), "")


class RenderRecordTests(unittest.TestCase):
    def test_level_message_and_context(self):
        rendered = renovate.render_record(TOKEN_WARNING_LOG)
        self.assertTrue(rendered.startswith("WARN "))
        self.assertIn("GitHub token is required", rendered)


# --- diagnostics -------------------------------------------------------------


def make_decisions(**overrides):
    decisions = {
        "mode": "local",
        "target": ".",
        "local_dir": "/nonexistent",
        "scratch_dir": "",
        "untracked": "exclude",
        "ignore_paths": [],
        "include_paths": [],
        "notes": [],
        "env": {},
    }
    decisions.update(overrides)
    return decisions


class DiagnoseTests(unittest.TestCase):
    def _facts(self, **attrs):
        facts = renovate.RunFacts()
        for key, value in attrs.items():
            setattr(facts, key, value)
        return facts

    def test_git_fallback_is_called_out(self):
        notes = renovate.diagnose(
            self._facts(git_listing=False), make_decisions(), [{"new": "1"}], "extract"
        )
        self.assertTrue(any("raw glob" in n for n in notes))

    def test_git_fallback_is_expected_when_we_caused_it(self):
        notes = renovate.diagnose(
            self._facts(git_listing=False),
            make_decisions(scratch_dir="/tmp/x"),
            [{"new": "1"}],
            "extract",
        )
        self.assertFalse(any("raw glob" in n for n in notes))

    def test_extract_scope_explains_the_empty_new_column(self):
        rows = renovate.rows_from_report(EXTRACT_REPORT)
        notes = renovate.diagnose(self._facts(), make_decisions(), rows, "extract")
        self.assertTrue(any("--scope=lookup" in n for n in notes))

    def test_no_such_note_once_versions_are_present(self):
        rows = renovate.rows_from_report(FULL_REPORT)
        notes = renovate.diagnose(self._facts(), make_decisions(), rows, "full")
        self.assertFalse(any("--scope=lookup" in n for n in notes))

    def test_empty_result_lists_the_filters_in_effect(self):
        notes = renovate.diagnose(
            self._facts(stats={"total": {"fileCount": 0, "depCount": 0}, "managers": {}}),
            make_decisions(include_paths=["a/**"], ignore_paths=["b/**"]),
            [],
            "extract",
        )
        text = "\n".join(notes)
        self.assertIn("a/**", text)
        self.assertIn("b/**", text)
        self.assertIn("managerFilePatterns are anchored at the repo root", text)

    def test_files_found_but_no_updates_says_so(self):
        notes = renovate.diagnose(
            self._facts(stats={"total": {"fileCount": 3, "depCount": 4}, "managers": {}}),
            make_decisions(),
            [],
            "lookup",
        )
        self.assertTrue(any("no dependency resolved to a newer version" in n for n in notes))


class LooksLikeAManifestTests(unittest.TestCase):
    def test_manifests(self):
        for path in ("a/b.yaml", "Dockerfile", "x/Chart.yaml", "deps/requirements.txt"):
            self.assertTrue(renovate.looks_like_a_manifest(path), path)

    def test_not_manifests(self):
        for path in ("README.md", "helpers/PLAN.md", "img/logo.png"):
            self.assertFalse(renovate.looks_like_a_manifest(path), path)


class UntrackedFilesTests(unittest.TestCase):
    def _repo(self, tmp):
        subprocess.run(["git", "init", "-q", tmp], check=True)
        (Path(tmp) / "tracked.yaml").write_text("a: 1\n")
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", tmp, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "init"],
            check=True,
        )

    def test_reports_only_plausible_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp)
            (Path(tmp) / "new.yaml").write_text("b: 2\n")
            (Path(tmp) / "NOTES.md").write_text("hi\n")
            self.assertEqual(renovate.untracked_files(tmp), ["new.yaml"])

    def test_narrowed_by_include_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp)
            (Path(tmp) / "sub").mkdir()
            (Path(tmp) / "sub" / "new.yaml").write_text("b: 2\n")
            (Path(tmp) / "other.yaml").write_text("c: 3\n")
            self.assertEqual(renovate.untracked_files(tmp, ["sub/**"]), ["sub/new.yaml"])

    def test_not_a_git_repo_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(renovate.untracked_files(tmp), [])


class ScratchCopyTests(unittest.TestCase):
    def test_copies_what_git_would_show_and_drops_dot_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            (Path(tmp) / "tracked.yaml").write_text("a: 1\n")
            (Path(tmp) / "untracked.yaml").write_text("b: 2\n")
            (Path(tmp) / ".gitignore").write_text("ignored.yaml\n")
            (Path(tmp) / "ignored.yaml").write_text("c: 3\n")
            subprocess.run(["git", "-C", tmp, "add", "tracked.yaml", ".gitignore"], check=True)
            dest, count = renovate.scratch_copy(tmp)
        names = sorted(p.name for p in Path(dest).iterdir())
        self.assertEqual(names, [".gitignore", "tracked.yaml", "untracked.yaml"])
        self.assertEqual(count, 3)
        self.assertFalse((Path(dest) / ".git").exists())

    def test_plain_directory_is_copied_wholesale(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.yaml").write_text("a: 1\n")
            dest, count = renovate.scratch_copy(tmp)
        self.assertTrue((Path(dest) / "a.yaml").is_file())
        self.assertEqual(count, 1)


# --- examples ----------------------------------------------------------------


class CheckPatternsTests(unittest.TestCase):
    def test_reports_only_unmatched_patterns(self):
        missed = renovate.check_patterns(
            [r"^chore\(deps\): update x", r"^never matches"],
            ["chore(deps): update x to v2"],
        )
        self.assertEqual(missed, ["^never matches"])

    def test_nothing_missed_when_all_match(self):
        self.assertEqual(renovate.check_patterns([r"v\d"], ["v1"]), [])


class ExtractedDepsTests(unittest.TestCase):
    def test_collapses_duplicate_rows_from_multiple_updates(self):
        deps = renovate.extracted_deps(renovate.rows_from_report(FULL_REPORT))
        self.assertEqual(len(deps), 2)
        self.assertEqual(sorted(d["dependency"] for d in deps), ["grafana", "no-update-available"])


class ExampleFixtureTests(unittest.TestCase):
    """Every example must be self-describing and self-checking."""

    def test_each_example_has_a_readme_a_config_and_expectations(self):
        examples = renovate.example_dirs()
        self.assertGreater(len(examples), 0)
        for example in examples:
            self.assertTrue((example / "README.md").is_file(), example.name)
            self.assertTrue((example / "expected.json").is_file(), example.name)

    def test_expectations_parse_and_are_shaped_right(self):
        for example in renovate.example_dirs():
            expected = json.loads((example / "expected.json").read_text())
            self.assertIn(expected.get("scope", "extract"), ("extract", "lookup", "full"))
            for dep in expected.get("dependencies", []):
                self.assertEqual(
                    sorted(dep), ["current", "dependency", "file", "manager"], example.name
                )

    def test_configs_are_valid_json(self):
        for example in renovate.example_dirs():
            json.loads((example / "renovate.json").read_text())


# --- progress ----------------------------------------------------------------


class ProgressReporterTests(unittest.TestCase):
    def test_lines_mode_never_uses_carriage_returns(self):
        """A \\r redraw renders as one garbled line wherever cursor movement is not emulated."""
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("lines", stream=stream)
        reporter.start()
        reporter.announce("something")
        reporter.stop()
        self.assertNotIn("\r", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("\n"))

    def test_lines_mode_prints_immediately_on_start(self):
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("lines", stream=stream)
        reporter.start()
        reporter.stop()
        self.assertIn("starting renovate", stream.getvalue())

    def test_announce_bypasses_the_throttle(self):
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("lines", stream=stream)
        reporter.start()
        reporter.announce("milestone one")
        reporter.announce("milestone two")
        reporter.stop()
        self.assertIn("milestone one", stream.getvalue())
        self.assertIn("milestone two", stream.getvalue())

    def test_update_is_throttled_within_the_heartbeat_interval(self):
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("lines", stream=stream)
        reporter.HEARTBEAT_INTERVAL = 100
        reporter.start()
        reporter.update("first")
        reporter.update("second")
        reporter.stop()
        self.assertNotIn("second", stream.getvalue())

    def test_heartbeat_keeps_printing_while_renovate_is_silent(self):
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("lines", stream=stream)
        reporter.HEARTBEAT_INTERVAL = 0.2
        reporter.start()
        reporter.update("only status")
        time.sleep(0.6)
        reporter.stop()
        self.assertGreaterEqual(stream.getvalue().count("only status"), 2)

    def test_off_mode_writes_nothing(self):
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("off", stream=stream)
        reporter.start()
        reporter.announce("anything")
        reporter.update("anything else")
        reporter.stop()
        self.assertEqual(stream.getvalue(), "")

    def test_spinner_stays_on_one_line(self):
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("spinner", stream=stream)
        reporter.SPINNER_INTERVAL = 0.02
        reporter.start()
        reporter.update("extracting dependencies")
        time.sleep(0.2)
        reporter.stop()
        self.assertNotIn("\n", stream.getvalue())

    def test_spinner_erases_itself_so_only_the_table_is_left(self):
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("spinner", stream=stream)
        reporter.SPINNER_INTERVAL = 0.02
        reporter.start()
        reporter.update("extracting dependencies")
        time.sleep(0.2)
        reporter.stop()
        self.assertEqual(visible(stream.getvalue()), "")

    def test_spinner_pads_over_a_longer_previous_line(self):
        stream = io.StringIO()
        reporter = renovate.ProgressReporter("spinner", stream=stream)
        reporter.status = "a very long status line that will be replaced by a short one"
        reporter.start()
        reporter.status = "short"
        reporter._paint()
        self.assertIn("short", visible(stream.getvalue()))
        self.assertNotIn("replaced", visible(stream.getvalue()))

    def test_spinner_falls_back_to_ascii_frames_without_utf8(self):
        class Stream(io.StringIO):
            """io.StringIO's own .encoding is read-only, so shadow it with a property."""

            def __init__(self, encoding):
                super().__init__()
                self._encoding = encoding

            @property
            def encoding(self):
                return self._encoding

        self.assertEqual(
            renovate.ProgressReporter("spinner", stream=Stream("ascii")).frames,
            renovate.ProgressReporter.FRAMES_ASCII,
        )
        self.assertEqual(
            renovate.ProgressReporter("spinner", stream=Stream("UTF-8")).frames,
            renovate.ProgressReporter.FRAMES_UNICODE,
        )


class ProgressModeTests(unittest.TestCase):
    """--progress=auto has to avoid \\r wherever the terminal will not honour it."""

    def setUp(self):
        self._env = dict(os.environ)
        for key in ("INSIDE_EMACS", "TERM"):
            os.environ.pop(key, None)
        os.environ["TERM"] = "xterm-256color"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _auto(self, isatty=True, verbose=False, streaming=False):
        with unittest.mock.patch.object(sys.stderr, "isatty", return_value=isatty, create=True):
            return renovate.progress_mode("auto", verbose, streaming)

    def test_spinner_on_a_real_terminal(self):
        self.assertEqual(self._auto(), "spinner")

    def test_nothing_when_piped(self):
        self.assertEqual(self._auto(isatty=False), "off")

    def test_nothing_while_streaming_raw_logs(self):
        self.assertEqual(self._auto(streaming=True), "off")

    def test_lines_under_verbose(self):
        self.assertEqual(self._auto(verbose=True), "lines")

    def test_lines_inside_emacs_where_a_redraw_would_garble(self):
        os.environ["INSIDE_EMACS"] = "29.1,comint"
        self.assertEqual(self._auto(), "lines")

    def test_lines_on_a_dumb_terminal(self):
        os.environ["TERM"] = "dumb"
        self.assertEqual(self._auto(), "lines")

    def test_an_explicit_choice_is_never_second_guessed(self):
        for choice in ("spinner", "lines", "off"):
            self.assertEqual(renovate.progress_mode(choice, False, True), choice)


class PhaseTests(unittest.TestCase):
    def test_extraction_complete_reports_the_counts(self):
        step = renovate.phase(EXTRACTION_COMPLETE_LOG)
        self.assertIn("6997 dep(s)", step)
        self.assertIn("looking up versions", step)

    def test_known_markers_map_to_a_phase(self):
        self.assertEqual(renovate.phase({"msg": "extract()"}), "extracting dependencies")

    def test_internal_chatter_is_not_a_phase(self):
        self.assertIsNone(renovate.phase({"msg": "Post-massage config"}))
        self.assertIsNone(renovate.phase(UNRELATED_LOG))


class MilestoneTests(unittest.TestCase):
    def test_extraction_complete_becomes_the_stats_line(self):
        self.assertIn("6997", renovate.milestone(EXTRACTION_COMPLETE_LOG))

    def test_warnings_are_milestones(self):
        self.assertEqual(
            renovate.milestone(TOKEN_WARNING_LOG),
            "WARN: GitHub token is required for some dependencies",
        )

    def test_ordinary_debug_chatter_is_not(self):
        self.assertIsNone(renovate.milestone(UNRELATED_LOG))


if __name__ == "__main__":
    unittest.main(verbosity=2)
