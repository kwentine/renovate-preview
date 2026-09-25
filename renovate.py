#!/usr/bin/env python3
"""Run Renovate (through podman) against a repo and explain what it found.

Renovate is hard to debug: a dry run emits thousands of log lines, and the interesting
facts - which files it even looked at, why a manager matched nothing, which config actually
won - are buried in them. This wraps a run, feeds Renovate's structured report through a
summary table, and calls out the failure modes that are otherwise invisible.

    renovate_check                     list what the current directory extracts
    renovate_check --scope=lookup      also resolve the new versions available
    renovate_check @org/repo           run against a remote GitHub repo
    renovate_check --validate FILE     validate a config file and exit
    renovate_check --examples          run the renovate-examples/ regression fixtures

`make install` symlinks this into ~/.local/bin as renovate_check. See README.md for usage,
PLAN.md for the design decisions and the root causes behind the non-obvious bits, and
renovate-examples/ for one minimal fixture per feature used in renovate.json.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES_DIR = HERE / "renovate-examples"

IMAGE = "ghcr.io/renovatebot/renovate"
CACHE_VOLUME = "renovate-tmp"
CACHE_MOUNT = "/tmp/renovate/cache"

# The Renovate image's WORKDIR. Mounting the target repo here means Renovate's `--platform
# local` finds it as the current directory without any extra flags.
REPO_MOUNT = "/usr/src/app"
CONFIG_MOUNT = "/renovate-effective-config.json"
# Scratch root for --validate: the config is mounted *underneath* this at exactly the path
# the user typed, so the validator's own output needs no rewriting. See PLAN.md R-1.
VALIDATE_ROOT = "/renovate-validate"

# Forwarded to the container if set in the environment. RENOVATE_TOKEN is the GitHub PAT;
# the rest are per-host credentials picked up by RENOVATE_DETECT_HOST_RULES_FROM_ENV.
PASSTHROUGH_ENV = (
    "RENOVATE_TOKEN",
    "RENOVATE_GITHUB_COM_TOKEN",
    "GITHUB_COM_TOKEN",
    "RENOVATE_DOCKER_NVCR_IO_PASSWORD",
)

# Renovate's own default, which a CLI --ignore-paths would otherwise *replace* (ignorePaths
# is mergeable:false), silently re-enabling node_modules scanning. We union instead.
RENOVATE_DEFAULT_IGNORE_PATHS = ("**/node_modules/**", "**/bower_components/**")
# Vendored/rendered Helm chart trees (`helm pull` dumps) are the single biggest source of
# runaway extraction - thousands of files picked up by the generic html/helm-values managers.
# Usually gitignored, and then already invisible to Renovate (see PLAN.md R-4), but not always.
DEFAULT_CHART_IGNORE_PATHS = ("**/charts/**",)
# What Renovate's own `:ignoreModulesAndTests` preset sets, and therefore what a config
# extending config:recommended or config:best-practices expects to be ignored. Restated here
# because passing --ignore-paths replaces the resolved value outright, which would otherwise
# silently undo the preset. Only applied when the target's config extends something.
PRESET_IGNORE_PATHS = (
    "**/vendor/**",
    "**/examples/**",
    "**/__tests__/**",
    "**/test/**",
    "**/tests/**",
    "**/__fixtures__/**",
)

GLOB_CHARS = set("*?[]{}!")

LEVEL_NAMES = {10: "trace", 20: "debug", 30: "info", 40: "warn", 50: "error", 60: "fatal"}
LEVEL_ORDER = {"trace": 10, "debug": 20, "info": 30, "warn": 40, "error": 50, "fatal": 60}

# Self-hosted options that are rejected inside the repo-level `force` object, so they stay at
# the top level of a generated effective config. Not exhaustive - just the ones plausibly
# found in a config someone points --config at.
GLOBAL_ONLY_KEYS = frozenset(
    {
        "$schema",
        "allowedCommands",
        "allowedEnv",
        "autodiscover",
        "autodiscoverFilter",
        "baseDir",
        "binarySource",
        "cacheDir",
        "detectHostRulesFromEnv",
        "dryRun",
        "endpoint",
        "exposeAllEnv",
        "force",
        "forceCli",
        "gitPrivateKey",
        "globalExtends",
        "hostRules",
        "logFile",
        "logFileLevel",
        "onboarding",
        "onboardingConfig",
        "password",
        "persistRepoData",
        "platform",
        "prCommitsPerRunLimit",
        "printConfig",
        "privateKey",
        "redisUrl",
        "reportPath",
        "reportType",
        "repositories",
        "repositoryCache",
        "requireConfig",
        "secrets",
        "token",
        "username",
    }
)


def record_level_name(record: dict) -> str:
    level = record.get("level")
    if isinstance(level, str):
        return level.lower()
    if isinstance(level, int):
        return LEVEL_NAMES.get(level, str(level))
    return ""


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def die(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


# --- argument parsing --------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="renovate_check",
        description="Run Renovate through podman and summarize what it found.",
        epilog="""\
examples:
  renovate_check                                list what the current directory extracts
                                                (scope=extract: offline, no version lookups)
  renovate_check --scope=lookup                 also resolve the new versions available
  renovate_check --scope=full --wide            also show branches/PRs, with extra columns
  renovate_check --run @org/repo                a real run: creates branches and PRs
  renovate_check @ScilifeLabDataCentre/argocd-matomo --config renovate.debug.json
                                                remote repo with an alternate config
  renovate_check ../other-repo --managers kustomize --include-paths manifests/cert-manager
  renovate_check renovate-examples/custom-manager-jsonata
  renovate_check --examples [--with-network]    run the fixtures and check them
  renovate_check --validate renovate.json
  renovate_check --format=raw --log-level=debug stream Renovate's own log instead

