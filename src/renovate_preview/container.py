"""The container's side of a run: podman or docker, and what the container sees.

A command is ``[engine, "run", *runtime_args, image, *container_args]``:

runtime_args
    What goes between ``podman run`` and the image: mounts, environment, working
    directory. Built here, as a list, since those flags repeat.
container_args
    What goes after the image: Renovate's arguments (``renovate.container_args()``), or
    another program of the image and its arguments.

As in ``renovate.py``, `opts` is renovate-preview's options and `environ` its environment.
Only ``detect_engine()`` and ``run()`` look outside.
"""

from __future__ import annotations

import argparse
import logging
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from . import renovate
from .report import PHASES, level, parse_record

log = logging.getLogger(__name__)

IMAGE = "ghcr.io/renovatebot/renovate"
TAG = "latest"
ENGINES = ("podman", "docker")
# The exit statuses podman and docker keep for themselves: any other is the container's.
# 125: the engine failed (a missing image, say); 126: the command can't be invoked; 127:
# it isn't found. https://docs.podman.io/en/latest/markdown/podman-run.1.html#exit-status
ENGINE_FAILURES = (125, 126, 127)
# Renovate's working directory (baseDir): its caches, and the clones of remote repositories.
# Kept in a named volume, so successive runs are fast.
VOLUME = "renovate-preview"
VOLUME_MOUNT = "/tmp/renovate"
# The image's WORKDIR: `--platform local` examines the current directory.
REPO_MOUNT = "/usr/src/app"
# The bind-mounted repository is owned by a uid unknown inside the container, so git
# refuses it as "dubious ownership", and Renovate then falls back to globbing every
# file, .gitignore'd ones included.
GIT_SAFE_DIRECTORY = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "safe.directory",
    "GIT_CONFIG_VALUE_0": "*",
}


def mounts(opts: argparse.Namespace) -> list[tuple[str, str]]:
    """Return the (host, inside) paths to mount: the local repository, the config files."""
    pairs = [] if opts.remote else [(opts.directory, REPO_MOUNT)]
    for name, path in (("global", opts.global_config), ("config", opts.config)):
        if path:
            pairs.append((str(Path(path).resolve()), renovate.inside(name, path)))
    return pairs


def runtime_args(opts: argparse.Namespace, environ: Mapping[str, str]) -> list[str]:
    """Return the runtime arguments of a preview, or of its --shell."""
    env = renovate.environment(opts)
    # Never pull: a missing image is an error, and pulling is the user's decision.
    args = ["--rm", "--pull=never"]
    if opts.shell:
        args.append("-it")
    if opts.name:
        args += ["--name", opts.name]
    for name in renovate.forwarded(opts, environ):
        if name not in env:
            args += ["-e", name]
    for key, value in {**env, **GIT_SAFE_DIRECTORY}.items():
        args += ["-e", f"{key}={value}"]
    for host, inside in mounts(opts):
        # :z relabels for SELinux with a *shared* label, so that several containers can
        # mount the same path at once (and it is harmless without SELinux).
        args += ["-v", f"{host}:{inside}:ro,z"]
    args += ["-v", f"{VOLUME}:{VOLUME_MOUNT}"]
    if not opts.remote:
        args += ["-w", REPO_MOUNT]
    return args


def detect_engine() -> str | None:
    """Return podman, or else docker: whichever is installed."""
    return next((name for name in ENGINES if shutil.which(name)), None)


def printable(argv: list[str], head: int = 3) -> str:
    """Return a command to paste, one option per line, `podman run --rm` kept together."""
    lines = [shlex.join(argv[:head])]
    takes_value = False
    for word in argv[head:]:
        quoted = shlex.quote(word)
        if word.startswith("-"):
            lines.append(quoted)
            takes_value = "=" not in word
        elif takes_value:
            lines[-1] += f" {quoted}"
            takes_value = False
        else:
            lines.append(quoted)
    return " \\\n    ".join(lines)


class Progress:
    """Show what Renovate is doing as it runs.

    The Renovate version, then each phase; or each of Renovate's log records with
    --verbose (the DEBUG level).
    """

    def __init__(self, source: str):
        self.source = source
        self.version_shown = False

    def __call__(self, line: str) -> None:
        record = parse_record(line)
        if record is None:
            return
        if not self.version_shown and record.get("renovateVersion"):
            self.version_shown = True
            log.info("Renovate %s (%s)", record["renovateVersion"], self.source)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("%-5s %s", level(record).upper(), record.get("msg"))
        elif record.get("msg") in PHASES:
            log.info("%s...", PHASES[record["msg"]])


def run(argv: list[str], on_line) -> tuple[int, list[str]]:
    """Run the container, calling `on_line` on each line of its output as it arrives.

    Return its exit status and its output: Renovate's log, on stdout. Its stderr, where
    the engine's errors and Renovate's command-line errors go, is the terminal's.
    """
    lines = []
    with subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, bufsize=1) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            on_line(line)
    return proc.returncode, lines
