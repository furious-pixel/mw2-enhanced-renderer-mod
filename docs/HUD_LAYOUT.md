# HUD layout contract

## Purpose

This document defines how the mod renderer lays out the in-mission HUD at any
resolution. The 1024x768 HUD is the reference layout. Higher-resolution output
changes that layout through a small set of independent scale controls rather
than through resolution-specific coordinates or draw-entry special cases.

This is the design contract for the implementation. It does not cover the game
shell, loading screens, the Esc menu, or side-by-side presentation itself.

## Core rules

1. All authored HUD coordinates are in a 1024x768 reference space.
2. The output viewport height determines the proportional scale. Width never
   changes the aspect ratio of HUD content.
3. Panel position, ordinary panel size, camera-viewport size, and font size are
   separate choices.
4. A panel has one explicit anchor and one transform. Every primitive belonging
   to it uses that transform, including its clipping rectangle.
5. Related elements such as heat, heat-rate, and jump-jet meters form one panel
   group. Internal distances are never recomputed from the output resolution.
6. Scene-projected markers and screen-edge indicators are not ordinary panels.
   They have separate placement contracts described below.
7. At 1024x768 every scale resolves to `1.0`, so reference placement remains
   pixel-identical. Explicit enhanced-artwork replacements, such as the
   antialiased compass carets, retain the same logical anchors but need not
   reproduce defects in the original pixels.

## Resolution and scale model

For a mod-renderer viewport of width `W` and height `H`:

```text
vertical_scale = H / 768
resolved_scale(control) = 1 + control * (vertical_scale - 1)
```

Each control is clamped to `0.0..1.0`:

- `0.0` retains the native 768p size.
- `1.0` scales in direct proportion to viewport height.
- Values between them provide partial growth.

For viewports shorter than 768 pixels, every resolved scale is capped to the
vertical scale. This safety rule fits the complete HUD into a smaller viewport
instead of allowing nominally native-sized content to overflow it.

The position scale defines a virtual 4:3 layout canvas:

```text
canvas_width  = 1024 * position_scale
canvas_height =  768 * position_scale
canvas_left   = (W - canvas_width) / 2
canvas_top    = (H - canvas_height) / 2
```

Consequently, proportional positioning fills the viewport height and remains
centered on widescreen or ultrawide output. HUD elements do not move toward the
physical widescreen edges merely because more horizontal space is available.

The calculation always uses the actual mod viewport. A 3440x1440 mod-only view
uses 3440x1440; a 1024x768 comparison pane uses 1024x768. Presentation mode does
not need its own HUD coordinates.

## Configuration

The `[HUD]` growth controls are resolution-independent and clamped to `0..1`:

| Control | Applies to | Parser fallback | Shipped value |
| --- | --- | ---: | ---: |
| `position_scaling` | panel anchors | 1.0 | 1.0 |
| `panel_scaling` | ordinary artwork, meters, lines, and gaps | 0.0 | 0.6 |
| `viewport_scaling` | target-display and MFD camera frames | 1.0 | 1.0 |
| `font_scaling` | runtime glyphs and line metrics | 0.0 | 0.6 |
| `target_marker_scaling` | center reticle, NAV circle, offscreen carets | 0.0 | 0.0 |

Target brackets use `panel_scaling`, not `target_marker_scaling`.
`alt_throttle_indicator_position = true` selects the right-center throttle,
speed, and MASC group; `false` retains the native panel locations.

## Panel contract

Layout policy is stored once per logical panel, not copied onto each draw
entry. A panel definition contains:

```text
id                 stable logical name
reference_bounds   complete bounds at 1024x768
anchor             explicit reference point and attachment sides
size_role          panel, viewport, damage_sprite, or legacy
clip_policy        panel bounds, viewport bounds, or none
font_role          HUD font role used by its text
```

The anchor is explicit. It is not guessed from the panel's current rectangle.
The implemented registry is:

