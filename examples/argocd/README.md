# argocd manager

Illustrates Renovate's native `argocd` manager, which reads `spec.source.targetRevision`
(a pinned Helm chart version) out of an ArgoCD `Application` resource. In a GitOps repo it is
typically enabled for `manifests/**/*.yaml`.

```
$ renovate-preview examples/argocd
```

Expected: a row for `example-chart`, current `1.2.3`, from `application.yaml`.
