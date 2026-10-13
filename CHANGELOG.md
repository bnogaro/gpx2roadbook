## v0.7.0 (2026-10-13)

### Feat

- **booklet**: water and food stretches in their own bands over the cover's profile
- **render**: the strips, reference sheet, climb pages and map page in French
- **booklet**: the booklet in French
- **i18n**: name a date in full, as the booklet heads a new day
- **gui**: a switch to French, for the window's own words and its settings
- **i18n**: a language for the road book, and the catalogs to word it in
- **booklet**: list the cover's warnings from km to km, shaded on its profile
- **app**: a downloadable app for Windows and macOS, no Python or terminal needed

### Fix

- **render**: figures in the text font, not the emoji font, where Segoe UI is missing
- **render**: a checkpoint's countdown on a sub line, where it fits
- **render**: French "segment" for a climb profile's slab
- **i18n**: French dates read "1er novembre", "5 oct.", not "01", "05"
- **gui**: French "arrêts denses" for busy stops
- **gui**: a shorter French date label, which an empty field shows whole

### Refactor

- **render**: split render.py into strips, sheet and climbs
- **booklet**: read the shops' hours and climb cards typed
- **config**: type the settings a run reads, once, as it starts
- **render**: the strips, reference sheet and climb cards typed
- **booklet**: its content, layout and HTML apart, its pages typed

## v0.6.0 (2026-10-11)

### Feat

- **booklet**: a mini-map for each spread, and the overview without POIs
- **booklet**: the stops, climbs and map as an A5 or A6 booklet to fold and take on the ride
- **map**: contour lines with their heights, from Mapzen's Terrain Tiles
- **map**: a page with a map of the whole route, its POIs, towns and climbs
- **climb-pages**: cut a short climb into 500 m or 250 m slabs
- climb pages, each climb's profile a kilometre at a time
- **gui**: fit phones and narrow windows, and serve phones on the network
- **gui**: a live preview of the road book, from the cache only
- **gui**: remember the last settings, reset them, or save them to share
- **gui**: a window to pick a GPX, set the options and make the road book
- **hours**: leave a blank to write in the hours OSM doesn't know

### Fix

- **gui**: the run's log shows only the run's steps, not a preview's built meanwhile
- **gui**: name the road book's buttons Strips, as it holds the strips alone with a booklet

### Refactor

- one run function for the CLI and a GUI
- describe each setting once, for the CLI, -i and a GUI

## v0.5.0 (2026-10-09)

### BREAKING CHANGE

- Python 3.11 is no longer supported. Installing with uv
fetches a newer Python by itself; with pipx, use a Python 3.12 or later.

### Feat

- **towns**: join the small stops at a town's edges to it
- **render**: frame each named town with its row on the strip
- **towns**: make one stop of each town, whatever --gap says
- **render**: show where a long stop ends in the km column

### Refactor

- use Python 3.12 type aliases, @override and nested f-string quotes

### Build

- require Python 3.12

## v0.4.0 (2026-10-08)

### BREAKING CHANGE

- --leg-elevation and [render] leg_elevation are gone.
- --max-offset and [pois] max_offset_m are gone; set the
distance in onroutemap.de instead. A config file that still sets
max_offset_m is not refused: the key is ignored.

### Feat

- **climbs**: optionally close every climb with a summit row
- **pois**: keep every POI of the export, without --max-offset
- **climbs**: colour climbs by gradient

### Refactor

- **render**: drop --leg-elevation and each leg's climbing and descent

## v0.3.0 (2026-10-07)

### Feat

- warn when a route has no elevation, or arrival times have no date
- **cli**: say what it does with -v, -vv and -vvv
- **cli**: look up opening hours, towns and climb names again with --refresh
- **towns**: name the towns at busy stops by default
- **interactive**: ask for planned breaks along with the arrival times
- **cli**: print the version with --version
- **ride**: slow the arrival estimate down on climbs and add planned breaks
- **climbs**: name climbs by default
- **climbs**: name cols, passes and peaks at climb summits
- **towns**: also name villages: 5 POIs or more when one is a bakery or grocery
- **towns**: right-align town lines; let the finish overrun the last strip
- **towns**: name the town on a line of its own above the stop's row
- **towns**: name the town at busy stops
- **render**: watermark the strips and the reference sheet
- **hours**: estimate arrival at each stop and judge shops open, closed or tight
- **hours**: show each shop's hours on the ride day with --date
- **hours**: add shops' opening hours from OpenStreetMap to the reference sheet

### Fix

- **interactive**: ask for arrival times without opening hours
- **cli**: report a bad --checkpoint or a failed PDF export instead of crashing
- say nothing about OSM lookups that had nothing to find
- **render**: never cut a climb's gain on its stats line
- **hours**: only match a shop to an OSM place of a fitting kind

### Refactor

- **render**: merge the strip and ribbon CSS rules split across features
- drop profile_svg and _fit, which only their tests still used
- **osm**: share the lookups' report, Overpass and Nominatim calls

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
