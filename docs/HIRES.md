# High-resolution rendering contract

This document defines the renderer-wide resolution and presentation contracts.
The detailed HUD coordinate, scaling, animation, and artwork rules live in
[`HUD_LAYOUT.md`](HUD_LAYOUT.md).

## Reference spaces

- `1024x768` is the canonical scene and HUD reference resolution.
- Resolution-dependent linear sizes use output height relative to 768 pixels.
  Output width changes aspect ratio and available horizontal space; it must not
  independently stretch scene or HUD geometry.
- Mod-rendered output uses the complete viewport published by DOSBox-X. In
  mod-only presentation that is the drawable; in side-by-side presentation it
  is the mod pane.
- Scene and HUD render targets are recreated when that viewport changes. Camera
  panes render into targets allocated at their resolved destination size rather
  than rendering at 1024x768 and being enlarged afterward.

## Perspective projection

- The scene projection starts from the game's focal length in the canonical
  768-pixel-high reference space.
- `perspective_projection_info()` is the authoritative perspective resolver. It
  derives the game's horizontal field of view, applies height-relative focal
  scaling at the output aspect ratio, and clamps the result with
  `[renderer] max_horizontal_fov_degrees`.
- A field-of-view clamp changes both axes uniformly. It must not squeeze or
  stretch one axis independently.
- The resolved output focal length and effective horizontal field of view are
  shared by scene geometry, scene-projected HUD markers, target visibility, and
  cockpit and satellite field-of-view indicators. These consumers must not
  reproduce the projection calculation independently.
- Scene line widths and point sizes scale from their 768p values using output
  height.

## Presentation modes

### Mod-only

- The enhanced scene fills the published mod viewport.
- Until Python publishes the first usable enhanced frame, the native game image
  is the presentation fallback.
- Renderer activation alone does not establish that an enhanced frame is
  usable. If the callback is absent, disabled, or fails, presentation retains
  the native fallback instead of displaying a cleared pane.

### Side-by-side comparison

- `[render] mod renderer comparison resolution` defines the size of each
  comparison pane. The shipped `1024x768` setting therefore defines a logical
  `2048x768` two-pane canvas.
- The native game occupies the left pane and the mod renderer occupies the
  complete right pane.
- Windowed presentation resizes the drawable to the logical comparison canvas.
  Fullscreen presentation centers that canvas in the desktop drawable with
  black margins.
- When the drawable cannot contain the logical canvas, the complete canvas is
  uniformly fitted. Its panes must not be resized independently.
- Native output is recalculated for the left pane using the active DOSBox-X
  aspect and pixel-scaling policy. Lower-resolution or differently shaped game
  modes retain letterboxing or pillarboxing rather than being stretched.
- Native pixel-perfect scaling is used when its minimum integer-scaled footprint
  fits. Otherwise the native image is uniformly fitted and centered with
  nearest-neighbor sampling so it cannot overflow into the mod pane.

## Non-scene surfaces

- Loading artwork is drawn at its native `1024x768` size and centered over black
  margins.
- Escape-menu pages remain native-sized. The decoded background RLE's visible
  bounds, including shape-origin offsets, are centered, and every page element
  receives the same translation.
- Short-message bars span the centered 4:3 reference canvas derived from output
  height, not the complete widescreen width. Their slots attach to the physical
  top and bottom output edges.
- Fullscreen radar is a resolution-aware overlay. Its X and Y geometry fill the
  physical output independently, while its stroke width remains defined in
  output pixels.
- Satellite projection retains the game-selected world half-width and derives
  orthographic half-height from the actual target aspect ratio.
- The damaged-satellite target uses `320x240` as its 1024x768 reference size.
  Its height scales with output height and its width follows the physical output
  aspect before the degraded image is presented across the scene target.

## Launcher defaults

- `launchmw2_60fps.bat` and `launchmw2_72fps.bat` apply fullscreen,
  desktop-resolution, hidden-menu, mod-only presentation with independent host
  swap VSync. They retain emulated VGA VSync off and select renderer-owned 60
  or 72 FPS pacing respectively. Fixed-refresh displays should use an integer
  multiple of the selected target, such as 120 Hz or 144 Hz.
- `launchmw2_sbs_compare.bat` retains the canonical 1024x768-per-pane comparison
  setup and remains outside renderer-owned frame pacing.

## Runtime and lifecycle constraints

- Resolution changes may recreate size-dependent render targets, cached atlas
  entries, text layouts, and OpenGL resources at the established resize or
  context boundary.
- Steady-state high-resolution presentation must not add another game-memory
  walk, scene walk, geometry extraction, GPU readback, or output-sized CPU
  conversion.
- Size-dependent assets may be cached by resolved physical size and OpenGL
  context. They must not be regenerated or uploaded every frame.
