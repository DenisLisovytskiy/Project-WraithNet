# Texture prompt — SAM_SITE

WraithNet target `SAM_SITE` (installation, fires missiles, engagement disc in the viewer). Drawn today as a filled red octagon `(210, 60, 60)`. Hostile surface-to-air battery, not AAA guns.

## Prompt

```
Game-ready concept render of a fictional land-based SAM battery, single launcher cluster only, centered in frame.

Octagonal concrete pad with one transporter-erector-launcher in the middle: four to six fat surface-to-air missile canisters raised at a steep angle, plus a small trailer radar on a short mast. Sandbag ring and cable spools. Looks like a missile site, not a gun pit and not a bunker.

Paint: dusty hostile red-oxide and dark iron, scorched blast marks on the pad. Canister noses a dull brick red (around RGB 210,60,60). No national flags, no unit numerals, no “SAM” lettering.

Camera: elevated three-quarter view, about 40 degrees down, octagonal pad readable from above. Orthographic / long lens. Isolated on a fully transparent background.

Lighting: cool moonlight, faint red-orange heat on the canister rims, cyan edge on the concrete so it pops on a dark map, PBR military game asset.

Square 1024x1024, PNG, transparent background, subject filling about 80% of the frame.
```

## Negative prompt

```
towed anti-aircraft gun, flak, tank, bunker, aircraft, people, Patriot battery photo, S-300 photo, national flag, text, numbers, explosions, missiles in flight, cartoon, sky, watermark
```

## Output spec

- 1024×1024 PNG, transparent
- Octagonal pad + raised canisters (this replaces the octagon glyph)
- Missiles stay in tubes; do not draw in-flight trails