| Panel/content | Horizontal anchor | Vertical anchor | Size role | Notes |
| --- | --- | --- | --- | --- |
| Weapon readout | right | top | panel | All weapon rows and the active-row outline form one panel. |
| Target display and attached text | left | bottom | viewport | Live view, empty fill, glitch RLE, frame, name, and range share one stable frame. |
| MFD | right | bottom | viewport frame | Camera/noise uses viewport size; HTAL uses panel size; damage RLE uses its integer-sprite rule. All content is centered in the stable frame. |
| Heat, heat-rate, and jump-jet meters | center of virtual canvas | bottom | panel | One union-bounds group; see the cluster rule below. |
| Throttle, speed, and MASC (alternate) | right | center | panel | One group mirroring the altimeter's top and bottom; its authored text is not clipped to a native pane. |
| Throttle and MASC (native positions) | right | bottom | panel | Used when `alt_throttle_indicator_position = false`; independent from the heat/jump cluster. |
| Autopilot | center | bottom | panel | Uses its native panel position within the centered 4:3 canvas. |
| Cockpit radar (modes 1 and 2) | left | top | panel | Animation scales about the final pane center without changing its anchor. |
| Compass (enhanced) | center | top | panel | Ticks and rectangular indicators are immutable palette geometry. Fixed labels follow font scaling. Diagonal carets use the resolution-matched atlas contract below. |
| Altimeter (enhanced) | left | center | panel | Ticks and rectangular indicators are immutable palette geometry. Fixed labels follow font scaling. Target carets use the same atlas and integer-apex placement. |
| Compass and altimeter (native) | native | native | legacy | The original RLE strips and placement remain unchanged. |
| On-screen reticle and target markers | scene projection | scene projection | target-marker artwork | Position follows the effective 3D projection, not a panel anchor. |
| Offscreen target indicator | fixed safe-rectangle edge | fixed safe-rectangle edge | target-marker artwork | Direction is camera-space and independent of zoom/FOV once offscreen. |
| Message bars | centered 4:3 viewport | physical top/bottom | font-derived | Width is `output_height * 4/3`; they remain screen-edge overlays. |
| Satellite radar (mode 4) | physical screen | physical screen | full output | It intentionally fills the complete output and is not a cockpit panel. |

For a reference anchor `A` and a reference-space point `R` belonging to the
panel:

```text
screen_anchor = canvas_origin + A * position_scale
screen_point  = screen_anchor + (R - A) * content_scale
```

This formula is the separation between position and size. Changing panel size
cannot move its attached edge or center. It also preserves every native gap
inside a group because all members use the same transform.

The following are single layout groups even if the game exposes several panel
records:

- heat, heat-rate, and jump-jet fuel;
- all rows of the weapon readout;
- a target camera plus the information attached to that camera;
- the MFD frame and the content selected for that frame.

The reference bounds of a multi-record group are the union of its members at
1024x768. The group anchor is applied to that union. Individual members do not
receive their own screen-relative correction.

The heat/jump union includes configured pane bounds even when a live fill is
unavailable. Its center is X=512 in the virtual canvas, its native bottom inset
is retained, and all labels, bars, and gaps use the same transform.

## Ownership and resolution

The snapshot stores ordinary primitives in their owning panel's 1024x768
reference space. The panel registry supplies complete bounds, anchor, size role,
animation pivot, and clipping policy; missing policy is an error. Scene markers,
edge indicators, and physical-screen overlays remain separate non-panel types.

Layout resolves one transform per visible panel. Every child primitive and its
clip use that transform. The GPU pass consumes resolved values and does not
infer anchors, inspect panel names, or apply resolution-specific corrections.

## Pixel-aligned box outlines

Every renderer-authored, axis-aligned box outline is resolved as one atomic
rectangle. It is not drawn as four independently transformed lines or as a GPU
line loop. The renderer transforms and rounds the four outer edges to output
pixel boundaries once, then resolves one integer output stroke width:

```text
stroke_pixels = max(1, round(min(abs(scale_x), abs(scale_y))))
inner_bounds  = outer_bounds inset by stroke_pixels on every side
```

The stroke is clamped only when necessary to keep the inner bounds valid. A
source rectangle whose right or bottom coordinate is inclusive must first be
normalized to half-open bounds; output-space snapping does not change its
reference-space convention.

