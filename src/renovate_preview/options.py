"""renovate-preview's options: from the command line and the environment to ``Options``.

A parameter is a name, a parser (a string in, a value out, ValueError otherwise), a
default, and whether a RENOVATE_PREVIEW_<NAME> variable may set it. ``parse_args()``:

1. extract: the command line and the environment give values, for the parameters they
   have;
2. resolve: per parameter, the first source that has a value wins;
3. parse the strings, collecting every error;
4. default the rest;
5. read what else a run needs from the environment: which variables Renovate gets, and
   where the last run is kept.

The result is a frozen ``Options``, which also says where each value comes from. After
parse_args(), nothing reads the environment. What depends on several options, and
whether they are consistent, is up to the program.
"""

from __future__ import annotations

import argparse
import enum
import os
from collections import ChainMap
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from . import __version__
from .container import IMAGE, TAG, VOLUME, VOLUME_MOUNT
from .render import COLUMNS, DEFAULT_COLUMNS
from .renovate import GITHUB

Output = Literal["table", "json", "html", "raw", "report"]
Verbosity = Literal["silent", "normal", "verbose"]


class Source(enum.Enum):
    """Where a value comes from, in order of precedence."""

    CLI = "command line"
    ENV = "environment"
    DEFAULT = "default"


class UsageError(Exception):
    """What's wrong with the command line or the environment: main() logs it, exits 2."""

    def __init__(self, messages: list[str]):
        super().__init__("\n".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class Remote:
    """A remote repository: @owner/repo on the command line."""

    slug: str  # "owner/repo"; GitLab's subgroups give more parts.


# Parsers: a string in, a value out, ValueError otherwise. They know nothing of argparse
# or of where the string came from.


def boolean(value: str) -> bool:
    if value.lower() in ("1", "true", "yes", "on"):
        return True
    if value.lower() in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{value!r} is not a boolean: true or false")


def one_of(choices: Any) -> Callable[[str], str]:
    """Return the parser of a Literal type's values."""
    allowed = get_args(choices)

    def parse(value: str) -> str:
        if value not in allowed:
            raise ValueError(f"{value!r} is not one of {', '.join(allowed)}")
        return value

    return parse


def repository(value: str) -> Remote | Path:
    if not value.startswith("@"):
        return Path(value)  # As given: resolving it reads the cwd, a fact of the run.
    parts = value[1:].split("/")
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"{value!r} is not @owner/repo")
    return Remote(value[1:])


def columns(value: str) -> tuple[str, ...]:
    if value == "all":
        return tuple(COLUMNS)
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    if unknown := [name for name in names if name not in COLUMNS]:
        raise ValueError(f"unknown column(s) {','.join(unknown)}, see --columns=help")
    if not names:
        raise ValueError("no column names, see --columns=help")
    return names


def variable_names(value: str) -> tuple[str, ...]:
    """Names, comma-separated. Never values, which would show in the command."""
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    if any("=" in name for name in names):
        raise ValueError("variable names only: set their values in your shell")
    return names


def path(value: str) -> Path:
    return Path(value).expanduser()


@dataclass(frozen=True)
class Parameter:
    parse: Callable[[str], Any] = str  # for a source that gives a string
    default: Any = None  # what the program gets when no source has a value
    variable: bool = True  # RENOVATE_PREVIEW_<NAME> may set it
    help: str = ""  # for --help, which adds the variable and the default


