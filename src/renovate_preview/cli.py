"""The main flow: parse the options, check them, run Renovate, show the result.

Only ``main()`` reads the process environment, and only ``preview()`` runs anything.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlencode

from . import container, renovate
from .container import ENGINE_FAILURES, Progress
from .options import Options, Source, UsageError, parse_args
from .render import html_page, table, to_json
from .report import parse_record, raw_report, summarize

log = logging.getLogger("renovate_preview")

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


class Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        prefix = "error: " if record.levelno >= logging.ERROR else ""
        return f"renovate-preview: {prefix}{record.getMessage()}"


# Which of our messages each verbosity shows.
LEVELS = {"silent": logging.ERROR, "normal": logging.INFO, "verbose": logging.DEBUG}


def setup_logging(verbosity: str) -> None:
    """Send our messages to stderr, as many as `verbosity` asks for."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(Formatter())
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(LEVELS[verbosity])


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run renovate-preview, by default with sys.argv and os.environ."""
    if argv is None:
        argv = sys.argv[1:]
    if environ is None:
        environ = os.environ
    try:
        opts, theirs = parse_args(argv, environ)
    except UsageError as err:
        setup_logging("normal")
        for message in err.messages:
            log.error(message)
        return 2
    setup_logging(opts.verbosity)
    try:
        return preview(opts, theirs)
    except BrokenPipeError:
        # Output piped into `head` and the like, which stopped reading. Python would raise
        # again when flushing stdout at exit: send what is left to /dev/null. See
        # https://docs.python.org/3/library/signal.html#note-on-sigpipe
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1


def preview(opts: Options, theirs: list[str]) -> int:
    """Run Renovate, or replay a log, and show the result. Return the exit status."""
    if opts.pat_url:
        print(pat_url(opts.host))
        return 0

    errors, warnings = check(opts, theirs)
    for message in warnings:
        log.warning(message)
    for message in errors:
        log.error(message)
    if errors:
        return 2
    if opts.source:
        return replay(opts)

    engine = container.detect_engine()
    if engine is None:
        if not opts.just_print:
            log.error("neither podman nor docker found")
            return 2
        engine = "podman"
    image = f"{opts.image}:{opts.tag}"

    runtime_args = container.runtime_args(opts)
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
            "%s could not run Renovate (status %s): see its error above.\n"
            "  If the image is missing, pull it: %s pull %s\n"
            "  or specify another one using the --image and --tag options.",
            engine,
            status,
            engine,
            image,
        )
        return status
    records = [r for r in map(parse_record, lines) if r is not None]
    keep_last_run(lines, opts.last_run)
    document = summarize(str(opts.repository), records)
    return show(opts, document, lines, records, status)


def check(opts: Options, theirs: list[str]) -> tuple[list[str], list[str]]:
    """Validate the options as a whole: each one is valid on its own.

    Returns:
        (errors, warnings). Any error means no run.
    """
    errors, warnings = [], []
    if opts.source:
        return errors, warnings  # A replay: only the output options apply.
    if opts.directory and not opts.directory.is_dir():
        errors.append(f"no such directory: {opts.directory}")
    if opts.given("host") and not opts.remote:
        errors.append("--host goes with a remote repository: @owner/repo")
    # docker would create a missing file as a directory.
    for name in ("global_config", "config"):
        path = getattr(opts, name)
        if path and not path.is_file():
            errors.append(f"{opts.origin(name)}: no such config file: {path}")
    if not opts.remote and renovate.their_flags(theirs).dry_run == "full":
        errors.append(
            "the local platform can't do a full dry run (Renovate turns it into 'lookup'). "
            "For PR titles, preview the remote repository: renovate-preview @owner/repo"
        )
    if opts.remote and not opts.just_print and not opts.is_set("RENOVATE_TOKEN"):
        errors.append("RENOVATE_TOKEN must be set to reach a remote repository (see --pat-url)")

    if opts.config and opts.directory:
        origin = opts.origin("config")
        warnings.append(
            f"{origin} on a local repository: Renovate always uses the repository's config "
            f"if detected, with higher precedence than {origin}."
        )
    warnings += [
        f"{opts.origin('env')}: {name} is not set" for name in opts.env if not opts.is_set(name)
    ]
    runs_lookups = not (opts.extract or opts.shell or opts.just_print)
    if runs_lookups and (message := renovate.credentials_warning(opts)):
        warnings.append(message)
    return errors, warnings


def show(opts: Options, document: dict, lines: list[str], records: list[dict], status: int) -> int:
    """Write the output, then Renovate's problems. Return the exit status.

    A failed run still writes json, raw and report; a table or a page would mislead.
    """
    error = failure(document, records, status)
    if opts.show_config and document["config"] is None:
        log.warning("no config in Renovate's log: it needs a run with --show-config")
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


def keep_last_run(lines: list[str], path: Path) -> None:
    # Readable by the user only: a debug log shows configuration and URLs, and a home
    # directory isn't always private.
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as err:
        log.warning("could not keep this run for --from last: %s", err)


def replay(opts: Options) -> int:
    """Show a Renovate log (--from) as a run would. Options for a run are ignored."""
    if opts.source == "-":
        label, text = "stdin", sys.stdin.read()
    else:
        path = opts.last_run if opts.source == "last" else Path(opts.source)
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


def print_command(argv: list[str]) -> int:
    print(container.printable(argv))
    return 0


def render(opts: Options, document: dict, lines: list[str], records: list[dict]) -> str:
    if opts.output == "raw":
        return "\n".join(lines)
    if opts.output == "report":
        return json.dumps(raw_report(records), indent=2)
    if opts.output == "json":
        return to_json(document)
    names = columns_to_show(opts)
    if opts.output == "html":
        return html_page(document, names, opts.show_config)
    if opts.show_config and document["config"] is not None:
        return f"{json.dumps(document['config'], indent=2)}\n\n{table(document, names)}"
    return table(document, names)


def columns_to_show(opts: Options) -> tuple[str, ...]:
    """Return the user's columns, or the default ones fitted to the run.

    NEW is empty without lookups, and PR TITLE needs a remote repository.
    """
    if opts.sources["columns"] is not Source.DEFAULT:
        return opts.columns
    if opts.extract:
        return tuple(name for name in opts.columns if name != "new")
    if opts.remote:
        return (*opts.columns, "pr-title")
    return opts.columns


def write(text: str, path: Path | None) -> None:
    if path:
        path.write_text(text + "\n", encoding="utf-8")
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
