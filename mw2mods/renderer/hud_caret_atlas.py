"""Compatibility import for the generalized static HUD atlas."""

from .hud_atlas import HudAtlas as CaretAtlas
from .hud_atlas import HudAtlasEntry as CaretAtlasEntry
from .hud_atlas import hud_atlas


def round_caret_atlas(panel_scale):
    return hud_atlas(panel_scale, 1.0)
