"""The command line: renovate-preview's options, and the main flow.

``main()`` is the only code that reads the process environment, and with ``preview()`` it
is the only code that runs anything. Everything it calls computes from three values:

opts
    Our options, from ``parse_args()``. It also resolves the repository argument once:
    ``opts.remote`` is "owner/repo", or None and then ``opts.directory`` is the local
    repository's absolute path.
theirs
    The arguments after ``--``, for Renovate.
environ
    Our environment: ``os.environ`` unless a test gives another.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlencode

from . import __version__, container, renovate
from .container import ENGINE_FAILURES, IMAGE, TAG, VOLUME, VOLUME_MOUNT, Progress
from .render import COLUMNS, default_columns, html_page, table, to_json
from .renovate import GITHUB
from .report import parse_record, raw_report, summarize

log = logging.getLogger("renovate_preview")

EPILOG = f"""\
examples:
  renovate-preview                         # the repository in the current directory
  renovate-preview @owner/repo             # a remote GitHub repository (needs RENOVATE_TOKEN)
  renovate-preview -o html -w report.html  # read the result in a browser
  renovate-preview --from last -o json     # the last run again, without running Renovate
  renovate-preview --just-print            # show the container command, run nothing
  renovate-preview --shell                 # a shell in the container, set up as for Renovate
  renovate-preview --pat-url               # a link to a token form, with read-only permissions

  # a remote repository, with your config instead of its own
  renovate-preview @owner/repo --config renovate.json
  # pass arguments on to Renovate
  renovate-preview -- --include-paths='base/**' --enabled-managers=kustomize
  # for example, to use a platform other than GitHub
  renovate-preview @owner/repo -- --platform=gitlab --endpoint=https://gitlab.example.com/api/v4/

environment forwarded to Renovate:
  RENOVATE_TOKEN          GitHub token: needed for @owner/repo, and lifts the rate limit on
                          version lookups (RENOVATE_GITHUB_COM_TOKEN does that alone).

  GITHUB_COM_TOKEN        alias for RENOVATE_GITHUB_COM_TOKEN.

  any other NAME          with --env NAME, e.g. for registry credentials, see
                          https://docs.renovatebot.com/self-hosted-configuration/#detecthostrulesfromenv

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


def column_names(value: str) -> list[str]:
    """Parse --columns: a comma-separated list of names, 'all', or 'help' (as ['help'])."""
    if value == "all":
        return list(COLUMNS)
    if value == "help":
        return ["help"]
    names = [name.strip() for name in value.split(",") if name.strip()]
    unknown = [name for name in names if name not in COLUMNS]
    if unknown or not names:
        raise argparse.ArgumentTypeError(
            f"unknown column(s) {','.join(unknown)}: see --columns=help"
        )
    return names


def variable_name(value: str) -> str:
    """Parse --env: a variable's name, never its value, which would show in the command."""
    if "=" in value:
        raise argparse.ArgumentTypeError("a variable's name only: set its value in your shell")
    return value


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
    what.add_argument(
        "--global-config", metavar="FILE", help="a self-hosted Renovate global config"
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
        type=column_names,
        help="comma-separated columns for table and html "
        f"(default: {','.join(default_columns(False))}); 'all' for every one, 'help' to list them",
    )
    out.add_argument("-w", "--write", metavar="FILE", help="write the output to FILE")
    loudness = out.add_mutually_exclusive_group()
    loudness.add_argument(
        "-v", "--verbose", action="store_true", help="show what Renovate is doing as it runs"
    )
    loudness.add_argument("--silent", action="store_true", help="no progress, hints or warnings")
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
        "--env",
        metavar="NAME",
        type=variable_name,
        action="append",
        default=[],
        help="pass this variable on to Renovate too, by name (repeatable)",
    )
    ctr.add_argument(
        "--shell", action="store_true", help="a shell in the container, instead of Renovate"
    )
    return p


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Return our options, and the arguments after `--`, which are Renovate's."""
    theirs: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, theirs = argv[:split], argv[split + 1 :]
    opts = parser().parse_args(argv)
    if opts.repository and opts.repository.startswith("@"):
        opts.remote, opts.directory = opts.repository[1:], None
    else:
        opts.remote, opts.directory = None, str(Path(opts.repository or ".").resolve())
    return opts, theirs


class Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        prefix = "error: " if record.levelno >= logging.ERROR else ""
        return f"renovate-preview: {prefix}{record.getMessage()}"


def setup_logging(opts: argparse.Namespace) -> None:
    """Send our messages to stderr.

    Errors always; progress and warnings unless --silent; Renovate's own log records with
    --verbose.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(Formatter())
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG if opts.verbose else logging.ERROR if opts.silent else logging.INFO)


def main(argv: list[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    try:
        opts, theirs = parse_args(sys.argv[1:] if argv is None else argv)
        setup_logging(opts)
        return preview(opts, theirs, environ)
    except BrokenPipeError:
        # Output piped into `head` and the like, which stopped reading. Python would raise
        # again when flushing stdout at exit: send what is left to /dev/null. See
        # https://docs.python.org/3/library/signal.html#note-on-sigpipe
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1


def preview(opts: argparse.Namespace, theirs: list[str], environ: Mapping[str, str]) -> int:
    if opts.pat_url:
        print(pat_url(opts.host))
        return 0
    if opts.columns == ["help"]:
        print(columns_help())
        return 0
    if opts.source:
        return replay(opts, theirs, environ)

    errors, warnings = check(opts, theirs, environ)
    for message in warnings:
        log.warning(message)
    for message in errors:
        log.error(message)
    if errors:
        return 2

    engine = container.detect_engine()
    if engine is None:
        if not opts.just_print:
            log.error("neither podman nor docker found")
            return 2
        engine = "podman"
    image = f"{opts.image}:{opts.tag}"

    runtime_args = container.runtime_args(opts, environ)
    if opts.shell:
        argv = [engine, "run", *runtime_args, image, "bash"]
        if opts.just_print:
            return print_command(argv)
        return subprocess.run(argv, check=False).returncode

    argv = [engine, "run", *runtime_args, image, *renovate.container_args(opts, theirs)]
    if opts.just_print:
        return print_command(argv)
    log.debug("%s", container.printable(argv))

    status, lines = container.run(argv, Progress(f"{engine}, {image}"))
    if status in ENGINE_FAILURES:
        # The engine's own message is above, on stderr.
        log.error(
            "%s could not run Renovate (status %s). If the image is missing: %s pull %s",
            engine,
            status,
            engine,
            image,
        )
        return status
    records = [r for r in map(parse_record, lines) if r is not None]
    keep_last_run(lines, environ)
    document = summarize(opts.repository or opts.directory, records)
    return show(opts, document, lines, records, status)


def check(
    opts: argparse.Namespace, theirs: list[str], environ: Mapping[str, str]
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) about a run, before anything is run.

    Only what would otherwise fail silently, confusingly or late. argparse rejects the
    rest. Any error means no run.
    """
    errors, warnings = [], []
    if not opts.remote and not Path(opts.directory).is_dir():
        errors.append(f"no such directory: {opts.directory}")
    if opts.host != GITHUB and not opts.remote:
        errors.append("--host goes with a remote repository: @owner/repo")
    # docker would create a missing file as a directory.
    for config in (opts.global_config, opts.config):
        if config and not Path(config).is_file():
            errors.append(f"no such config file: {config}")
    if not opts.remote and renovate.their_flags(theirs).dry_run == "full":
        errors.append(
            "the local platform can't do a full dry run (Renovate turns it into 'lookup'). "
            "For PR titles, preview the remote repository: renovate-preview @owner/repo"
        )
    if opts.remote and not opts.just_print and not environ.get("RENOVATE_TOKEN"):
        errors.append("RENOVATE_TOKEN must be set to reach a remote repository (see --pat-url)")

    if opts.config and not opts.remote and (own := renovate.find_config_file(opts.directory)):
        warnings.append(
            f"{own.relative_to(opts.directory)} is read too, and wins over --config: "
            "Renovate's local platform always reads the repository's config"
        )
    warnings += [f"--env {name}: not set" for name in opts.env if name not in environ]
    runs_lookups = not (opts.extract or opts.shell or opts.just_print)
    if runs_lookups and (message := renovate.credentials_warning(environ)):
        warnings.append(message)
    return errors, warnings


