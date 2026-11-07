"""The command line: renovate-preview's flags, and the main flow."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

from . import __version__, container, renovate
from .container import IMAGE, TAG, VOLUME, VOLUME_MOUNT, Progress
from .render import COLUMNS, default_columns, html_page, table, to_json
from .renovate import GITHUB, Target
from .report import parse_record, raw_report, summarize

log = logging.getLogger("renovate_preview")

EPILOG = f"""\
examples:
  renovate-preview                        the repository in the current directory
  renovate-preview @owner/repo            a remote GitHub repository (needs RENOVATE_TOKEN)
  renovate-preview @owner/repo --config renovate.json   ... with your config instead of its own
  renovate-preview --only base --managers kustomize
  renovate-preview -o html -w report.html read the result in a browser
  renovate-preview --from last -o json    the last run again, without running Renovate
  renovate-preview --just-print           show the container command, run nothing
  renovate-preview --shell                a shell in the container, set up as for Renovate
  renovate-preview -- --ignore-paths='overlays/**'   pass arguments on to Renovate

environment:
  RENOVATE_TOKEN          GitHub token: needed for @owner/repo, and lifts the rate limit on
                          version lookups (RENOVATE_GITHUB_COM_TOKEN does that alone).
                          renovate-preview --pat-url links to a pre-filled token form.
  RENOVATE_CONFIG_FILE    default for --global-config.
  RENOVATE_PLATFORM       with RENOVATE_ENDPOINT, for @owner/repo on GitLab, Forgejo...
  RENOVATE_*              forwarded to Renovate, like GITHUB_COM_TOKEN.

Renovate runs in podman, or else docker. Its working directory, {VOLUME_MOUNT}, is the
{VOLUME} volume: its caches survive between runs.
"""

# What a dry run reads. Renovate documents the permissions of a real run, which writes:
# https://docs.renovatebot.com/modules/platform/github/#permissions
# The URL parameters: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
PAT_PERMISSIONS = {
    "contents": "read",
    "issues": "read",
    "pull_requests": "read",
    "statuses": "read",
    "vulnerability_alerts": "read",
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="renovate-preview",
        description="Dry-run Renovate against a repository and summarize what it would do.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "repository",
        nargs="?",
        help="a local directory (default: the current one), or @owner/repo on GitHub",
    )

    what = p.add_argument_group("what to run")
    what.add_argument(
        "--extract",
        action="store_true",
        help="only list the dependencies found, do not lookup available versions",
    )
    what.add_argument("--validate", action="store_true", help="only validate the config")
    what.add_argument("--only", metavar="GLOBS", help="comma-separated paths to limit the run to")
    what.add_argument(
        "--except",
        dest="excluded",
        metavar="GLOBS",
        help="comma-separated paths to skip; replaces Renovate's ignorePaths",
    )
    what.add_argument("--managers", metavar="LIST", help="comma-separated managers to enable")
    what.add_argument(
        "--global-config",
        metavar="FILE",
        default=os.environ.get("RENOVATE_CONFIG_FILE"),
        help="a self-hosted Renovate global config (default: $RENOVATE_CONFIG_FILE)",
    )
    what.add_argument(
        "--from",
        dest="source",
        metavar="LOG",
        help="replay a Renovate JSON log instead of running: a file, - for stdin, "
        "or 'last' for the previous run",
    )
    what.add_argument(
        "--config",
        metavar="FILE",
        help="a repository config, instead of the repository's own (e.g. a PR's renovate.json)",
    )
    what.add_argument(
        "--host",
        default=GITHUB,
        help=f"with @owner/repo: a GitHub Enterprise Server (default: {GITHUB})",
    )

    out = p.add_argument_group("output")
    out.add_argument(
        "-o",
        "--output",
        choices=("table", "json", "html", "raw", "report"),
        default="table",
        help="table (default), json, html; raw, report: Renovate's own logs",
    )
    out.add_argument(
        "--columns",
        metavar="LIST",
        help="comma-separated columns for table and html "
        f"(default: {','.join(default_columns(False))}); 'all' for every one, 'help' to list them",
    )
    out.add_argument("-w", "--write", metavar="FILE", help="write the output to FILE")
    out.add_argument(
        "-v", "--verbose", action="store_true", help="show what Renovate is doing as it runs"
    )
    out.add_argument("--silent", action="store_true", help="no progress, hints or warnings")
    out.add_argument(
        "--just-print", action="store_true", help="print the container command and exit"
    )
    out.add_argument(
        "--pat-url",
        action="store_true",
        help="print a link to create a read-only GitHub token for --host, and exit",
    )

    ctr = p.add_argument_group("container")
    ctr.add_argument("--image", default=IMAGE, help=f"container image (default: {IMAGE})")
    ctr.add_argument("--tag", default=TAG, help=f"image tag (default: {TAG})")
    ctr.add_argument("--name", help="container name")
    ctr.add_argument(
        "--shell", action="store_true", help="a shell in the container, instead of Renovate"
    )
    return p


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Everything after `--` is passed on to Renovate untouched."""
    passthrough: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, passthrough = argv[:split], argv[split + 1 :]
    opts = parser().parse_args(argv)
    opts.passthrough = passthrough
    return opts


class Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        prefix = "error: " if record.levelno >= logging.ERROR else ""
        return f"renovate-preview: {prefix}{record.getMessage()}"


def setup_logging(opts: argparse.Namespace) -> None:
    """Our messages, on stderr: errors always; progress and warnings unless --silent;
    Renovate's own log records with --verbose."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(Formatter())
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG if opts.verbose else logging.ERROR if opts.silent else logging.INFO)


def main(argv: list[str] | None = None) -> int:
    try:
        opts = parse_args(sys.argv[1:] if argv is None else argv)
        setup_logging(opts)
        return preview(opts)
    except BrokenPipeError:
        # Output piped into `head` and the like, which stopped reading. Python would raise
        # again when flushing stdout at exit: send what is left to /dev/null. See
        # https://docs.python.org/3/library/signal.html#note-on-sigpipe
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1


def preview(opts: argparse.Namespace) -> int:
    if opts.pat_url:
        print(pat_url(opts.host))
        return 0
    if opts.columns == "help":
        print(columns_help())
        return 0
    if opts.source:
        return replay(opts)
    target = Target.parse(opts.repository)
    if error := check(opts, target):
        log.error(error)
        return 2

    engine = container.detect_engine()
    if engine is None:
        if not opts.just_print:
            log.error("neither podman nor docker found")
            return 2
        engine = "podman"
    image = f"{opts.image}:{opts.tag}"

    if opts.validate:
        return validate(opts, target, engine, image)

    fs = container.filesystem(target, opts.global_config, opts.config)
    env = renovate.environment(fs.global_config, fs.config, target.remote)
    if opts.config and not target.remote and (own := renovate.find_config_file(target.value)):
        log.warning(
            "%s is read too, and wins over --config: Renovate's local platform always reads "
            "the repository's config",
            own.relative_to(target.value),
        )

    if opts.shell:
        argv = container.command(engine, image, fs, env, [], "bash", opts.name, interactive=True)
        if opts.just_print:
            return print_command(argv)
        return subprocess.run(argv, check=False).returncode

    args = renovate.arguments(opts, target)
    argv = container.command(engine, image, fs, env, args, name=opts.name)
    if opts.just_print:
        return print_command(argv)

    if message := renovate.credentials_warning(opts.extract):
        log.warning(message)
    log.debug("%s", container.printable(argv))

    status, lines = container.run(argv, Progress(f"{engine}, {image}"))
    records = [r for r in map(parse_record, lines) if r is not None]
    if status != 0 and not records:
        # Not even Renovate's first log record: the engine itself failed.
        log.error(
            "the container did not start: %s\nIf the image is missing: %s pull %s",
            last_line(lines),
            engine,
            image,
        )
        return status

    keep_last_run(lines)
    document = summarize(opts.repository or target.value, records)
    return show(opts, document, lines, records, status)


def show(opts, document: dict, lines: list[str], records: list[dict], status: int) -> int:
    """The output, and what Renovate had to say: the same for a run and a replay."""
    write(render(opts, document, lines, records), opts.write)
    if document["problems"]:
        log.warning("Renovate reported:\n  %s", "\n  ".join(document["problems"][:10]))
    if status != 0:
        log.error("renovate exited with status %s; see its whole log with -o raw", status)
    return status


def last_run() -> Path:
    """Where each run keeps its log, for --from last: the last one only. Logs are "state"
    in the XDG Base Directory spec, whose variables are ignored unless absolute:
    https://specifications.freedesktop.org/basedir-spec/latest/"""
    state = os.environ.get("XDG_STATE_HOME", "")
    base = Path(state) if os.path.isabs(state) else Path.home() / ".local" / "state"
    return base / "renovate-preview" / "last.ndjson"


def keep_last_run(lines: list[str]) -> None:
    # Readable by the user only: a debug log shows configuration and URLs, and a home
    # directory isn't always private.
    path = last_run()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as err:
        log.warning("could not keep this run for --from last: %s", err)


def replay(opts) -> int:
    """--from: a log instead of a run, rendered as a run would be."""
    if error := check_replay(opts):
        log.error(error)
        return 2
    if opts.source == "-":
        label, text = "stdin", sys.stdin.read()
    else:
        path = last_run() if opts.source == "last" else Path(opts.source)
        label = "last run" if opts.source == "last" else opts.source
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as err:
            hint = " (no run yet?)" if opts.source == "last" else ""
            log.error("cannot read %s%s: %s", path, hint, err.strerror)
            return 2
    lines = text.splitlines()
    records = [r for r in map(parse_record, lines) if r is not None]
    if not records:
        log.error("no Renovate JSON log records in %s", label)
        return 2
    document = summarize(label, records)
    log.info("Renovate %s, replayed from %s", document["renovate_version"] or "?", label)
    return show(opts, document, lines, records, status=0)


