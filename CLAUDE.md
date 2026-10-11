## Agent skills

### Issue tracker

Issues live as GitHub issues in `bnogaro/gpx2roadbook`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout (`CONTEXT.md` + `docs/adr/` at the repo root). See `docs/agents/domain.md`.

## Commands

```bash
make dev        # uv sync --all-groups --all-extras + prek hooks
make lint       # ruff format --check, ruff check (every rule), ty check src/ tests/
make format
make test       # pytest, coverage ≥ 80%
make app        # downloadable app via PyInstaller (scripts/build_app.py), ZIP=1 to zip
make release    # opens the "bump: …" PR; merging it publishes to PyPI (DRY_RUN=1 for local only)
```

Python 3.12+, uv only (no pip). Single test: `uv run pytest tests/test_booklet.py -k name`.

## Layout

- `src/roadbook/build.py`: GPX → road book (stops, climbs, POIs, towns, hours); `api.py` (`run`, `preview`) is the entry for the CLI and GUI
- `render.py` + `templates/`: the strips and reference sheet (Jinja, typed view-model dataclasses)
- `booklet/`: `content.py` (what goes in), `layout.py` (spreads, pages), `__init__.py` (HTML, mini-maps); `imposition.py` holds the A5/A6 formats and sheet order
- `routemap.py`, `contours.py`: the SVG map
- `default.toml` + `config.py`: the settings, a dict where they are edited and saved (CLI flags, GUI, `--config` files), converted once by `api.run`/`api.preview` into the frozen `config.Settings`, whose sections (`MapSettings`, `BookletSettings`…) the run's functions take; `settings.py` describes them for the GUI form and the interactive CLI
- `gui/`: NiceGUI window (native via pywebview); `scripts/app.py` is the frozen app's entry point

## Conventions and gotchas

- Conventional Commits (commitizen hook). PRs are **rebase-merged**; never rewrite main's history.
- Refactors must not change output: diff `api.preview(...)` HTML over `samples/*.gpx`, before and after, under several settings (defaults, A6, booklet off, hours with a speed, climb pages on, topo map).
- A PR that changes a default strip's or the map's look regenerates `docs/img/strip-example.png` / `map.png`.
- GUI work: read and follow the installed `nicegui/llms.md`.
- On Windows, `uv sync` fails while `roadbook-gui.exe` runs (the file is locked): use another env (`UV_PROJECT_ENVIRONMENT=…`) or `uv run --no-sync`.