environment:
  RENOVATE_TOKEN            GitHub PAT. Raises datasource rate limits, and is required by
                            --run. PR titles and branch names come from --scope=full, with
                            or without it.
  RENOVATE_PY_RUNTIME       default for --runtime (podman, docker or local)
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "repository",
        nargs="?",
        help="local path (default: current directory) or '@org/repo' for a remote GitHub repo",
    )

    what = parser.add_argument_group("what to run")
    what.add_argument(
        "--run",
        action="store_true",
        help="perform a REAL run instead of a dry-run (creates branches/PRs)",
    )
    what.add_argument(
        "--scope",
        choices=("extract", "lookup", "full"),
        help="dry-run depth. 'extract' lists the deps found, no version lookups (offline). "
        "'lookup' also resolves new versions. 'full' also computes the branches and PR "
        "titles that would be created. Default: 'extract'. Ignored with --run.",
    )
    what.add_argument(
        "--validate",
        metavar="PATH",
        help="validate a config file with renovate-config-validator and exit",
    )
    what.add_argument(
        "--validate-as",
        choices=("repo", "global"),
        default="repo",
        help="config flavour to validate as (default: repo, i.e. a repository renovate.json)",
    )
    what.add_argument(
        "--with-network",
        action="store_true",
        help="with --examples, also run the fixtures whose expectations need version lookups",
    )
    what.add_argument(
        "--examples",
        nargs="*",
        metavar="NAME",
        help=f"run the fixtures in {EXAMPLES_DIR.name}/ and check each still extracts what "
        "its expected.json says (no argument: all of them)",
    )

    cfg = parser.add_argument_group("what Renovate should look at")
    cfg.add_argument("--config", metavar="FILE", help="alternate Renovate config file to use")
    cfg.add_argument(
        "--config-mode",
        choices=("force", "file"),
        default="force",
        help="'force' (default) nests --config under Renovate's `force` so it outranks the "
        "target repo's own renovate.json; 'file' passes it as plain file config, which the "
        "repo config silently overrides (see PLAN.md D3)",
    )
    cfg.add_argument("--managers", metavar="LIST", help="comma-separated managers to enable")
    cfg.add_argument(
        "--include-paths", metavar="GLOBS", help="comma-separated glob(s) limiting files considered"
    )
    cfg.add_argument("--ignore-paths", metavar="GLOBS", help="comma-separated glob(s) to skip")
    cfg.add_argument(
        "--no-default-ignores",
        action="store_true",
        help=f"do not add {', '.join(DEFAULT_CHART_IGNORE_PATHS)} and the target's own "
        "gitignored chart trees to --ignore-paths",
    )
    cfg.add_argument(
        "--untracked",
        choices=("auto", "include", "exclude"),
        default="auto",
        help="what to do about files git does not track. Renovate lists files with "
        "'git ls-files', so an un-added file extracts to nothing. 'auto' (default) runs "
        "faithfully and warns; 'include' runs against a scratch copy so they are seen; "
        "'exclude' runs faithfully and stays quiet.",
    )
    cfg.add_argument(
        "--gh-host", metavar="HOST", help="GitHub Enterprise host for a remote repository"
    )

    out = parser.add_argument_group("output")
    out.add_argument(
        "--format",
        choices=("summary", "json", "report", "raw", "ndjson"),
        default="summary",
        help="summary (default) | json (this tool's normalized structure) | report "
        "(Renovate's own report object) | raw (Renovate's log, rendered) | ndjson (verbatim)",
    )
    out.add_argument(
        "--wide", action="store_true", help="add MANAGER/DATASOURCE/TYPE columns to the summary"
    )
    out.add_argument(
        "--log-level",
        choices=("trace", "debug", "info", "warn", "error"),
        default="info",
        help="minimum level shown by --format=raw/ndjson (default: info). Renovate always runs "
        "at debug internally, so raising this never needs a re-run.",
    )
    out.add_argument(
        "--progress",
        choices=("auto", "spinner", "lines", "off"),
        default="auto",
        help="liveness on stderr while Renovate runs (default: auto - a self-erasing spinner "
        "on a terminal, heartbeat lines under --verbose or where a spinner would garble, "
        "nothing when piped)",
    )
    out.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show the whole story: the command, the ignorePaths union, extraction stats, "
        "planned branches, diagnostics, Renovate's warnings and the log path. Without it "
        "the summary is just the table.",
    )
    out.add_argument(
        "--print-command",
        action="store_true",
        help="print the command that would be run and exit (does not need the runtime installed)",
    )

    rt = parser.add_argument_group("runtime")
    rt.add_argument(
        "--runtime",
        choices=("podman", "docker", "local"),
        default=os.environ.get("RENOVATE_PY_RUNTIME", "podman"),
        help="how to run Renovate (default: podman). 'local' uses a renovate already on $PATH "
        "- much faster to iterate with, and the fallback where no container engine exists.",
    )
    rt.add_argument("--tag", default="latest", help="Renovate image tag (default: latest)")
    rt.add_argument(
        "--cache-volume",
        default=CACHE_VOLUME,
        help=f"podman volume holding Renovate's cache (default: {CACHE_VOLUME})",
    )
    rt.add_argument("--no-cache", action="store_true", help="run without the cache volume")
    rt.add_argument(
        "--reset-cache", action="store_true", help="delete the cache volume before running"
    )
    return parser


# --- target resolution -------------------------------------------------------


def resolve_target(repository: str | None) -> tuple[str, str]:
    """Return (mode, value) where mode is 'local' or 'remote'.

    '@org/repo' is a remote GitHub repository; anything else is a local path.
    """
    if repository is None:
        return "local", "."
    if repository.startswith("@"):
        return "remote", repository[1:]
    return "local", repository


def expand_bare_paths(value: str) -> list[str]:
    """Split a comma-separated glob list, expanding bare directory entries to subtree globs.

    Renovate matches includePaths with `file === path || minimatch(path).match(file)`, so a
    bare `manifests/cert-manager` matches only a file with that exact name - never anything
    beneath it - and silently selects nothing. Every entry without a glob metacharacter
    therefore also gets an `entry/**` companion.
    """
    globs = []
    for entry in value.split(","):
        entry = entry.strip().rstrip("/")
        if not entry:
            continue
        globs.append(entry)
        if not any(c in entry for c in GLOB_CHARS):
            globs.append(f"{entry}/**")
    return globs


def split_globs(value: str) -> list[str]:
    """Split a comma-separated glob list verbatim.

    Unlike includePaths, Renovate's ignorePaths matching also does a plain substring test
    (`file.includes(ignorePath)`), so a bare directory already excludes its whole subtree.
    """
    return [entry.strip().rstrip("/") for entry in value.split(",") if entry.strip()]


def gitignore_chart_globs(local_dir: Path) -> list[str]:
    """Chart trees the target's own .gitignore excludes, as ignorePaths globs.

    Normally redundant - Renovate lists files with `git ls-files`, which already honours
    .gitignore - but it is the safety net for when that falls back to a raw glob.
    """
    gitignore = local_dir / ".gitignore"
    if not gitignore.is_file():
        return []
    globs = []
    for line in gitignore.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "charts/" not in line:
            continue
        pattern = line.lstrip("/").rstrip("/")
        if not pattern.endswith("*"):
            pattern += "/**"
        globs.append(pattern)
    return globs


def load_json_config(path: Path) -> dict | None:
    """Parse a Renovate config file, or return None if it is not plain JSON we can read."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def target_own_config(mode: str, local_dir: str, config: str | None) -> dict | None:
    """The config that will act as the target's repository config, if we can read it."""
    if config:
        return load_json_config(Path(config))
    if mode == "local":
        for name in ("renovate.json", "renovate.json5", ".renovaterc", ".renovaterc.json"):
            candidate = Path(local_dir) / name
            if candidate.is_file():
                return load_json_config(candidate)
    return None


def resolve_ignore_paths(args, mode: str, local_dir: str, own_config: dict | None) -> list[str]:
    """Union the ignorePaths that should be in effect.

    Renovate declares ignorePaths as mergeable:false, so whatever we pass on the CLI
    *replaces* both Renovate's defaults and the target's own value rather than adding to
    them. Anything we want kept has to be restated here.
    """
    if args.no_default_ignores and not args.ignore_paths:
        return []

    globs: list[str] = list(RENOVATE_DEFAULT_IGNORE_PATHS)
    if isinstance(own_config, dict):
        if own_config.get("extends"):
            # Presets we cannot resolve without running may well set ignorePaths.
            globs += list(PRESET_IGNORE_PATHS)
        own = own_config.get("ignorePaths")
        if isinstance(own, list):
            globs += [g for g in own if isinstance(g, str)]
    if not args.no_default_ignores:
        globs += list(DEFAULT_CHART_IGNORE_PATHS)
        if mode == "local":
            globs += gitignore_chart_globs(Path(local_dir))
    if args.ignore_paths:
        globs += split_globs(args.ignore_paths)
    return list(dict.fromkeys(globs))


