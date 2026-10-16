# examples

One minimal, runnable fixture per Renovate feature. Each directory holds the smallest
manifest that exercises the feature, a `renovate.json` containing that feature and nothing
else, a `README.md` explaining what to look for, and an `expected.json` listing the dependencies
it should extract.

They are meant to be read *and* run:

```console
$ renovate-preview examples/custom-manager-jsonata   # one example
$ renovate-preview examples/kustomize --just-print   # ... and the Renovate command behind it
$ make examples                                      # check them all (offline)
```

`make examples` runs each one with `--just-extract`, and checks the dependencies found
against its `expected.json`.

## What each one covers

| Example | Feature |
|---|---|
| [`argocd`](./argocd/) | the `argocd` manager and its `managerFilePatterns` |
| [`kubernetes`](./kubernetes/) | the `kubernetes` manager and its `managerFilePatterns` |
| [`kustomize`](./kustomize/) | the `kustomize` manager, which needs no configuration at all |
| [`custom-manager-regex`](./custom-manager-regex/) | `customManagers` of `customType: regex`, in both the `depNameTemplate` and captured-`depName` flavours |
| [`custom-manager-jsonata`](./custom-manager-jsonata/) | `customManagers` of `customType: jsonata` with `fileFormat: yaml` |
| [`preset-extends`](./preset-extends/) | `extends: ["config:best-practices"]`, and the `ignorePaths` it brings with it |
| [`pr-title`](./pr-title/) | `semanticCommits` and the `commitMessageTopic` Handlebars template |
| [`separate-multiple-minor`](./separate-multiple-minor/) | `separateMultipleMinor`, and negated `matchPackageNames` |

## Adding one

1. `mkdir examples/<feature>` with the smallest manifest that shows the behaviour.
2. Put *only* the feature under test in its `renovate.json`.
3. Write the `README.md`: what the feature does, what to expect, and the gotcha if there is one.
4. Write `expected.json` from what `renovate-preview --just-extract -o json examples/<feature>` finds.
5. `make examples`.

The repository's own [`renovate.json`](../renovate.json) ignores `examples/**`: the versions
here are deliberately outdated, and an upgrade PR against one of them would break the
expectation it exists to illustrate.
