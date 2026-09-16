# Texture prompt — INTERCEPTOR

WraithNet frame `INTERCEPTOR`: fastest jet (speed 7, high turn rate, 4 HP, small radar cross-section). Drawn today as a colored dart. Needs a small, aggressive air-superiority silhouette that is obviously *not* the strike fighter or the bomber.

## Prompt

```
Game-ready concept render of a fictional sixth-generation interceptor fighter, single aircraft only, centered in frame.

Compact, needle-nosed twin-engine jet with close-coupled canards and twin outward-canted vertical tails. Short-span cropped-delta wings, long thin fuselage, intake scoops under the canards. Looks built for dash speed and turning, not payload. Roughly the size class of a small air-superiority fighter, not a bomber.

Paint: dark charcoal radar-absorbent gray with a faint cool cyan edge highlight. Smooth uncluttered hull panels so a later in-engine tint can recolor it. No national markings, no tail numbers, no logos, no weapons hanging off pylons.

Camera: three-quarter view from slightly above and ahead of the nose, aircraft flying toward the upper-right, wings level. Orthographic / very long lens, no perspective distortion. Isolated on a fully transparent background, no ground, no clouds, no hangar.

Lighting: cool moonlight key from upper left, thin cyan rim light on the trailing edges, sharp readable silhouette, PBR game asset, high detail metal and RAM coating, not a photograph of a real jet, not a schematic, not a cartoon.

Square 1024x1024, PNG, transparent background, single subject filling about 80% of the frame.
```

## Negative prompt

```
real F-22, F-35, Typhoon, Rafale, MiG, Sukhoi, national flag, roundel, USAF, NATO star, text, letters, numbers, HUD, cockpit glass selfie, pilot face, missiles on pylons, bombs, runway, sky, clouds, ground, city, watermark, blurry, extra wings, second aircraft, isometric diorama base
```

## Output spec

- 1024×1024 PNG, transparent
- One aircraft, no ordinance (the sim draws weapons separately)
- Must read as *small and sharp* next to a heavier strike fighter