def build_effective_config(config_path: Path, mode: str) -> tuple[Path, str | None]:
    """Rewrite --config so it actually wins over the target repo's own renovate.json.

    A config passed as RENOVATE_CONFIG_FILE is *file* config, and Renovate merges the target
    repository's own config on top of it - so an alternate config is silently ignored for
    every key the repo also sets. Nesting the repo-level keys under `force`, which by design
    outranks repository config, is what makes --config mean what it looks like it means.

    Returns (path to use, note to show the user).
    """
    raw = load_json_config(config_path)
    if raw is None:
        return config_path, (
            f"{config_path} is not plain JSON, so it is passed as file config; the target's "
            "own renovate.json will override any key it also sets"
        )

    forced = {k: v for k, v in raw.items() if k not in GLOBAL_ONLY_KEYS}
    if not forced:
        return config_path, None

    effective = {k: v for k, v in raw.items() if k in GLOBAL_ONLY_KEYS}
    existing_force = raw.get("force")
    effective["force"] = {**forced, **(existing_force if isinstance(existing_force, dict) else {})}

    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix="renovate-effective-", delete=False
    )
    with handle as fh:
        json.dump(effective, fh, indent=2)
    where = "remote repo's" if mode == "remote" else "repo's"
    return Path(handle.name), (
        f"--config {config_path} was nested under `force` so it outranks the {where} own "
        f"renovate.json (use --config-mode=file for the raw behaviour)"
    )


# --- runtimes ----------------------------------------------------------------


class Runtime:
    """Turns a Renovate invocation into an argv, mapping host paths into the container.

    Path handling is the only real difference between the runtimes, so callers build their
    arguments out of `self.path(...)` and stay runtime-agnostic.
    """

    def __init__(self, kind: str, tag: str, cache_volume: str | None):
        self.kind = kind
        self.tag = tag
        self.cache_volume = cache_volume
        self.mounts: list[tuple[str, str, str]] = []
        self.env: dict[str, str] = {}

    @property
    def containerized(self) -> bool:
        return self.kind in ("podman", "docker")

    def path(self, host_path: Path | str, at: str, mode: str = "ro") -> str:
        """Make `host_path` visible to Renovate and return the path it should use."""
        host_path = str(Path(host_path).resolve())
        if not self.containerized:
            return host_path
        self.mounts.append((host_path, at, mode))
        return at

    def build(self, program: str, args: list[str], env: dict[str, str], workdir: str) -> list[str]:
        # A container gets its configuration through -e flags; a local renovate has to inherit
        # it from our own environment, so remember it either way.
        self.env = dict(env)
        if not self.containerized:
            return [program, *args]

        cmd = [self.kind, "run", "--rm"]
        for name in PASSTHROUGH_ENV:
            if name in os.environ:
                cmd += ["-e", name]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        for host, at, mode in self.mounts:
            # :z relabels for SELinux; harmless elsewhere. Read-only wherever we only read.
            cmd += ["-v", f"{host}:{at}:{mode},z" if mode else f"{host}:{at}:z"]
        if self.cache_volume:
            cmd += ["-v", f"{self.cache_volume}:{CACHE_MOUNT}"]
        if workdir:
            cmd += ["-w", workdir]
        cmd += [f"{IMAGE}:{self.tag}"]
        if program != "renovate":
            cmd += [program]
        return cmd + args

    def subprocess_env(self) -> dict[str, str] | None:
        """The environment for the subprocess, or None to inherit ours unchanged."""
        if self.containerized:
            return None
        return {**os.environ, **self.env}

    def check(self) -> str | None:
        """Return an error message if this runtime is not usable."""
        if self.containerized:
            if shutil.which(self.kind) is None:
                return (
                    f"{self.kind} not found on PATH. Install it, or use --runtime=local with a "
                    "user install of Renovate (npm install -g renovate)."
                )
            return None
        if shutil.which("renovate") is None:
            return (
                "--runtime=local needs 'renovate' on PATH "
                "(npm config set prefix ~/.local && npm install -g renovate)"
            )
        return None

    def reset_cache(self, verbose: bool) -> None:
        if not self.containerized or not self.cache_volume:
            return
        cmd = [self.kind, "volume", "rm", "-f", self.cache_volume]
        if verbose:
            print(f"+ {shlex.join(cmd)}", file=sys.stderr)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


# --- command construction ----------------------------------------------------


def base_env(runtime: Runtime, structured: bool = True) -> dict[str, str]:
    env = {
        "NODE_OPTIONS": "--no-deprecation",
        "RENOVATE_DETECT_HOST_RULES_FROM_ENV": "true",
    }
    if structured:
        # The summary is built from debug-level records; --log-level filters the *display*.
        env["LOG_LEVEL"] = "debug"
        env["LOG_FORMAT"] = "json"
    else:
        # The validator's own human-readable output is the product here, so leave it alone.
        env["LOG_LEVEL"] = "info"
    if runtime.containerized:
        # Renovate's local platform lists files with `git ls-files` and silently falls back to
        # a raw glob (which ignores .gitignore, so vendored charts/ trees get scanned) when git
        # errors out. A bind-mounted repo is owned by a uid the container does not recognise,
        # so git refuses it as "dubious ownership" - unless we mark it safe. See PLAN.md R-4.
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = "*"
    return env


def build_validate_command(runtime: Runtime, typed_path: str, validate_as: str) -> list[str]:
    """Validate a config, reporting the path the user actually typed.

    The file is mounted at exactly that path under a scratch root used as the working
    directory, so the validator's own output needs no rewriting.
    """
    resolved = Path(typed_path).resolve()
    if Path(typed_path).is_absolute():
        # Mount it at the very same absolute path, so the argument we pass and the file we
        # mounted are literally the same string.
        inside = typed_path
    else:
        # Relative: mount under a scratch root and make that the working directory, so the
        # argument resolves against it exactly as it does against the user's cwd (including
        # a leading ../).
        inside = f"{VALIDATE_ROOT}/{typed_path}"
    runtime.path(resolved, inside)

    args = [] if validate_as == "global" else ["--no-global"]
    args.append(typed_path)
    return runtime.build(
        "renovate-config-validator", args, base_env(runtime, structured=False), VALIDATE_ROOT
    )


