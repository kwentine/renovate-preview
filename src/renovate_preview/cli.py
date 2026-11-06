"""The command line: renovate-preview's flags, and the main flow."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

from . import __version__, container, renovate
from .container import IMAGE, TAG, VOLUME, VOLUME_MOUNT, Filesystem, Progress
from .render import html_page, table, to_json
from .renovate import Target
from .report import parse_record, raw_report, summarize

EPILOG = f"""\
examples:
  renovate-preview                        the repository in the current directory
  renovate-preview @org/repo              a remote GitHub repository (needs RENOVATE_TOKEN)
  renovate-preview --only base --managers kustomize
  renovate-preview -o html -w report.html read the result in a browser
  renovate-preview --just-print           show the container command, run nothing
  renovate-preview --shell                a shell in the container, set up as for Renovate
  renovate-preview -- --ignore-paths='overlays/**'   pass arguments on to Renovate

environment:
  RENOVATE_TOKEN          GitHub token: needed for @org/repo, and lifts the rate limit on
                          version lookups (RENOVATE_GITHUB_COM_TOKEN does that alone).
                          renovate-preview --pat-url links to a pre-filled token form.
  RENOVATE_CONFIG_FILE    default for --global-config.
  RENOVATE_PLATFORM       with RENOVATE_ENDPOINT, for @org/repo outside github.com.
  RENOVATE_*              forwarded to Renovate, like GITHUB_COM_TOKEN.
  RENOVATE_PREVIEW_RUNTIME  default for --runtime.

