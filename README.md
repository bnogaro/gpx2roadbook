# gpx2roadbook

[![PyPI](https://img.shields.io/pypi/v/gpx2roadbook)](https://pypi.org/project/gpx2roadbook/)
[![Python](https://img.shields.io/pypi/pyversions/gpx2roadbook)](https://pypi.org/project/gpx2roadbook/)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](https://github.com/bnogaro/gpx2roadbook/blob/main/LICENSE)

`gpx2roadbook` (command: `roadbook`) turns a GPX route with points of interest (water, food, toilets, …) into a compact road book you can print.
You cut the strips out and tape them to your bike's top tube. Each one lists the stops, climbs and distances for a stretch of the ride, next to a small elevation profile.

<img src="https://raw.githubusercontent.com/bnogaro/gpx2roadbook/main/docs/img/strip-example.png" alt="A printed strip covering km 0 to 60.7: stops with water, toilet, food and shop emojis; the town names Saint-Béat-Lez, Saint-Lary and Saint-Girons above their stops; the Col de Menté and Col de Portet d&#39;Aspet above their climb rows, with length, grade and gain, and a summit row; and an elevation profile down the right edge, its climbs coloured from yellow to dark red by grade" width="220">

*The first strip of [`samples/entrainement_ubf.gpx`](https://github.com/bnogaro/gpx2roadbook/blob/main/samples/entrainement_ubf.gpx), with the default settings, shown here at about twice its printed size. On paper it is 35 mm wide. Town and col names come from OpenStreetMap; the steeper a climb, the darker its colour.*

## Getting a GPX with POIs

The easiest source is [onroutemap.de](https://onroutemap.de). It is free and needs no account. Upload your route's GPX ("Upload GPX file") and set the maximum distance to the route: `roadbook` keeps every POI of the export, so that distance is the one that counts. It then finds supermarkets, bakeries, cafés, drinking water, toilets, fuel stations, fast food and more along the way. Use the download button (top left of the map) and choose the export with **the route and every discovered POI**, as GPX. The routes in [`samples/`](https://github.com/bnogaro/gpx2roadbook/blob/main/samples/) were made this way.

Any other GPX works if it has:

- **a track or a route**, ideally with elevation (without elevation you get no climbs and no profile);
- **waypoints** for the POIs, each with a `<type>` or a `<name>` that one of the categories in [`src/roadbook/default.toml`](https://github.com/bnogaro/gpx2roadbook/blob/main/src/roadbook/default.toml) can match. For example, a type `Boulangerie` or `bakery` becomes 🥖, and `Eau potable` or `drinking water` becomes 🚰. Matching ignores case and accents. Waypoints that match no category are dropped.

## Install

You need:

- [uv](https://docs.astral.sh/uv/), which installs Python and the tool for you (or [pipx](https://pipx.pypa.io/));
- Google Chrome or Microsoft Edge, only if you want `--pdf`.

Then:

```sh
uv tool install gpx2roadbook     # or: pipx install gpx2roadbook
```

This puts the `roadbook` command on your PATH (`gpx2roadbook` works too, it is the same command). To update to the latest release:

```sh
uv tool upgrade gpx2roadbook     # or: pipx upgrade gpx2roadbook
```

`roadbook --version` tells you which version you have.

To try it once without installing anything:

```sh
uvx gpx2roadbook my_ride.gpx
```

## Usage

```sh
roadbook my_ride.gpx
```

This writes `my_ride.roadbook.html` next to the GPX and prints a short summary: distance, climbing, and how many POIs, stops and climbs were found. To write it somewhere else, use `-o`:

```sh
roadbook my_ride.gpx -o ~/Desktop/ride.html
```

### Interactive mode

Not sure which options to use? Let `roadbook` ask:

```sh
roadbook -i                 # or: roadbook -i my_ride.gpx
```

It asks for the GPX file (if you didn't give one, with Tab to complete the path), then the layout and paper size (pick with the arrow keys), checkpoints, whether to name towns and to look up opening hours, then the ride date, start time, speed and planned breaks for arrival times (leave the date empty to skip them), the POI categories to show (tick them with Space), PDF and output file. Each starts on its default, so Enter keeps it. A last checklist shows the advanced settings with their current values (strip size, how POIs are grouped into stops, smallest climb, emoji lines, reference sheet, arrival margin, climb names, summit rows); tick the ones to change. Options given on the command line become the defaults. It needs a real terminal (on Windows: Windows Terminal, PowerShell or cmd).

At the end it prints the equivalent command, e.g. `Same as: roadbook my_ride.gpx --page A5 --min-climb 50`, so next time you can run it straight away.

### Printing

Open the HTML in your browser and print it. Print at **100 % scale**, not "fit to page", so the strips keep their real size in millimetres. If the coloured rows come out white, turn on "background graphics".

Or let `roadbook` make the PDF for you. It drives Chrome or Edge in the background and writes `my_ride.roadbook.pdf` next to the HTML:

```sh
roadbook my_ride.gpx --pdf
```

Either way, cut along the dashed lines.

### Layouts

- **`--layout strip`** (default): vertical strips, 35 mm wide and 200 mm long, read top to bottom. One row per stop or climb, with an elevation profile down the right edge. Made for the top tube.
- **`--layout line`**: horizontal ribbons, 16 mm high, read left to right. One small block per stop or climb, with short climb stats and no profile. Use it when a 35 mm strip is too wide for where you want to tape it.

### Common options

| Option | What it does |
| --- | --- |
| `--width MM`, `--length MM` | Size of each strip: the short side (default 35 for `strip`, 16 for `line`) and the long side (default 200). |
| `--page A4` | Paper size, e.g. `A4`, `A5`, `Letter`. |
| `--checkpoint-every KM` | Add a checkpoint row every N km (🚩 `CP1 · 120 to go`). |
| `--checkpoint KM:LABEL` | Add one checkpoint, e.g. `--checkpoint 87.5:Lunch`. Repeat it for more. |
| `--categories water,bakery,toilets` | Keep only these POI categories. The names are the `[categories.*]` sections of `default.toml`. |
| `--min-climb M` | Only count ascents gaining at least this many metres as climbs (default 80). Lower it to see smaller rises. |
| `--gap M` | Merge POIs closer than this many metres into one stop (default 500). |
| `--max-span M` | The longest a stop may stretch, in metres (default 3 × `--gap`). Longer runs of POIs are split into several stops, never between POIs at the same spot. |
| `--emoji-lines N` | How many lines a stop's emojis may fill on a strip (default 2). Use 3 to see every kind of POI at busy stops, or 1 for the shortest road book. |
| `--no-details` | Leave out the reference sheet with the POI names. |
| `--no-towns` | Leave out the names of the towns at busy stops. They are on by default, from OpenStreetMap (see [Town names](#town-names)), and need internet the first time. |
| `--no-climb-names` | Leave out the names of the cols, passes and peaks climbs top out at. They are on by default, from OpenStreetMap (see [Climb names](#climb-names)), and need internet the first time. |
| `--hours` | Add shops' opening hours to the reference sheet, from OpenStreetMap (see [Opening hours](#opening-hours)). Needs internet the first time. |
| `--date YYYY-MM-DD` | With `--hours`: show each shop's hours on your ride day only, or **closed**. |
| `--start HH:MM`, `--speed KM/H` | With `--date`: estimate when you reach each stop, and say whether its shops will be open then (see [Arrival times](#arrival-times)). |
| `--margin PCT` | How far off those estimates may be, as a share of the time ridden (default 15 %, at least 20 min). |
| `--climb MIN` | Minutes the estimate adds per 100 m climbed (default 5). `0` treats the route as flat. |
| `--break KM:MINUTES` | A planned break, e.g. `--break 95:45` for lunch at km 95. It delays every stop after it. Repeat it for more. |
| `--refresh` | Look up opening hours, town names and climb names again, instead of using the answers cached from earlier runs. |
| `-i`, `--interactive` | Ask for the settings step by step (see [Interactive mode](#interactive-mode)). |
| `-v`, `-vv`, `-vvv` | Say what it does: `-v` each step, why POIs were left out and how long the lookups took; `-vv` each stop, climb, shop, town and climb name found; `-vvv` every request to OpenStreetMap too. |
| `--config my.toml` | Override any setting, see below. |

For the full list:

```sh
roadbook --help
```

## Customising

Every setting lives in [`src/roadbook/default.toml`](https://github.com/bnogaro/gpx2roadbook/blob/main/src/roadbook/default.toml). Don't edit it. Write a small TOML file with only the keys you want to change and pass it with `--config`. It is merged over the defaults, table by table.

For example, to show hotels and campsites, snap stops onto climbs from further away, and widen the profile:

```toml
# my.toml
[pois]
# a list replaces the default list entirely, so repeat the categories you want to keep
enabled = ["water", "toilets", "bakery", "cafe", "food", "grocery", "lodging"]

[climbs]
snap_m = 500      # default 300

[render]
gutter_mm = 6     # default 4; 0 turns the profile off
```

```sh
roadbook my_ride.gpx --config my.toml
```

The climb colours are `climbs.grade_colours`, a list of `[grade, colour]` steps: each colour applies from its grade up, in %.

To add a category of your own, add a table and put its name in `pois.enabled`. The order of the tables sets which emoji comes first when a row is short of space:

```toml
[categories.bike]
emoji = "🔧"
match = ["bicycle", "velo", "cycle shop"]
```

## How to read the road book

Each strip has a dark header with its km range and its number (`2/3`). The tool's name runs in small grey letters up its bottom-left edge (up the right end of a `line` ribbon), in space no row uses. Below the header, one row per point on the road:

- **The bold number** is the km where the row sits. **`↓4.3`** at the right is the distance to the next row; the profile on the right shows the climbing and descent on the way.
- **🟢 START, 🏁 FINISH, 🚩 CP…** are the start, the finish and checkpoints, on a blue background.
- **Emojis** are the POIs at that stop. A small number after one (🚰²) says how many POIs of that kind are there. When a stop has more kinds than fit beside its km, they carry on a line below (up to `--emoji-lines`). A **`+`** means there are still more kinds than fit. They are all listed on the reference sheet.
- **A second, lighter km** under a stop's km, joined to it by dots, is where the stop ends: its POIs stretch from the first km to the second, e.g. 122.4 to 129.1. Stops longer than 1 km (`render.stop_range_m`) show it, in a town or with a large `--gap`. It sits on the stop's second line of emojis when it has one, or else on a short line of its own. On a `line` ribbon, a long stop's token says `→129.1` instead.
- ***Saint-Girons*** in italics, at the right above a row, is the town that stop is in (see [Town names](#town-names)).
- **⛰️ rows** (orange) mark the foot of a climb. The bar down their left edge is in the colour of the climb's average grade (see below). The line below gives its length, average grade and total gain, e.g. `4.3km 2.6% ↗110`, or `21km 7.4% ↗1552` from 10 km on. The gain sits at the right, under the distances, and is never cut: on a narrow strip, the length and grade give way first. **`Cat 4`** … **`Cat HC`** is its category, scored as length × grade, the same way as the Tour de France. Small climbs have no category. Ascents gaining less than 80 m (`--min-climb`) are not shown as climbs at all.
- **Col de Portet d'Aspet** in brown, at the right above a ⛰️ row, is the col or peak that climb tops out at (see [Climb names](#climb-names)).
- **Stops on a climb.** A stop within 300 m (`climbs.snap_m`) of a climb's foot or summit is merged into that row rather than given a row of its own. A ⛰️ row with a stop on it has no room for "Cat 4", so the category becomes a small superscript on the mountain (⛰️⁴ 🍔).
- **🔝 rows** mark a summit. They only appear when a stop sits at the top; the profile already shows every other summit. `--summit-rows` gives every climb one, to see each top's elevation at the cost of a row per climb. If there is room, the row also gives the summit's elevation. A 🔝 row closes its climb with an orange line.

### The profile on the right

The narrow strip on the right edge of each strip is the elevation profile of that stretch. Elevation grows to the right.

- **Climbs are coloured by grade**, a kilometre at a time, so the steep ones show where they are: yellow under 3 %, then orange (3–6 %), dark orange (6–9 %), red (9–12 %) and dark red from 12 %. **Grey** is everything else. The colours get darker as the road steepens, so they still read printed in black and white. A key at the end of the reference sheet recalls them, and each named climb there has a square in the colour of its average grade.
- **Each dot** lines up with a row, so you can see where each stop is on the profile.
- **Distance on the profile is not to scale.** The profile is stretched to fit between the rows, so a 10 km gap between two rows takes the same space as a 1 km gap. Use it to see the shape of the climbs, not to measure distance.
- **Elevation is scaled for each strip separately.** A big slope on one strip is not the same height as a big slope on the next. Very flat stretches are kept flat, so small rolls don't look like mountains.

### The reference sheet

After the strips comes a page listing every stop by km (as a range, e.g. `km 122.4 → 129.1`, for long stops), with the name of each POI grouped by emoji. When a POI is more than 30 m off the route, its distance from the route is shown in brackets, e.g. `Intermarché Super (265 m)`. Keep it in a pocket, or leave it out with `--no-details`. A footer line at its end says which version of gpx2roadbook made it and where to get it.

### Town names

A stop grouping many POIs is usually a town, so it gets the town's name: easier to remember than a km, and handy to talk about the plan ("lunch in Saint-Girons"). `--no-towns` leaves them out.

```text
km 60.0 → 61.5 · Saint-Girons
🚰 Eau potable (124 m), Eau potable (146 m)
🚻 Toilettes (183 m), Toilettes, Toilettes (209 m)
🥖 Boulangerie, Le Blé Doré (234 m), Pâtisserie Boissonnot, …
```

- **Which stops.** Those with at least 8 POIs (`towns.min_pois`), or at least 5 when one is a bakery or a grocery (`towns.shop_min_pois`, `towns.shops`), counting only the categories you show: a town centre, or a village with its shop, not a fountain, a cemetery tap or a lone bakery.
- **One stop per town.** A town's POIs often fall into several stops, split by `--gap`. Its named stops, and the unnamed ones between them, are merged into one, whatever `--gap` says: Le Mans is one row from km 264.8 to 269.4 rather than three. Only stops up to 5 km apart join (`towns.group_km`; 0 never groups), since a route may come back through a town later. The small stops just outside a town's named ones, up to 2 km (`towns.edge_km`), are asked which town they are in, and join it if it's the same: the supermarket on the way out of Saint-Girons does, the next village's bakery doesn't. That's one more request, once, for each such stop.
- **On the strip**, the name gets a line of its own above the stop's row, in italics and right-aligned, so the km down the left read on undisturbed. A thin frame, open on the right, holds the name, the row and the town's range together. That line takes 3.2 mm, so a strip holds a little less. When two busy stops in a row are in the same town but too far apart to be one, only the first names it.
- **The finish never ends up alone.** If the last row doesn't quite fit, the last strip runs up to 6 mm longer rather than start a new strip for it.
- **On the reference sheet**, every busy stop has its name next to its km.
- **Where it comes from.** The GPX has no place names, so each busy stop asks [Nominatim](https://nominatim.org) (OpenStreetMap) which city, town or village its middle POI is in. That is one request per busy stop, one per second: a few seconds for most routes.
- **Then it's instant.** Answers are cached on your computer for a year (`towns.max_age_days`); `--refresh` looks them up again. If Nominatim doesn't answer, you get a warning and a road book without the missing names.

### Climb names

A climb is easier to recognise, and to talk about, by its name. Each climb that tops out at a named col, pass or peak gets that name (`--no-climb-names` leaves them out):

```text
km 25.6 → 29.9 · Col de Portet d'Aspet
⛰️ Cat 2 · 4.3 km at 9.6 % · ↗️408 m
```

- **On the strip**, the name gets a line of its own above the climb's ⛰️ row, the row at its foot: it announces the climb before you start it, right above its length and grade. It is right-aligned and in the climbs' brown, and takes 3.2 mm like a town line. The 🔝 summit row isn't used, as it only shows when a stop sits at the top. A climb starting at a busy stop in a named town gets both lines: the town, then the climb.
- **On the reference sheet**, each named climb gets an entry of its own, at its foot's km between the stops: its name, category, length, grade and gain.
- **Which name.** OpenStreetMap places tagged as a mountain pass (`mountain_pass=yes`), a saddle (`natural=saddle`) or a peak (`natural=peak`), with a name, near the climb's top. A pass or saddle within 300 m (`climb_names.pass_m`) wins, since the road crosses it; else a peak within 200 m (`climb_names.peak_m`), since the road only skirts one: the nearest peak is often a summit off to the side, not where the road tops out. Then the closest. Most small rises have neither, and get no name.
- **Where it comes from.** One [Overpass](https://overpass-api.de) request for the whole route; if its servers are busy, a mirror, then [Nominatim](https://nominatim.org), up to three requests per climb, one per second.
- **Then it's instant.** Answers are cached on your computer for a year (`climb_names.max_age_days`); `--refresh` looks them up again. If OpenStreetMap doesn't answer, or you're offline, you get a warning and a road book without the missing names.

### Opening hours

With `--hours`, each bakery, grocery, petrol station, café, fast food and ice cream shop on the reference sheet gets its own line with its opening hours, as listed on [OpenStreetMap](https://www.openstreetmap.org):

```text
km 4.3
🥖 Les Délices de Saint-Béat Th-Tu 08:00–13:00
🛒 Vival Mo-Su 08:30–12:30, 15:00–19:30
```

- **On your ride day.** Add `--date 2026-10-11` and each shop shows only that day's hours, e.g. `08:00–12:30, 15:00–19:30`, or **closed**. Public holidays count, for the country the shop is in: a shop marked `PH off` is closed on 11 November in France. A `?` after hours means OpenStreetMap isn't sure the shop is open then. Hours that aren't in OpenStreetMap's format are shown as written, in grey.
- **Where they come from.** The GPX has no hours, so each shop is matched to the OpenStreetMap place within 80 m of it with the closest name (`hours.match_m`). onroutemap.de uses OpenStreetMap too, so the names usually line up.
- **Coverage.** OpenStreetMap doesn't know every shop's hours: expect roughly half of them, more for supermarkets. The others say *hours unknown*.
- **It takes a while the first time.** The hours are looked up online, through the [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API) or, when its servers are busy, [Nominatim](https://nominatim.org), which allows one request per second. For a 200 km route, count from half a minute to a few minutes.
- **Then it's instant.** Answers are cached on your computer for 30 days (`hours.max_age_days`), so rendering the same route again needs no internet. Use `--refresh` to look them up again.
- **If the lookup fails**, you get a warning and a road book without the missing hours. Run it again later.
- **Check the ones you rely on.** Hours change, and OpenStreetMap may be out of date.

Choose which categories get hours in your `--config` file:

```toml
[hours]
categories = ["bakery", "grocery"]
```

### Arrival times

Give a start time and your speed on the flat, short stops included, and the road book works out when you reach each stop:

```sh
roadbook my_ride.gpx --hours --date 2026-10-17 --start 07:00 --speed 28 --break 95:45
```

```text
km 123.9 ~13:39–15:46
☕ Le Bistrot 08:00–20:00
🛒 Proxi Super ⚠ opens 15:00 08:00–12:15, 15:00–19:15
km 151.7 ~14:45–17:15
🥖 De Oliveira ⚠ opens 15:30 07:00–12:45, 15:30–19:15
🛒 Carrefour Express 08:00–20:00
```

- **A window, not a time.** You won't ride exactly at the planned speed, so each stop gets the earliest and latest time you may reach it: ±15 % of the time ridden so far, at least ±20 min (`--margin`). The window widens along the route, as small differences add up.
- **closed**: closed the whole time you may be there. The shop's hours that day follow, in grey, so you can see when it opens instead.
- **⚠ closes 12:15**, **⚠ opens 09:30**: it opens or closes while you may be there. Whether you make it depends on your pace, so check before counting on it.
- Shops shown with just their hours are open the whole time.
- **On the strip**, a POI emoji is greyed out when every shop it stands for is known to be closed when you pass. Shops with unknown hours never grey out an emoji.
- **Climbing slows you down.** Each 100 m climbed adds 5 minutes (`--climb`), on top of the distance at your flat speed. So the stop after a big climb comes later than at a constant speed, and the flat stretches after it go quicker. Fitted on real 100–250 km rides with 800–4,100 m of climbing, this got the ride time within about half an hour. Descents are not counted separately: the 5 minutes are what a climb costs once the descent after it has given some time back.
- **Planned breaks**, such as `--break 95:45` for a 45-minute lunch at km 95, delay every stop after them. They don't widen the margin, since you plan them.
- The sheet's title sums up the assumptions, e.g. *start Sat 17 Oct 2026 07:00 at 28 km/h + 5 min/100 m climbed (±15 %, ≥20 min) · breaks: km 95 (45 min)*.

## Development

```sh
git clone https://github.com/bnogaro/gpx2roadbook.git
cd gpx2roadbook
make dev                       # install everything, plus the commit hooks (Conventional Commits, ruff, …)
uv run roadbook my_ride.gpx    # run your working copy
make lint test                 # what CI checks
```

### Releasing

Versions come from the commit messages ([Conventional Commits](https://www.conventionalcommits.org/)): `fix:` bumps the patch, `feat:` the minor version.

```sh
make release
```

This works out the next version with [commitizen](https://commitizen-tools.github.io/commitizen/), updates `pyproject.toml`, `uv.lock` and `CHANGELOG.md`, and opens a `bump: version X.Y.Z` pull request. Merging it publishes the release: the [release workflow](https://github.com/bnogaro/gpx2roadbook/blob/main/.github/workflows/release.yml) builds and smoke-tests the package, uploads it to [PyPI](https://pypi.org/project/gpx2roadbook/) with trusted publishing (no API token), then tags `vX.Y.Z` and creates the GitHub Release.

It needs a clean working tree. To see what it would do without pushing or opening a PR, run `make release DRY_RUN=1` (it tells you how to drop the local branch afterwards).

## License

Copyright © 2026 Bastien Nogaro. Released under the [GNU Affero General Public License v3.0 or later](https://github.com/bnogaro/gpx2roadbook/blob/main/LICENSE): you may use, change and share this software, including commercially, as long as you keep the copyright notice and share your changes under the same license, also when you run a modified version as a web service.