def build_run_command(args, runtime: Runtime, scope: str) -> tuple[list[str], dict]:
    """Build the Renovate invocation. Returns (argv, facts about what we decided)."""
    mode, value = resolve_target(args.repository)
    env = base_env(runtime)
    renovate_args: list[str] = []
    notes: list[str] = []

    local_dir = str(Path(value).resolve()) if mode == "local" else ""
    workdir = ""
    scratch_dir = ""
    if mode == "local":
        if args.untracked == "include" and not args.run:
            scratch_dir, count = scratch_copy(local_dir)
            notes.append(
                f"--untracked=include: running against a scratch copy of {count} file(s) at "
                f"{scratch_dir} (no .git, so Renovate globs and sees un-added files too)"
            )
        elif args.untracked == "include" and args.run:
            warn("--untracked=include is ignored with --run; a real run must use the real tree")
        workdir = runtime.path(
            scratch_dir or local_dir, REPO_MOUNT, mode="rw" if args.run else "ro"
        )

    own_config = target_own_config(mode, local_dir, args.config)

    if args.config:
        config_path = Path(args.config).resolve()
        if args.config_mode == "force":
            config_path, note = build_effective_config(config_path, mode)
            if note:
                notes.append(note)
        env["RENOVATE_CONFIG_FILE"] = runtime.path(config_path, CONFIG_MOUNT)

    if args.gh_host:
        env["RENOVATE_ENDPOINT"] = f"https://{args.gh_host}/api/v3"
    # Cheap for a local target (the local platform has no branch SHA, so the extract cache can
    # never validate) and a large speedup for repeated remote runs.
    env["RENOVATE_REPOSITORY_CACHE"] = "enabled"
    # One structured record with packageFiles + branches + problems, instead of scraping logs.
    renovate_args += ["--report-type=logging"]

    if not args.run:
        # Deliberately the CLI flag, not RENOVATE_DRY_RUN: the env var does not reach
        # GlobalConfig, so `dryRun === 'extract'` never fires and Renovate runs lookups
        # anyway while never logging the extracted deps. See PLAN.md R-5.
        renovate_args += [f"--dry-run={scope}"]

    if args.managers:
        renovate_args += ["--enabled-managers", args.managers]
    if args.include_paths:
        renovate_args += ["--include-paths", ",".join(expand_bare_paths(args.include_paths))]

    ignore_paths = resolve_ignore_paths(args, mode, local_dir, own_config)
    if ignore_paths:
        renovate_args += ["--ignore-paths", ",".join(ignore_paths)]

    if mode == "local":
        renovate_args += ["--platform", "local"]
    else:
        renovate_args += ["--platform", "github", value]

    if ignore_paths and args.verbose:
        print(
            "ignorePaths passed to Renovate (a CLI value REPLACES the resolved config value, "
            f"so this is the full union):\n  {', '.join(ignore_paths)}",
            file=sys.stderr,
        )

    cmd = runtime.build("renovate", renovate_args, env, workdir)
    facts = {
        "mode": mode,
        "target": value,
        "local_dir": local_dir,
        "scratch_dir": scratch_dir,
        "untracked": args.untracked,
        "ignore_paths": ignore_paths,
        "include_paths": expand_bare_paths(args.include_paths) if args.include_paths else [],
        "notes": notes,
        "env": env,
    }
    return cmd, facts


# --- log / report parsing ----------------------------------------------------


def iter_json_records(raw_lines):
    for line in raw_lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def find_report(records) -> dict | None:
    """Renovate's own report object, emitted once by --report-type=logging."""
    for rec in records:
        if rec.get("msg") == "Printing report" and isinstance(rec.get("report"), dict):
            return rec["report"]
    return None


def dep_rows(manager: str, package_file: dict, repo: str) -> list[dict]:
    file_path = package_file.get("packageFile", "?")
    rows = []
    for dep in package_file.get("deps", []):
        name = dep.get("depName") or dep.get("packageName") or "?"
        current = dep.get("currentValue") or dep.get("currentVersion")
        base = {
            "repository": repo,
            "manager": manager,
            "file": file_path,
            "dependency": name,
            "current": current,
            "datasource": dep.get("datasource") or "",
        }
        updates = dep.get("updates") or []
        if not updates:
            rows.append({**base, "new": None, "update_type": "", "branch": "", "pr_title": ""})
            continue
        for upd in updates:
            new = upd.get("newValue") or upd.get("newVersion")
            digest = upd.get("newDigest")
            # A digest update keeps the same tag, so "1.38 -> 1.38" reads as a no-op unless
            # the digest that actually changed is shown.
            if digest and new == current:
                new = f"{new}@{digest[:14]}..."
            rows.append(
                {
                    **base,
                    "new": new,
                    "update_type": upd.get("updateType") or "",
                    "branch": "",
                    "pr_title": "",
                }
            )
    return rows


def rows_from_report(report: dict) -> list[dict]:
    rows: list[dict] = []
    for repo, data in (report.get("repositories") or {}).items():
        for manager, files in (data.get("packageFiles") or {}).items():
            for package_file in files:
                rows.extend(dep_rows(manager, package_file, repo))
        attach_branches(rows, data.get("branches") or [])
    return rows


def attach_branches(rows: list[dict], branches: list[dict]) -> None:
    """Annotate rows with the branch/PR title Renovate would use (scope=full only).

    Upgrades inside a branch carry no branchName of their own, so they are matched back onto
    the extracted rows by (file, dependency, new version).
    """
    index = {}
    for branch in branches:
        for upgrade in branch.get("upgrades") or []:
            key = (
                upgrade.get("packageFile"),
                upgrade.get("depName"),
                upgrade.get("newValue") or upgrade.get("newVersion"),
            )
            index[key] = (branch.get("branchName") or "", branch.get("prTitle") or "")
    for row in rows:
        hit = index.get((row["file"], row["dependency"], row["new"]))
        if hit:
            row["branch"], row["pr_title"] = hit


def rows_from_logs(records, scope: str) -> list[dict]:
    """Fallback for a Renovate without the report facility."""
    rows: list[dict] = []
    for rec in records:
        if rec.get("msg") == "Extracted dependencies":
            package_files = rec.get("packageFiles") or {}
        elif rec.get("msg") == "packageFiles with updates":
            package_files = rec.get("config") or {}
        else:
            continue
        for manager, files in package_files.items():
            if not isinstance(files, list):
                continue
            for package_file in files:
                rows.extend(dep_rows(manager, package_file, "local"))
    return rows


def build_rows(records, report: dict | None, scope: str) -> list[dict]:
    if report:
        return rows_from_report(report)
    return rows_from_logs(records, scope)


def planned_branches(report: dict | None, records) -> list[dict]:
    if report:
        planned = []
        for data in (report.get("repositories") or {}).values():
            for branch in data.get("branches") or []:
                planned.append(
                    {
                        "branch": branch.get("branchName") or "?",
                        "pr_title": branch.get("prTitle") or "",
                        "pr_number": branch.get("prNo"),
                        "result": branch.get("result") or "",
                        "upgrades": len(branch.get("upgrades") or []),
                    }
                )
        return planned
    # Fallback: the verbatim DRY-RUN lines.
    seen, planned = set(), []
    for rec in records:
        msg = rec.get("msg")
        if isinstance(msg, str) and msg.startswith("DRY-RUN:") and msg not in seen:
            seen.add(msg)
            planned.append({"branch": "", "pr_title": msg, "pr_number": None, "result": "", "upgrades": 0})
    return planned


class RunFacts:
    """What the log says Renovate actually did - the raw material for the diagnostics."""

    def __init__(self):
        self.stats: dict | None = None
        self.git_listing: bool | None = None
        self.repo_ignore_paths: list[str] = []
        self.repo_include_paths: list[str] = []
        self.enabled_managers: list[str] = []
        self.matched_managers: list[str] = []
        self.config_file: str | None = None
        self.problems: list[str] = []
        self._seen_problems: set[str] = set()
        self.renovate_version: str | None = None

    def observe(self, rec: dict) -> None:
        msg = rec.get("msg")
        if not isinstance(msg, str):
            return
        if msg == "Dependency extraction complete":
            self.stats = rec.get("stats")
        elif msg == "Got file list using git":
            self.git_listing = True
        elif msg == "Could not get file list using git, using glob instead":
            self.git_listing = False
        elif msg == "Found repo ignorePaths":
            self.repo_ignore_paths = rec.get("ignorePaths") or []
        elif msg == "Found repo includePaths":
            self.repo_include_paths = rec.get("includePaths") or []
        elif msg == "Repository started":
            self.renovate_version = rec.get("renovateVersion")
        elif msg == "Repository config":
            self.config_file = rec.get("fileName")
        elif msg == "Combined config":
            managers = (rec.get("config") or {}).get("enabledManagers")
            if isinstance(managers, list):
                self.enabled_managers = managers

        level = record_level_name(rec)
        if level in ("warn", "error", "fatal") and msg not in self._seen_problems:
            self._seen_problems.add(msg)
            self.problems.append(f"{level.upper()}: {msg}")


