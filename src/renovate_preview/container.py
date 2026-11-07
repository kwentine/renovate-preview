"""The container's side: its filesystem, and running it with podman or docker."""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .renovate import Target, forwarded_environment
from .report import PHASES, level, parse_record

log = logging.getLogger(__name__)

IMAGE = "ghcr.io/renovatebot/renovate"
TAG = "latest"
ENGINES = ("podman", "docker")
# Renovate's working directory (baseDir): its caches, and the clones of remote repositories.
# Kept in a named volume, so successive runs are fast.
VOLUME = "renovate-preview"
VOLUME_MOUNT = "/tmp/renovate"
# The image's WORKDIR: `--platform local` examines the current directory.
REPO_MOUNT = "/usr/src/app"
# Where config files are mounted.
CONFIG_MOUNT = "/renovate-preview"
GIT_SAFE_DIRECTORY = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "safe.directory",
    "GIT_CONFIG_VALUE_0": "*",
}


@dataclass
class Filesystem:
    """What the container sees: host paths mounted read-only, and where Renovate starts."""

    mounts: list[tuple[str, str]] = field(default_factory=list)  # (host, inside)
    workdir: str | None = None
    # Where the config files are, inside.
    global_config: str | None = None
    config: str | None = None

    def mount(self, host_path: str, name: str) -> str:
        """Mount a config file as CONFIG_MOUNT/<name><its extension>; return that path."""
        path = Path(host_path).resolve()
        inside = f"{CONFIG_MOUNT}/{name}{path.suffix}"
        self.mounts.append((str(path), inside))
        return inside


def filesystem(target: Target, global_config: str | None, config: str | None) -> Filesystem:
    fs = Filesystem()
    if not target.remote:
        fs.mounts.append((target.value, REPO_MOUNT))
        fs.workdir = REPO_MOUNT
    if global_config:
        fs.global_config = fs.mount(global_config, "global")
    if config:
        fs.config = fs.mount(config, "config")
    return fs


def detect_engine() -> str | None:
    """podman, or else docker: whichever is installed."""
    return next((name for name in ENGINES if shutil.which(name)), None)


def command(
    engine: str,
    image: str,
    fs: Filesystem,
    env: dict[str, str],
    args: list[str],
    program: str | None = None,
    name: str | None = None,
    interactive: bool = False,
) -> list[str]:
    """`podman run ...`: runs Renovate, or `program` instead (any command in the image)."""
    # Never pull: a missing image is an error, and pulling is the user's decision.
    cmd = [engine, "run", "--rm", "--pull=never"]
    if interactive:
        cmd.append("-it")
    if name:
        cmd += ["--name", name]
    for var in forwarded_environment():
        if var not in env:
            cmd += ["-e", var]
    # The bind-mounted repository is owned by a uid unknown inside the container, so git
    # refuses it as "dubious ownership", and Renovate then falls back to globbing every
    # file, .gitignore'd ones included.
    env = {**env, **GIT_SAFE_DIRECTORY}
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    for host, inside in fs.mounts:
        # Read-only. :z relabels for SELinux with a *shared* label, so that several containers
        # can mount the same path at once (and it is harmless without SELinux).
        cmd += ["-v", f"{host}:{inside}:ro,z"]
    cmd += ["-v", f"{VOLUME}:{VOLUME_MOUNT}"]
    if fs.workdir:
        cmd += ["-w", fs.workdir]
    cmd.append(image)
    # The image's entrypoint runs any command it is given, and Renovate otherwise.
    if program:
        cmd.append(program)
    return cmd + args


def printable(argv: list[str], head: int = 3) -> str:
    """A command to paste, one option per line, `podman run --rm` kept together."""
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
    """What the user sees while Renovate runs: the Renovate version, then each phase, or
    each of Renovate's log records with --verbose (the DEBUG level)."""

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
    """Run the container, calling `on_line` on each line of output as it arrives."""
    lines = []
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            on_line(line)
    return proc.returncode, lines
