.PHONY: dev lint format test audit build

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

# audits the locked environment; the project itself is not on PyPI, so skip it
audit:
	uv run --locked pip-audit --skip-editable

build:
	uv build