# A full-scope run against a plain directory computes every branch name and PR title, then
# fails to actually write the branch, because the local platform has no git remote to sync
# with. That is expected, and drowning the summary in it would hide real problems.
LOCAL_FULL_SCOPE_NOISE = ("Error updating branch", "Cannot sync git when platform=local")


def split_problems(problems: list[str], mode: str, scope: str) -> tuple[list[str], bool]:
    """Return (problems worth showing, whether local full-scope noise was suppressed)."""
    if mode != "local" or scope != "full":
        return problems, False
    kept = [p for p in problems if not any(n in p for n in LOCAL_FULL_SCOPE_NOISE)]
    return kept, len(kept) != len(problems)


def format_stats_line(stats: dict | None) -> str | None:
    if not stats:
        return None
    total = stats.get("total", {})
    managers = stats.get("managers", {})
    return (
        f"Scanned {total.get('fileCount', '?')} file(s), found {total.get('depCount', '?')} "
        f"dependency(ies) across {len(managers)} manager(s)."
    )


# --- diagnostics -------------------------------------------------------------


# Extensions a Renovate manager might plausibly read. Used to keep the untracked-files
# warning about manifests rather than about every note and README in a dirty work tree.
MANIFEST_SUFFIXES = frozenset(
    {".yaml", ".yml", ".json", ".json5", ".toml", ".tf", ".tfvars", ".lock", ".txt", ".nix"}
)
MANIFEST_NAMES = frozenset({"Dockerfile", "Containerfile", "Chart.yaml", "Gemfile", "Makefile"})


