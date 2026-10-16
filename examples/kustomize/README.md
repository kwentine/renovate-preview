# kustomize manager

Renovate's `kustomize` manager reads `helmCharts[].version` out of a `kustomization.yaml`.
It needs no configuration at all — note that `renovate.json` here is empty — because
`kustomization.yaml` is one of the manager's default `managerFilePatterns`.

This is the manager that finds most of this repo's dependencies, and it is why
`manifests/*/kustomization.yaml` shows up throughout a top-level run.

```
$ renovate-preview examples/kustomize
```

Expected: `cert-manager`, current `v1.20.2`, from `kustomization.yaml`.
