"""The command line."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .render import html_page, table, to_json
from .renovate import (
    CACHE_VOLUME,
    IMAGE,
    TAG,
    Target,
    build_command,
    build_validate_command,
    credentials_warning,
    detect_runtime,
    find_config_file,
    run,
)
from .report import PHASES, level, summarize

EPILOG = f"""\
examples:
  renovate-preview                        the repository in the current directory
  renovate-preview @org/repo              a remote GitHub repository (needs RENOVATE_TOKEN)
  renovate-preview @org/repo --config renovate.json   ... previewing a config of yours
  renovate-preview --only base --managers kustomize
  renovate-preview -o html --open         read the result in a browser
  renovate-preview --just-print           show the Renovate command, run nothing
  renovate-preview -- --ignore-paths='overlays/**'   pass arguments on to Renovate

environment:
  RENOVATE_TOKEN          GitHub token: needed for @org/repo, and lifts the rate limit on
                          version lookups (RENOVATE_GITHUB_COM_TOKEN does that alone).
  RENOVATE_CONFIG_FILE    your self-hosted (global) config, if you have one.
  RENOVATE_PLATFORM       with RENOVATE_ENDPOINT, for @org/repo outside github.com.
  RENOVATE_*              forwarded to Renovate, like GITHUB_COM_TOKEN.
  RENOVATE_PREVIEW_RUNTIME  default for --runtime.