def looks_like_a_manifest(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in MANIFEST_NAMES or Path(name).suffix in MANIFEST_SUFFIXES


def untracked_files(local_dir: str, include_paths: list[str] | None = None) -> list[str]:
    """Files git does not track, which Renovate therefore never sees.

    `git ls-files` lists tracked files only, so a manifest that has not been `git add`ed
    extracts to nothing - a genuinely baffling way for a new file to be ignored. Narrowed to
    files a manager could plausibly read, and to the paths under consideration, so a dirty
    work tree full of scratch notes does not drown the signal.
    """
    try:
        result = subprocess.run(
            ["git", "-C", local_dir, "ls-files", "--others", "--exclude-standard"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    files = [line for line in result.stdout.splitlines() if looks_like_a_manifest(line)]
    if include_paths:
        files = [f for f in files if any(fnmatch.fnmatch(f, g) for g in include_paths)]
    return files


def scratch_copy(local_dir: str) -> tuple[str, int]:
    """Copy the target to a scratch dir with no .git, so Renovate globs instead of asking git.

    Renovate's local platform lists files with `git ls-files` and only falls back to a plain
    glob when git errors out. Dropping .git triggers that fallback, which is the only way to
    make an un-added file visible - the usual reason a brand-new manifest extracts to nothing.

    Only what git would show (tracked + untracked-but-not-ignored) is copied, so vendored
    chart trees and node_modules do not come along for the ride.
    """
    source = Path(local_dir)
    dest = Path(tempfile.mkdtemp(prefix="renovate-scratch-")) / source.name
    names = git_visible_files(local_dir)
    if names is None:
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        return str(dest), sum(1 for _ in dest.rglob("*") if _.is_file())
    for name in names:
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source / name, target)
        except OSError:
            continue
    return str(dest), len(names)


def git_visible_files(local_dir: str) -> list[str] | None:
    """Tracked plus untracked-not-ignored files, or None if this is not a git work tree."""
    try:
        result = subprocess.run(
            ["git", "-C", local_dir, "ls-files", "--cached", "--others", "--exclude-standard"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def diagnose(facts: RunFacts, decisions: dict, rows: list[dict], scope: str) -> list[str]:
    """Explain a surprising result, especially an empty one."""
    notes: list[str] = []

    if facts.git_listing is False and decisions["mode"] == "local" and not decisions["scratch_dir"]:
        notes.append(
            "Renovate could not run 'git ls-files' and fell back to a raw glob of every file, "
            "so .gitignore was NOT honoured - this is what makes vendored charts/ trees get "
            "scanned. Inside a container it usually means git rejected the bind mount as "
            "'dubious ownership'; this tool passes safe.directory=* to prevent it, so seeing "
            "this means something else went wrong (try --runtime=local, or -v to inspect)."
        )

    if (
        decisions["mode"] == "local"
        and decisions["untracked"] == "auto"
        and not decisions["scratch_dir"]
        and facts.git_listing
    ):
        untracked = untracked_files(decisions["local_dir"], decisions["include_paths"])
        if untracked:
            shown = ", ".join(untracked[:5])
            more = f" (+{len(untracked) - 5} more)" if len(untracked) > 5 else ""
            notes.append(
                f"{len(untracked)} untracked file(s) were invisible to this run, because "
                f"Renovate lists files with 'git ls-files': {shown}{more}. "
                "Re-run with --untracked=include to have them extracted anyway."
            )

    if rows:
        if scope == "extract" and not any(row["new"] for row in rows):
            notes.append(
                "NEW is empty because --scope=extract stops before any version lookup "
                "(which is what makes it fast and offline). Use --scope=lookup for new "
                "versions, or --scope=full to also see the branches and PR titles."
            )
        return notes

    notes.append("No dependencies to show. What the run reported:")
    stats_line = format_stats_line(facts.stats)
    notes.append(f"  - {stats_line}" if stats_line else "  - extraction never completed")

    if facts.stats and facts.stats.get("total", {}).get("fileCount"):
        if scope == "extract":
            notes.append(
                "  - files WERE matched, so extraction worked; if the table is empty the "
                "report could not be read (try --format=report)"
            )
        else:
            notes.append(
                "  - files were matched but no dependency resolved to a newer version; "
                "use --scope=extract to see what was extracted regardless of updates"
            )
    else:
        notes.append("  - no file matched any enabled manager. Common causes:")
        if decisions["include_paths"]:
            notes.append(f"      * --include-paths in effect: {decisions['include_paths']}")
        if decisions["ignore_paths"]:
            notes.append(f"      * ignorePaths in effect: {decisions['ignore_paths']}")
        if facts.enabled_managers:
            notes.append(f"      * enabled managers: {', '.join(facts.enabled_managers)}")
        notes.append(
            "      * managerFilePatterns are anchored at the repo root: a pattern like "
            "/^manifests/.+\\.yaml$/ matches nothing when you point this tool at a "
            "subdirectory, because paths are then relative to that subdirectory"
        )
        if facts.config_file:
            notes.append(f"      * repository config used: {facts.config_file}")
        else:
            notes.append(
                "      * no repository config file was found in the target - only Renovate's "
                "defaults applied (use --config to supply one)"
            )
    return notes


# --- output ------------------------------------------------------------------


def format_table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    headers = [title for title, _ in columns]
    data = [[str(row.get(key) or "-") for _, key in columns] for row in rows]
    widths = [max(len(headers[i]), *(len(r[i]) for r in data)) for i in range(len(headers))]

    def fmt(cols):
        return "  ".join(c.ljust(w) for c, w in zip(cols, widths)).rstrip()

    return "\n".join([fmt(headers), fmt(["-" * w for w in widths]), *(fmt(r) for r in data)])


def summary_columns(rows: list[dict], wide: bool) -> list[tuple[str, str]]:
    columns = [("DEPENDENCY", "dependency"), ("CURRENT", "current"), ("NEW", "new")]
    if wide:
        columns += [("TYPE", "update_type"), ("MANAGER", "manager"), ("DATASOURCE", "datasource")]
    columns.append(("FILE", "file"))
    if any(row.get("pr_title") for row in rows):
        columns += [("PR TITLE", "pr_title"), ("BRANCH", "branch")]
    return columns


def print_summary(
    rows, planned, facts: RunFacts, decisions, scope, wide, diagnostics, log_path, verbose
) -> None:
    """The table, and - only when asked - everything that led to it.

    The default is deliberately close to bare: a run that worked prints its table and
    nothing else, so the output pipes, pastes and scrolls cleanly. Everything that explains
    the run moves behind --verbose, with two exceptions that would be negligent to hide: an
    empty result always explains itself, and a run with problems always says so in one line.
    """
    if verbose:
        stats_line = format_stats_line(facts.stats)
        if stats_line:
            print(stats_line)
        for note in decisions["notes"]:
            print(f"note: {note}")
        if stats_line or decisions["notes"]:
            print()

    problems, suppressed = split_problems(facts.problems, decisions["mode"], scope)

    if rows:
        print(
            format_table(
                sorted(rows, key=lambda r: (r["file"], r["dependency"])),
                summary_columns(rows, wide),
            )
        )
    elif diagnostics:
        # An unexplained empty table is the least useful thing this tool could print, so the
        # diagnosis (which opens with "No dependencies to show") is never behind a flag.
        for line in diagnostics:
            print(line)
    else:
        print("No dependencies found.")

    if not verbose:
        hint_at_the_rest(rows, diagnostics, problems, log_path)
        return

    if planned and not any(row.get("pr_title") for row in rows):
        print("\nPlanned branches / PRs:")
        for entry in planned:
            print(f"  {entry['branch'] or '?'}\n    {entry['pr_title'] or '(no title)'}")

    if rows and diagnostics:
        print()
        for line in diagnostics:
            print(line)

    if suppressed:
        print(
            "\nnote: branch names and PR titles above are what Renovate would use; it then "
            "declined to write the branches (no git remote for a local target), which is "
            "expected. Point this at '@org/repo' to exercise the real thing."
        )
    if problems:
        print("\nRenovate reported:")
        for problem in problems[:20]:
            print(f"  {problem}")
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more (see the full log)")

    sys.stdout.flush()
    print(f"\nFull log: {log_path}", file=sys.stderr)


def hint_at_the_rest(rows, diagnostics, problems, log_path) -> None:
    """One stderr line pointing at what --verbose would have added, when there is any.

    On stderr and at most one line, so it neither pollutes a pipe nor buries the table - but
    a warning Renovate raised should never be silently dropped on the floor.
    """
    parts = []
    if problems:
        parts.append(f"{len(problems)} problem(s)")
    if rows and diagnostics:
        parts.append(f"{len(diagnostics)} note(s)")
    if not parts:
        return
    sys.stdout.flush()
    print(f"({', '.join(parts)} - re-run with -v, or see {log_path})", file=sys.stderr)


# Bumped whenever the --format=json shape changes in a way a consumer would notice.
# renovate-tui/ checks it, so a drifting contract fails loudly instead of silently.
JSON_SCHEMA_VERSION = 1


def print_json(rows, planned, facts: RunFacts, decisions, scope, diagnostics, log_path) -> None:
    print(
        json.dumps(
            {
                "schema_version": JSON_SCHEMA_VERSION,
                "log": str(log_path),
                "target": decisions["target"],
                "mode": decisions["mode"],
                "scope": scope,
                "renovate_version": facts.renovate_version,
                "stats": facts.stats,
                "ignore_paths": decisions["ignore_paths"],
                "include_paths": decisions["include_paths"],
                "git_file_listing": facts.git_listing,
                "updates": rows,
                "planned_branches": planned,
                "problems": split_problems(facts.problems, decisions["mode"], scope)[0],
                "diagnostics": diagnostics,
            },
            indent=2,
        )
    )


def render_record(rec: dict) -> str:
    level = record_level_name(rec).upper()
    msg = rec.get("msg", "")
    extra = {
        k: v
        for k, v in rec.items()
        if k not in ("name", "hostname", "pid", "level", "msg", "time", "v", "logContext")
    }
    suffix = f" {json.dumps(extra)}" if extra else ""
    return f"{level:<5} {msg}{suffix}"


# --- execution ---------------------------------------------------------------


class ProgressReporter:
    """Liveness on stderr while Renovate runs, in one of three modes.

    Renovate's Node startup alone is several silent seconds and a container pull is minutes,
    so some sign of life is needed - but by default it should leave no trace once the run is
    over, so the table is the only thing in the scrollback.

    - ``spinner``: one self-erasing line, redrawn in place. The default on a real terminal.
    - ``lines``: plain newline-terminated ``[+  12s] ...`` heartbeats. What ``--verbose``
      uses, and the fallback anywhere a ``\r`` redraw would render as one long garbled line
      instead of moving the cursor - notably Emacs shell buffers (``TERM=dumb``,
      ``INSIDE_EMACS``), which is what broke the original spinner.
    - ``off``: nothing at all.
    """

    HEARTBEAT_INTERVAL = 5.0
    SPINNER_INTERVAL = 0.12
    FRAMES_UNICODE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    FRAMES_ASCII = "|/-\\"

    def __init__(self, mode: str = "lines", stream=sys.stderr):
        self.mode = mode
        self.stream = stream
        self.status = "starting renovate..."
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._start = 0.0
        self._last_print = 0.0
        self._frame = 0
        self._painted = 0
        encoding = (getattr(stream, "encoding", None) or "").lower()
        self.frames = self.FRAMES_UNICODE if "utf" in encoding else self.FRAMES_ASCII

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self.mode == "off":
            return
        self._start = time.monotonic()
        with self._lock:
            self._paint()
        interval = self.SPINNER_INTERVAL if self.mode == "spinner" else 0.25
        self._thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self.mode == "spinner":
            with self._lock:
                self._erase()

    # -- updates ------------------------------------------------------------

    def update(self, text: str) -> None:
        """A routine status. In ``lines`` mode it waits for the heartbeat interval so a
        chatty run does not flood stderr; the spinner repaints on its own timer anyway."""
        self.status = text[:200]
        if self.mode != "lines":
            return
        with self._lock:
            if time.monotonic() - self._last_print >= self.HEARTBEAT_INTERVAL:
                self._paint()

    def announce(self, text: str) -> None:
        """A milestone: shown immediately, bypassing the ``lines`` throttle."""
        self.status = text[:200]
        if self.mode != "lines":
            return
        with self._lock:
            self._paint()

    # -- painting -----------------------------------------------------------

    def _run(self, interval: float) -> None:
        while not self._stop.wait(interval):
            with self._lock:
                if self.mode == "spinner":
                    self._paint()
                elif time.monotonic() - self._last_print >= self.HEARTBEAT_INTERVAL:
                    self._paint()

    def _paint(self) -> None:
        elapsed = time.monotonic() - self._start
        if self.mode == "spinner":
            frame = self.frames[self._frame % len(self.frames)]
            self._frame += 1
            line = f"{frame} {elapsed:.0f}s  {self.status}"
            width = shutil.get_terminal_size((80, 24)).columns - 1
            if len(line) > width:
                line = line[: max(0, width - 1)] + "…"
            # Redraw in place, padding over whatever a previous longer line left behind,
            # and park the cursor at column 0 so a Ctrl-C lands somewhere sensible.
            padding = " " * max(0, self._painted - len(line))
            self.stream.write("\r" + line + padding + "\r")
            self._painted = len(line)
        else:
            self.stream.write(f"[+{elapsed:>4.0f}s] {self.status}\n")
        self.stream.flush()
        self._last_print = time.monotonic()

    def _erase(self) -> None:
        """Leave no trace: the summary should be the only thing in the scrollback."""
        if self._painted:
            self.stream.write("\r" + " " * self._painted + "\r")
            self.stream.flush()
            self._painted = 0


def progress_mode(choice: str, verbose: bool, streaming: bool) -> str:
    """Resolve --progress=auto.

    A \r-redrawn spinner needs a terminal that actually moves the cursor. Emacs shell
    buffers advertise themselves as a tty but do not, which is how the first version of this
    ended up printing one enormous concatenated line - so they get the heartbeat instead.
    """
    if choice != "auto":
        return choice
    if streaming or not sys.stderr.isatty():
        return "off"
    if verbose:
        return "lines"
    if os.environ.get("INSIDE_EMACS") or os.environ.get("TERM", "") in ("", "dumb"):
        return "lines"
    return "spinner"


# Coarse phases for the spinner. Renovate's debug stream is a blur of internal messages
# ("Post-massage config", "Using file pattern: ..."), which flicker past too fast to read
# and say nothing to someone waiting; these are the handful of records that mark real
# progress. --verbose gets the unfiltered stream instead.
PHASES = {
    "Repository started": "reading configuration",
    "extract()": "extracting dependencies",
    "Dependency extraction complete": "looking up versions",
    "Package releases lookups complete": "planning branches",
    "branchifyUpgrades": "planning branches",
    "Repository finished": "wrapping up",
}


def phase(rec: dict) -> str | None:
    """The coarse phase a log record marks, if any."""
    msg = rec.get("msg")
    if not isinstance(msg, str):
        return None
    if msg == "Dependency extraction complete":
        stats = (rec.get("stats") or {}).get("total") or {}
        found = f"found {stats.get('depCount', '?')} dep(s) in {stats.get('fileCount', '?')} file(s)"
        return f"{found}; looking up versions"
    return PHASES.get(msg)


def milestone(rec: dict) -> str | None:
    """A log record worth showing live while the run is in progress."""
    msg = rec.get("msg")
    if not isinstance(msg, str):
        return None
    if msg == "Dependency extraction complete":
        return format_stats_line(rec.get("stats"))
    if msg in ("Repository started", "extract()", "Package releases lookups complete"):
        return msg
    if record_level_name(rec) in ("warn", "error", "fatal"):
        return f"{record_level_name(rec).upper()}: {msg}"
    if msg.startswith("DRY-RUN:"):
        return msg
    return None


def run_and_capture(cmd, runtime, verbose, stream_format, min_level, progress_style):
    """Run Renovate, tee its output to a log file, and optionally stream it."""
    if verbose:
        print(f"+ {shlex.join(cmd)}", file=sys.stderr)

    log_dir = Path(tempfile.gettempdir()) / "renovate-py"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.ndjson"

    raw_lines: list[str] = []
    facts = RunFacts()
    progress = ProgressReporter(progress_style) if progress_style != "off" else None
    if progress:
        progress.start()

    env = runtime.subprocess_env()
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                raw_lines.append(stripped)
                logf.write(line)

                rec = None
                if stripped.lstrip().startswith("{"):
                    try:
                        rec = json.loads(stripped)
                    except json.JSONDecodeError:
                        rec = None
                if rec is not None:
                    facts.observe(rec)

                if progress and rec is not None:
                    if progress.mode == "spinner":
                        step = phase(rec)
                        if step:
                            progress.update(step)
                    else:
                        note = milestone(rec)
                        if note:
                            progress.announce(note)
                        elif isinstance(rec.get("msg"), str):
                            progress.update(rec["msg"])

                if stream_format:
                    if rec is None:
                        print(stripped)
                    elif LEVEL_ORDER.get(record_level_name(rec), 0) >= min_level:
                        print(stripped if stream_format == "ndjson" else render_record(rec))
            exit_code = proc.wait()
    finally:
        if progress:
            progress.stop()

    return exit_code, log_path, raw_lines, facts


def print_failure(exit_code: int, raw_lines: list[str], log_path: Path) -> None:
    print(f"\nERROR: renovate exited with status {exit_code}", file=sys.stderr)
    print("--- last lines of output ---", file=sys.stderr)
    for line in raw_lines[-20:]:
        print(line[:2000], file=sys.stderr)
    print(f"--- full log: {log_path} ---", file=sys.stderr)


# --- examples ----------------------------------------------------------------


def example_dirs() -> list[Path]:
    if not EXAMPLES_DIR.is_dir():
        return []
    return sorted(p for p in EXAMPLES_DIR.iterdir() if (p / "renovate.json").is_file())


def run_one_example(args, runtime: Runtime, example: Path, scope: str):
    """Run a single fixture and return (exit code, rows, planned branches, log path)."""
    example_args = argparse.Namespace(**vars(args))
    example_args.repository = str(example)
    example_args.examples = None
    example_args.config = None
    example_args.format = "json"
    example_args.include_paths = None
    example_args.ignore_paths = None
    example_args.run = False
    # Fixtures are illustrations, not git history: run from a scratch copy so a freshly
    # written example works before it has been committed.
    example_args.untracked = "include"

    # A fresh Runtime per example: each accumulates its own mounts and env.
    ex_runtime = Runtime(runtime.kind, runtime.tag, runtime.cache_volume)
    cmd, decisions = build_run_command(example_args, ex_runtime, scope)
    original_cwd = os.getcwd()
    if ex_runtime.kind == "local":
        os.chdir(decisions["scratch_dir"] or decisions["local_dir"])
    try:
        exit_code, log_path, raw_lines, _facts = run_and_capture(
            cmd, ex_runtime, args.verbose, None, 0, False
        )
    finally:
        os.chdir(original_cwd)

    records = list(iter_json_records(raw_lines))
    report = find_report(records)
    rows = build_rows(records, report, scope)
    planned = planned_branches(report, records) if scope == "full" else []
    return exit_code, rows, planned, log_path


def extracted_deps(rows: list[dict]) -> list[dict]:
    """The stable part of a run: what was found, independent of what versions exist today."""
    unique = {
        (r["file"], r["dependency"], r["current"], r["manager"]): {
            "dependency": r["dependency"],
            "current": r["current"],
            "manager": r["manager"],
            "file": r["file"],
        }
        for r in rows
    }
    return sorted(unique.values(), key=lambda d: (d["file"], d["dependency"]))


def check_patterns(patterns: list[str], values: list[str]) -> list[str]:
    """Return the patterns that nothing matched.

    Expectations about PR titles and branch names are written as regexes on purpose: the
    versions in them change every few weeks, the shape they are illustrating does not.
    """
    return [p for p in patterns if not any(re.search(p, v) for v in values)]


def run_examples(args, runtime: Runtime, names: list[str]) -> int:
    """Run the fixtures and check each still illustrates what its expected.json claims."""
    available = example_dirs()
    if not available:
        return die(f"no examples found in {EXAMPLES_DIR}")
    if names:
        wanted = {n.rstrip("/").split("/")[-1] for n in names}
        unknown = wanted - {p.name for p in available}
        if unknown:
            return die(
                f"unknown example(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(p.name for p in available)}"
            )
        available = [p for p in available if p.name in wanted]

    failures = 0
    skipped = 0
    for example in available:
        expected_path = example / "expected.json"
        expected = json.loads(expected_path.read_text()) if expected_path.is_file() else {}
        scope = expected.get("scope", "extract")
        needs_network = bool(expected.get("network"))

        if needs_network and not args.with_network:
            skipped += 1
            print(f"skip {example.name}: needs network (--with-network to run it)")
            continue

        exit_code, rows, planned, log_path = run_one_example(args, runtime, example, scope)
        found = extracted_deps(rows)

        if not expected:
            print(f"?? {example.name}: no expected.json; extracted {len(found)} dep(s)")
            print(json.dumps({"scope": scope, "dependencies": found}, indent=2))
            continue

        problems = []
        if exit_code != 0:
            problems.append(f"renovate exited {exit_code}")
        want = sorted(
            expected.get("dependencies", []), key=lambda d: (d["file"], d["dependency"])
        )
        if found != want:
            problems.append(
                f"dependencies differ\n     expected: {json.dumps(want)}"
                f"\n     actual:   {json.dumps(found)}"
            )
        titles = [b["pr_title"] for b in planned if b.get("pr_title")]
        branches = [b["branch"] for b in planned if b.get("branch")]
        for label, patterns, values in (
            ("pr_title", expected.get("pr_title_patterns", []), titles),
            ("branch", expected.get("branch_patterns", []), branches),
        ):
            missed = check_patterns(patterns, values)
            if missed:
                problems.append(
                    f"no {label} matched {missed}\n     actual:   {json.dumps(values)}"
                )
        minimum = expected.get("min_updates")
        if minimum is not None and len(rows) < minimum:
            problems.append(f"expected at least {minimum} update row(s), got {len(rows)}")

        if problems:
            failures += 1
            print(f"FAIL {example.name} (scope={scope})")
            for problem in problems:
                print(f"     {problem}")
            print(f"     log: {log_path}")
        else:
            detail = f"{len(found)} dep(s)"
            if titles:
                detail += f", {len(titles)} PR title(s)"
            print(f"ok   {example.name} (scope={scope}): {detail}")

    total = len(available)
    print()
    print(f"{total - failures - skipped}/{total} example(s) passed, {skipped} skipped")
    return 1 if failures else 0


# --- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    runtime = Runtime(
        args.runtime, args.tag, None if args.no_cache else args.cache_volume
    )
    problem = None if args.print_command else runtime.check()
    if problem:
        return die(problem)

    if args.reset_cache:
        runtime.reset_cache(args.verbose)

    if args.examples is not None:
        names = list(args.examples)
        if args.repository:
            # `--examples --with-network NAME` parses NAME as the positional target, because
            # nargs="*" stops at the next option. Treat it as the example name it looks like.
            names.append(args.repository)
        return run_examples(args, runtime, names)

    if args.validate:
        if not Path(args.validate).is_file():
            return die(f"no such file: {args.validate}")
        cmd = build_validate_command(runtime, args.validate, args.validate_as)
        if args.print_command:
            print(shlex.join(cmd))
            return 0
        if args.verbose:
            print(f"+ {shlex.join(cmd)}", file=sys.stderr)
        return subprocess.run(cmd, env=runtime.subprocess_env(), check=False).returncode

    if args.config and not Path(args.config).is_file():
        return die(f"no such file: {args.config}")

    mode, value = resolve_target(args.repository)
    if mode == "local" and not Path(value).is_dir():
        return die(f"no such directory: {value}")

    if args.run and args.scope is not None:
        warn("--scope is ignored with --run")
    if args.gh_host and mode == "local":
        warn("--gh-host is ignored for a local target")
    if args.run:
        if mode == "local":
            warn(
                "--run against a local directory cannot create anything: Renovate's local "
                "platform has no remote to push to. Use '@org/repo' for a real run."
            )
        elif not os.environ.get("RENOVATE_TOKEN"):
            return die("--run against a remote repository needs RENOVATE_TOKEN")
        else:
            print(
                f"note: this is a REAL run against {value} - it can create branches and PRs.",
                file=sys.stderr,
            )

    # The default scope never depends on the environment: what a run does should be legible
    # from the command line alone. RENOVATE_TOKEN raises lookup rate limits and authorizes a
    # real --run; it does not change how deep a dry run goes.
    scope = args.scope or "extract"
    effective_scope = "full" if args.run else scope

    cmd, decisions = build_run_command(args, runtime, scope)
    if args.print_command:
        print(shlex.join(cmd))
        return 0

    if runtime.kind == "local" and mode == "local":
        cwd = decisions["scratch_dir"] or decisions["local_dir"]
    else:
        cwd = None

    stream_format = args.format if args.format in ("raw", "ndjson") else None
    min_level = LEVEL_ORDER.get(args.log_level, 30)
    progress = progress_mode(args.progress, args.verbose, stream_format is not None)

    original_cwd = os.getcwd()
    if cwd:
        os.chdir(cwd)
    try:
        exit_code, log_path, raw_lines, facts = run_and_capture(
            cmd, runtime, args.verbose, stream_format, min_level, progress
        )
    finally:
        os.chdir(original_cwd)

    records = list(iter_json_records(raw_lines))
    report = find_report(records)
    rows = build_rows(records, report, effective_scope)
    planned = planned_branches(report, records) if effective_scope == "full" else []
    diagnostics = diagnose(facts, decisions, rows, effective_scope)

    if args.format == "summary":
        print_summary(
            rows, planned, facts, decisions, effective_scope, args.wide, diagnostics,
            log_path, args.verbose,
        )
    elif args.format == "json":
        print_json(rows, planned, facts, decisions, effective_scope, diagnostics, log_path)
    elif args.format == "report":
        print(json.dumps(report, indent=2) if report else "null")

    # print_summary already prints the log path under --verbose; the other formats have no
    # place to put it, so they get it on stderr for the same audience.
    if args.verbose and args.format in ("json", "report"):
        sys.stdout.flush()
        print(f"Full log: {log_path}", file=sys.stderr)
    if exit_code != 0:
        print_failure(exit_code, raw_lines, log_path)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
