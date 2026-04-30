# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This repository is an early-stage scaffold for a "Telegram Token Bot". As of this writing, the project files exist as placeholders only:

- `BOT.py` — empty
- `server.py.` — empty (note the trailing dot in the filename; this is unusual and likely a typo from `server.py`. Confirm with the user before renaming, since the file is tracked in git history under this exact name)
- `requirements.txt` — empty
- `README.md` — contains only the project title

There is no source code, no tests, no build configuration, and no documented architecture yet. Treat any task here as greenfield work: ask the user for intent (what the bot should do, which Telegram library to use, whether `server.py` is meant to host a webhook receiver, etc.) before scaffolding non-trivial structure.

## Conventions inferred from the repo

- **Language**: Python (per `.gitignore`, `.py` files, and `requirements.txt`).
- **Standard Python `.gitignore`** is already in place — virtualenvs (`.venv`, `venv/`, `env/`), `__pycache__/`, `.env`, and common tool caches are ignored. Do not commit a `.env` file.
- **License**: MIT (see `LICENSE`, copyright `valentino500x`, 2025).

## Commands

No build, lint, or test tooling is configured yet. Once dependencies are added to `requirements.txt`, the standard workflow will be:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python BOT.py    # entry point (currently empty)
```

If the user asks to add lint/test tooling, propose it explicitly rather than assuming a stack.

## Branching

Per the harness configuration for this session, all development for documentation tasks happens on `claude/add-claude-documentation-H1GrI`. For other tasks, confirm the target branch with the user — `main` is the default branch but the user has historically merged work via short-lived `valentino500x-patch-*` branches (see `git log`).
