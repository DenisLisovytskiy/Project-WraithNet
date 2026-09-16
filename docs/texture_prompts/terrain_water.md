# Texture prompt — terrain WATER

WraithNet terrain cell `WATER`. Currently a dark blue emissive plane. Needs a **seamless tiling albedo**, not a sprite.

## Prompt

```
Seamless tileable game albedo of dark night ocean water for a holographic military map.

Deep teal-black water, very subtle swell, faint cyan caustic streaks, no whitecaps, no ships, no shoreline, no sun path. Looks like the map’s WATER cells in a sixth-gen briefing, not a tropical postcard.

Orthographic top-down, perfectly tileable on all four edges, no text, no UI.

Square 1024x1024, PNG, fully opaque, seamless.
```

## Negative prompt

```
boat, island, beach, sunset, bright tropical water, foam, text, watermark, perspective waves, shoreline, people
```

## Output spec

- 1024×1024 **seamless** PNG (not transparent)
- Top-down only
