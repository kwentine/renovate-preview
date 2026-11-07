"""Renovate's side: renovate-preview's options translated into Renovate's arguments and
environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Config file names Renovate looks for, in its own order of preference.
CONFIG_FILE_NAMES = (
    "renovate.json",
    "renovate.json5",
    ".renovaterc",
    ".renovaterc.json",
    ".github/renovate.json",
    ".gitlab/renovate.json",
)
GLOB_CHARS = set("*?[]{}!")
GITHUB = "github.com"
# Any of these authenticates github.com version lookups (the last two as host rules).
LOOKUP_TOKENS = ("RENOVATE_TOKEN", "RENOVATE_GITHUB_COM_TOKEN", "GITHUB_COM_TOKEN")


@dataclass
class Target:
    """What Renovate runs against: a local directory, or a remote repository."""

    remote: bool
    value: str  # absolute directory, or owner/repo

    @classmethod
    def parse(cls, repository: str | None) -> Target:
        if repository and repository.startswith("@"):
            return cls(True, repository[1:])
        return cls(False, str(Path(repository or ".").resolve()))


def arguments(opts, target: Target) -> list[str]:
    """Renovate's command line. Arguments passed after `--` win over ours: we drop a flag of
    ours that they also give, and --force-cli makes them win over the repository config."""
    ours = {"--report-type": "logging"}
    if opts.extract:
        ours["--dry-run"] = "extract"
    else:
        # The local platform can't do 'full': it would silently downgrade to 'lookup'.
        ours["--dry-run"] = "full" if target.remote else "lookup"
    if opts.managers:
        ours["--enabled-managers"] = opts.managers
    if opts.only:
        ours["--include-paths"] = ",".join(include_paths(opts.only))
    if opts.excluded:
        # Replaces Renovate's ignorePaths, including its defaults and the repository's.
        ours["--ignore-paths"] = opts.excluded
    if opts.passthrough:
        # Renovate's default, but a global config could turn it off.
        ours["--force-cli"] = "true"
    # RENOVATE_PLATFORM (with RENOVATE_ENDPOINT) reaches GitLab, Forgejo...
    remote_platform = os.environ.get("RENOVATE_PLATFORM", "github")
    ours["--platform"] = remote_platform if target.remote else "local"
    if opts.host != GITHUB:
        ours["--endpoint"] = f"https://{opts.host}/api/v3/"  # GitHub Enterprise Server

    theirs = {flag(arg) for arg in opts.passthrough}
    args = [f"{name}={value}" for name, value in ours.items() if name not in theirs]
    args += opts.passthrough
    return [*args, target.value] if target.remote else args


def flag(arg: str) -> str:
    """'--dry-run' for '--dry-run=full'."""
    return arg.split("=", 1)[0]


def passthrough_value(passthrough: list[str], name: str) -> str | None:
    """The value given to flag `name` after `--`, as `--name=value` or `--name value`."""
    for i, arg in enumerate(passthrough):
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
        if arg == name:
            following = passthrough[i + 1 : i + 2]
            return following[0] if following and not following[0].startswith("-") else ""
    return None


def environment(global_config: str | None) -> dict[str, str]:
    """`global_config`: where Renovate finds the global config file, if there is one."""
    env = {
        "NODE_OPTIONS": "--no-deprecation",
        "LOG_LEVEL": "debug",
        "LOG_FORMAT": "json",
        "RENOVATE_DETECT_HOST_RULES_FROM_ENV": "true",
        "RENOVATE_REPOSITORY_CACHE": "enabled",
    }
    if global_config:
        env["RENOVATE_CONFIG_FILE"] = global_config
    return env


def forwarded_environment() -> list[str]:
    """Names of the user's variables passed on to Renovate: credentials and settings."""
    return sorted(
        k
        for k in os.environ
        if (k.startswith("RENOVATE_") and not k.startswith("RENOVATE_PREVIEW_"))
        or k in LOOKUP_TOKENS
    )


def find_config_file(directory: str) -> Path | None:
    for name in CONFIG_FILE_NAMES:
        if (Path(directory) / name).is_file():
            return Path(directory) / name
    return None


def include_paths(value: str) -> list[str]:
    """Renovate matches a bare `base` against a file of exactly that name; also add `base/**`."""
    globs = []
    for entry in filter(None, (e.strip().rstrip("/") for e in value.split(","))):
        globs.append(entry)
        if not GLOB_CHARS & set(entry):
            globs.append(f"{entry}/**")
    return globs


def credentials_warning(extract: bool) -> str | None:
    if extract or any(os.environ.get(name) for name in LOOKUP_TOKENS):
        return None
    return (
        f"none of {', '.join(LOOKUP_TOKENS)} is set: GitHub rate-limits anonymous version "
        "lookups, and a throttled lookup looks just like 'no update available'."
    )
