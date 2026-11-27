"""Renovate's side of a run: its command line and its environment.

The functions here are pure: they compute from two values, and never read the process
environment themselves.

opts
    renovate-preview's own options, an ``options.Options``. ``opts.remote`` is
    "owner/repo" for a remote repository; otherwise it is None, and ``opts.directory`` is
    the local repository.
theirs
    The arguments after ``--``, passed on to Renovate as they are.

``container_args()`` is what goes after the image in ``podman run ... IMAGE``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # options.py imports this module.
    from .options import Options

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


def container_args(opts: Options, theirs: list[str]) -> list[str]:
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
    if opts.show_config:
        ours["--print-config"] = "true"
    if theirs:
        # Renovate's default, but a global config could turn it off.
        ours["--force-cli"] = "true"
    # Other platforms: -- --platform=gitlab --endpoint=...
    ours["--platform"] = "github" if opts.remote else "local"
    if opts.remote and opts.host != GITHUB:
        ours["--endpoint"] = f"https://{opts.host}/api/v3/"  # GitHub Enterprise Server

    given = {flag(arg) for arg in theirs}
    args = [f"{name}={value}" for name, value in ours.items() if name not in given]
    args += theirs
    return [*args, opts.remote] if opts.remote else args


def flag(arg: str) -> str:
    """Return the flag of an argument: '--dry-run' for '--dry-run=full'."""
    return arg.split("=", 1)[0]


def inside(name: str, path: Path) -> str:
    """Return where the config file `path` is mounted in the container, as `name`."""
    return f"{CONFIG_DIR}/{name}{path.suffix}"


def environment(opts: Options) -> dict[str, str]:
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


def find_config_file(directory: Path) -> Path | None:
    """Return the repository config Renovate would read in `directory`, if any."""
    for name in CONFIG_FILE_NAMES:
        if (directory / name).is_file():
            return directory / name
    return None


def credentials_warning(opts: Options) -> str | None:
    """Return a warning if no variable authenticates version lookups."""
    if any(name in opts.forwarded for name in opts.default_env):
        return None
    return (
        f"none of {', '.join(opts.default_env)} is set: GitHub rate-limits anonymous version "
        "lookups, and a throttled lookup looks just like 'no update available'."
    )