def check_replay(opts) -> str | None:
    """What only applies to a run is an error with --from, rather than silently ignored."""
    given = {
        "a repository": opts.repository,
        "--extract": opts.extract,
        "--validate": opts.validate,
        "--only": opts.only,
        "--except": opts.excluded,
        "--managers": opts.managers,
        "--config": opts.config,
        "--global-config": opts.global_config
        not in (None, os.environ.get("RENOVATE_CONFIG_FILE")),
        "--host": opts.host != GITHUB,
        "--image": opts.image != IMAGE,
        "--tag": opts.tag != TAG,
        "--name": opts.name,
        "--shell": opts.shell,
        "--just-print": opts.just_print,
        "arguments after --": opts.passthrough,
    }
    if run_only := [name for name, value in given.items() if value]:
        return f"--from replays a log: {', '.join(run_only)} only apply to a run"
    return None


def last_line(lines: list[str]) -> str:
    return next((line for line in reversed(lines) if line.strip()), "no output")


def check(opts, target: Target) -> str | None:
    """Reject what cannot work, before anything is run."""
    if opts.silent and opts.verbose:
        return "--silent and --verbose contradict each other"
    if not target.remote and not Path(target.value).is_dir():
        return f"no such directory: {target.value}"
    if opts.validate and target.remote and not opts.config:
        return "--validate checks the config of a local repository, or --config"
    if opts.host != GITHUB and not target.remote:
        return "--host goes with a remote repository: @owner/repo"
    if opts.columns is not None:
        unknown = [name for name in columns(opts) if name not in COLUMNS]
        if unknown or not columns(opts):
            return f"unknown column(s) {','.join(unknown)}: see --columns=help"
    for config in (opts.global_config, opts.config):
        if config and not Path(config).is_file():
            return f"no such config file: {config}"
    if not target.remote and renovate.passthrough_value(opts.passthrough, "--dry-run") in (
        "",
        "full",
    ):
        return (
            "the local platform can't do a full dry run (Renovate turns it into 'lookup'). "
            "For PR titles, preview the remote repository: renovate-preview @owner/repo"
        )
    if target.remote and not opts.just_print and not os.environ.get("RENOVATE_TOKEN"):
        return "RENOVATE_TOKEN must be set to reach a remote repository (see --pat-url)"
    return None


def validate(opts, target: Target, engine: str, image: str) -> int:
    """renovate-config-validator, on --config or the repository's config. The file is
    mounted in the working directory, so the validator reports it by the name you know."""
    if opts.config:
        config, path = Path(opts.config).resolve(), Path(opts.config).name
    else:
        config = renovate.find_config_file(target.value)
        if config is None:
            log.error("no Renovate config in %s", target.value)
            return 2
        path = str(config.relative_to(target.value))
    fs = container.Filesystem(
        [(str(config), f"{container.REPO_MOUNT}/{path}")], container.REPO_MOUNT
    )
    env = {"NODE_OPTIONS": "--no-deprecation", "LOG_LEVEL": "info"}
    argv = container.command(
        engine, image, fs, env, ["--no-global", path], "renovate-config-validator", opts.name
    )
    if opts.just_print:
        return print_command(argv)
    return subprocess.run(argv, check=False).returncode


def print_command(argv: list[str]) -> int:
    print(container.printable(argv))
    return 0


def render(opts, document: dict, lines: list[str], records: list[dict]) -> str:
    if opts.output == "raw":
        return "\n".join(lines)
    if opts.output == "report":
        return json.dumps(raw_report(records), indent=2)
    if opts.output == "json":
        return to_json(document)
    names = columns(opts)
    return html_page(document, names) if opts.output == "html" else table(document, names)


def columns(opts) -> list[str]:
    if opts.columns is None:
        return default_columns(opts.extract)
    if opts.columns == "all":
        return list(COLUMNS)
    return [name.strip() for name in opts.columns.split(",") if name.strip()]


def columns_help() -> str:
    """--columns=help: the names, which column each shows, and the defaults."""

    def note(name: str) -> str:
        if name not in default_columns(extract=False):
            return ""
        return (
            "(default)" if name in default_columns(extract=True) else "(default, unless --extract)"
        )

    lines = [f"{name:<12}{title:<12}{note(name)}".rstrip() for name, (title, _) in COLUMNS.items()]
    return "\n".join([*lines, "all         every column above"])


def write(text: str, path: str | None) -> None:
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def pat_url(host: str) -> str:
    query = {
        "name": "renovate-preview",
        "description": "Read-only, for Renovate dry runs with renovate-preview",
        **PAT_PERMISSIONS,
    }
    return f"https://{host}/settings/personal-access-tokens/new?{urlencode(query)}"


if __name__ == "__main__":
    sys.exit(main())
