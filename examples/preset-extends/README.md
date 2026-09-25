# extends: a shared preset

Mirrors the `"extends": ["config:best-practices"]` in this repo's top-level `renovate.json`.

A preset is not just defaults you could have written yourself — it changes what Renovate
*sees*. `config:best-practices` → `config:recommended` → `:ignoreModulesAndTests`, which adds
`**/test/**`, `**/tests/**`, `**/examples/**`, `**/vendor/**` and friends to `ignorePaths`.

So of the two identical kustomizations here, only one is extracted:

```
$ renovate_check renovate-examples/preset-extends
```

Expected: `cert-manager` from `kustomization.yaml`, and **nothing** from
`tests/kustomization.yaml`.

## The gotcha this example exists to document

`ignorePaths` is `mergeable: false`, and a command-line `--ignore-paths` *replaces* the
value the config resolved to rather than adding to it. Passing `--ignore-paths something`
would therefore silently un-ignore `tests/` and this example would extract two charts.

`renovate.py` compensates: when the target's config has an `extends`, it folds
`:ignoreModulesAndTests`' globs back into the list it passes (see `PRESET_IGNORE_PATHS` in
`renovate.py`). Run with `-v` to see the full union it settled on.
