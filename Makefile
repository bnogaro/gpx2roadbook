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
release:
	git switch main && git pull --ff-only
	uvx --from commitizen cz bump --files-only --changelog --yes
	$(eval VERSION := $(shell uv version --short))
	git switch -c release/v$(VERSION)
	git commit -am "bump: version $(VERSION)"
	git push -u origin release/v$(VERSION)
	gh pr create --base main --title "bump: version $(VERSION)" --body "Release v$(VERSION). Merging publishes it to PyPI and creates the tag and GitHub Release."
