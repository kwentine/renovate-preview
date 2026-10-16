"""Building the Renovate command line, and running it."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

IMAGE = "ghcr.io/renovatebot/renovate"
TAG = "latest"
CACHE_VOLUME = "renovate-preview-cache"
CACHE_MOUNT = "/tmp/renovate/cache"
# The image's WORKDIR: `--platform local` examines the current directory.
REPO_MOUNT = "/usr/src/app"
# Where config files are mounted in the container.
CONFIG_MOUNT = "/renovate-preview"
ENGINES = ("podman", "docker")

# Config file names Renovate looks for, in its own order of preference.
CONFIG_FILE_NAMES = (
    "renovate.json",
    "renovate.json5",
    ".renovaterc",
    ".renovaterc.json",
    ".github/renovate.json",
    ".gitlab/renovate.json",
)
# Renovate's default ignorePaths, which a CLI --ignore-paths replaces.
DEFAULT_IGNORE_PATHS = ("**/node_modules/**", "**/bower_components/**")
GLOB_CHARS = set("*?[]{}!")
# Any of these authenticates github.com version lookups (the last two as host rules).
LOOKUP_TOKENS = ("RENOVATE_TOKEN", "RENOVATE_GITHUB_COM_TOKEN", "GITHUB_COM_TOKEN")


@dataclass
class Target:
    """What Renovate runs against: a local directory, or a remote GitHub repository."""

    remote: bool
    value: str  # absolute directory, or org/repo

    @classmethod
    def parse(cls, repository: str | None) -> Target:
        if repository and repository.startswith("@"):
            return cls(True, repository[1:])
        return cls(False, str(Path(repository or ".").resolve()))


@dataclass
class Command:
    argv: list[str]
    env: dict[str, str]  # only for a local renovate; a container gets it through -e
    cwd: str | None
    head: int = 1  # words kept together on the first line, like `podman run --rm`

    def printable(self) -> str:
        """The command as something to paste, one option per line."""
        lines = [f"{k}={shlex.quote(v)}" for k, v in self.env.items()]
        lines.append(shlex.join(self.argv[: self.head]))
        takes_value = False
        for word in self.argv[self.head :]:
            quoted = shlex.quote(word)
            if word.startswith("-"):
                lines.append(quoted)
                takes_value = "=" not in word
            elif takes_value:
                lines[-1] += f" {quoted}"
                takes_value = False
            else:
                lines.append(quoted)
        text = " \\\n    ".join(lines)
        return f"cd {shlex.quote(self.cwd)} && \\\n  {text}" if self.cwd else text


def detect_runtime(choice: str) -> str | None:
    """'podman', 'docker' or 'local'; None if what was asked for is not installed."""
    candidates = ENGINES if choice == "auto" else (choice,)
    for name in candidates:
        if shutil.which("renovate" if name == "local" else name):
            return name
    return None


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


def ignore_paths(value: str, target: Target) -> list[str]:
    """--except adds to ignorePaths, but a CLI --ignore-paths *replaces* the configured value.

    So restate Renovate's defaults and the repository's own list, when it is plain JSON.
    """
    globs = list(DEFAULT_IGNORE_PATHS)
    config = None if target.remote else find_config_file(target.value)
    if config:
        try:
            own = json.loads(config.read_text()).get("ignorePaths", [])
            globs += [g for g in own if isinstance(g, str)]
        except (OSError, ValueError, AttributeError):
            pass
    globs += [e.strip() for e in value.split(",") if e.strip()]
    return list(dict.fromkeys(globs))


def renovate_env(runtime: str, validate: bool = False) -> dict[str, str]:
    env = {"NODE_OPTIONS": "--no-deprecation"}
    if validate:
        env["LOG_LEVEL"] = "info"
    else:
        env |= {
            "LOG_LEVEL": "debug",
            "LOG_FORMAT": "json",
            "RENOVATE_DETECT_HOST_RULES_FROM_ENV": "true",
            # Renovate's default ('required') skips a repository without a committed config.
            "RENOVATE_REQUIRE_CONFIG": "optional",
            "RENOVATE_REPOSITORY_CACHE": "enabled",
        }
    if runtime != "local":
        # The bind-mounted repository is owned by a uid unknown inside the container, so git
        # refuses it as "dubious ownership", and Renovate then falls back to globbing every
        # file, .gitignore'd ones included.
        env |= {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
        }
    return env


def forwarded_env() -> list[str]:
    """Credentials and settings from the user's environment, passed on to the container."""
    return sorted(
        k
        for k in os.environ
        if (k.startswith("RENOVATE_") and not k.startswith("RENOVATE_PREVIEW_"))
        or k in LOOKUP_TOKENS
    )


