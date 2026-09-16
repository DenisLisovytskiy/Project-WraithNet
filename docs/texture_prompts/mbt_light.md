# Texture prompt — MBT_LIGHT

WraithNet frame `MBT_LIGHT`: faster, thinner tank (speed 1.2, 4 HP, no armor bonus). Drawn today as a trapezoid hull. Ground vehicle only; it never shoots aircraft or ships.

Friendly and enemy share the hull. Generate **both** paint variants.

## Prompt — friendly

```
Game-ready concept render of a fictional light main battle tank / cavalry tank, single vehicle only, centered in frame.

Compact tracked hull, short low turret, modest-caliber cannon (not a huge MBT gun), slat-light armor, no ERA bricks stacked like a heavy tank. Looks quicker and smaller than a standard MBT.

Paint: friendly sea-olive and teal-gray, faint cyan rim. Uncluttered side skirts for later tinting. No national flags, no turret numbers, no infantry.

Camera: elevated three-quarter view, about 40 degrees down, hull facing upper-right (gun toward +Y). Orthographic / long lens. Isolated on a fully transparent background.

Lighting: cool moonlight, PBR military game asset, sharp trapezoid silhouette from above.

Square 1024x1024, PNG, transparent background, subject filling about 80% of the frame.
```

## Prompt — enemy

```
Same vehicle and camera as above, but hostile paint: rust-red and dry earth, darker iron tracks, faint copper rim. Still no flags, no text. Same compact light-tank silhouette.
```

## Negative prompt

```
Abrams, Leopard, T-72, T-90, IFV with missiles, people, national flag, text, numbers, aircraft, ship, cartoon, sky, ground plane, watermark, huge railgun
```

## Output spec

- Two PNGs if possible: `mbt_light_friendly.png`, `mbt_light_enemy.png`
- Smaller / shorter gun than `MBT_STANDARD`
