# renovate-preview

Perfrom a [Renovate](https://docs.renovatebot.com/) dry run to preview the results.

Renovate executes in a container from the `ghcr.io/renovatebot/renovate` image,
using whichever of `podman` or `docker` is installed.

The intended benefits are:
1. Avoiding the need to install it on your machine with `npm`
2. Sparing you the complex commands to invoke Renovate though `podman` or `docker`
3. Extracting and summarizing useful information from the profuse Renovate logs
4. Assessing the behavior of local configuration changes on a remote repository (such as pull request titles).

`renovate-preview` is implemented in Python and has no dependencies.
It is developed with considerable help from Claude Code, but not vide-coded.

## Use cases

### Preview dependencies and available updates from a local repository

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


### Test the behavior of a local configuration on a remote repository
Let's say you have edited a local checkout of `renovate.json`,
and would like to verify that pull request titles are formatted according to your wishes:
```
$ renovate-preview @owner/repo --columns=dependency,current,new,pr-title
DEPENDENCY     CURRENT  NEW    PR TITLE
-------------  -------  ---    --------
example-chart  1.2.3    1.2.4  chore(deps): Update example-chart to v1.2.4
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

Renovate gets these variables of yours, by name, so their values never show in the
command:

| Variable                                        | What for                                                           |
|-------------------------------------------------|--------------------------------------------------------------------|
| `RENOVATE_TOKEN`                                | GitHub token. Required for `@owner/repo`.                          |
| `RENOVATE_GITHUB_COM_TOKEN`, `GITHUB_COM_TOKEN` | Authenticates version lookups on github.com, avoiding rate limits. |

No other variable reaches Renovate unless you name it with `--env NAME`, for example a
private registry's credentials, as Renovate
[reads them from the environment](https://docs.renovatebot.com/self-hosted-configuration/#detecthostrulesfromenv):

```sh
renovate-preview --env DOCKER_REGISTRY_EXAMPLE_COM_USERNAME --env DOCKER_REGISTRY_EXAMPLE_COM_PASSWORD
```

Renovate's settings are its arguments, after `--`. For example, `@owner/repo` on GitLab:

```sh
renovate-preview @owner/repo -- --platform=gitlab --endpoint=https://gitlab.example.com/api/v4/
```

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

For trivia about what can surprise you and why, see: [docs/good-to-know.org](https://github.com/kwentine/renovate-preview/blob/main/docs/good-to-know.org).

## With renovate-pretty-log

[renovate-pretty-log](https://gitlab.com/tanna.dev/renovate-pretty-log) is for exploring a
Renovate debug log: a colourised printer, an HTML report, and a TUI with folding and
search.

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
