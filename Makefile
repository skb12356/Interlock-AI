# Interlock -- developer entry points.
# Docker was dropped (TODO.md P0.2); `up` supervises native processes instead.
# Windows users: the .ps1 twins in scripts/ are equivalent.

.PHONY: help install up down demo eval lint fmt type test check clean

help:
	@echo "install  -- sync core+dev dependencies (no ml extra)"
	@echo "install-ml -- add the heavy ML extra (torch, transformers, presidio)"
	@echo "up       -- start gateway + observer + console, wait for healthy"
	@echo "down     -- stop them"
	@echo "demo     -- run the bank-support demo end to end"
	@echo "eval     -- run the seeded eval set, Interlock off vs on, print six metrics"
	@echo "check    -- lint + type + test (what CI runs)"

install:
	uv sync --group dev

install-ml:
	uv sync --group dev --extra ml

up:
	@pwsh -NoProfile -File scripts/up.ps1 2>/dev/null || powershell -NoProfile -File scripts/up.ps1

down:
	@pwsh -NoProfile -File scripts/down.ps1 2>/dev/null || powershell -NoProfile -File scripts/down.ps1

demo:
	@echo "TODO(D1-A5): bank support assistant end-to-end demo"

eval:
	@echo "TODO(D3-B7): seeded eval set, off vs on, six metrics"

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

type:
	uv run mypy interlock/core

test:
	uv run pytest -q

check: lint type test

clean:
	uv run python -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	uv run python -c "import shutil; [shutil.rmtree(d, ignore_errors=True) for d in ('.mypy_cache','.ruff_cache','.pytest_cache')]"
