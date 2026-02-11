# AGENTS.md

## Development Environment

This project uses **Nix flakes** for reproducible development environments. All tooling (Python, black, flake8, pytest) is pinned via `flake.nix` + `flake.lock` to ensure identical versions locally and in CI.

### Setup

```bash
# Enter the dev shell (installs all tools automatically)
nix develop

# Install Python dependencies
uv sync

# You're ready to go
```

### Common Commands

All commands should be run inside `nix develop`:

```bash
# Run the MCP server (stdio mode)
uv run python main.py

# Run tests
uv run python -m pytest -v

# Check formatting
black --check .

# Auto-format
black .

# Lint
flake8 .

# Run with Docker
docker-compose up --build
```

### CI

CI workflows use `nix develop --command ...` to run the same pinned tools. This means:
- **No version drift** between local and CI
- **No `pip install`** in CI workflows — everything comes from nix
- Formatting, linting, and tests all use the exact same tool versions

### Key Files

- `flake.nix` — Dev shell definition, pinned tools
- `flake.lock` — Locked nixpkgs revision (determines exact tool versions)
- `pyproject.toml` — Python project config, black/flake8 settings
- `.github/workflows/` — CI pipelines (all use nix)

### Rules

- **Always use `nix develop`** for local development — don't install black/flake8/pytest via pip
- **Don't update `flake.lock`** without running tests locally first (`nix flake update && uv run python -m pytest -v`)
- **Run `black --check .`** before pushing — CI will reject unformatted code
- **Test files** go next to the code they test, prefixed with `test_`
