# MISRA baseline

The configured baseline is `misra-baseline.json`. It is intentionally absent after `misch init --scaffold`: creating a baseline explicitly accepts the project's current finding counts.

After reviewing the initial report, run:

```sh
misch baseline
misch run --baseline
```

Commit the resulting JSON file so CI can reject findings above the accepted counts. Regenerate it only after a deliberate review; do not edit it by hand.
