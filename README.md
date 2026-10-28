# Animundo Builder

Open-source tools that grow Animundo's world ahead of the children who
explore it. Animundo is a nature game where children play real animals in
real places. Built with Muse Code for the Meta Global AI Developer Hackathon
(Oct 26 to Nov 4, 2026).

The grown-up builder is gone. Every grown-up-built place was a separate
world and a separate decision; nature changes slowly, so most land is now
made by code from content already reviewed, and agents build only where
children reach the edge of the known world.

The builder is a factory: it is given work, says what the work needs and
costs, makes it, and reports back. It holds no policy and no game code;
the game runs the world. No web pages, no game addon, no Firestore rules.

**Status:** phase 0. The contracts are schemas, the grown-up builder is
removed, and the tile maker lands next. The full README, written for the
hackathon judges, lands with the finished work.

## Layout

```
builder/    # the Python package: CLI, worker, pipeline, stages, tools
contracts/  # the shared contracts as JSON Schemas
schemas/    # the agents' working schemas
tests/      # pytest suite; no paid API calls in unit tests
docs/       # design docs, written as each part lands
firebase/   # Storage rules with emulator tests (Firestore rules are the game's)
data/       # baked data: the habitat grid, well-known places
```

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
