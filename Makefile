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

# Opens a "bump: …" PR for the next version, worked out by commitizen from the Conventional Commits since the last
# tag; merging it publishes to PyPI (.github/workflows/release.yml). main only takes PRs, so nothing is tagged here.
# The version is read by the shell after the bump: make would expand $(shell …) before running any line of the recipe.
# PUSH and GH can be set to `true` to try it without touching GitHub: make release PUSH=true GH=true
PUSH ?= git push
GH ?= gh
release:
	@test -z "$$(git status --porcelain)" || { echo "make release needs a clean working tree"; exit 1; }
	git switch main && git pull --ff-only
	uvx --from commitizen cz bump --files-only --changelog --yes
	version="$$(uv version --short)" && 	git switch -c "release/v$$version" && 	git add pyproject.toml uv.lock CHANGELOG.md && 	git commit -m "bump: version $$version" && 	$(PUSH) -u origin "release/v$$version" && 	$(GH) pr create --base main --title "bump: version $$version" 		--body "Release v$$version. Merging publishes it to PyPI and creates the tag and GitHub Release."
