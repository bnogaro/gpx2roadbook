.PHONY: dev lint format test build

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

build:
	uv build
