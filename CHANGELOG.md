# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Until 1.0, a
minor version may break the command line: such changes are marked **Breaking**.

## [0.2.0] - 2026-11-28

### Changed

- **Breaking:** `--env` takes a comma-separated list, `--env A,B`, and is no longer
  repeatable: with `--env A --env B`, only `B` reaches Renovate.
- When podman or docker can't run Renovate, a missing image being the usual cause, the
  error says how to pull it, or how to choose another one (`--image`, `--tag`).
- Every invalid value is reported at once, named by where it comes from: its option, or its
  `RENOVATE_PREVIEW_*` variable.
- `@owner/repo` is checked as you type it; GitLab's subgroups (`@group/subgroup/repo`) are
  accepted.
- An empty variable counts as unset, also for the tokens and the variables named by `--env`.
- `--config` on a local repository warns that Renovate reads the repository's own config
  too, whichever file it is.
- The report's title, for a local repository, is its absolute path.

### Added

- Defaults from the environment for more options: `RENOVATE_PREVIEW_OUTPUT`, `_IMAGE`,
  `_TAG`, `_ENV`, `_EXTRACT`, `_SHOW_CONFIG` (`true` or `false`), and `_VERBOSITY`
  (`silent`, `normal` or `verbose`).

### Removed

- `RENOVATE_PREVIEW_COLUMNS=help`: it is now an error. `--columns=help` still lists the
  columns.

## [0.1.0] - 2026-11-14

First release.

[0.2.0]: https://github.com/kwentine/renovate-preview/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kwentine/renovate-preview/releases/tag/v0.1.0