The outline is emitted as opaque, axis-aligned filled quads. The top and bottom
quads span the complete snapped outer width, including the corner pixels; the
left and right quads fill the interval between them. This construction gives
both the outside and inside of every corner a sharp 90-degree angle at any
stroke width. It also prevents fractional transforms, line
endpoint rules, antialiasing, or driver-specific line joins from leaving corner
holes or producing uneven edge widths.

This rule applies generally to HUD box outlines, including the active-weapon
row selector and the border of a filled meter. Native RLE frame artwork and
non-rectangular line primitives retain their own sampling and stroke contracts.

## Panel animation

Animation is another component of the panel transform. It does not create a
temporary panel definition or cause panel contents to be laid out again.

For either axis, reveal extent `a`, final half-open bounds `[L, R)`, and the
integer native pivot `P`, the common resolver first computes the animated
half-open bounds:

```text
animated_left  = lerp(P,     L, a)
animated_right = lerp(P + 1, R, a)
```

It then derives one affine transform from those bounds and composes that with
the panel's ordinary anchor and sizing transform. X uses `ax` and Y uses `ay`.

Both extents are `1.0` for a steady or fully started panel. Radar uses equal
extents because its native pane interpolates all four edges together. The target
display and MFD use the game's two-stage reveal: startup grows horizontally
from the center during the first half and then vertically during the second;
shutdown collapses vertically first and then horizontally. The pivot's screen
position follows the native inclusive-window interpolation. Odd-sized panes
remain exactly center-fixed; an even-sized pane can differ by the native
half-pixel between its one-pixel source and final rectangle, without the larger
bottom-right drift caused by anchoring animation to a changing bounding box.

An extent of zero resolves to the transition controller's one-native-pixel
source window, not a zero-area mathematical rectangle. The same common transform
therefore leaves the target frame's horizontal stage visible just as it does for
an MFD camera. Panel fills are clipped to that animated frame, including their
minimum one-pixel border thickness.

At 1024x768, position and content scales resolve to `1.0`; the same formula
produces the reference animation. At higher resolutions, configuration changes
only the resolved layout scales. No separate high-resolution animation path is
allowed.

The authoritative transition state is the game's HUD mode at `mech + 0xA0`.
Mission loading arms a gate only to suppress stale mode-2 snapshots that occur
before the game begins its real startup sequence. The first observed mode-1
snapshot releases that gate, initializes the ordinary shared radar,
target-display, and camera-MFD clock, and uses the mode-1 callback family for
the whole animation. Mode 2 then selects the complete steady HUD. There is no
synthetic replay and no fade or loading-presentation timestamp in the HUD
timeline. This keeps the palette fade orthogonal and prevents steady panels or
radar range text from appearing before the startup animation. Radar range and
bearing text are explicitly startup-hidden because the replacement renderer
does not run the native wrapper that normally updates its text gate byte.

The loading compositor begins its 0.25-second cosmetic fade as soon as the first
complete enhanced frame is handed off. There is no additional fully-lit hold
after handoff and no loading-presentation timestamp in the HUD state machine.

The panel clip follows the animated transform. At transition completion the
panel becomes invisible rather than remaining as a degenerate rectangle. Child
artwork whose native size is independent of pane size, notably radar sprites,
does not inherit the reveal extent as an artwork-size multiplier; only its
position and clip follow the pane. Likewise, camera scenes render into their
stable final-size targets and only the destination blit is animated. This avoids
per-frame render-target reallocations and nearest-neighbor resampling shimmer.
The animation pivot is the integer center of the game's inclusive reference
window, preserving native odd-width/odd-height center points such as the target
display's `(114, 623)` rather than introducing a half-pixel drift.

## Panel-specific contracts

Most cockpit elements need no panel-specific renderer code:

- The weapon readout is one right-anchored panel containing visible weapon-row
  text and an optional active-row outline. Weapon names, ammunition text, color,
  and reveal timing are resolved while building the reference frame. Sizing and
  position are ordinary panel layout.
- Heat, heat-rate, and jump-jet fuel are one bottom-center panel containing
  meter and label primitives with their native gaps.
- With `alt_throttle_indicator_position = false`, throttle, MASC, and autopilot
  retain their ordinary independent panels. The enabled alternate position
  combines throttle, speed, and active MASC text in one right-center panel.
