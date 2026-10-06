.PHONY: dev lint format test audit build release

dev:
	uv sync --all-groups
	uv run prek install

lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check src/

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest

# audits the locked dependencies; the project itself is the local editable install, not a PyPI release, so skip it
audit:
	uv run --locked pip-audit --skip-editable

build:
	uv build

# Opens a "bump: …" PR for the next version; merging it publishes to PyPI. See scripts/release.py.
# make release DRY_RUN=1 bumps and commits on a local branch only, without pushing or opening a PR.
release:
	uv run python scripts/release.py $(if $(DRY_RUN),--dry-run,)
