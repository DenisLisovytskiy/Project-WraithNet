# Texture prompt — FLEET_CARRIER

WraithNet frame `FLEET_CARRIER`: larger friendly carrier (hangar 8, more HP, slower turn). Drawn today with the same rectangle as the escort, so this texture has to show a longer, wider deck.

## Prompt

```
Game-ready concept render of a fictional fleet aircraft carrier, single ship only, centered in frame.

Long angled flight deck, two catapult tracks, a larger island, deck cranes, closed blast deflectors. Clearly bigger than an escort carrier. No parked air wing (aircraft are spawned separately). Clean waterline for a transparent sprite.

Paint: weathered teal-gray friendly navy, darker non-skid deck, faint cyan rim. No hull numbers, no flags, no text.

Camera: elevated three-quarter bow-starboard view, about 35–40 degrees down, rectangular deck dominant. Orthographic / long lens. Isolated on a fully transparent background.

Lighting: cool moonlight, PBR naval game asset.

Square 1024x1024, PNG, transparent background, ship filling about 90% of the frame, bow up.
```

## Negative prompt

```
Nimitz photo, Ford photo, escort carrier, parked F-35, people, hull number, national flag, battleship, cartoon, sky, ocean to the edges, watermark
```

## Output spec

- 1024×1024 PNG, transparent
- Longer / wider than `ESCORT_CARRIER`
- Empty deck