- Throttle is a bordered meter, not an independent line outline plus a displaced
  fill. At 1024x768 its inclusive outer rectangle is 17 pixels wide, with a
  one-reference-pixel red border and a 15-pixel interior. The horizontal meter
  interior is derived from that outer rectangle; native `fill_x` and
  `fill_width` do not independently reposition or resize it. After the shared
  panel transform, the outer rectangle is snapped once and its equal-width
  border and exact inner fill bounds follow the general box-outline contract.
- The alternate bar spans reference Y=285 through Y=428 inclusive, matching
  only the enhanced altimeter strip's top and bottom. Its zero-throttle row is
  Y=380, preserving the native two-to-one forward/reverse division; it is not
  aligned to the altimeter's reference marker. Nonzero fill extents are scaled
  into those two sections with inclusive endpoints, so full forward and reverse
  commands meet the corresponding inside border without a hole while zero is
  exactly one green row.
- Alternate speed and active MASC text are right-aligned at reference X=972,
  leaving eight pixels before the bar at X=980. Speed is vertically centered on
  the zero-throttle row and MASC is centered 24 reference pixels below it. These
  are renderer-authored labels rather than clipped native-pane contents, so the
  complete signed, three-digit speed string and unit remain visible. Position,
  gaps, meter geometry, and text placement pass through their respective panel
  and font transforms.
- Radar modes 1 and 2 are one animated panel containing an ellipse, lines,
  blips, sprites, and text. All of them share its transform.
- The target display and MFD are stable panel containers with mode-specific
  content.

These differences affect only how reference primitives are constructed. The
layout resolver and GPU renderer do not branch on these panel identities.

## MFD and target-display composition

The target display and MFD each have a stable destination frame. Startup,
shutdown, empty, glitch, and live-camera states do not replace that frame with a
new anchor.

For the MFD:

- the outer MFD frame uses `viewport_scaling`;
- live rear, down, and weapon-camera images fill the final inner frame;
- noise and glitch frames apply only to those three camera modes and fill the
  same inner frame with nearest-neighbor sampling;
- HTAL and other ordinary native panel artwork use `panel_scaling` and are
  centered as a complete content group within the final MFD frame;
- the damage wireframe is an RLE sprite with a dedicated `damage_sprite` size
  role. At 768p it is drawn 1:1. Above 768p, layout rounds the resolved viewport
  scale to the nearest positive integer, then reduces that integer only if the
  visible base RLE would cross an edge of the final MFD frame. The common origin
  is snapped to an output pixel, and every damage-colored clipped pass uses the
  same transform. The decoded indexed-alpha texture continues to use nearest-
  neighbor sampling;
- damage-wireframe and HTAL content are steady-state views: they are omitted
  throughout MFD startup and shutdown rather than being squeezed through the
  camera-pane reveal;
- camera modes use the runtime-font labels `rear`, `down`, and `wpn`. Lowercase
  input selects the font renderer's smaller uppercase form. Each label is
  horizontally centered on the MFD frame and uses live palette index `0x06`,
  exactly matching the camera-frame border;
- the camera-label row origin is nine reference pixels below the MFD frame's
  exclusive bottom edge. Its position follows the frame transform while its
  glyph size follows `font_scaling`;
- text and meter alignment is relative to the MFD content group, not to the
  screen or to an individual primitive's bounds.

The same principle applies to the target display. Camera content fills its
stable frame, while its name and range text use explicit frame-relative anchors.
The target-name row shares the camera label's nine-reference-pixel offset below
the frame. The range row remains 16 reference pixels below the name row. Thus
both target rows move together and the target name stays vertically aligned
with the camera label. Changing viewport size must not introduce extra space
between either image and its text.

The MFD content/state contract is normative:

| HUD state | Damage wireframe (mode 1) | HTAL (mode 2) | Camera modes 3/4/5 |
|---|---|---|---|
| startup | hidden | hidden | two-stage animated camera or camera glitch |
| steady | visible | visible | full-size camera or camera glitch |
| shutdown | hidden | hidden | reverse two-stage animated camera or camera glitch |

