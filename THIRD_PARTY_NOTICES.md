# Third-party notices

MechWarrior 2 Enhanced Renderer Mod is built with and distributed alongside
several open-source projects. These components are not relicensed under the
mod's GPL-2.0-or-later license; each remains under its own license.

The release packaging must preserve the complete license files supplied with
the bundled DOSBox-X build, Python runtime, font, Python packages, native DLLs,
and shaders. The notices below are a practical guide, not a replacement for
those license texts.

## Bundled host and assets

| Component | License | Source or included notice |
| --- | --- | --- |
| [dosbox-x-mod](https://github.com/furious-pixel/dosbox-x-mod) | GPL-2.0-or-later | Bundle its `COPYING`, README, credits, and applicable shader notices. The bundled host is [dosbox-x-mod v0.2.0](https://github.com/furious-pixel/dosbox-x-mod/releases/tag/v0.2.0). |
| Squarish Sans CT | SIL Open Font License 1.1 | `mw2mods/fonts/OFL.txt` |
| CPython | Python Software Foundation License Version 2 and other included notices | Preserve the runtime's `LICENSE.txt` and bundled notices. |
| FreeType | FreeType License or GPL-2.0 | The Windows `freetype-py` wheel includes `libfreetype.dll`; preserve the FreeType notice with the packaged runtime. |

The mod does not contain or redistribute MechWarrior 2 game code, data, or disc
images. MechWarrior 2 and its trademarks belong to their respective owners.

## Locked Python packages and DLL bundles

The following list reflects the packages currently locked for the Windows x64
release. Package versions are recorded in `uv.lock`.

| Package | License reported by the installed distribution |
| --- | --- |
| Bottle | MIT |
| cffi | MIT-0 |
| clr-loader | MIT |
| freetype-py | BSD-3-Clause |
| glcontext | MIT |
| llvmlite | BSD-2-Clause and Apache-2.0 WITH LLVM-exception |
| ModernGL | MIT |
| Numba | BSD-2-Clause |
| NumPy | BSD-3-Clause, with separately licensed bundled components |
| proxy_tools | MIT |
| pycparser | BSD-3-Clause |
| PySDL2 | Public Domain / zlib |
| pysdl2-dll | MPL-2.0, with separately licensed bundled SDL libraries |
| pythonnet | MIT |
| pywebview | BSD-3-Clause |
| typing_extensions | PSF-2.0 |

When producing a release, keep each package's `.dist-info` license files and
all license files included by `pysdl2-dll`, llvmlite, Numba, and NumPy. If the
locked dependency set changes, regenerate and review this table before
publishing.

Some Windows wheels also carry native libraries or support assemblies, notably
FreeType, LLVM, OpenBLAS, SDL, the .NET support used by pythonnet, and WebView2
support used by pywebview. The final archive audit must preserve the notices
that accompany the exact wheels and embedded runtime rather than relying only
on the package names in this summary.

## Project-authored material

The HUD and message-bar artwork and the generated terrain correction data are
original project assets. Except where otherwise noted, they are covered by the
mod's GPL-2.0-or-later license along with the project-authored source code.