def config_files(opts, runtime: str) -> dict[str, tuple[str, str]]:
    """Config files for Renovate: {environment variable: (host path, path Renovate sees)}.

    RENOVATE_CONFIG_FILE is the user's self-hosted (global) config, if they have one.
    --config becomes RENOVATE_ADDITIONAL_CONFIG_FILE, which Renovate merges over it.
    """
    files = {}
    for name, value, mounted in (
        ("RENOVATE_CONFIG_FILE", os.environ.get("RENOVATE_CONFIG_FILE"), "global"),
        ("RENOVATE_ADDITIONAL_CONFIG_FILE", opts.config, "config"),
    ):
        if value:
            path = Path(value).resolve()
            inside = str(path) if runtime == "local" else f"{CONFIG_MOUNT}/{mounted}{path.suffix}"
            files[name] = (str(path), inside)
    return files


def wrap(
    runtime: str,
    program: str,
    args: list[str],
    env: dict[str, str],
    image: str,
    mounts: list[tuple[str, str]],
    workdir: str | None = None,
) -> list[str]:
    """Wrap a Renovate program invocation in `podman run` / `docker run`."""
    cmd = [runtime, "run", "--rm"]
    for name in forwarded_env():
        if name not in env:
            cmd += ["-e", name]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    for host, inside in mounts:
        # Read-only, and relabelled for SELinux (:z is harmless elsewhere).
        cmd += ["-v", f"{host}:{inside}:ro,z"]
    if workdir:
        cmd += ["-w", workdir]
    cmd += ["-v", f"{CACHE_VOLUME}:{CACHE_MOUNT}"]
    cmd.append(image)
    if program != "renovate":
        cmd.append(program)
    return cmd + args


def build_command(opts, target: Target, runtime: str) -> Command:
    args = ["--report-type=logging"]
    if opts.just_extract:
        args.append("--dry-run=extract")
    elif not opts.run:
        # The local platform silently downgrades 'full' to 'lookup', so ask for what we get.
        args.append("--dry-run=full" if target.remote else "--dry-run=lookup")
    if opts.managers:
        args += ["--enabled-managers", opts.managers]
    if opts.only:
        args += ["--include-paths", ",".join(include_paths(opts.only))]
    if opts.excluded:
        args += ["--ignore-paths", ",".join(ignore_paths(opts.excluded, target))]
    args += opts.passthrough
    if target.remote:
        # RENOVATE_PLATFORM (with RENOVATE_ENDPOINT) reaches GitLab, Forgejo...
        args += ["--platform", os.environ.get("RENOVATE_PLATFORM", "github"), target.value]
    else:
        args += ["--platform", "local"]

    env = renovate_env(runtime)
    files = config_files(opts, runtime)
    env |= {name: inside for name, (_, inside) in files.items()}
    if opts.config:
        # The repository's own config is not read at all: ours stands in for it.
        env["RENOVATE_REQUIRE_CONFIG"] = "ignored"
        # Renovate can be configured to delete that file once read; it is the user's.
        env["RENOVATE_DELETE_ADDITIONAL_CONFIG_FILE"] = "false"

    if runtime == "local":
        return Command(["renovate", *args], env, None if target.remote else target.value)
    mounts = list(files.values())
    if not target.remote:
        mounts.insert(0, (target.value, REPO_MOUNT))
    workdir = None if target.remote else REPO_MOUNT
    argv = wrap(runtime, "renovate", args, env, opts.image, mounts, workdir)
    return Command(argv, {}, None, head=3)


def build_validate_command(opts, runtime: str, config: Path) -> Command:
    env = renovate_env(runtime, validate=True)
    if runtime == "local":
        return Command(["renovate-config-validator", "--no-global", str(config)], env, None)
    inside = f"{CONFIG_MOUNT}/{config.name}"
    argv = wrap(
        runtime,
        "renovate-config-validator",
        ["--no-global", inside],
        env,
        opts.image,
        [(str(config.resolve()), inside)],
    )
    return Command(argv, {}, None, head=3)


def run(command: Command, on_line) -> int:
    """Run Renovate, calling `on_line(line, record)` for each line of output as it arrives.

    `record` is the parsed JSON log record, or None for a line of plain text.
    """
    env = {**os.environ, **command.env} if command.env else None
    with subprocess.Popen(
        command.argv,
        cwd=command.cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            on_line(line, parse_record(line))
    return proc.returncode


def parse_record(line: str) -> dict | None:
    """A JSON log record, or None for the plain text Renovate interleaves with them."""
    if not line.startswith("{"):
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def credentials_warning(just_extract: bool) -> str | None:
    if just_extract or any(os.environ.get(name) for name in LOOKUP_TOKENS):
        return None
    return (
        f"none of {', '.join(LOOKUP_TOKENS)} is set: GitHub rate-limits anonymous version "
        "lookups, and a throttled lookup looks just like 'no update available'."
    )
