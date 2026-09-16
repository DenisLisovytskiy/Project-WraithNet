# Texture prompt — MBT_STANDARD

WraithNet frame `MBT_STANDARD`: heavier tank (speed 1.0, 6 HP, armor 1). Drawn today as the same trapezoid as the light tank, so the texture has to show the extra mass.

## Prompt — friendly

```
Game-ready concept render of a fictional standard main battle tank, single vehicle only, centered in frame.

Heavy tracked MBT: thick turret, long rifled/smoothbore cannon with a fume extractor, composite side skirts, storage bins, deep tracks. Clearly bulkier than a light cavalry tank. No ATGMs on the turret, no people.

Paint: friendly sea-olive and teal-gray, faint cyan rim, uncluttered turret roof. No flags, no turret numbers.

Camera: elevated three-quarter view, about 40 degrees down, hull facing upper-right. Orthographic / long lens. Isolated on a fully transparent background.

Lighting: cool moonlight, PBR military game asset, trapezoid hull readable from above.

Square 1024x1024, PNG, transparent background, subject filling about 80% of the frame.
```

## Prompt — enemy

```
Same heavy MBT and camera, hostile paint: rust-red, dry dust, darker iron, faint copper rim. No flags, no text.
```

## Negative prompt

```
Abrams photo, Leopard photo, T-90 photo, light tank, IFV, people, national flag, text, numbers, aircraft, cartoon, sky, watermark
```

## Output spec

- Two PNGs: `mbt_standard_friendly.png`, `mbt_standard_enemy.png`
- Longer gun and thicker turret than `MBT_LIGHT`
