# MechWarrior 2 Enhanced Renderer Mod

An experimental C++ OpenGL renderer mod and collection of fixes for the DOS version
of **MechWarrior 2: 31st Century Combat**.

**Status:** v0.10.0 beta (C++ renderer). Expect some rough edges and please
report major, reproducible issues.

**Supported platform:** Windows x64 on Intel or AMD 64-bit hardware. Linux,
Wine, and Windows ARM64 are not supported.

![Widescreen enhanced cockpit HUD showing the damage wireframe and centered HTAL meters](media/mw2-EnhancedRendererMod-htal-damage-wireframe.png)

I made this to give my first *MechWarrior 2: 31st Century Combat* playthrough a
polished take on the DOS version's retro aesthetic—and to play it with a
HOTAS. The mod still runs the original game and uses its original assets.
MechWarrior 2 remains in charge of the simulation, missions, AI, controls,
sound, music, and interface.

This is primarily a renderer mod for the 3D missions, with HOTAS support (axis
input only) and a handful of bug fixes for issues that get in the way of
playing. The MW2 shell (that is the non-mission interface) remains native,
with a CRT shader making it more presentable on a modern display.

**[Watch the v0.9.0 showcase video (MP4, 18 MB)](https://github.com/furious-pixel/mw2-enhanced-renderer-mod/releases/download/v0.9.0/mw2-EnhancedRendererModv0.9.mp4)**

## Highlights

![Native and enhanced renderers showing the same dropship and mech scene side by side](media/native-vs-enhanced-dropship-mech.png)

*The original game renderer is on the left; the enhanced renderer is on the
right.*

![Native and enhanced renderers showing the helicopter rotor treatment side by side](media/native-vs-enhanced-heli.png)

*The enhanced renderer, on the right, gives helicopter rotors a smoother
motion-blurred appearance.*

![Native HTAL meters and the enhanced combined HTAL and damage-wireframe layout](media/native-vs-enhanced-HTAL.png)

*The native HTAL display is on the left; the enhanced combined HTAL and damage
wireframe layout is on the right.*

### Rendering

- High-resolution and widescreen mission rendering with 4x supersampling.
- Switch between the enhanced and original game renderers at any time with
  `Ctrl+/`, or place them side by side with `Ctrl+Shift+/`.
- A scalable widescreen HUD using MechWarrior 2's iconic font.
- Higher-precision cockpit motion without the original polygon wobble.
- Crisper rebuilt compass and altimeter displays that retain the original
  visual style.
- Enhanced imaging that keeps explosions, weapon effects, jump-jet puffs,
  flags, and other sprites textured instead of using placeholder geometry
  which ruins immersion.
- Full mission render distance. This remains experimental and may interfere
  with or spoil some missions.
- Better model LOD selection and reduced terrain seams.
- Motion-blurred helicopter rotors and aeroplane lift fans.
- Bicubic-filtered mech camouflage for a smoother appearance, with repeated
  panel textures adding apparent detail to dropships.
- A combined HUD layout that shows the animated damage wireframe and HTAL
  meters simultaneously, with centered HTAL labels and more intuitive compass
  rotation.

### Gameplay and input

- HOTAS axis mapping and a live input preview in the configurator's Input
  section (run `configure.bat`).
- Frame pacing with 60 and 72 FPS profiles for smoother motion and fewer
  timing-related LRM and audio problems.
- A fix for jump-jet fuel failing to recharge correctly at higher frame rates.

## More detail

### Cockpit and HUD

The cockpit model is composed at higher precision, removing much of the
polygon wobble caused by the original integer-rounded transforms.

The in-mission HUD is redrawn as a resolution-aware OpenGL overlay, with
independent scaling for its layout, panels, text, markers, and camera views
instead of simply stretching the original 640x480 output.

HUD markers, meters, and radar contacts retain smooth motion at the final
output resolution. The compass and altimeter are rebuilt for a crisper result
while preserving the look of the original instruments.

The compass now behaves more intuitively from the cockpit point of view: when
you turn right, the compass moves left along with the world. The original DOS
game moved the compass right as well, an odd behavior that appears to have
been corrected in later MechWarrior 2 games.

The HTAL labels are centered over their armor and internal-structure meters,
and a combined layout can show those meters and the animated damage wireframe
at the same time.

The HUD now uses MechWarrior 2's iconic font. The font used is Squarish Sans
CT, a freely licensed reproduction rather than the Bank Gothic Medium used
for MechWarrior 2 artwork.

The enhanced HUD faithfully reproduces the cockpit startup and shutdown
sequences, the animated damage display, interference on damaged video feeds,
and the damaged satellite uplink's animated glitches.

### Cameras, radar, satellite view, and enhanced imaging

The rear camera is mirrored by default, like a vehicle mirror, and the target
and MFD cameras use the enhanced renderer.

Damaged satellite mode faithfully recreates the original animated degraded
rendering and signal glitches.

Enhanced imaging now keeps explosions, weapon effects, jump-jet puffs, flags,
and other billboards textured and colorful instead of using placeholder
geometry which ruins immersion. HUD camera panes remain normally rendered
while enhanced imaging is active, and the original radial scan reveal is
retained.

### Presentation modes and smoother frame pacing

The package includes tear-free fullscreen launch profiles for 30, 60, and 72
FPS; any can be used. Instead of adjusting DOSBox's CPU-cycle budget to
approximate a frame rate—a rate that changes with the load of each scene—the
renderer paces frames toward a consistent target. Timers, interrupts, input,
and audio continue running between frames. Besides looking smoother, this
helps avoid timing-sensitive problems such as LRMs exploding immediately
after launch and audio glitches.

You can switch presentation at any time:

| Shortcut | Action |
| --- | --- |
| `Ctrl+/` | Switch between the original game image and enhanced renderer. |
| `Ctrl+Shift+/` | Show the original and enhanced renderers side by side. |
| `Ctrl+Alt+/` | Show the comparison while allowing native 3D rendering to be suppressed. |

The enhanced renderer uses the same brightness setting as the native renderer,
controlled by the brightness slider in the in-mission Escape menu.

## Configuration

Run `configure.bat` to edit the renderer, HUD, and HOTAS axis settings, or edit
the `.conf` files directly. The configurator includes a live preview of the
calibrated turret, chassis-turn, and throttle commands. HOTAS button binding is
not built in; use a tool such as Joystick Gremlin to map buttons to MechWarrior
2 keyboard controls. Joystick input remains disabled until axes are configured.

## Updating from an earlier installation

Extract the release into a fresh directory, then copy these from your previous
installation:

- The `game/` directory.
- `mw2mods/mod.conf`.
- `mw2mods/joystick.conf`, if present.

You can then launch the game without running the configurator again.

## Installing

Download the Windows x64 package from the
[v0.10.0 beta release](https://github.com/furious-pixel/mw2-enhanced-renderer-mod/releases/tag/v0.10.0).
The [v0.9.2 beta](https://github.com/furious-pixel/mw2-enhanced-renderer-mod/releases/tag/v0.9.2)
remains available as the earlier Python-renderer fallback.

The release is intended to be self-contained. It includes the mod, its Python
runtime and dependencies, and the required
[dosbox-x-mod](https://github.com/furious-pixel/dosbox-x-mod) host. You do not
need to install Python, uv, or a separate copy of DOSBox-X.

Supported setup:

- Windows x64 on Intel or AMD 64-bit hardware. Linux, Wine, and Windows ARM64
  are not supported.
- Your own copy of **MechWarrior 2: 31st Century Combat for DOS**, updated to
  **version 1.1**. Other editions are not supported.
- One of the included 30, 60, or 72 FPS launch profiles.
- The installed DOS game directory and your `.bin`/`.cue` CD image files.

### First-time installation

Extract the release into a fresh directory, run `./configure.bat`, and follow
the onscreen instructions to install and configure the game.

The following sections provide manual setup details for reference.

### 1. Copy the game files

After extracting the release, place your files like this:

```text
MW2-EnhancedRenderer/
├── game/
│   ├── MECH2_16B.BIN
│   ├── MECH2_16B.CUE
│   └── c_mech2/
│       └── mech2/  <- complete installed game directory
│           ├── MW2.EXE
│           ├── MW2.PRJ
│           └── ... all other installed game files
├── bin/
├── mw2mods/
├── configure.bat
├── install_mw2_v11_patch.bat
├── launchmw2_30fps.bat
├── launchmw2_60fps.bat
```

If your DOS copy is not already updated:

1. Download the
   [official DOS v1.1 patch](https://www.moddb.com/games/mechwarrior-2-31st-century-combat/downloads/mechwarrior-2-dos-v11-patch)
   and save `mech2v11.zip` beside `install_mw2_v11_patch.bat`.
2. Run `install_mw2_v11_patch.bat`. In the DOSBox-X window that opens, choose
   option 2 to apply the patch. When it reports
   `Version 1.1 patching process complete`, type `EXIT`.
3. Run `configure.bat`, open **Game Installation**, and confirm that `MW2.EXE`
   and `MW2.PRJ` are verified.

The configurator checks the expected directory shown above; it does not
discover a different mount path edited into the DOSBox configuration.

### 2. Configure the DOS game

In combat variable, set the **"Detail section"** as follows:

| Setting | Value |
| --- | --- |
| Object Textures | On |
| Terrain Textures | On |
| Display Detail | High |
| Object Density | High |
| Chunky Explosions | On |
| Resolution | 1024x768 |

The mod is verified to work only with all effects enabled and the game
resolution set to 1024x768.

After reviewing any other settings in `configure.bat`, run
`launchmw2_60fps.bat` to play at up to 60 FPS, or `launchmw2_30fps.bat` for
a 30 FPS target. Both use a fixed 250,000 CPU cycles.
`launchmw2_72fps.bat` also uses maximum cycles and targets 72 FPS.

No game files are included with this project or its releases.

## Current limitations

This renderer deliberately concentrates on the playable 3D missions. The MW2
shell, briefings, mission selection, and other non-mission screens remain
native.

A few known rough edges remain:

- Higher frame rates such as 90 FPS are supported by editing the batch file,
  but currently break LRM missiles. Use one of the included 30 or 60 FPS
  profiles for normal play.
- Full render distance may interfere with or spoil how some missions are
  intended to play.
- Occasionally, pressing `Esc` during a mission can terminate the mission with
  a `divide overflow` error. Similar errors are known to other MechWarrior 2
  players, but the cause in this setup is not yet understood.
- The renderer preloads all mission textures. On some systems this may cause
  resource-cache pressure or long loading times in certain missions. If this
  happens, run `configure.bat`, set **Advanced → Disable texture preload** to
  **true**, and use `launchmw2_sbs_compare.bat` to launch the enhanced and
  original renderers side by side.
- Terrain correction greatly reduces cracks, but a few residual seams may
  remain.
- Impact red-out can differ slightly from the native ground color.

## Issues

Reports of major reproducible issues are welcome and appreciated. Please
report them through
[GitHub Issues](https://github.com/furious-pixel/mw2-enhanced-renderer-mod/issues).

## A note on the bundled DOSBox-X

This package requires the native-renderer build of
[dosbox-x-mod](https://github.com/furious-pixel/dosbox-x-mod).
The exact host revision and binary hash are recorded in `BUILD_INFO.json`.
The v0.2.1 Python-release host is not a substitute. Python remains embedded
for input and gameplay mods; rendering runs in `mw2mods/mw2renderer.dll`.

The fork is not an official DOSBox-X release. General DOSBox-X information and
the upstream project are available at
[joncampbell123/dosbox-x](https://github.com/joncampbell123/dosbox-x).

## Developing from source

To run the mod from a source checkout on Windows, install
[uv](https://docs.astral.sh/uv/) and create the locked Python environment from
the repository root:

```powershell
uv sync --frozen
```

Build the DLL using the CMake instructions in
[src/mw2renderer/README.md](src/mw2renderer/README.md). Stage its Runtime
component and pair it with a matching native DOSBox-X build, including
`bin/glshaders/`. The DLL is generated, not committed to Git.

`tools/package_release.py` assembles a fresh portable package from a CMake
runtime stage, a prepared host distribution, and a clean locked Python
environment plus its base runtime. Run it with `--help` for input paths.
Local packaging and the release workflow use this same entry point.

## How the mod works

The host calls the C++ renderer through a versioned DLL interface. The renderer
captures live camera, geometry, palette, texture, and HUD state, renders into
owned OpenGL targets, and composites the finished image into DOSBox-X.
The original game continues to own simulation, missions, AI, sound, and music.

Decoded assets and GPU resources are retained across frames. FreeType is
privately linked into the DLL. Python remains for gameplay/input mods and
the configurator; NumPy, Numba, ModernGL, and Python FreeType bindings are
not required or shipped by the native package.

## Clean-room reverse engineering

This is a clean-room reverse-engineered implementation built with AI agents.
Reverse-engineering agents analyze the original game on one computer and turn
their findings into behavioral and data-format specifications. Separate
implementation agents on another computer write the mod from those
specifications, without using proprietary source code, copied disassembly, or
decompiler output as implementation input. When a specification needs
clarification, narrow runtime instrumentation and memory observation is used
to test the game's behavior.

## A note on the code

The renderer was prototyped in Python and is now implemented in C++. It
still follows the original game data structures rather than replacing the
game. The Python implementation remains available in earlier releases.

## Acknowledgements

Thanks to @anpage for [documenting the high-frame-rate jump-jet fuel issue in
detail](https://gist.github.com/anpage/9b5ec3d72200117e224b2e696e8b4280),
which helped me understand the fuel-recharge issue in depth.

Thanks to @Kaidine for the [MechWarrior Joystick
Guide](https://github.com/Kaidine/Mechwarrior-Joystick-Guide/blob/main/mechwarrior%202/31st%20century%20combat/setup%20instructions.md#play-the-game),
which made me aware that *MW2: 31CC* uses zero-order absolute joystick
positioning. That informed the mod's HOTAS axis support for both
absolute-position and relative-rate control.

## Credits and license

The MechWarrior 2 Enhanced Renderer mod is an unofficial fan project and is
not affiliated with or endorsed by the creators or publishers of MechWarrior
2. All game names and trademarks belong to their respective owners.

Except where otherwise noted, the mod's original code and original assets are
licensed under the GNU General Public License version 2 or any later version
(GPL-2.0-or-later). See `LICENSE` for the license text and `COPYRIGHT` for its
scope. DOSBox-X, the bundled font, Python, and the Python/OpenGL dependencies
retain their own licenses, documented in `THIRD_PARTY_NOTICES.md` and the
license files shipped with them.

The mod does not include or redistribute MechWarrior 2 itself.

Thanks to the DOSBox-X project and to the open-source projects that make the
renderer possible, including FreeType, Khronos OpenGL headers, stb, PySDL2, and
PyWebView.
