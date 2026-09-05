# Meter drawing (working spec)

Temporary construction contract for HUD power meters. Layout, scale, and
anchors remain [`HUD_LAYOUT.md`](HUD_LAYOUT.md) / [`HIRES.md`](HIRES.md).
This file is only how a meter becomes output pixels.

## Rule

Snap **positions** to output-pixel boundaries first. Build rectangles from
those integers. Do not snap independently transformed rectangle edges after
the fact.

Output pixels are the hi-res native pixel grid of the mod viewport. Panel
and height scaling apply **before** that snap, never after.

Inclusive pixel ranges. Adjacent spans meet as `[A, P]` then `[P + 1, B]`.
They never share a column and never leave a hole.

## Shared setup

In 1024x768 reference space the bar has origin and size. The panel transform
maps that to continuous output coordinates. Then:

```text
L = round(output_left)
T = round(output_top)
R = round(output_right) - 1    # inclusive
B = round(output_bottom) - 1
```

`round` is half-up (`floor(x + 0.5)`). If `R < L` or `B < T`, draw nothing.

A scalar `t` in `[0, 1]` becomes one inclusive pixel along the bar:

```text
span = R - L + 1                 # or B - T + 1 if vertical
P    = L + round(t * span) - 1   # last pixel of the "positive" span
```

- `t <= 0` → no positive span (`P < L`)
- `t >= 1` → `P = R` (or `B`)
- otherwise `L <= P <= R`

Empty and full are whole-bar rectangles in one color. Partial fills are two
(or three) inclusive rectangles.

Shading (native four-band or enhanced gradient) is applied **inside** each
already-built rectangle. It must not change the rectangle's output bounds.

## Jump-jet (left-to-right)

`t = clamp(charge / 0x71C, 0, 1)` after the interpolator. Negative `mech+0xC0`
hides the bar.

- `P < L` → `[L, R]` uncharged `0x0B`
- `P >= R` → `[L, R]` charged `0x0F`
- else → charged `[L, P]` `0x0F`, uncharged `[P + 1, R]` `0x0B`

Heat-rate is the same geometry with its own `t` (`current / 0x300`) and
palette pairs.

## Heat (symmetric about center)

Same snapped `[L, R]`. `t` is the interpolator's pixel width versus
`bar_width` (16.16, then `/ 65536` in reference pixels, then scaled).

Empty (`t <= 0`): whole bar `0x07`.
Full (`t >= 1`): whole bar `0x0B`.

Partial: treat the right half as the same prefix/suffix fill as jump-jet
(`t' = 2t` from the outer end while `t <= 0.5`, then `t' = 2t - 1` from the
center). Mirror those spans onto the left half. An odd center column copies
the color of the inner edge of the right half, so both sides round the same
way.

## Throttle (framed vertical)

### Frame first

Reference outer box is 17×N inclusive, color `0x0A`. Snap its four edges,
then:

```text
stroke = max(1, round(min(|scale_x|, |scale_y|)))
inner  = outer inset by stroke on every side
```

The fill lives strictly inside `inner`. Interior width is `inner_right -
inner_left` in half-open output pixels (inclusive: `IR - IL + 1` if using
inclusive ends). Native `fill_x` / `fill_width` do not place this box.

Full forward reaches the inner top (`outer_top + stroke`).
Full reverse reaches the inner bottom (`outer_bottom - stroke`).

### Rest (zero)

Snap the reference rest row (`neutral_y`, or alternate `Y=380`) to an output
pixel `Z` clamped into the inner span.

Zero draw is that rest row at **minimum size** equal to the outline
stroke (`max(1, round(scale))`). Forward uses color `0x0F`; reverse uses
`0x07` even when thrust is zero. Reverse grows downward from `Z`,
forward grows upward, both including the rest row.

### Forward / reverse

`t_fwd = clamp(throttle, 0, 1)` against forward max.
`t_rev = clamp(-throttle, 0, 1)` against reverse max (native reverse max is
half the forward max).

Forward last pixel `P` is interpolated from `Z` toward inner top (inclusive).
Reverse last pixel toward inner bottom.

- rest: `[Z, Z]` `0x0F`
- forward: `[P, Z]` `0x0F` (includes rest row)
- reverse: `[Z, P]` `0x07` (includes rest row)

When `P ==` inner top or inner bottom, the fill meets the inner frame. No
second remap through a reference inner height.

## Implementation

`renderer/hud_meter_spans.py` snaps positions and builds inclusive spans.
Snapshot code stores a reference bar, `amount` in `[0, 1]`, and a grow mode
(`LEFT_TO_RIGHT` or `SYMMETRIC`). The draw path scales, snaps, then emits
rectangles. Native four-band and enhanced gradient shading fill those
rectangles without moving their edges.

Throttle rest is one output-pixel row at the snapped rest Y, color `0x0F`.
Native original zero used two inclusive scanlines; the enhanced contract
keeps a single rest row.
