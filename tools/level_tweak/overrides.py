"""Mission policy and durable tester findings; no renderer dependencies."""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_PATH = ROOT / "mw2mods" / "level_overrides.json"
USER_PATH = ROOT / "mw2mods" / "user_level_overrides.json"
PRESETS = ("game", "shipped", "max", "custom", "game-radial", "farpatcher")
RADIAL_SCALE = math.sqrt(2)
SLIDER_MAX = 8000


def mission_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,31}", value) or "scn" not in value.lower():
        raise ValueError("Invalid mission id")
    return value.lower()


def metres_to_fixed(value):
    if isinstance(value, bool):
        raise ValueError("Distance must be a number")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 0xFFFFFFFF / 100:
        raise ValueError("Distance must be finite, nonnegative, and within the supported range")
    return round(value * 100)


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate override field")
        out[key] = value
    return out


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


def load(path):
    path = Path(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
    except FileNotFoundError:
        return {}
    if len(raw) > 1024 * 1024:
        raise ValueError("Override file is too large")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] not in (1, 2):
        raise ValueError("Unsupported override schema")
    rows = data.get("overrides")
    if not isinstance(rows, dict):
        raise ValueError("Overrides must be an object")
    result = {}
    for scn, row in rows.items():
        key = mission_key(scn)
        if key in result or not isinstance(row, dict):
            raise ValueError("Invalid or duplicate mission override")
        if row.get("preset", "custom") not in PRESETS:
            raise ValueError("Unknown distance preset")
        if "metres" in row:
            if not isinstance(row["metres"], (int, float)):
                raise ValueError("Saved distance must be a number")
            metres_to_fixed(row["metres"])
        result[key] = row
    return result


def community_metres(row):
    metres = float(row.get("metres", 0))
    return metres if 0 < metres < SLIDER_MAX else None


class Catalog:
    def __init__(self, shipped_path=SHIPPED_PATH, user_path=USER_PATH):
        self.user_path = Path(user_path)
        self.shipped = load(shipped_path)
        self.user = load(self.user_path)

    def resolve(self, scn):
        shipped = community_metres(self.shipped.get(scn, {}))
        row = self.user.get(scn)
        preset, metres, source = "max", 0, "default"
        if row is not None:
            preset, source = row.get("preset", "custom"), "user"
            metres = float(row.get("metres", 0))
            if preset == "shipped":
                metres = shipped or community_metres(row) or 0
                if not metres:
                    preset = "max"
            elif preset == "farpatcher":
                metres = community_metres(row) or 0
            elif preset == "max":
                metres = 0
        elif shipped is not None:
            preset, metres, source = "shipped", shipped, "shipped"
        return dict(preset=preset, metres=metres, source=source,
                    comment=str((row or {}).get("comment", "")), critical=bool((row or {}).get("critical")),
                    shipped_metres=shipped, shipped_available=shipped is not None)

    def save(self, scn, preset, fixed, comment, critical, code="", title=""):
        from level_tweak.protocol import K, checked, mutex_lock
        scn = mission_key(scn)
        if preset not in PRESETS or preset == "shipped":
            raise ValueError("Choose a user preset before saving")
        if type(fixed) is not int or not 0 <= fixed <= 0xFFFFFFFF:
            raise ValueError("Invalid distance")
        if not isinstance(comment, str) or len(comment) > 2000 or type(critical) is not bool:
            raise ValueError("Invalid comment or critical flag")
        path = self.user_path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()
        mutex = checked(K.CreateMutexW(None, False, "Local\\mw2_level_save_" + key))
        try:
            with mutex_lock(mutex, 2000):
                # Read under the cross-process lock, preserving saves by other
                # independently launched panels and unrelated mission metadata.
                rows = load(path)
                rows[scn] = dict(rows.get(scn, {}), preset=preset, metres=(0 if preset == "max" else fixed / 100),
                                 comment=comment, critical=critical, code=code, title=title)
                text = json.dumps(dict(schema_version=2, overrides=rows), indent=2, allow_nan=False) + "\n"
                if len(text.encode("utf-8")) > 1024 * 1024:
                    raise ValueError("Save would exceed the renderer's override file size limit")
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                                 prefix=path.stem + ".", suffix=".tmp", delete=False) as tmp:
                    tmp.write(text)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                for attempt in range(4):
                    try:
                        os.replace(tmp.name, path)
                        break
                    except PermissionError:
                        if attempt == 3:
                            raise
                        time.sleep(0.05)
                self.user = rows
        finally:
            K.CloseHandle(mutex)
        return path
