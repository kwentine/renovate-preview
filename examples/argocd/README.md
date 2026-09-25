# argocd manager

Illustrates Renovate's native `argocd` manager, which reads `spec.source.targetRevision`
(a pinned Helm chart version) out of an ArgoCD `Application` resource — the same manager this
repo's top-level `renovate.json` enables for `manifests/**/*.yaml`.

Run from `helpers/renovate/`:

```
$ renovate_check renovate-examples/argocd
```

Expected: a row for `example-chart`, current `1.2.3`, from `application.yaml`.
