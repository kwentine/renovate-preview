# renovate-preview

Dry-run [Renovate](https://docs.renovatebot.com/) against a repository, and preview the
results as a table.

Renovate runs in a container, with `podman` or `docker` (whichever is installed).

```console
$ cd path/to/repo
$ $EDITOR renovate.json
$ podman pull ghcr.io/renovatebot/renovate
$ renovate-preview
DEPENDENCY     CURRENT  NEW      FILE
-------------  -------  -------  ------------------
trust-manager  v0.22.0  v0.26.0  kustomization.yaml
trust-manager  v0.22.0  v0.22.1  kustomization.yaml
```

## Try it

No installation needed, only Python 3.9 or later:

```console
$ git clone https://github.com/kwentine/renovate-preview.git
$ cd renovate-preview/src
$ python3 -m renovate_preview ~/path/to/my/repo
```

## Install

From PyPI:

```sh
uv tool install renovate-preview   # or: pipx install renovate-preview
```

From a local checkout:

```sh
uv tool install .   # or,
make install        # a launcher in ~/.local/bin, running this checkout: edits apply at once
```

## Usage

```sh
renovate-preview                           # the repository in the current directory
renovate-preview path/to/repo              # another one
renovate-preview @owner/repo               # a GitHub repository (needs RENOVATE_TOKEN)
renovate-preview @owner/repo --host ghe.example.com   # ... on a GitHub Enterprise Server
renovate-preview @owner/repo --config renovate.json   # ... with your config instead of its own
renovate-preview --pat-url                 # a link to create that token: read-only, pre-filled

renovate-preview --extract                 # only the dependencies found: fast, offline
renovate-preview --validate                # only validate the config

# Arguments after -- go to Renovate, for example:
renovate-preview -- --include-paths='base/**'       # limit to some paths
renovate-preview -- --ignore-paths='overlays/**'    # skip some (replaces ignorePaths)
renovate-preview -- --enabled-managers=kubernetes   # limit to some managers

renovate-preview --columns dependency,new,type,breaking,age   # the columns to show
renovate-preview --columns all             # every column; --columns help lists them
renovate-preview -o json                   # table (default), json, html
renovate-preview -o html -w report.html    # ... written to a file rather than stdout
renovate-preview -o raw                    # Renovate's own log; or its report: -o report
renovate-preview --from last --columns all # the last run again, other columns: no re-run
renovate-preview --from run.ndjson         # any Renovate JSON log (- for stdin)
renovate-preview --verbose                 # what Renovate is doing, as it runs
renovate-preview --silent                  # no progress hints or warnings

renovate-preview --global-config config.js # a self-hosted (global) config
renovate-preview --tag 44.115.2            # a given Renovate version
renovate-preview --just-print              # the container command, without running it
renovate-preview --shell                   # a shell in that container, instead of Renovate
```

Arguments after `--` take precedence over everything else.

Renovate's working directory and caches are kept in the `renovate-preview` volume. To
clear them: `podman volume rm renovate-preview`.

## Environment

These variables are forwarded to Renovate:

| Variable                                        | What for                                                            |
|-------------------------------------------------|---------------------------------------------------------------------|
| `RENOVATE_TOKEN`                                | GitHub token. Required for `@owner/repo`.                           |
| `RENOVATE_GITHUB_COM_TOKEN`, `GITHUB_COM_TOKEN` | Authenticates version lookups on github.com, avoiding rate limits.  |
| `RENOVATE_PLATFORM`, `RENOVATE_ENDPOINT`        | For `@owner/repo` on GitLab, Forgejo...                             |

## Configuration

Which configuration wins, and why: [docs/configuration.org](https://github.com/kwentine/renovate-preview/blob/main/docs/configuration.org).

### Global

`--global-config FILE` gives Renovate a self-hosted (global) config.

### Repository

For a local repository, the preview uses its `renovate.json`. To test a change, edit it
and run `renovate-preview` again.

For a remote repository, `--config FILE` uses a local config file instead of the
repository's own. For example, to review a pull request that changes `renovate.json`,
check out its branch and run `renovate-preview @owner/repo --config renovate.json`. Unlike a
local preview, this shows PR titles.

On a local repository, `--config` is read too, but the repository's own `renovate.json`
takes precedence.

## Good to know

- **No PR titles for a local repository.** Renovate's local platform stops after version
  lookups (it silently turns `--dry-run=full` into `lookup`, so renovate-preview refuses
  it): branch names are known, PR titles are not. Against `@owner/repo` the dry run is
  complete.
- **AGE and PENDING come from `minimumReleaseAge`.** AGE is the new version's age, empty
  when the registry gives no release date (many container registries don't). Renovate then
  can't check `minimumReleaseAge`, and by default holds the update back. PENDING is `yes`
  when the update itself waits for its checks, or names the newest version held back, like
  the Pending column of Renovate's PRs. Show them with `--columns`; `-o html` highlights
  both.
- **Columns**: `dependency`, `current`, `new`, `file` by default (`new` only when versions
  are looked up). Also: `type`, `breaking` (as the versioning scheme says: under semver,
  any 0.x change is), `age`, `pending`, `manager`, `datasource`, `versioning`, `branch`,
  `pr-title`. `-o json` always has everything.
- **`--from last` is the last run only.** Each run keeps its log in
  `$XDG_STATE_HOME/renovate-preview/last.ndjson` (by default `~/.local/state/...`),
  readable by you only, replacing the previous one. To keep a run: `-o raw -w FILE`, then
  `--from FILE`.
- **Only files known to git are seen.** Renovate lists files with `git ls-files`, so
  `git add` a new manifest before previewing it.
- **`--ignore-paths` replaces Renovate's `ignorePaths`.** Passed after `--`, it replaces
  everything configured: Renovate's defaults (`**/node_modules/**`...), your
  `renovate.json`'s list, and its presets'. List what you still want ignored, or use
  `--include-paths`. See
  [examples/preset-extends](https://github.com/kwentine/renovate-preview/tree/main/examples/preset-extends).
- **`--include-paths` needs a glob for a directory: `base/**`.** A bare `base` only matches
  a *file* named `base`, so nothing is scanned. (`--ignore-paths`, by contrast, matches any
  path *containing* what you give: `base` also ignores `database/`.)

## With renovate-pretty-log

[renovate-pretty-log](https://gitlab.com/tanna.dev/renovate-pretty-log) is for exploring a
Renovate debug log: a colourised printer, an HTML report, and a TUI with folding and
search. It reads the log of any Renovate run, including the ones your production instance
writes.

renovate-preview comes before the log. It runs Renovate for you, with the settings a
preview needs, and answers one question: which dependencies were found, and which updates
are available. When that answer is surprising, the log explains why:

```sh
renovate-preview -o raw -w run.log                 # the same run, as Renovate's own log
renovate-pretty-log-tui -path run.log              # ... explored
renovate-pretty-log -path run.log -level warn      # ... or just its problems
renovate-preview -o raw | renovate-pretty-log -path /dev/stdin
```

## Examples

[examples/](https://github.com/kwentine/renovate-preview/tree/main/examples) holds one
small, runnable example per common Renovate configuration:

```sh
renovate-preview examples/custom-manager-regex
```

## Development

The tests need pytest: `uv run make test` installs it in `.venv`, with ruff.

```sh
make test       # unit tests
make examples   # check examples/ against a real Renovate, in podman or docker
make lint
make build
```
