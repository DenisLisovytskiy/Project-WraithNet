# Texture prompt — ESCORT_CARRIER

WraithNet frame `ESCORT_CARRIER`: friendly surface vessel, small hangar (capacity 4), speed 1.5. Drawn today as a long thin rectangle (the deck). This is a pocket carrier, not a supercarrier.

## Prompt

```
Game-ready concept render of a fictional escort / light aircraft carrier, single ship only, centered in frame.

Short straight flight deck, one island superstructure on the starboard side, a few closed deck elevators, arresting-wire chevrons, no parked aircraft (jets are separate entities). Hull is a compact carrier, maybe 150–180 m class, not a Nimitz. Wake not required; cut the waterline clean for a transparent sprite.

Paint: weathered teal-gray friendly navy, dark deck non-skid, faint cyan rim on the island. No hull numbers, no flags, no “CVN” text.

Camera: elevated three-quarter bow-starboard view, about 35–40 degrees down, so the rectangular deck is the silhouette. Orthographic / long lens. Isolated on a fully transparent background, no ocean plane.

Lighting: cool moonlight, PBR naval game asset.

Square 1024x1024, PNG, transparent background, ship filling about 85% of the frame, deck aligned roughly vertical in the image (bow up).
```

## Negative prompt

```
Nimitz, Ford, Kuznetsov, real US Navy photo, parked jets, helicopters, people, hull number, national flag, battleship, corvette, submarine, cartoon, sky, open ocean to the edges, watermark
```

## Output spec

- 1024×1024 PNG, transparent
- Shorter deck than `FLEET_CARRIER`
- Empty deck
