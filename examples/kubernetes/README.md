# kubernetes manager

Illustrates Renovate's native `kubernetes` manager, which reads container `image:` references
straight out of Kubernetes manifests — the same manager this repo's top-level `renovate.json`
enables for a handful of specific files (e.g. `manifests/sysctl-config/daemonset.yaml`).

Run from `helpers/renovate/`:

```
$ renovate_check renovate-examples/kubernetes
```

Expected: a row for `busybox`, current `1.36`, from `daemonset.yaml`.