PARAMETERS = {
    "repository": Parameter(
        repository,
        Path("."),
        variable=False,
        help="a local directory, or @owner/repo on GitHub",
    ),
    "extract": Parameter(
        boolean,
        False,
        help="only list the dependencies found, do not look up available versions",
    ),
    "global_config": Parameter(path, help="a self-hosted Renovate global config"),
    "source": Parameter(
        variable=False,
        help="replay a Renovate JSON log instead of running: a file, - for stdin, "
        "or 'last' for the previous run",
    ),
    "config": Parameter(
        path,
        help="a repository config, instead of the repository's own: e.g. a PR's renovate.json",
    ),
    "host": Parameter(default=GITHUB, help="with @owner/repo: a GitHub Enterprise Server"),
    "output": Parameter(
        one_of(Output), "table", help="table, json, html; raw, report: Renovate's own logs"
    ),
    # The run adjusts it: see cli.columns_to_show().
    "columns": Parameter(
        columns,
        DEFAULT_COLUMNS,
        help="comma-separated columns for table and html; 'all' for every one, "
        "'help' to list them",
    ),
    "write": Parameter(path, variable=False, help="write the output to FILE"),
    "show_config": Parameter(
        boolean,
        False,
        help="show the config Renovate resolved, presets included, before the table",
    ),
    "verbosity": Parameter(one_of(Verbosity), "normal"),  # --verbose, --silent
    "just_print": Parameter(
        boolean, False, variable=False, help="print the container command and exit"
    ),
    "pat_url": Parameter(
        boolean,
        False,
        variable=False,
        help="print a link to create a read-only GitHub token for --host, and exit",
    ),
    "image": Parameter(default=IMAGE, help="container image"),
    "tag": Parameter(default=TAG, help="image tag"),
    "name": Parameter(variable=False, help="container name"),
    "env": Parameter(variable_names, (), help="pass these variables on to Renovate too, by name"),
    "shell": Parameter(
        boolean, False, variable=False, help="a shell in the container, instead of Renovate"
    ),
    # Passed on to Renovate, when set, without --env. Each authenticates version lookups on
    # github.com (the last two as host rules); RENOVATE_TOKEN is also the token of the
    # platform, for @owner/repo. No flag, no variable: the default is the whole story.
    "default_env": Parameter(
        variable_names,
        ("RENOVATE_TOKEN", "RENOVATE_GITHUB_COM_TOKEN", "GITHUB_COM_TOKEN"),
        variable=False,
    ),
}


@dataclass(frozen=True)
class Options:
    """Everything a run needs from the command line and the environment, parsed."""

    repository: Remote | Path
    extract: bool
    global_config: Path | None
    source: str | None
    config: Path | None
    host: str
    output: Output
    columns: tuple[str, ...]
    write: Path | None
    show_config: bool
    verbosity: Verbosity
    just_print: bool
    pat_url: bool
    image: str
    tag: str
    name: str | None
    env: tuple[str, ...]
    shell: bool
    default_env: tuple[str, ...]
    # From the environment, not parameters.
    forwarded: tuple[str, ...]  # The variables Renovate gets, by name: default_env, env.
    last_run: Path  # Where a run keeps its log, for --from last.
    sources: Mapping[str, Source] = field(repr=False, compare=False)

    @property
    def remote(self) -> str | None:
        """Return "owner/repo" for a remote repository; then `directory` is None."""
        return self.repository.slug if isinstance(self.repository, Remote) else None

    @property
    def directory(self) -> Path | None:
        """Return the local repository, as given (relative or not)."""
        return None if isinstance(self.repository, Remote) else self.repository

    def given(self, name: str) -> bool:
        """Whether `name` is on the command line: an instruction, not a preference."""
        return self.sources[name] is Source.CLI

    def origin(self, name: str) -> str:
        return origin(name, self.sources[name])


def last_run(environ: Mapping[str, str]) -> Path:
    """Return where each run keeps its log, for --from last: the last one only.

    Logs are "state" in the XDG Base Directory spec, whose variables are ignored unless
    absolute: https://specifications.freedesktop.org/basedir-spec/latest/
    """
    state = environ.get("XDG_STATE_HOME", "")
    home = Path(environ.get("HOME") or Path.home())
    base = Path(state) if os.path.isabs(state) else home / ".local" / "state"
    return base / "renovate-preview" / "last.ndjson"


def variable(name: str) -> str:
    return f"RENOVATE_PREVIEW_{name.upper()}"


def flag(name: str) -> str:
    if name == "repository":  # The positional argument.
        return name
    if name == "verbosity":
        return "--verbose/--silent"
    return "--" + name.replace("_", "-")


def origin(name: str, source: Source) -> str:
    """Return where `name` was set, for a message: its flag, or its variable."""
    return variable(name) if source is Source.ENV else flag(name)


def help_text(name: str) -> str:
    """Return the help of a parameter: its own, then its variable and default value."""
    parameter = PARAMETERS[name]
    sources = [f"${variable(name)}"] if parameter.variable else []
    if isinstance(parameter.default, tuple):
        sources += [",".join(parameter.default)] if parameter.default else []
    elif parameter.default not in (None, False):
        sources.append(str(parameter.default))
    if not sources:
        return parameter.help
    return f"{parameter.help} (default: {', or '.join(sources)})"


VARIABLES_HELP = "\n".join(
    f"  {variable(name):<33}{flag(name)}"
    for name, parameter in PARAMETERS.items()
    if parameter.variable
)

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

  any other NAME          with --env NAME[,NAME...], e.g. for registry credentials, see
                          https://docs.renovatebot.com/self-hosted-configuration/#detecthostrulesfromenv

defaults for renovate-preview's options, from its environment (booleans: true or false;
verbosity: silent, normal or verbose):
{VARIABLES_HELP}

