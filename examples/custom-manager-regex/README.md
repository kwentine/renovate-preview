# regex customManager

Illustrates the `regex` customManager in the two flavors used by this repo's top-level
`renovate.json`:

- `depname-template.yaml` — `currentValue` is captured by the regex, `depName` comes from a
  fixed `depNameTemplate` (mirrors the `grafana-operator` entry).
- `depname-captured.yaml` — both `depName` and `currentValue` are captured directly by named
  groups in the regex, no template needed (mirrors the `rabbitmq-operator` entry).

Run from `helpers/renovate/`:

```
$ renovate_check renovate-examples/custom-manager-regex
```

Expected: rows for `example-org/example-operator` (current `v5.22.2`) and
`example-org/example-tool` (current `v2.19.1`).
