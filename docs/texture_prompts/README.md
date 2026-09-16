# WraithNet texture prompts

Image-generation prompts for replacing the colored primitive glyphs in `viewer3d/scene.py` and `viewer/render.py`. Simulation IDs and silhouettes stay as they are today; these files only describe art.

Each `*.md` file is a self-contained prompt. Paste the fenced **Prompt** (and **Negative prompt**) into Gemini, Imagen, Flux, Midjourney, or similar. Do not generate assets from this folder automatically.

## Shared output contract

Unless a file says otherwise:

- Square **1024×1024 PNG**, **transparent background**
- Single subject, centered, no ground plane, no skybox, no HUD, no text
- Fictional 6th-gen hardware: **no real national flags, roundels, tail codes, or manufacturer logos**
- Readable at tiny map scale (the 2D glyph is ~16–22 px; the 3D glyph is about one world unit)
- Cool moonlight + faint cyan rim light, so the piece sits on the dark holographic map
- Leave large hull/armor panels uncluttered so the engine can still tint by faction or jet palette

## Catalog

| Sim ID / terrain | Current glyph | Prompt file |
|---|---|---|
| `INTERCEPTOR` | colored dart | [interceptor.md](interceptor.md) |
| `STRIKE_FIGHTER` | colored dart | [strike_fighter.md](strike_fighter.md) |
| `HEAVY_BOMBER` | colored dart | [heavy_bomber.md](heavy_bomber.md) |
| `HARDENED_BUNKER` | brown square | [hardened_bunker.md](hardened_bunker.md) |
| `MOBILE_RADAR` | blue diamond | [mobile_radar.md](mobile_radar.md) |
| `SAM_SITE` | red octagon | [sam_site.md](sam_site.md) |
| `AAA_LIGHT` | orange AAA disc | [aaa_light.md](aaa_light.md) |
| `AAA_TURRETED` | orange AAA disc | [aaa_turreted.md](aaa_turreted.md) |
| EW jammer | purple hexagon | [ew_jammer.md](ew_jammer.md) |
| `home_base` | green flag + pad | [home_base.md](home_base.md) |
| `MBT_LIGHT` | trapezoid hull | [mbt_light.md](mbt_light.md) |
| `MBT_STANDARD` | trapezoid hull | [mbt_standard.md](mbt_standard.md) |
| `ESCORT_CARRIER` | deck rectangle | [escort_carrier.md](escort_carrier.md) |
| `FLEET_CARRIER` | deck rectangle | [fleet_carrier.md](fleet_carrier.md) |
| `CORVETTE_LIGHT` | hull diamond | [corvette_light.md](corvette_light.md) |
| `CORVETTE_STANDARD` | hull diamond | [corvette_standard.md](corvette_standard.md) |
| Terrain `WATER` | dark blue plane | [terrain_water.md](terrain_water.md) |
| Terrain `PLAINS` | dark ground plane | [terrain_plains.md](terrain_plains.md) |
| Terrain `FOREST` | green cells | [terrain_forest.md](terrain_forest.md) |
| Terrain `CITY` | building blocks | [terrain_city.md](terrain_city.md) |

Tanks have two paint variants in the same file (friendly teal-olive vs enemy rust-red). Generate both if you want faction colors without engine tinting.
