# extends: a shared preset

What `"extends": ["config:best-practices"]` actually brings with it.

A preset is not just defaults you could have written yourself — it changes what Renovate
*sees*. `config:best-practices` → `config:recommended` → `:ignoreModulesAndTests`, which adds
`**/test/**`, `**/tests/**`, `**/examples/**`, `**/vendor/**` and friends to `ignorePaths`.

So of the two identical kustomizations here, only one is extracted:

```
$ renovate-preview examples/preset-extends
```

Expected: `cert-manager` from `kustomization.yaml`, and **nothing** from
`tests/kustomization.yaml`.

## The gotcha this example exists to document

`ignorePaths` is `mergeable: false`, and a command-line `--ignore-paths` *replaces* the
value the config resolved to rather than adding to it. Passing `--ignore-paths something`
would therefore silently un-ignore `tests/` and this example would extract two charts.

`renovate-preview --except` has the same problem: it restates Renovate's defaults and the
`ignorePaths` written in `renovate.json`, but not those a preset brings in. See for yourself:

```
$ renovate-preview examples/preset-extends --except foo    # tests/ is extracted too
```

To skip more paths *and* keep the preset's, list the preset's globs in `ignorePaths` yourself.
