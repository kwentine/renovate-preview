# Context

`renovate-preview` runs Renovate and produces a human-readable summary.

## Main objectives

The primary use case: run Renovate on the user's local host,
against a local or remote repository, with a local repository configuration (`renovate.json`).

The main objectives that should guide development:

1. Invoke Renovate easily, with sensible defaults.

Instead of:
```
podman run --rm -e RENOVATE_GITHUB_COM_TOKEN \
                -e NODE_OPTIONS=--no-deprecation \
                -e RENOVATE_DETECT_HOST_RULES_FROM_ENV=true \
                -e LOG_LEVEL=debug -e LOG_FORMAT=json \
                -e RENOVATE_REQUIRE_CONFIG=optional \
                -e RENOVATE_REPOSITORY_CACHE=enabled \
                -v /path/to/repo:/usr/src/app:ro,z \
                -v renovate-preview-cache:/tmp/renovate/cache \
                -w /usr/src/app ghcr.io/renovatebot/renovate:latest \
                --report-type=logging --dry-run=lookup \
                --platform local /path/to/repo
```
I want:
```
renovate-preview /path/to/repo
```

2. Expose an intuitive CLI (see usage examples)
However, Renovate power-users should be able to passthrough arguments, and not be required to learn new ones.

3. Provide clear human-readable output from Renovate's structured logs.

4. Educational
Provide a set of small examples that illustrate common Renovate configurations.
Allow the user to view simplified Renovate command lines equivalent to `renovate-preview` invocations.

## Basic user workflow
```
cd path/to/repo
# Edit renovate.json
# Test the changes:
renovate-preview
# Examine a table-formatted output with detected dependencies an available upgrades
```

Installing `renovate-preview` should be possible through:
```
uv tool install renovate-preview
```
Or by running `make install` that drops an executatble on the user's path.

`podmand`, `docker`, and local `renovate` runtimes should be supported.

Output formats should include `table`, `json` and `html` (for viewing results with pretty formatting in a browser).

## Usage examples

Below are some examples to get a feel of the intended CLI:
```
# Local git repository
renovate-preview path/to/directory

# Remote repository, that may require RENOVATE_TOKEN to be set if private.
renovate-preview @ScilifelabDataCentre/argocd-matomo

# Output pretty html for viewing in a browser
renovate-preview -o htlm

# Examine raw renovate output
renovate-preview --raw ndjson

# Choose the scope of the dry-run
# By default, dry-run is full so as to get PR titles and branches
# To only discover dependencies:
renovate-preview --just-extract

# Only validate the configuration
renovate-preview --validate

# Run for real!
renovate-preview --run

# Limit to a subset of files
renovate-preview --only base
renovate-preview --except overlays/**

# Limit to a subset of managers
renovate-preview --managers kubernetes

# Show details about what renovate is doing while running
renovate-preview --verbose

# Do not even display progress hints, just output when done
renovate-preview --silent

# Run a given renovate version
renovate-preview --image ghcr.io/renovatebot/renovate --tag 44.115.2

# Show the corresponding renovate command
renovate-preview --managers kubernetes --only base/ --just-print

# Pass arguments directly to Renovate
renovate-preview -- --ignore-paths='overlays/**'
```

# Technical guidelines

## Project structure

- `renovate-preview` should be written in clear Python, using the standard library as far as possible.
- A `Makefile` should be provided for a simple user-installation dropping `renovate-preview` in the user's `PATH`
- We should also structure the repository following modern Python project best-practices, and have everything
  in place to push the project to PyPI, and allow users to `uv install renovate-preview`.
- The README should be concise and document the most straightforward and practical day-to-day workflows.
- This repository should have a `renovate.json` file :)

## Implementation

The general guideline is to make design choices that favor simplicity,
with the aforementioned main objectives as "north stars".

- The general idea is to produce human-readable output from parsing the `json` output of renovate `--dry-run`.
- Renovate should run in a container (`docker` or `podman` detecting what the user has installed), unless the user opts-in for a locally installed executable.
- Renovate caching mechanisms and named container volumes should be used to speed up successive runs.
- Environment variables, used by `renovate-preview`, should be named like the corresponding `renovate` ones whenever possible
  and the user should be clearly informed of missing credentials when running against a remote repo,
  or fetching updates from container repositories that require authentication.
- No complex configuration merging logic: source of truth is the local configuration

# Work methodology

You are working in an isolated Fedora 44 container with common tools in the environment.
You should let me know any additional tools you would find useful.
Go ahead and perform all installations you see fit, and clone Github repositories that may be useful for the work.
You can also create and edit the `claude.Dockerfile` of your dreams! Perhaps that is where you will wake up the next time we interact.

In addition to terminal interaction, we use a set of `.org` files to organize our work.
They are written in the Emacs `org-mode` format.

- I provide feedback and questions in `FEEDBACK.org`
- I keep a list of tasks (`org-mode` TODO entries) in `TODO.org`
  You can toggle them to DONE or DOING as you see fit, and include comments in the subtree.
  Lengthy answers and plans should be abbreviated in the chat, and expanded in the corresponding TODO subtree.
  Please consider I am a human with limited cognitive bandwidth. Be brief, provide key links, highlight most important arguments.
  I may use the tags :plan: and :question:.
- You keep `SUMMARY.org` up-to-date so that we can conveniently resume work sessions.

We use a non-versioned directory called `scratch` that we can use to reproduce bugs.
You can drop integration tests you would like me to run there for example.

I may change my mind, but for now do not commit changes to git yourself, leave that to me.
