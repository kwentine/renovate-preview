# jsonata customManager

Illustrates the `jsonata` customManager, which extracts a value via a JSONata query instead of
a regex — here `spec.version` out of a Grafana Operator custom resource.

```
$ renovate-preview examples/custom-manager-jsonata
```

Expected: a row for `grafana/grafana`, current `13.0.1`, from `grafana.yaml`.
