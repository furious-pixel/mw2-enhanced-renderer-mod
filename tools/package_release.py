"""Assemble a portable native release from independently prepared inputs.

Always creates a fresh output directory. Never overlays an existing installation.
Uses only the standard library; the same entry point is used locally and in CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    'README.md', 'LICENSE', 'COPYRIGHT', 'THIRD_PARTY_NOTICES.md', 'VERSION',
    'configure.bat', 'install_mw2_v11_patch.bat', 'dosbox-mw2.conf',
    'launchmw2_30fps.bat', 'launchmw2_60fps.bat', 'launchmw2_72fps.bat',
    'launchmw2_72fps_windowed.bat', 'launchmw2_60fps_maxcycles.bat',
    'launchmw2_sbs_compare.bat',
)
MOD_FILES = ('mod_init.py', 'joystick_input.py', 'jumpjet_fuel_recharge_fix.py',
             '_printfps.py', 'joystick.example.conf')
REMOVED_MODULES = ('numba', 'llvmlite', 'numpy', 'moderngl', 'glcontext', 'freetype')
REQUIRED = ('bin/dosbox-x.exe', 'bin/COPYING', 'bin/glshaders/NOTICE',
            'mw2mods/mw2renderer.dll', 'mw2mods/mod.conf',
            'licenses/freetype/GPLv2.TXT', 'licenses/khronos-opengl.txt',
            'licenses/khronos-platform.txt', 'licenses/stb.txt',
            'licenses/proxy_tools.txt', 'licenses/webview2/LICENSE.txt',
            'licenses/webview2/NOTICE.txt', 'bin/CREDITS.md',
            'bin/HOST_BUILD_INFO.json', 'bin/licenses/zlib.txt',
            'bin/licenses/libpng.txt', 'bin/licenses/SDL2.txt',
            'bin/licenses/SDL_net.txt', 'bin/licenses/freetype/GPLv2.TXT',
            'bin/licenses/pdcurses/core.md', 'bin/licenses/pdcurses/wincon.md', '.venv/Scripts/python.exe',
            '.venv/Scripts/pythonw.exe', '.venv/python-home/python314.dll',
            '.venv/python-home/LICENSE.txt', 'tools/config_ui/index.html')


def copy_file(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_tree(source: Path, destination: Path):
    for file in sorted(source.rglob('*')):
        if file.is_symlink():
            raise ValueError(f'Symlinks are not package inputs: {file}')
        if not file.is_file():
            continue
        relative = file.relative_to(source)
        if '__pycache__' in relative.parts or file.suffix.lower() in ('.pyc', '.pyo', '.lib', '.pdb'):
            continue
        # The configurator uses core SDL2 only. Do not ship optional codecs or
        # SDL_image/mixer/ttf from the wheel, including their transitive DLLs.
        if ('sdl2dll' in relative.parts and file.suffix.lower() == '.dll'
                and file.name.lower() != 'sdl2.dll'):
            continue
        copy_file(file, destination / relative)


def sha256(path: Path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def audit(stage: Path):
    for name in (*ROOT_FILES, *REQUIRED):
        if not (stage / name).is_file():
            raise ValueError(f'Missing runtime file: {name}')
    if not any((stage / 'bin/glshaders/crt').rglob('*.glsl')):
        raise ValueError('Missing CRT shader tree')
    for name in ('inpoutx64.dll', 'FREECG98.BMP', 'wqy_11pt.bdf', 'wqy_12pt.bdf',
                 'Nouveau_IBM.ttf', 'SarasaGothicFixed.ttf'):
        if (stage / 'bin' / name).exists():
            raise ValueError(f'Unused host asset in mod package: {name}')
    mods = stage / 'mw2mods'
    if set(p.name for p in mods.glob('*.py')) != set(MOD_FILES) - {'joystick.example.conf'}:
        raise ValueError('Unexpected Python mod entry points')
    if (mods / 'renderer').exists() or (mods / 'render.py').exists():
        raise ValueError('Legacy Python renderer must not be packaged')
    sites = stage / '.venv/Lib/site-packages'
    if {p.name.lower() for p in (sites / 'sdl2dll').rglob('*.dll')} != {'sdl2.dll'}:
        raise ValueError('Expected only core SDL2 in the Python DLL bundle')
    for name in REMOVED_MODULES:
        if any(sites.glob(name + '*')):
            raise ValueError(f'Unused rendering dependency still packaged: {name}')
    for path in stage.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(stage)
        if (path.suffix.lower() in ('.pdb', '.lib', '.log', '.pyc', '.pyo')
                or '.git' in relative.parts or '__pycache__' in relative.parts):
            raise ValueError(f'Development artifact in package: {relative}')
        if relative.parts[0] == 'game' and relative.as_posix() != 'game/README.txt':
            raise ValueError(f'Game data in package: {relative}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-stage', type=Path, required=True)
    parser.add_argument('--host-root', type=Path, required=True)
    parser.add_argument('--host-revision', required=True)
    parser.add_argument('--venv', type=Path, required=True)
    parser.add_argument('--python-home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-zip', type=Path)
    args = parser.parse_args()
    host_info = json.loads((args.host_root / 'HOST_BUILD_INFO.json').read_text(encoding='utf-8'))
    if host_info['revision'] != args.host_revision:
        raise ValueError('Host archive revision does not match requested revision')
    if host_info['executable_sha256'] != sha256(args.host_root / 'dosbox-x.exe'):
        raise ValueError('Host executable does not match its manifest')
    args.output.mkdir(parents=True, exist_ok=False)
    version = (ROOT / 'VERSION').read_text().strip()
    if tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version'] != version:
        raise ValueError('VERSION and pyproject.toml disagree')
    package_name = f'mw2-enhanced-renderer-mod-v{version}-windows-x64'
    stage = args.output / package_name
    stage.mkdir()
    copy_tree(args.runtime_stage, stage)
    for file in ROOT_FILES:
        copy_file(ROOT / file, stage / file)
    copy_file(ROOT / f'.github/release-notes/v{version}.md', stage / 'RELEASE_NOTES.md')
    for file in MOD_FILES:
        copy_file(ROOT / 'mw2mods' / file, stage / 'mw2mods' / file)
    copy_file(stage / 'mw2mods/mod.conf.example', stage / 'mw2mods/mod.conf')
    for name in ('media', 'tools/config_ui'):
        copy_tree(ROOT / name, stage / name)
    copy_file(ROOT / 'tools/configure.py', stage / 'tools/configure.py')
    copy_tree(args.host_root, stage / 'bin')
    # Copy only the environment's runtime directories, never a developer base runtime.
    for name in ('Scripts', 'Lib'):
        copy_tree(args.venv / name, stage / '.venv' / name)
    copy_tree(args.python_home, stage / '.venv/python-home')
    (stage / '.venv/pyvenv.cfg').write_text(
        'home = .venv\\python-home\nimplementation = CPython\n'
        'version_info = 3.14.3\ninclude-system-site-packages = false\n', newline='\n')
    (stage / 'game').mkdir()
    (stage / 'game/README.txt').write_text(
        'Add your own DOS v1.1 game installation and BIN/CUE image here.\n'
        'See README.md. No game files are included.\n', newline='\n')
    provenance = {
        'renderer_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'renderer_worktree_changes': subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=ROOT, text=True).splitlines(),
        'host_revision': args.host_revision,
        'renderer_sha256': sha256(stage / 'mw2mods/mw2renderer.dll'),
        'host_sha256': sha256(stage / 'bin/dosbox-x.exe'),
        'uv_lock_sha256': sha256(ROOT / 'uv.lock'),
        'renderer_abi': 6,
    }
    (stage / 'BUILD_INFO.json').write_text(json.dumps(provenance, indent=2) + '\n', newline='\n')
    audit(stage)
    archive_path = args.output / f'{package_name}.zip'
    with zipfile.ZipFile(archive_path, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in sorted(stage.rglob('*')):
            if file.is_file():
                archive.write(file, file.relative_to(stage).as_posix())
    (args.output / f'{package_name}.zip.sha256').write_text(
        f'{sha256(archive_path)}  {archive_path.name}\n', newline='\n')
    sizes = {'candidate': {'compressed': archive_path.stat().st_size,
                          'extracted': sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())}}
    if args.baseline_zip:
        with zipfile.ZipFile(args.baseline_zip) as archive:
            sizes['v0.9.2'] = {'compressed': args.baseline_zip.stat().st_size,
                               'extracted': sum(p.file_size for p in archive.infolist())}
    (args.output / 'sizes.json').write_text(json.dumps(sizes, indent=2) + '\n', newline='\n')
    print(json.dumps(sizes, indent=2))
    print(stage)


if __name__ == '__main__':
    main()
