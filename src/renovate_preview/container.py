"""The container: podman or docker, its arguments, and running it.

A command is ``[engine, "run", *runtime_args(), image, *renovate.container_args()]``.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess

from . import renovate
from .options import VOLUME, VOLUME_MOUNT, Options
from .report import PHASES, level, parse_record

log = logging.getLogger(__name__)

ENGINES = ("podman", "docker")
# The exit statuses podman and docker keep for themselves: any other is the container's.
# 125: the engine failed (a missing image, say); 126: the command can't be invoked; 127:
# it isn't found. https://docs.podman.io/en/latest/markdown/podman-run.1.html#exit-status
ENGINE_FAILURES = (125, 126, 127)
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


def mounts(opts: Options) -> list[tuple[str, str]]:
    """Return the (host, inside) paths to mount."""
    pairs = [] if opts.remote else [(str(opts.directory), REPO_MOUNT)]
    for name, path in (("global", opts.global_config), ("config", opts.config)):
        if path:
            pairs.append((str(path.resolve()), renovate.inside(name, path)))
    return pairs


def runtime_args(opts: Options) -> list[str]:
    """Return what goes between ``podman run`` and the image."""
    env = renovate.environment(opts)
    # Never pull: a missing image is an error, and pulling is the user's decision.
    args = ["--rm", "--pull=never"]
    if opts.shell:
        args.append("-it")
    if opts.name:
        args += ["--name", opts.name]
    for name in opts.forwarded:
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
    """Return podman, or else docker, if installed."""
    return next((name for name in ENGINES if shutil.which(name)), None)


def printable(argv: list[str], head: int = 3) -> str:
    """Return a command to paste, one option per line."""
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
    """Log Renovate's version and phases as it runs; every record, when verbose."""

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
    """Run the container, calling `on_line` on each line of its output.

    Returns:
        The exit status, and the output: Renovate's log. Errors go to the terminal.
    """
    lines = []
    with subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, bufsize=1) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            on_line(line)
    return proc.returncode, lines
