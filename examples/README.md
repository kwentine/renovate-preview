# renovate-examples

One minimal, runnable fixture per feature used in this repo's top-level
[`renovate.json`](../../../renovate.json). Each directory holds the smallest manifest that
exercises the feature, a `renovate.json` containing that feature and nothing else, a
`README.md` explaining what to look for, and an `expected.json` the runner checks against.

They are meant to be read *and* run:

```console
$ renovate_check renovate-examples/custom-manager-jsonata   # one example, full output
$ renovate_check --examples                                 # all of them, checked
$ renovate_check --examples --with-network                  # including the lookup ones
$ renovate_check --examples kustomize preset-extends        # just these
```

`--examples` runs every fixture from a scratch copy at `--scope=extract`, so it needs no
network, no token and no git history — a fixture works the moment you write it.

## What each one covers

| Example | Feature in `renovate.json` | Needs network |
|---|---|---|
| [`argocd`](./argocd/) | the `argocd` manager and its `managerFilePatterns` | no |
| [`kubernetes`](./kubernetes/) | the `kubernetes` manager and its `managerFilePatterns` | no |
| [`kustomize`](./kustomize/) | the `kustomize` manager, which needs no configuration at all | no |
| [`custom-manager-regex`](./custom-manager-regex/) | `customManagers` of `customType: regex`, in both the `depNameTemplate` and captured-`depName` flavours | no |
| [`custom-manager-jsonata`](./custom-manager-jsonata/) | `customManagers` of `customType: jsonata` with `fileFormat: yaml` | no |
| [`preset-extends`](./preset-extends/) | `extends: ["config:best-practices"]`, and the `ignorePaths` it brings with it | no |
| [`pr-title`](./pr-title/) | `semanticCommits` and the `commitMessageTopic` Handlebars template | yes |
| [`separate-multiple-minor`](./separate-multiple-minor/) | `separateMultipleMinor`, and negated `matchPackageNames` | yes |

The two network examples assert on PR titles and branch names, which only exist at
`--scope=full`. Their expectations are regexes rather than literals, so they survive the
upstream releases that move the version numbers.

## Adding one

1. `mkdir renovate-examples/<feature>` with the smallest manifest that shows the behaviour.
2. Put *only* the feature under test in its `renovate.json`.
3. Write the `README.md`: what the feature does, what to expect, and the gotcha if there is one.
4. Run `renovate_check --examples <feature>` — with no `expected.json` it prints one ready to
   paste into the file.
5. Re-run to confirm it passes.
