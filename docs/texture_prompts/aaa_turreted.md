# Texture prompt — AAA_TURRETED

WraithNet frame `AAA_TURRETED` (target type `AAA_SITE`). Same role as light AAA but 3 HP and a directed turret gun. Drawn today with the same orange glyph as light AAA, so this texture has to carry the difference.

## Prompt

```
Game-ready concept render of a fictional turreted self-propelled anti-aircraft gun, single vehicle only, centered in frame.

Tracked or 6x6 armored chassis with a fully enclosed turret mounting a radar-directed autocannon (single or twin barrel) and a small search radar dish on the turret roof. Heavier, boxed, more “SPAAG” than a sandbag pit. No missile pods (this unit never launches missiles in the sim).

Paint: dusty orange-brown armor plates with dark rubber tracks, turret face catching a copper-orange highlight around RGB 230,140,40. No flags, no turret numbers, no logos.

Camera: elevated three-quarter view, about 40 degrees down, turret facing upper-right. Orthographic / long lens. Isolated on a fully transparent background.

Lighting: cool moonlight, warm metal rim on the gun barrels, PBR military game asset.

Square 1024x1024, PNG, transparent background, subject filling about 80% of the frame.
```

## Negative prompt

```
open gun pit, towed flak, SAM missiles, Pantsir, Tunguska, Gepard photo copy, tank with a long tank gun, people, national flag, text, explosions, aircraft, cartoon, sky, watermark
```

## Output spec

- 1024×1024 PNG, transparent
- Enclosed turret + roof radar; must not look like `AAA_LIGHT` or `SAM_SITE`
