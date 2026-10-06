## v0.2.0 (2026-10-06)

### Feat

- **cli**: pick interactive settings from menus and checklists with questionary
- **cli**: add -i/--interactive to ask for the settings step by step
- **render**: wrap a crowded stop's emojis onto extra lines
- **cli**: also install the command as gpx2roadbook

### Fix

- **release**: run the release steps from a Python script, not shell recipe lines
- **release**: name the bump branch after the new version and commit the changelog

## v0.1.0 (2026-10-06)

### Feat

- **climbs**: only count ascents gaining 80 m as climbs, with --min-climb
- **climbs**: merge climbs split by a short, shallow dip
- **render**: put the leg distance on the row's main line
- **render**: make per-leg climbing/descent opt-in
- **climbs**: snap stops at a climb's foot or summit onto that climb
- **svg**: draw a row-aligned elevation gutter down each strip
- **svg**: render a mini elevation profile with climb shading

### Fix

- **render**: keep a crowded stop's first emoji on a climb or summit row
- **stops**: let --gap grow stops, keep one spot together, show long stops' range
- **climbs**: snap a stop to its next free climb edge
- **render**: keep a snapped stop visible when max_emojis is 1

### Refactor

- bundle row layout settings and add Profile.ele_range
- give emoji superscripts their own field
- one definition per item kind
- fix remaining ruff and ty findings
