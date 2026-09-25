# Context

This repository contains tools to help testing Renovate configurations.

The main objectives are:
- Facilitate running Renovate locally and examining the results
- Provide a set of small examples that illustrate common Renovate configurations.

## Basic workflow

The user should be able to install an executable `renovate-preview`,
that performs a containerized dry-run of `renovate` in the current directory,
and prints a human-readable summary of extracted dependencies and detected upgrades.
The current repository is assumed to be a Git repository with a `renovate.json` file at its root.

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
renovate-preview --raw json

# Choose the scope of the dry-run
## Detect dependencies and lookup available upgrades, the default
renovate-preview --scope lookup
## Only detect dependencies
renovate-preview --scope extract

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

- The general idea is to produce human-readable output from parsing the `json` output of renovate `--dry-run`.
- Renovate should run in a container (`docker` or `podman` detecting what the user has installed), unless the user opts-in for a locally installed executable.
- Renovate caching mechanisms and named container volumes should be used to speed up successive runs.
- environment variables, whe used by `renovate-preview`, should be named like the corresponding `renovate` ones whenever possible
  and the user should be clearly informed of missing credentials when running against a remote repo,
  or fetching updates from container repositories that require authentication.