Renovate's working directory, {VOLUME_MOUNT}, is the {VOLUME} volume: its caches
survive between runs.
"""

# The permissions Renovate documents for a fine-grained token, minus the organization-only
# Members, which the form refuses for a personal account (and a dry run doesn't need):
# https://docs.renovatebot.com/modules/platform/github/#permissions
# The URL parameters: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
PAT_PERMISSIONS = {
    "contents": "write",
    "issues": "write",
    "pull_requests": "write",
    "statuses": "write",
    "vulnerability_alerts": "read",
    "workflows": "write",
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
        "--global-config",
        metavar="FILE",
        default=os.environ.get("RENOVATE_CONFIG_FILE"),
        help="a self-hosted (global) Renovate config (default: $RENOVATE_CONFIG_FILE)",
    )

    out = p.add_argument_group("output")
    out.add_argument(
        "-o",
        "--output",
        choices=("table", "json", "html", "raw", "report"),
        default="table",
        help="table (default), json, html; or Renovate's own: raw (its whole log), report",
    )
    out.add_argument("-w", "--write", metavar="FILE", help="write the output to FILE")
    out.add_argument(
        "-v", "--verbose", action="store_true", help="show what Renovate is doing as it runs"
    )
    out.add_argument("--silent", action="store_true", help="no progress, hints or warnings")
    out.add_argument(
        "--just-print", action="store_true", help="print the container command and exit"
    )

    ctr = p.add_argument_group("container")
    ctr.add_argument(
        "--runtime",
        choices=("auto", "podman", "docker"),
        default=os.environ.get("RENOVATE_PREVIEW_RUNTIME", "auto"),
        help="'auto' (default) picks podman, then docker",
    )
    ctr.add_argument("--image", default=IMAGE, help=f"container image (default: {IMAGE})")
    ctr.add_argument("--tag", default=TAG, help=f"image tag (default: {TAG})")
    ctr.add_argument("--name", help="container name")
    ctr.add_argument(
        "--shell", action="store_true", help="a shell in the container, instead of Renovate"
    )

    p.add_argument(
        "--pat-url", action="store_true", help="print a link to create a GitHub token, and exit"
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


def main(argv: list[str] | None = None) -> int:
    try:
        return preview(parse_args(sys.argv[1:] if argv is None else argv))
    except BrokenPipeError:
        # Output piped into `head` and the like: stop quietly.
        sys.stdout = None
        return 0


def preview(opts: argparse.Namespace) -> int:
    if opts.pat_url:
        print(pat_url())
        return 0
    target = Target.parse(opts.repository)
    warn = warner(opts)
    if error := check(opts, target):
        print(f"renovate-preview: error: {error}", file=sys.stderr)
        return 2

    engine = container.detect_engine(opts.runtime)
    if engine is None:
        if not opts.just_print:
            print(f"renovate-preview: error: {engine_missing(opts.runtime)}", file=sys.stderr)
            return 2
        engine = "podman" if opts.runtime == "auto" else opts.runtime
    image = f"{opts.image}:{opts.tag}"

    if opts.validate:
        return validate(opts, target, engine, image)

    fs = container.filesystem(target, opts.global_config)
    env = renovate.environment(fs.global_config)
    args = renovate.arguments(opts, target)

    if opts.shell:
        argv = container.command(engine, image, fs, env, [], "bash", opts.name, interactive=True)
        if opts.just_print:
            return print_command(argv)
        warn(f"Renovate would run as: renovate {shlex.join(args)}")
        return subprocess.run(argv, check=False).returncode

    argv = container.command(engine, image, fs, env, args, name=opts.name)
    if opts.just_print:
        return print_command(argv)

    if message := renovate.credentials_warning(opts.just_extract):
        warn(message)
    if opts.run:
        warn(f"this is a REAL run against {target.value}: it can create branches and PRs")
    if opts.verbose:
        print(container.printable(argv), file=sys.stderr)

    status, lines = container.run(argv, Progress(warn, opts.verbose, f"{engine}, {image}"))
    records = [r for r in map(parse_record, lines) if r is not None]
    if status != 0 and not records:
        # Not even Renovate's first log record: the engine itself failed.
        print(
            f"renovate-preview: error: the container did not start: {last_line(lines)}\n"
            f"If the image is missing: {engine} pull {image}",
            file=sys.stderr,
        )
        return status

    document = summarize(opts.repository or target.value, records)
    write(render(opts.output, document, lines, records), opts.write)
    if document["problems"] and not opts.verbose:
        warn("Renovate reported:\n  " + "\n  ".join(document["problems"][:10]))
    if status != 0:
        print(
            f"renovate-preview: renovate exited with status {status}; "
            "see its whole log with -o raw",
            file=sys.stderr,
        )
    return status


def last_line(lines: list[str]) -> str:
    return next((line for line in reversed(lines) if line.strip()), "no output")


def warner(opts):
    """Progress, hints and warnings on stderr, unless --silent."""

    def warn(message: str) -> None:
        if not opts.silent:
            print(f"renovate-preview: {message}", file=sys.stderr, flush=True)

    return warn


def check(opts, target: Target) -> str | None:
    """Reject what cannot work, before anything is run."""
    if opts.silent and opts.verbose:
        return "--silent and --verbose contradict each other"
    if opts.run and opts.just_extract:
        return "--run and --just-extract contradict each other"
    if not target.remote and not Path(target.value).is_dir():
        return f"no such directory: {target.value}"
    if opts.run and not target.remote:
        return "--run needs a remote repository (@org/repo): a local one has nowhere to push"
    if opts.validate and target.remote:
        return "--validate checks the config of a local repository"
    if opts.global_config and not Path(opts.global_config).is_file():
        return f"no such config file: {opts.global_config}"
    if not target.remote and renovate.passthrough_value(opts.passthrough, "--dry-run") in (
        "",
        "full",
    ):
        return (
            "the local platform can't do a full dry run (Renovate turns it into 'lookup'). "
            "For PR titles, preview the remote repository: renovate-preview @org/repo"
        )
    if target.remote and not opts.just_print and not os.environ.get("RENOVATE_TOKEN"):
        return "RENOVATE_TOKEN must be set to reach a remote repository (see --pat-url)"
    return None


def engine_missing(choice: str) -> str:
    if choice == "auto":
        return "neither podman nor docker found"
    return f"{choice} not found on PATH"


def validate(opts, target: Target, engine: str, image: str) -> int:
    config = renovate.find_config_file(target.value)
    if config is None:
        print(f"renovate-preview: error: no Renovate config in {target.value}", file=sys.stderr)
        return 2
    inside = f"{container.CONFIG_MOUNT}/{config.name}"
    fs = Filesystem(mounts=[(str(config.resolve()), inside)])
    env = {"NODE_OPTIONS": "--no-deprecation", "LOG_LEVEL": "info"}
    argv = container.command(
        engine, image, fs, env, ["--no-global", inside], "renovate-config-validator", opts.name
    )
    if opts.just_print:
        return print_command(argv)
    return subprocess.run(argv, check=False).returncode


def print_command(argv: list[str]) -> int:
    print(container.printable(argv))
    return 0


def render(output: str, document: dict, lines: list[str], records: list[dict]) -> str:
    if output == "raw":
        return "\n".join(lines)
    if output == "report":
        return json.dumps(raw_report(records), indent=2)
    if output == "json":
        return to_json(document)
    if output == "html":
        return html_page(document)
    return table(document)


def write(text: str, path: str | None) -> None:
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def pat_url() -> str:
    query = {
        "name": "renovate-preview",
        "description": "Renovate, run by renovate-preview. Permissions from "
        "https://docs.renovatebot.com/modules/platform/github/#permissions",
        **PAT_PERMISSIONS,
    }
    return f"https://github.com/settings/personal-access-tokens/new?{urlencode(query)}"


if __name__ == "__main__":
    sys.exit(main())