def show(opts, document: dict, lines: list[str], records: list[dict], status: int) -> int:
    """Write the output, and what Renovate had to say: the same for a run and a replay.

    When the run failed, json, raw and report are still written, for scripts and a closer
    look; a table or a page would only mislead ("No dependencies found.").
    """
    error = failure(document, records, status)
    if not error or opts.output in ("json", "raw", "report"):
        write(render(opts, document, lines, records), opts.write)
    if error:
        log.error("%s", error)
        return status or 1
    if document["problems"]:
        log.warning("Renovate reported:\n  %s", "\n  ".join(document["problems"][:10]))
    if status != 0:
        log.error("renovate exited with status %s; see its whole log with -o raw", status)
    return status


def failure(document: dict, records: list[dict], status: int) -> str | None:
    """Return why the run gave no usable result, if it didn't."""
    if raw_report(records) is None:
        # A fatal error, or one on its command line: that one is on stderr, as plain text.
        exited = f" (status {status})" if status else ""
        problems = "".join(f"\n  {problem}" for problem in document["problems"][:10])
        return (
            f"Renovate stopped before its report{exited}; see its whole log with -o raw{problems}"
        )
    return None


def last_run(environ: Mapping[str, str]) -> Path:
    """Return where each run keeps its log, for --from last: the last one only.

    Logs are "state" in the XDG Base Directory spec, whose variables are ignored unless
    absolute: https://specifications.freedesktop.org/basedir-spec/latest/
    """
    state = environ.get("XDG_STATE_HOME", "")
    home = Path(environ.get("HOME") or Path.home())
    base = Path(state) if os.path.isabs(state) else home / ".local" / "state"
    return base / "renovate-preview" / "last.ndjson"


def keep_last_run(lines: list[str], environ: Mapping[str, str]) -> None:
    # Readable by the user only: a debug log shows configuration and URLs, and a home
    # directory isn't always private.
    path = last_run(environ)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as err:
        log.warning("could not keep this run for --from last: %s", err)


def replay(opts: argparse.Namespace, theirs: list[str], environ: Mapping[str, str]) -> int:
    """Render a log instead of a run (--from), as a run would be."""
    if error := check_replay(opts, theirs):
        log.error(error)
        return 2
    if opts.source == "-":
        label, text = "stdin", sys.stdin.read()
    else:
        path = last_run(environ) if opts.source == "last" else Path(opts.source)
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


def check_replay(opts: argparse.Namespace, theirs: list[str]) -> str | None:
    """Return an error if an option only applies to a run, rather than silently ignore it."""
    given = {
        f"the repository ({opts.repository})": opts.repository,
        "--extract": opts.extract,
        "--config": opts.config,
        "--global-config": opts.global_config,
        "--host": opts.host != GITHUB,
        "--image": opts.image != IMAGE,
        "--tag": opts.tag != TAG,
        "--name": opts.name,
        "--env": opts.env,
        "--shell": opts.shell,
        "--just-print": opts.just_print,
        "arguments after --": theirs,
    }
    run_only = [name for name, value in given.items() if value]
    if not run_only:
        return None
    message = (
        "--from shows a saved log instead of running Renovate, so these options don't "
        f"apply: {', '.join(run_only)}"
    )
    if opts.repository and opts.source == "last":
        message += ". --from last is the latest run, whichever repository it was"
    return message


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
    names = opts.columns or default_columns(opts.extract)
    return html_page(document, names) if opts.output == "html" else table(document, names)


def columns_help() -> str:
    """Return the help of --columns=help: the names, which column each shows, the defaults."""

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