The damage wireframe and HTAL must never be passed through the startup/shutdown
pane animation. The video-glitch latch must be consulted only for camera modes;
it must not replace or suppress damage-wireframe or HTAL content. Camera labels
exist only for fully started camera modes, remain present over damaged-video
substitution, and are hidden during startup and shutdown. They do not read,
decode, preload, or draw the native label RLEs.

The target display and camera MFD must call the same two-stage pane resolver.
They may differ in content, frame color, and resource selection, but must not
carry separate reveal formulas or separate minimum-size handling. In particular,
both must retain a visible one-reference-pixel horizontal line for the complete
horizontal-only stage.

## Fonts

Runtime-rendered HUD text starts from the 16-pixel 1024x768 font. Its effective
size is:

```text
font_pixels = round(16 * resolved_scale(font_scaling))
```

The font renderer rasterizes or caches the requested pixel size rather than
stretching a 16-pixel bitmap. Line height, baseline offsets, and text-local
padding use the same font scale.

Every text item has an explicit local alignment such as left/top,
center/top, or right/baseline. This allows font scaling to differ from panel
scaling without shifting the point to which the text is attached.

RLE label artwork is a sprite, not a runtime font. It follows its panel's size
role unless a panel definition explicitly assigns it to the viewport role.
The MFD camera labels are explicitly runtime text and are not part of this RLE
rule.

## Target markers and the offscreen indicator

On-screen reticles and target brackets remain scene-projected. Their positions
use the same effective camera projection as scene geometry, including the
effective horizontal FOV. Position never goes through a panel anchor. Final
atlas and RLE origins are snapped to output-pixel boundaries, and artwork clips
only at the physical output rectangle rather than the centered native reticle
pane transformed by the scene focal scale. Thus brackets remain complete until
their pixels actually reach a physical output edge.

Artwork sizing is deliberately split. The center reticle and selected-NAV
circle follow `target_marker_scaling`; target-bracket position and separation
follow the effective scene projection and final output resolution. Bracket
center and radius retain fractional values through scene scaling. The renderer
then rounds the completed physical-pixel center and truncates one nonnegative
physical-pixel radius shared by both axes, so all four corners advance together
without a minimum enclosure or 1024x768-grid stepping.

The center weapon reticle remains a nearest-sampled native RLE. The other
target markers are generated into the shared indexed-alpha HUD atlas:

- Four target brackets are 9x9 logical-pixel corners. Their one-pixel
  horizontal and vertical legs retain the native silhouette, their curved
  inner edges preserve the shared circular construction, and each outer tip
  fades across its terminal pixel instead of ending at full opacity. Atlas
  artwork remains resolution-matched while projected radius alone controls
  corner separation.
- Offscreen directions use four complete carets, 25x15 for up/down and 15x25
  for left/right. They never crop a cross-shaped RLE to synthesize an arrow.
- The selected direct/nav marker draws `NAV` with the runtime font at a base
  size of 16 pixels and draws a separate 13x13 logical-pixel atlas circle at
  the projected point.

Atlas entries retain palette indices plus alpha. Per-draw palette overrides
select the current target classification color through the live game palette.
The resolved atlas pixels are nearest-sampled and drawn 1:1 on integer output
coordinates; antialiasing is baked into each physical-size class.

The offscreen indicator is different: it is a screen-edge HUD widget, not a
scene-projected sprite.

Its contract is:

1. Transform the target into camera view space.
2. Use the sign and ratio of view-space X and Y to determine direction. Native
   behind-camera projection divides by absolute depth and does not apply a
   second direction reversal.
3. Intersect that direction with one fixed safe rectangle defined in 1024x768
   HUD reference space. This calculation must not use focal length, zoom, or the
   effective horizontal FOV.
4. Transform the resulting edge point through the HUD position canvas.
5. Draw the directional artwork at `target_marker_scaling` size and snap its
   final top-left position to an output-pixel boundary.
6. Carry the resolved `left`, `right`, `up`, or `down` direction with the edge
   indicator; the renderer must not infer it again from rounded screen position.
7. Select the complete generated caret for that direction. Do not infer a
   source crop or direction from its rounded output position.

Zoom may legitimately change whether a target is on screen. Once it is
offscreen, however, zoom must not change the indicator's edge position, size,
or visible arrow shape for an unchanged camera-to-target direction.

## Special elements

