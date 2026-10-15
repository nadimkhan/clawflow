# Contributing

Thanks for your interest in `clawflow`. The project is small on purpose —
a working v0.1 ships in ~1,500 lines of Python so you can read it in an
evening and have confidence your CLI isn't doing anything shady with
your tokens.

## Ground rules

1. **One PR per concern.** A typo and a refactor do not belong in the
   same PR.
2. **No surprise dependencies.** If you want to add a third-party
   package, justify it in the PR description. The current budget is
   `httpx`, `python-dotenv`, `feedparser`, `PyYAML`, and `pytest`. We
   are not adding `rich`, `typer`, `pydantic`, or `click`.
3. **Network calls belong in sources.** Anything that talks to a remote
   endpoint lives under `clawflow/sources/`. Test mode never makes real
   HTTP requests — use a fake `httpx` transport.
4. **Tests for behaviour changes.** If you change `clawflow run`
   semantics, add a smoke test. We aim for 80% line coverage on the
   core package.
5. **No secrets in commits.** The `.gitignore` already excludes
   `clawflow.yml`, `state.json`, and `.env`. Don't bypass it.

## Local dev setup

```bash
git clone https://github.com/nadimkhan/clawflow.git
cd clawflow
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
clawflow --help              # confirms the entry point wires up
```

## Coding style

- Python 3.11+ syntax (`from __future__ import annotations`,
  `if __name__ == "__main__":` guards in every file).
- `ruff check .` must pass. `ruff format .` is the canonical formatter.
- Public functions and modules get docstrings; private helpers can
  skip them if the name is self-evident.
- Type hints throughout. `Any` is allowed at the boundary with httpx
  responses, nowhere else.

## Adding a new source

See [`docs/sources.md`](docs/sources.md#adding-your-own). The short
version:

```python
# clawflow/sources/community/my_source.py
from clawflow.sources import Source
from clawflow.leads import Lead

class MySource(Source):
    id = "my_source"
    async def collect(self):  # -> AsyncIterator[Lead]
        raise NotImplementedError("TODO: implement")
```

Register by importing it from `clawflow/sources/__init__.py`. The
registry picks it up automatically.

## Release process

1. Bump version in `pyproject.toml` and `CHANGELOG.md`.
2. Tag `vX.Y.Z`. CI publishes to PyPI via trusted publishing.
3. Push the tag; GitHub Releases is generated from `CHANGELOG.md`.

## Code of conduct

Be kind. Disagree on substance, not on people. We're all freelancers
here.

## License

By contributing you agree your contributions are licensed under the
project's MIT license.