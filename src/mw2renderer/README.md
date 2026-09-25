# MW2 Enhanced Renderer

C++17 x64 Windows DLL for the MechWarrior 2 enhanced-renderer mod. DOSBox-X
loads it through the C API in `include/mw2er_abi.h` and supplies the current
OpenGL context. Building the DLL requires no DOSBox-X checkout or build output.

## Build

Install Visual Studio 2022's C++ build tools and Windows SDK, and CMake 3.21
or newer. With CMake on PATH, run these commands from this directory:

```bat
cmake --preset windows-msvc
cmake --build --preset windows-msvc-release
```

The output is `build/bin/Release/mw2renderer.dll`. Use the
`windows-msvc-debug` build preset for Debug. Normal builds do not copy the DLL
into `mw2mods` or change runtime assets.

`build_release.bat` is an optional Windows convenience wrapper around CMake.
Its `cmake_env.bat` helper discovers CMake on PATH or through Visual Studio,
and it uses `../../tools/run_clean_env.ps1` to normalize duplicate Windows
environment names. Both helpers belong in the source repository, not the
player package. CMake presets and install rules remain the build interface
used by automation.

FreeType 2.13.1 is fetched at configuration time from a SHA-256-pinned source
archive and built as a private static dependency. Its matching headers are
supplied by the same CMake target. Optional external FreeType dependencies
are disabled. Release uses the static MSVC runtime (/MT), and Debug uses
/MTd throughout the DLL's dependencies. There is no separate FreeType DLL.
The default renderer build does not use SDL. `MW2ER_BUILD_TOOLS=ON` enables
optional developer hosts and their separate SDL dependency.

The first configuration needs network access. Cached rebuilds work offline.
For a fresh offline build, supply an extracted FreeType source directory:

```bat
cmake --preset windows-msvc -DFETCHCONTENT_SOURCE_DIR_FREETYPE=C:/sources/freetype-2.13.1
```

An assembled release source package places that source under
`third_party/freetype`, which CMake detects automatically. The repository
itself does not vendor the downloaded dependency.

## Stage and package

Use CMake install components to stage into a fresh directory. From here:

```bat
cmake --install build --config Release --component Runtime --prefix ../../dist/renderer
cmake --install build --config Release --component Symbols --prefix ../../dist/renderer-symbols
cmake --install build --config Release --component DependencySource --prefix ../../dist/release-source
```

`Runtime` installs the just-built DLL, an explicit asset allowlist, and
license notices. Shaders, textures, fonts, and data remain authoritative in
`../../mw2mods`; there is no duplicate asset source tree. Configuration is
staged as `mod.conf.example`. Full package assembly seeds `mod.conf` for new
installations while upgrade handling preserves user settings. Symbols are
separate, and runtime staging excludes tools, logs, static libraries, and
any pre-existing source-tree DLL.

The full release workflow combines this renderer stage with a separately
built, pinned compatible DOSBox-X package, retained Python runtime/mods,
configuration tools, and launchers, then creates the player ZIP. CPack is
not required for this assembly. The standalone native-renderer workflow
builds and stages renderer artifacts; it does not publish a complete game
mod package.

Assemble the source package from the matching repository snapshot plus the
`DependencySource` component at the same package root. This supplies the exact
FreeType source alongside our build scripts and existing vendored sources.
Use FreeType's GPL licensing option and retain its complete notices. GitHub's
automatic repository source ZIP alone does not contain fetched dependencies.
Verify the assembled source builds with `FETCHCONTENT_FULLY_DISCONNECTED=ON`.

For local testing only, explicitly copy the built DLL into the worktree's
`mw2mods` directory. That runtime copy is ignored by Git; normal compilation
never deploys it automatically. Preserve the previous DLL if needed before
replacing it.
