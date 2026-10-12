---
name: output-diff
description: Render samples/*.gpx through api.preview under the standard settings matrix at a git ref (default main) and in the working tree, and diff the strips' and booklet's HTML. Use after any refactor that must not change the output, and before opening its PR.
---

# Output diff

A refactor in this repo is done when the road books it makes are byte-identical to `main`'s.

```bash
uv run --no-sync python .claude/skills/output-diff/output_diff.py            # against main
uv run --no-sync python .claude/skills/output-diff/output_diff.py HEAD~1     # against any ref
uv run --no-sync python .claude/skills/output-diff/output_diff.py --keep DIR # keep both sides' HTML in DIR
```

- **Each side:** the 3 samples × 11 settings: defaults, A6, booklet off, off + line layout, hours with a speed and a break, climb pages, topo without contours, checkpoints every 50 km, two POI categories, French (with hours and climb pages), and French again without a booklet (its reference sheet, climb pages and checkpoints). That's 33 strips files and 24 booklets, 57 in all.
- **Pinned:** the ref's `roadbook` package is checked out in a temporary worktree and put first on `PYTHONPATH`. The environment, the samples and the caches are the working tree's, so only the code differs.
- **Offline:** `api.preview` asks nothing of OpenStreetMap, so both sides read the same caches.
- **Exit code:** 0 if every file is identical, 1 otherwise, with the first lines that differ in each file; 2 if a render failed.
- **Time:** both sides render at once, about 10 minutes, so run it in the background.

## When it differs

- **A refactor should leave nothing different.** Find the cause, don't explain it away.
- **For a change meant to alter the output**, read the diffs and check each one is intended. Then say in the PR which files and settings changed, and why.
- **Settings the matrix misses:** if the change touches one, add a line for it to `CONFIGS` in the script and commit the change.
- **The CLI path** (`roadbook samples/x.gpx …` without the GUI) goes through `api.run` and isn't covered here. Diff it by hand when `cli.py` changes.