`[HUD] compass_altimeter = native` retains the original compass and altimeter
RLE strips, indicators, placement, and direction behavior through the `legacy`
size role.

The enhanced compass and altimeter do not read or test the availability of the
game's strip or indicator RLEs. They use immutable runtime geometry, the shared
generated HUD atlas, and fixed text layouts. Native mode continues to use the
original RLE resources and style offset.

Enhanced mode samples current enable, pane, heading, altitude, and target state
without depending on native RLE readiness. Ticks and rectangular bars are
hard-edged palette geometry. Their position and size follow `panel_scaling`;
fixed-label glyphs follow `font_scaling`, including both `00` instances needed
for compass wraparound. Heading and altitude retain fractional fixed-point
motion through output scaling, after which the complete generated scale origin
is snapped once to the nearest physical pixel. Ticks, labels, and attached
indicators share that translation, preserving rigid internal pixel geometry
while allowing correctly spaced output-resolution movement.

Only the four diagonal up, down, left, and right carets use the antialiased
indexed-alpha atlas. For resolved panel scale `S`, its physical content sizes
are:

```text
wide caret content = ceil(10 * S) by ceil(6 * S)
tall caret content = ceil(6 * S) by ceil(10 * S)
```

Each entry has a one-texel transparent pad and a two-texel gutter. Palette index
`0x0A` plus analytic alpha supplies live-palette round-cap and apex coverage.

Caret drawing is strictly pixel-aligned and 1:1: destination origins and sizes
are integers, and the atlas uses nearest sampling. Each atlas entry records the
integer pixel coordinate of its apex. Up/down compass apexes and the compass
center or centered-target bar use the same rounded output X coordinate. The
left/right compass command attaches explicitly to its corresponding strip edge;
the transparent horizontal gap is `round(2 * S)` output pixels, giving exactly
two pixels at 1024x768. Its apex Y coordinate is the rounded midpoint of the
12-reference-pixel long compass tick, six reference pixels below the strip
origin. The renderer consumes these explicit apex or edge-attachment commands;
it does not infer placement from a panel name. Altimeter target height changes
only the pixel-snapped apex position of the selected fixed atlas entry.

The full-screen satellite radar remains a separate resolution-aware overlay,
not a cockpit panel. Cockpit-radar and satellite FOV rays share the scene's
effective horizontal-FOV resolver, including cockpit zoom, output aspect, and
the configured horizontal-FOV clamp. Cockpit rays terminate on the radar
ellipse. Satellite rays terminate beyond the physical viewport edge by a
stroke-width-aware margin, so antialiased or wide line caps are clipped
offscreen. The selected target is projected through the same resolution-aware
orthographic satellite camera as the scene. An on-screen target retains the
native satellite cross; an offscreen target uses the shared full-size target
caret selected by the viewport edge crossed by its projection ray and colored
with the normal target classification. Message bars attach to the physical top
and bottom edges, but their horizontal span is the centered 4:3 viewport width
`output_height * 4/3`, rather than a fixed 1024 pixels or the full widescreen
width.

The damaged-satellite scratch target treats 320x240 as its 1024x768 reference,
not as a fixed allocation. Its height scales by `output_height / 768`, its width
follows the physical output aspect, and intermediate native damage windows
retain their proportional extent inside that maximum. The active damaged view
is then stretched across the scene target; all ordinary HUD layers remain at
native output resolution and are omitted from this degraded presentation. The
enhanced damaged-satellite policy also suppresses both forms of selected-target
indicator while retaining the satellite FOV and ordinary radar blips.

The in-mission B and U menu pages are also not anchored cockpit panels. Their
authored pane center retains its offset from the center of the 1024x768 reference
viewport, and that center-relative offset follows `position_scaling`. The page
contents remain native-sized and preserve all pane-local distances. Therefore
their translation is exactly zero at 1024x768. The Esc menu follows its separate
rule: center the visible background artwork and translate the complete page with
it.

## Runtime cost

Layout is arithmetic over the existing HUD snapshot. It must not add another
game-memory walk, geometry extraction, GPU readback, or output-sized CPU work.
Resolved artwork and text may be cached by physical size and OpenGL context;
cache construction or upload must not occur every frame.
