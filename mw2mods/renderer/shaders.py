import re
from pathlib import Path


SHADER_ROOT = Path(__file__).resolve().parent.parent / "shaders"
_UNRESOLVED_TOKEN = re.compile(r"@[A-Z][A-Z0-9_]*@")
_SHARED_SUBSTITUTIONS = {
    "SCENE_LIGHTING_FUNCTIONS": (
        SHADER_ROOT / "scene_lighting.glsl"
    ).read_text(encoding="utf-8")
}


def load_shader(filename, substitutions=None):
    path = SHADER_ROOT / filename
    source = path.read_text(encoding="utf-8")
    values = {**_SHARED_SUBSTITUTIONS, **(substitutions or {})}
    for name, value in values.items():
        source = source.replace(f"@{name}@", str(value))

    unresolved = _UNRESOLVED_TOKEN.search(source)
    if unresolved is not None:
        raise ValueError(
            f"unresolved shader token {unresolved.group(0)} in {path}"
        )
    return source


def load_program(ctx, name, substitutions=None):
    try:
        return ctx.program(
            vertex_shader=load_shader(f"{name}.vert", substitutions),
            fragment_shader=load_shader(f"{name}.frag", substitutions),
        )
    except Exception as error:
        raise RuntimeError(f"failed to compile shader program {name!r}") from error
