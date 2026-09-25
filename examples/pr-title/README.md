# semanticCommits and commitMessageTopic

Two settings from this repo's top-level `renovate.json` that shape the PR *title* rather
than what gets found, so they only show up at `--scope=full`:

- `"semanticCommits": "enabled"` — prefixes every title with `chore(deps):`. Renovate
  otherwise auto-detects this from git history and lands on "disabled" in a fresh fixture,
  so setting it explicitly is what makes the prefix deterministic.
- the `commitMessageTopic` `packageRule` — verbatim from the top-level config. A bare chart
  name like `cert-manager` says nothing about *which* deployment it belongs to, so the
  template falls back to the directory: `packageFileDir` with a leading `manifests/` and a
  trailing `/base` stripped. A chart whose name already looks like `org/name` is left alone
  by the `containsString depName '/'` branch. `{{prettyDepType}}` renders as `helmchart`.

The fixture mirrors the real layout, including the `base/` overlay directory, because the
template is written against exactly that shape:

```
manifests/cert-manager/kustomization.yaml         -> cert-manager
manifests/trust-manager/base/kustomization.yaml   -> trust-manager
```

```
$ renovate_check renovate-examples/pr-title --scope=full
```

Expected titles (the versions move, the shape does not):

```
chore(deps): update helmchart cert-manager/cert-manager to v1.21.x
chore(deps): update helmchart trust-manager/trust-manager to v0.2x.x
```

Needs network: version lookups hit `charts.jetstack.io`.
