# Translating

The road book's words, and the GUI's, are English in the code and French in `src/roadbook/locales/fr/*.toml`: see
`src/roadbook/i18n.py`.

- **A new message:** write it in English through `_("…")` (the road book, the booklet) or `translate("…", lang)` (the
  GUI), with `str.format` fields for what varies: `_("Climb at km {km}", km=km)`, never an f-string inside `_()`.
  Add its French to the catalog of its area: `output.toml` (strips, reference sheet, climb pages, map), `booklet.toml`,
  `gui.toml`, `settings.toml` (the settings' labels, help and choices, as the GUI shows them).
- **The test** (`tests/test_i18n.py`) fails on a message without its French, on French no message uses any more, and
  on fields that differ between the two.
- **Room:** much of the road book is measured in mm. A French word longer than its English can push a label off a
  strip or a block onto another page: check the samples in French (`output-diff` renders them, in its `fr` line).

## French

The same word for the same thing, everywhere:

| English | French |
| --- | --- |
| road book | roadbook |
| booklet | livret |
| strip (a strip's) | bande |
| ribbon (line layout) | ruban |
| reference sheet | fiche récapitulative |
| stop | arrêt |
| checkpoint | point de contrôle (`CP1` stays `CP1`) |
| start, finish | départ, arrivée |
| climb | montée (a named one is its name: `Col de …`) |
| climb category | `Cat` (as on the Tour: `Cat 2`, `HC`) |
| summit | sommet |
| grade | pente |
| gain, loss | dénivelé positif, négatif (`D+`, `D-` where short) |
| town | ville |
| opening hours | horaires d'ouverture |
| open, closed | ouvert, fermé |
| map | carte |
| warning | alerte |
| sunrise, sunset | lever, coucher du soleil |
| arrival time (estimate) | heure de passage (estimée) |
| water, toilets, bakery, café, food, fast food, grocery, fuel, vending machine, ice cream, lodging | eau, toilettes, boulangerie, café, restauration, restauration rapide, épicerie, station-service, distributeur, glacier, hébergement |

- **Typography:** a no-break space (U+00A0) before `:` `;` `!` `?` and between a number and its unit or `%`; `«` `»`
  for quotes; `–` between times, as in English.
- **Numbers keep their decimal point** (`4.3 km`) for now: see #108.
- **Dates** go through `i18n.date_text()`, never `strftime`'s `%a` / `%b`.
