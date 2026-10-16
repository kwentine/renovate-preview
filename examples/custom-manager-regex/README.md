# regex customManager

Illustrates the `regex` customManager in its two flavours:

- `depname-template.yaml` — `currentValue` is captured by the regex, `depName` comes from a
  fixed `depNameTemplate`.
- `depname-captured.yaml` — both `depName` and `currentValue` are captured directly by named
  groups in the regex, no template needed.

```
$ renovate-preview examples/custom-manager-regex
```

Expected: rows for `example-org/example-operator` (current `v5.22.2`) and
`example-org/example-tool` (current `v2.19.1`).
