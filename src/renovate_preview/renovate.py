"""Renovate's command line and environment, from the options.

`theirs` are the arguments after ``--``, passed on to Renovate as they are.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .options import GITHUB, Options

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
    """Parse THEIR_FLAGS in `theirs`, as ``--dry-run=x`` or ``--dry-run x``."""
    known, _ = THEIR_FLAGS.parse_known_args(theirs)
    return known


def container_args(opts: Options, theirs: list[str]) -> list[str]:
    """Return Renovate's arguments: ours, then `theirs`, which win."""
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
    """Return where config file `path` is mounted in the container, as `name`."""
    return f"{CONFIG_DIR}/{name}{path.suffix}"


def environment(opts: Options) -> dict[str, str]:
    """Return the variables we set for Renovate."""
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


def credentials_warning(opts: Options) -> str | None:
    """Return a warning if no variable authenticates version lookups."""
    if any(opts.is_set(name) for name in opts.default_env):
        return None
    return (
        f"none of {', '.join(opts.default_env)} is set: GitHub rate-limits anonymous version "
        "lookups, and a throttled lookup looks just like 'no update available'."
    )