Renovate runs in podman, or else docker. Its working directory, {VOLUME_MOUNT}, is the
{VOLUME} volume: its caches survive between runs.
"""


class ColumnsHelp(argparse.Action):
    """Store --columns; but --columns=help lists the columns and exits, as --help does."""

    def __call__(self, parser, namespace, values, option_string=None):
        if values == "help":
            print(columns_help())
            parser.exit()
        setattr(namespace, self.dest, values)


def columns_help() -> str:
    """Return the help of --columns=help: the names, and which column each shows."""
    lines = [f"{name:<12}{title}" for name, (title, _) in COLUMNS.items()]
    return "\n".join([*lines, "all         every column above"])


def parser() -> argparse.ArgumentParser:
    """Return the command line's parser. It gives the values given, and only those."""
    p = argparse.ArgumentParser(
        prog="renovate-preview",
        description="Dry-run Renovate against a repository and summarize what it would do.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        argument_default=argparse.SUPPRESS,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("repository", nargs="?", help=help_text("repository"))

    what = p.add_argument_group("what to run")
    what.add_argument("--extract", action="store_true", help=help_text("extract"))
    what.add_argument("--global-config", metavar="FILE", help=help_text("global_config"))
    what.add_argument("--from", dest="source", metavar="LOG", help=help_text("source"))
    what.add_argument("--config", metavar="FILE", help=help_text("config"))
    what.add_argument("--host", help=help_text("host"))

    out = p.add_argument_group("output")
    out.add_argument(
        "-o",
        "--output",
        metavar="{" + ",".join(get_args(Output)) + "}",
        help=help_text("output"),
    )
    out.add_argument("--columns", action=ColumnsHelp, metavar="LIST", help=help_text("columns"))
    out.add_argument("-w", "--write", metavar="FILE", help=help_text("write"))
    out.add_argument("--show-config", action="store_true", help=help_text("show_config"))
    loudness = out.add_mutually_exclusive_group()
    loudness.add_argument(
        "-v",
        "--verbose",
        dest="verbosity",
        action="store_const",
        const="verbose",
        help="show what Renovate is doing as it runs",
    )
    loudness.add_argument(
        "--silent",
        dest="verbosity",
        action="store_const",
        const="silent",
        help="no progress, hints or warnings",
    )
    out.add_argument("--just-print", action="store_true", help=help_text("just_print"))
    out.add_argument("--pat-url", action="store_true", help=help_text("pat_url"))

    ctr = p.add_argument_group("container")
    ctr.add_argument("--image", help=help_text("image"))
    ctr.add_argument("--tag", help=help_text("tag"))
    ctr.add_argument("--name", help=help_text("name"))
    ctr.add_argument("--env", metavar="NAME[,NAME...]", help=help_text("env"))
    ctr.add_argument("--shell", action="store_true", help=help_text("shell"))
    return p


def parse_args(argv: list[str], environ: Mapping[str, str]) -> tuple[Options, list[str]]:
    """Return our options, and the arguments after `--`, Renovate's. Or raise UsageError.

    argparse itself rejects what isn't an option of ours, and exits.
    """
    theirs: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, theirs = argv[:split], argv[split + 1 :]

    # 1. Extract: each source gives values, for the parameters it has.
    cli = vars(parser().parse_args(argv))
    env = {
        name: value
        for name, parameter in PARAMETERS.items()
        if parameter.variable and (value := environ.get(variable(name)))
    }
    # 2. Resolve: the first source that has a value wins.
    given = ChainMap(cli, env)
    sources = {
        name: Source.CLI if name in cli else Source.ENV if name in env else Source.DEFAULT
        for name in PARAMETERS
    }
    # 3. Parse the strings, in PARAMETERS' order. A value that has its type already, like a
    # flag's True, is kept.
    parsed, errors = {}, []
    for name, parameter in PARAMETERS.items():
        if name not in given:
            continue
        value = given[name]
        try:
            parsed[name] = parameter.parse(value) if isinstance(value, str) else value
        except ValueError as err:
            errors.append(f"{origin(name, sources[name])}: {err}")
    if errors:
        raise UsageError(errors)
    # 4. Default the rest.
    values = {**{name: parameter.default for name, parameter in PARAMETERS.items()}, **parsed}
    # 5. What else a run needs from the environment. Set means not empty, as for our own
    # variables. The values stay in the environment: podman passes them on by name.
    names = dict.fromkeys([*values["default_env"], *values["env"]])
    values["forwarded"] = tuple(name for name in names if environ.get(name))
    values["last_run"] = last_run(environ)
    return Options(**values, sources=sources), theirs
