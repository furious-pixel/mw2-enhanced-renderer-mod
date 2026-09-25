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
| [dosbox-x-mod](https://github.com/furious-pixel/dosbox-x-mod) | GPL-2.0-or-later | Bundle its `COPYING`, README, credits, and applicable shader notices. The exact bundled native host revision is recorded in BUILD_INFO.json. |
| Squarish Sans CT | SIL Open Font License 1.1 | `mw2mods/fonts/OFL.txt` |
| CPython | Python Software Foundation License Version 2 and other included notices | Preserve the runtime's `LICENSE.txt` and bundled notices. |
| FreeType | GPL-2.0-or-later (selected option for the native renderer) | The native renderer privately builds pinned upstream FreeType 2.13.1. Its complete license texts are staged under `licenses/freetype/`; the matching source is included in the release source package. |
| Khronos OpenGL API headers | MIT | `licenses/khronos-opengl.txt` and `licenses/khronos-platform.txt`; pinned header provenance is in `src/mw2renderer/third_party/khronos/README.md`. |
| stb_image and stb_image_write | MIT (selected option) | `licenses/stb.txt`; original notices also remain in the vendored headers. |

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
| proxy_tools | BSD-2-Clause (upstream LICENSE.txt; wheel metadata incorrectly reports MIT) |
| pycparser | BSD-3-Clause |
| PySDL2 | Public Domain / zlib |
| pysdl2-dll | MPL-2.0, with separately licensed bundled SDL libraries |
| pythonnet | MIT |
| pywebview | BSD-3-Clause |
| typing_extensions | PSF-2.0 |

When producing a release, keep each package's `.dist-info` license files and
all license files included by `pysdl2-dll` and the other retained packages. If the
locked dependency set changes, regenerate and review this table before
publishing.

Some Windows wheels also carry native libraries or support assemblies, notably
SDL, the .NET support used by pythonnet, and WebView2
support used by pywebview. The final archive audit must preserve the notices
that accompany the exact wheels and embedded runtime rather than relying only
on the package names in this summary.

## Project-authored material

The HUD and message-bar artwork and the generated terrain correction data are
original project assets. Except where otherwise noted, they are covered by the
mod's GPL-2.0-or-later license along with the project-authored source code.

Supplemental notices are included in `licenses/proxy_tools.txt` and
`licenses/webview2/`. The WebView2 notices come from Microsoft's exact
`Microsoft.Web.WebView2` 1.0.3856.49 NuGet package, matching the bundled SDK
assemblies. They cover the SDK components; the separate WebView2 browser runtime
is installed by Microsoft and is not included in this archive.

The Python SDL bundle intentionally retains only `SDL2.dll`. Optional SDL
add-ons and codec DLLs are omitted; the wheel's original metadata and license
files are preserved. The selected DOSBox-X shaders and LUT attribution are
preserved under `bin/glshaders/`, with host credits in `bin/CREDITS.md`.

The mod-specific host package omits optional external PC-98/CJK/TTF fonts
and the InpOut physical-port driver. These are not used by the shipped
VGA/OpenGL configuration. Host source is included as `dosbox-x-source.zip`
in the corresponding-source package, including its generated build header.
