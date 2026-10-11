---
name: readme-shots
description: Regenerate the README's images (docs/img/strip-example.png, map.png, booklet.png) from the working tree. Run in any PR that changes how a default strip, the map or the booklet looks.
disable-model-invocation: true
---

# README screenshots

The README is the project's shop window on GitHub and PyPI. Its images must show what the defaults make today.

```bash
uv run --no-sync --with pillow python .claude/skills/readme-shots/shots.py                    # all three
uv run --no-sync --with pillow python .claude/skills/readme-shots/shots.py strip map          # some
uv run --no-sync --with pillow python .claude/skills/readme-shots/shots.py booklet --pages 2 3
```

Each image comes from `samples/entrainement_ubf.gpx`, made by `api.run` with its lookups, as a user would get it. The script shoots it in the headless Chrome or Edge the road book prints with.

| Image | What | Size |
|---|---|---|
| `strip-example.png` | The first strip of the defaults, `details = false`: 35 × 200 mm at 192 dpi | 265 × 1512 px, shown at `width="220"` |
| `map.png` | The booklet's centre-spread map (the largest `svg.route-map`; the others are mini-maps): full width, 110 mm from y = 74, at 130 dpi | about 1438 × 563 px |
| `booklet.png` | Pages 2–3 of the A5 booklet, timed: `hours`, date 2026-10-11, start 07:30, 24 km/h | 150 dpi |

## After running

1. **Look at each image** (Read it). The palette step can go wrong:
   - `map.png` uses MAXCOVERAGE, because MEDIANCUT turns the green start disc grey;
   - `booklet.png` uses FASTOCTREE, because MAXCOVERAGE tints the greys of white pages pink.
2. **Check the pages:** `booklet.png` must show pages 2 and 3, with the mini-map on the left. If it shows other pages, adjust `--pages`.
3. **Update the `alt` text and captions** in `README.md` to what each image now shows.
4. **Commit the images in the same PR** as the change that altered the look.
