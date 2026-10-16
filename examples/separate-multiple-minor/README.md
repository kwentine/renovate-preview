# separateMultipleMinor, and negated matchPackageNames

A `packageRule` for stepwise upgrades, and the negated `matchPackageNames` that scopes it.

`separateMultipleMinor` asks Renovate for **one branch per intervening minor version**
instead of a single jump to the newest release — so a chart sitting on `v0.22.0` with
`v0.23`, `v0.24` and `v0.25` available produces three branches, not one. That is what makes
a stepwise upgrade reviewable one minor at a time.

`matchPackageNames: ["!renovate", "!loki"]` is the negation form: a rule that matches
everything *except* those two. Renovate treats an all-negative `matchPackageNames` as
"match all, minus these", which is why no positive `"*"` entry is needed.

```
$ renovate-preview examples/separate-multiple-minor
```

Expected: several rows for `trust-manager`, one per minor, each on its own `BRANCH`,
instead of a single row for the latest. Compare with the same fixture minus the `packageRules` block.

Needs network: version lookups hit `charts.jetstack.io`.
