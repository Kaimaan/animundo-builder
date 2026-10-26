# Animundo Builder

Open-source agents that turn a real natural area into a playable place for
Animundo, a nature game where children play real animals in real places.
Built with Muse Code for the Meta Global AI Developer Hackathon
(Oct 26 to Nov 4, 2026).

**Status:** day one. This repository is a skeleton; the pipeline, the Builder
mode addon and the rules are being built task by task. The full README,
written for the hackathon judges, lands with the finished work.

## Layout

```
builder/    # the Python package: CLI, worker, pipeline, stages, tools
tests/      # pytest suite; no paid API calls in unit tests
docs/       # design docs, written as each part lands
```

Later tasks add `prompts/`, `schemas/`, `contracts/`, `firebase/`,
`godot/addons/animundo_builder/`, `examples/` and `evals/`.

## Models

Every model call goes to a Meta model through `builder/meta.py`, the only
file that touches the network for models. Transport is the OpenAI-compatible
Python client pointed at `https://api.meta.ai/v1` (Meta's own documented
pattern); no OpenAI services, keys, or data are used anywhere.

## Setup and checks

Prerequisites: `uv`, Python 3.12, `gitleaks`.

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
gitleaks git --no-banner
uv run builder check --world ../animundo-world
./scripts/install-hooks   # once per clone: scan before every commit
```

All checks must pass before a task counts as done. See `AGENTS.md` for the
rules coding agents follow here.

## Secrets

This repository is public from its first commit. Never commit API keys,
tokens, `.env` files, service-account JSON, job folders, downloaded pages or
photos, or anything about a player or a family. Recorded API responses are
scrubbed before they are committed. The secret scan runs before every commit
and in CI; a finding stops the commit.
