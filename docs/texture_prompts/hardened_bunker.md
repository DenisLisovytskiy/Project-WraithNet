# Texture prompt — HARDENED_BUNKER

WraithNet target `HARDENED_BUNKER` (installation). Drawn today as a filled brown square `(150, 100, 60)`. Static ground objective: thick, buried, blast-proof.

## Prompt

```
Game-ready concept render of a fictional hardened underground bunker complex, single installation only, centered in frame.

Low earth-covered concrete bunker: thick sloped blast doors in a recessed portal, revetments of packed earth and gabions, a few camouflage nets, two tiny ventilation stacks, no people. The mass should read as a squat square from above — a buried cube more than a building. Surrounding dirt is part of the structure, not a landscape diorama.

Materials: weathered ochre-brown concrete and dry earth, rusted steel door seams, muted olive camo netting. Color family around RGB 150,100,60 so it still matches the current map legend. No flags, no warning text, no unit numbers.

Camera: elevated three-quarter view from slightly above, almost isometric, looking down at about 40 degrees so the square footprint is obvious. Orthographic / long lens. Isolated on a fully transparent background, no grass field extending to the edges, no sky.

Lighting: cool moonlight, faint cyan rim on concrete edges, sharp readable silhouette, PBR military game asset.

Square 1024x1024, PNG, transparent background, subject filling about 80% of the frame.
```

## Negative prompt

```
skyscraper, city, people, soldiers, vehicles, tanks, runway, trees filling the frame, national flag, text, signs, HUD, cartoon, photoreal tourist photo, ruins of a house, church, castle
```

## Output spec

- 1024×1024 PNG, transparent
- Must still read as a *square* mass at map scale (this replaces the square glyph)
