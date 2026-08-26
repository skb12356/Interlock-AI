# Interlock -- developer entry points.
# Docker was dropped (TODO.md P0.2); `up` supervises native processes instead.
# Windows users: the .ps1 twins in scripts/ are equivalent.

.PHONY: help install install-ml up down demo eval eval-guaranteed calibrate index lint fmt type test check clean

help:
	@echo "install  -- sync core+dev dependencies (no ml extra)"
	@echo "install-ml -- add the heavy ML extra (torch, transformers, presidio)"
	@echo "up       -- start gateway + observer + console, wait for healthy"
	@echo "down     -- stop them"
	@echo "demo     -- run the bank-support demo end to end"
	@echo "index    -- build the retrieval index over the corpus"
	@echo "calibrate-- fit isotonic calibration + certify a conformal threshold"
	@echo "eval     -- run the seeded eval set, Interlock off vs on, print six metrics"
	@echo "eval-guaranteed -- the same, with the conformal filter on"
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
	uv run python scripts/eval.py

# Guaranteed mode: the conformal filter strikes L0_pass above the certified
# threshold. Expect the false-intervention number to get worse -- that is the
# trade the guarantee costs, and seeing both is the point of having two targets.
eval-guaranteed:
	uv run python scripts/eval.py --conformal-filter --json artifacts/eval/report-guaranteed.json

calibrate:
	uv run python scripts/calibrate.py

index:
	uv run python scripts/build_index.py

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

type:
	uv run mypy --strict interlock/core interlock/retrieval interlock/interlock_tools

test:
	uv run pytest -q

check: lint type test

clean:
	uv run python -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	uv run python -c "import shutil; [shutil.rmtree(d, ignore_errors=True) for d in ('.mypy_cache','.ruff_cache','.pytest_cache')]"
