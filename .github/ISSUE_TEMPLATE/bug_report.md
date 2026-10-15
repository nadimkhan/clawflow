---
name: Bug report
about: Something in clawflow misbehaved and you want it fixed
labels: ["bug", "triage"]
---

## Describe the bug

<!-- A clear, short description. -->

## To reproduce

<!--
The exact commands you ran. Include the contents of `clawflow.yml` minus
any secrets. If it depends on a specific source, say which one.
-->

```bash
clawflow init
# ... edited clawflow.yml ...
clawflow run --once
```

## Expected behaviour

<!-- What you expected to happen. -->

## Actual behaviour

<!-- What actually happened. Paste the full traceback if there was one. -->

## Environment

- clawflow version: `clawflow --version` (or `pip show clawflow`)
- Python version: `python --version`
- OS:
- Source(s) enabled:
- Alerter(s) enabled:

## Logs

<!--
If you can, attach the output of `clawflow run --once --log-level DEBUG`
(masked of any secrets). Use a code block; don't paste into the issue
title.
-->

```text
paste here
```