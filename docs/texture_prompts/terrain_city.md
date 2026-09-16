# Texture prompt — terrain CITY

WraithNet terrain cell `CITY`. Currently a city base plus procedural building blocks whose height comes from the terrain grid. This albedo is the **rooftop / block tile** under those meshes.

## Prompt

```
Seamless tileable game albedo of a dark dense city block seen from directly above, night, holographic military map.

Packed flat rooftops, narrow alleys, asphalt, faint cyan edge lighting on roof parapets, a few dark HVAC lumps. No readable street names, no cars as heroes, no landmarks that break tiling (no unique stadium). Uniform industrial/residential mix.

Orthographic top-down, perfectly tileable on all four edges, no text.

Square 1024x1024, PNG, fully opaque, seamless.
```

## Negative prompt

```
Eiffel tower, known skyline, street labels, neon cyberpunk, daylight, people, unique plaza that cannot tile, watermark, cartoon
```

## Output spec

- 1024×1024 **seamless** PNG
- Rooftop language, not a perspective street photo