Renovate's cache is kept in the {CACHE_VOLUME} container volume between runs.
"""


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
        help="a local directory (default: the current one), or @org/repo on GitHub",
    )

    what = p.add_argument_group("what to run")
    what.add_argument(
        "--just-extract",
        action="store_true",
        help="only list the dependencies found: no version lookups, fast and offline",
    )
    what.add_argument("--validate", action="store_true", help="only validate the config")
    what.add_argument(
        "--run", action="store_true", help="a REAL run against @org/repo: creates branches and PRs"
    )
    what.add_argument("--only", metavar="GLOBS", help="comma-separated paths to limit the run to")
    what.add_argument(
        "--except", dest="excluded", metavar="GLOBS", help="comma-separated paths to skip"
    )
    what.add_argument("--managers", metavar="LIST", help="comma-separated managers to enable")
    what.add_argument(
        "--config",
        metavar="FILE",
        help="with @org/repo, use this repository config instead of the repository's own",
    )

    out = p.add_argument_group("output")
    out.add_argument("-o", "--output", choices=("table", "json", "html"), default="table")
    out.add_argument("--open", action="store_true", help="with -o html, open it in a browser")
    out.add_argument(
        "--raw",
        choices=("ndjson", "report"),
        help="Renovate's own output instead: its whole log, or just its report object",
    )
    out.add_argument(
        "-v", "--verbose", action="store_true", help="show what Renovate is doing as it runs"
    )
    out.add_argument("--silent", action="store_true", help="no progress, hints or warnings")
    out.add_argument(
        "--just-print", action="store_true", help="print the Renovate command and exit"
    )

    rt = p.add_argument_group("runtime")
    rt.add_argument(
        "--runtime",
        choices=("auto", "podman", "docker", "local"),
        default=os.environ.get("RENOVATE_PREVIEW_RUNTIME", "auto"),
        help="'auto' (default) picks podman, then docker; 'local' uses renovate from $PATH",
    )
    rt.add_argument("--image", default=IMAGE, help=f"container image (default: {IMAGE})")
    rt.add_argument("--tag", default=TAG, help=f"image tag (default: {TAG})")
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


def main(argv: list[str] | None = None) -> int:
    try:
        return preview(parse_args(sys.argv[1:] if argv is None else argv))
    except BrokenPipeError:
        # Output piped into `head` and the like: stop quietly.
        sys.stdout = None
        return 0


def preview(opts: argparse.Namespace) -> int:
    target = Target.parse(opts.repository)

    def warn(message: str) -> None:
        if not opts.silent:
            print(f"renovate-preview: {message}", file=sys.stderr, flush=True)

    if error := check(opts, target):
        print(f"renovate-preview: error: {error}", file=sys.stderr)
        return 2

    runtime = detect_runtime(opts.runtime)
    if runtime is None:
        if not opts.just_print:
            print(f"renovate-preview: error: {runtime_missing(opts.runtime)}", file=sys.stderr)
            return 2
        runtime = "podman" if opts.runtime == "auto" else opts.runtime
    opts.image = f"{opts.image}:{opts.tag}"

    if opts.validate:
        return validate(opts, target, runtime)

    if target.remote and not opts.config and find_config_file("."):
        warn(
            f"using the config of {target.value}; add --config {find_config_file('.')} "
            "to preview yours instead"
        )

    command = build_command(opts, target, runtime)
    if opts.just_print:
        print(command.printable())
        return 0

    if message := credentials_warning(opts.just_extract):
        warn(message)
    if opts.run:
        warn(f"this is a REAL run against {target.value}: it can create branches and PRs")
    if opts.verbose:
        print(command.printable(), file=sys.stderr)

    records: list[dict] = []

    def on_line(line: str, record: dict | None) -> None:
        if opts.raw == "ndjson":
            print(line, flush=True)
        if record is None:
            return
        records.append(record)
        if opts.verbose:
            print(f"{level(record).upper():<5} {record.get('msg')}", file=sys.stderr)
        elif record.get("msg") in PHASES:
            warn(f"{PHASES[record['msg']]}...")

    status = run(command, on_line)
    document = summarize(opts.repository or target.value, records)

    if opts.raw == "report":
        report = next((r["report"] for r in records if r.get("msg") == "Printing report"), None)
        print(json.dumps(report, indent=2))
    elif not opts.raw:
        output(document, opts)

    if document["problems"] and not opts.verbose:
        warn("Renovate reported:\n  " + "\n  ".join(document["problems"][:10]))
    if status != 0:
        print(
            f"renovate-preview: renovate exited with status {status}; "
            "see its whole log with --raw ndjson",
            file=sys.stderr,
        )
    return status


def check(opts, target: Target) -> str | None:
    """Reject what cannot work, before anything is run."""
    if opts.silent and opts.verbose:
        return "--silent and --verbose contradict each other"
    if opts.raw and opts.output != "table":
        return "--raw and -o are two different outputs; pick one"
    if opts.open and opts.output != "html":
        return "--open goes with -o html"
    if opts.run and opts.just_extract:
        return "--run and --just-extract contradict each other"
    if not target.remote and not Path(target.value).is_dir():
        return f"no such directory: {target.value}"
    if opts.run and not target.remote:
        return "--run needs a remote repository (@org/repo): a local one has nowhere to push"
    if opts.validate and target.remote and not opts.config:
        return "--validate needs a local repository, or --config"
    for name in (opts.config, os.environ.get("RENOVATE_CONFIG_FILE")):
        if name and not Path(name).is_file():
            return f"no such config file: {name}"
    if opts.config and not target.remote and not opts.validate:
        return (
            "--config goes with @org/repo. For a local repository, edit its renovate.json: "
            "Renovate's local platform always reads it."
        )
    if target.remote and not opts.just_print and not os.environ.get("RENOVATE_TOKEN"):
        return "RENOVATE_TOKEN must be set to reach a remote repository"
    return None


def runtime_missing(choice: str) -> str:
    if choice == "local":
        return "no 'renovate' on PATH (npm install -g renovate)"
    if choice == "auto":
        return "neither podman nor docker found (or use --runtime=local with a local renovate)"
    return f"{choice} not found on PATH"


def validate(opts, target: Target, runtime: str) -> int:
    config = Path(opts.config) if opts.config else find_config_file(target.value)
    if config is None:
        print(f"renovate-preview: error: no Renovate config in {target.value}", file=sys.stderr)
        return 2
    command = build_validate_command(opts, runtime, config)
    if opts.just_print:
        print(command.printable())
        return 0
    env = {**os.environ, **command.env} if command.env else None
    return subprocess.run(command.argv, cwd=command.cwd, env=env, check=False).returncode


def output(document: dict, opts) -> None:
    if opts.output == "table":
        print(table(document))
    elif opts.output == "json":
        print(to_json(document))
    elif sys.stdout.isatty() or opts.open:
        # A page is no use in a terminal: write it to a file, and say where.
        path = cache_dir() / "report.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_page(document), encoding="utf-8")
        print(path.as_uri())
        if opts.open:
            webbrowser.open(path.as_uri())
    else:
        print(html_page(document))


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "renovate-preview"


if __name__ == "__main__":
    sys.exit(main())
