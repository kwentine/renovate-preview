# renovate-preview

Runs Renovate (through podman) against a repo and explains what it found, so the answer to
*"why did it do that?"* is on screen instead of buried in a 2000-line log.

```console
$ renovate_check --scope=lookup --include-paths manifests/cert-manager
⠹ 4s  looking up versions                           ← a spinner that erases itself
DEPENDENCY     CURRENT  NEW      FILE
-------------  -------  -------  -----------------------------------------
cert-manager   v1.20.2  v1.20.4  manifests/cert-manager/kustomization.yaml
cert-manager   v1.20.2  v1.21.2  manifests/cert-manager/kustomization.yaml
trust-manager  v0.22.0  v0.22.1  manifests/cert-manager/kustomization.yaml
...
```

| | |
|---|---|
| [`renovate.py`](./renovate.py) | the tool |
| [`test_renovate.py`](./test_renovate.py) | unit tests — pure logic, never invokes Renovate |
| [`renovate-examples/`](./renovate-examples/) | one runnable fixture per feature used in our [`renovate.json`](../../renovate.json) |
| [`PLAN.md`](./PLAN.md) | the design decisions, and the root causes behind the non-obvious ones |
| [`Makefile`](./Makefile) | install, test, lint |

## Install

```console
$ make install          # symlinks ~/.local/bin/renovate_check -> ./renovate.py
$ make uninstall
```

A symlink rather than a copy, deliberately: `renovate.py` locates `renovate-examples/` next
to its own *resolved* path, so the link keeps `renovate_check --examples` working and picks
up edits to the checkout immediately. `make install PREFIX=/usr/local NAME=rc` if you want
it somewhere else or under another name.

Everything below works the same whether you invoke it as `renovate_check` or
`./helpers/renovate/renovate.py`.

## Usage

`--help` documents every flag. The things worth knowing up front:

| | |
|---|---|
| `renovate_check` | dry-run the current directory at `--scope=extract` |
| `--scope=lookup` | also resolve new versions |
| `--scope=full` | also compute the branch names and PR titles that would be created — works on a local directory, and needs no token |
| `--run` | a real run — creates branches and PRs |
| `@org/repo` | run against a remote GitHub repo instead (`--gh-host` for Enterprise) |
| `--config FILE` | use an alternate config, and actually make it win (see below) |
| `--managers`, `--include-paths`, `--ignore-paths` | narrow what is considered |
| `--wide` | add manager, datasource and update-type columns |
| `-v` | the whole story: command, stats, diagnostics, warnings, log path |
| `--format=json\|report\|raw\|ndjson` | machine-readable, Renovate's own report, or its raw log |
| `--validate FILE` | validate a config and exit |
| `--examples` | run the fixtures in `renovate-examples/` |
| `--print-command` | show the podman invocation without running it |
| `--runtime=local` | use a `renovate` on `$PATH` instead of podman — much faster to iterate with |

### Output

A run that worked prints its table and nothing else — no stats line, no notes, no log path —
so the output pipes and pastes cleanly. `-v` adds the whole story: the command, the
`ignorePaths` union it settled on, extraction stats, planned branches, the diagnostics and
Renovate's own warnings.

Two things are never hidden: an **empty result explains itself**, and a run with problems
prints **one line on stderr** saying so (`(1 problem(s), 2 note(s) - re-run with -v, ...)`).

Every run keeps the complete ndjson log; `-v` (or a failure) prints its path, so the quiet
default never costs you the detail.

### Progress

`--progress` picks how the wait is shown. `auto`, the default, reads the situation:

| | |
|---|---|
| a terminal | a one-line spinner that **erases itself**, leaving only the table |
| `-v` | `[+  12s] ...` heartbeat lines, unfiltered |
| `TERM=dumb` or `INSIDE_EMACS` | heartbeat lines — a `\r` redraw garbles in an Emacs shell buffer |
| piped, or `--format=raw` | nothing |

Force one with `--progress=spinner\|lines\|off`.

## Debugging aids

These exist because each one cost someone an afternoon. [`PLAN.md`](./PLAN.md) has the
evidence for each.

- **`git ls-files` and `.gitignore`.** Renovate's local platform lists files with
  `git ls-files`, and falls back to globbing *everything* when git errors out — which is
  what makes a vendored `charts/` tree turn 57 files into 1784. The helper marks the bind
  mount as a safe git directory so that never happens, and says so loudly if it does anyway.
- **Untracked files are invisible**, because `git ls-files` only lists tracked ones. The
  helper tells you which ones were skipped; `--untracked=include` runs against a scratch
  copy so they are seen.
- **`--config` alone does not win.** The target repo's own `renovate.json` is merged on top
  of a config passed with `RENOVATE_CONFIG_FILE`. The helper nests yours under Renovate's
  `force` so it actually applies; `--config-mode=file` gives you the raw behaviour.
- **An empty table explains itself** — which managers were enabled, how many files were
  matched, what `includePaths`/`ignorePaths` were in effect, and the fact that
  `managerFilePatterns` are anchored at the repo root and so match nothing when you point
  the tool at a subdirectory.
- **`--ignore-paths` replaces rather than adds.** The helper restates Renovate's defaults,
  the target's own value, and the ones its presets imply, so passing one flag does not
  silently un-ignore `node_modules/`. `-v` prints the union it settled on.

## Environment

| | |
|---|---|
| `RENOVATE_TOKEN` | GitHub PAT. Raises datasource rate limits, and is required by `--run`. It does **not** change the dry-run scope: PR titles come from `--scope=full` with or without it. |
| `RENOVATE_GITHUB_COM_TOKEN`, `RENOVATE_DOCKER_NVCR_IO_PASSWORD` | forwarded to the container when set, picked up via `detectHostRulesFromEnv` |
| `RENOVATE_PY_RUNTIME` | default for `--runtime` |

## Tests

```console
$ make test                       # unit tests, no Renovate needed
$ make examples                   # end-to-end fixtures, offline
$ make examples-network           # also the ones that need version lookups
$ make check                      # test + examples
```

`make examples RUNTIME=local` runs them against a user install of Renovate
(`npm install -g renovate`) instead of the podman image — much faster to iterate with, and
the fallback on a machine without a container engine.

## A TUI, if you prefer one

[`../renovate-tui/`](../renovate-tui/) is a k9s-style front end: filter, re-run at a
different scope, inspect a row, read the problems, tail the log. It shells out to
`renovate.py --format=json` and holds no Renovate knowledge of its own — see
[../TUI.md](../TUI.md).
