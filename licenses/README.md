# Supplemental dependency notices

- Khronos: OpenGL API header pinned to OpenGL-Registry
  `9cb90ca4902d588bef3c830fbb1da484893bd5fb`; MIT as identified in the header.
  `khronos-platform.txt` reproduces the platform header's embedded grant.
- stb: the MIT alternative from the vendored image headers.
- proxy_tools: upstream `LICENSE.txt`, last changed at
  `ccd35a569b95bec271a59890780c7821756c548a` in
  https://github.com/jtushman/proxy_tools . This BSD license is retained verbatim;
  the installed 0.1.0 wheel's MIT metadata does not replace it.
- WebView2: `LICENSE.txt` and `NOTICE.txt` from the Microsoft-authored
  https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49 package,
  matching the SDK assemblies distributed by the locked pywebview wheel.
- FreeType notices are staged by CMake from the pinned dependency source.

The package also preserves Python wheel notices and the host's `licenses/`,
`COPYING`, `CREDITS.md`, and shader attribution.
