"""Renovate's side of a run: its command line and its environment.

The functions here are pure: they compute from three values, and never read the process
environment themselves.

opts
    renovate-preview's own options, from ``cli.parse_args()``. ``opts.remote`` is
    "owner/repo" for a remote repository; otherwise it is None, and ``opts.directory`` is
    the local repository's absolute path.
theirs
    The arguments after ``--``, passed on to Renovate as they are.
environ
    renovate-preview's environment, a mapping like ``os.environ``.

``container_args()`` is what goes after the image in ``podman run ... IMAGE``.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

GITHUB = "github.com"

# Config file names Renovate looks for, in its own order of preference.
CONFIG_FILE_NAMES = (
    "renovate.json",
    "renovate.json5",
    ".renovaterc",
    ".renovaterc.json",
    ".github/renovate.json",
    ".gitlab/renovate.json",
)

# Where config files are mounted in the container: CONFIG_DIR/<name><extension>.
CONFIG_DIR = "/renovate-preview"

# The user's variables that Renovate gets, by name only, so that their values stay out of
# the command line. Each authenticates version lookups on github.com (the last two as host
# rules); RENOVATE_TOKEN is also the token of the platform, for @owner/repo. --env adds more.
FORWARDED = ("RENOVATE_TOKEN", "RENOVATE_GITHUB_COM_TOKEN", "GITHUB_COM_TOKEN")

# What every preview sets. Renovate's knobs are its arguments, after `--`.
ENVIRONMENT = {
    "NODE_OPTIONS": "--no-deprecation",
    "LOG_LEVEL": "debug",
    "LOG_FORMAT": "json",
    "RENOVATE_DETECT_HOST_RULES_FROM_ENV": "true",
    "RENOVATE_REPOSITORY_CACHE": "enabled",
}

# The few of Renovate's flags we look at, when they come after `--`. Abbreviations off:
# Renovate (commander) takes none. A bare --dry-run is Renovate's to reject.
THEIR_FLAGS = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
THEIR_FLAGS.add_argument("--dry-run", nargs="?")


def their_flags(theirs: list[str]) -> argparse.Namespace:
    """Parse the flags of THEIR_FLAGS in `theirs`, as ``--dry-run=x`` or ``--dry-run x``."""
    known, _ = THEIR_FLAGS.parse_known_args(theirs)
    return known


def container_args(opts: argparse.Namespace, theirs: list[str]) -> list[str]:
    """Return Renovate's command line.

    Ours come first, then `theirs`, which win: we drop a flag of ours that they give
    too, and --force-cli makes them win over the repository config.
    """
    ours = {"--report-type": "logging"}
    if opts.extract:
        ours["--dry-run"] = "extract"
    else:
        # The local platform can't do 'full': it would silently downgrade to 'lookup'.
        ours["--dry-run"] = "full" if opts.remote else "lookup"
    if theirs:
        # Renovate's default, but a global config could turn it off.
        ours["--force-cli"] = "true"
    # Other platforms: -- --platform=gitlab --endpoint=...
    ours["--platform"] = "github" if opts.remote else "local"
    if opts.host != GITHUB:
        ours["--endpoint"] = f"https://{opts.host}/api/v3/"  # GitHub Enterprise Server

    given = {flag(arg) for arg in theirs}
    args = [f"{name}={value}" for name, value in ours.items() if name not in given]
    args += theirs
    return [*args, opts.remote] if opts.remote else args


def flag(arg: str) -> str:
    """Return the flag of an argument: '--dry-run' for '--dry-run=full'."""
    return arg.split("=", 1)[0]


def inside(name: str, path: str) -> str:
    """Return where the config file `path` is mounted in the container, as `name`."""
    return f"{CONFIG_DIR}/{name}{Path(path).suffix}"


def environment(opts: argparse.Namespace) -> dict[str, str]:
    """Return the variables we set for Renovate: ENVIRONMENT, and the config files."""
    env = dict(ENVIRONMENT)
    if opts.global_config:
        env["RENOVATE_CONFIG_FILE"] = inside("global", opts.global_config)
    if opts.config:
        # Merged over the global config, where the repository's config would come.
        env["RENOVATE_ADDITIONAL_CONFIG_FILE"] = inside("config", opts.config)
        if opts.remote:
            # Don't read the repository's own config at all. The local platform reads it
            # regardless: it forces requireConfig=optional.
            env["RENOVATE_REQUIRE_CONFIG"] = "ignored"
    return env


def forwarded(opts: argparse.Namespace, environ: Mapping[str, str]) -> list[str]:
    """Return the names of the variables Renovate gets from `environ`: those set."""
    return [name for name in dict.fromkeys([*FORWARDED, *opts.env]) if name in environ]


def find_config_file(directory: str) -> Path | None:
    """Return the repository config Renovate would read in `directory`, if any."""
    for name in CONFIG_FILE_NAMES:
        if (Path(directory) / name).is_file():
            return Path(directory) / name
    return None


def credentials_warning(environ: Mapping[str, str]) -> str | None:
    """Return a warning if no variable authenticates version lookups."""
    if any(environ.get(name) for name in FORWARDED):
        return None
    return (
        f"none of {', '.join(FORWARDED)} is set: GitHub rate-limits anonymous version "
        "lookups, and a throttled lookup looks just like 'no update available'."
    )
