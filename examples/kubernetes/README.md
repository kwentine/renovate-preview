# kubernetes manager

Illustrates Renovate's native `kubernetes` manager, which reads container `image:` references
straight out of Kubernetes manifests. It matches nothing by default, so it is usually enabled
for a handful of named files — which is what `managerFilePatterns` below does.

```
$ renovate-preview examples/kubernetes
```

Expected: a row for `busybox`, current `1.36`, from `daemonset.yaml`.
