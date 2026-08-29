"""Local pywebview configuration application for the enhanced renderer."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parents[1]
MODS_DIR = ROOT_DIR / "mw2mods"
UI_PATH = Path(__file__).resolve().parent / "config_ui" / "index.html"
if str(MODS_DIR) not in sys.path:
    sys.path.insert(0, str(MODS_DIR))

from mod_init import (  # noqa: E402
    CONFIG_FILE_PATHS,
    config_schema_records,
    load_mod_config,
    normalize_config_values,
)


CANONICAL_RECORDS = config_schema_records()
CANONICAL_BY_SETTING = {
    (record["section"].lower(), record["name"]): record
    for record in CANONICAL_RECORDS
}
CONFIG_PATHS = {
    file_name: Path(path)
    for file_name, path in CONFIG_FILE_PATHS.items()
}
RESET_FILE_PATHS = [str(path) for path in CONFIG_PATHS.values()]

GAME_INSTALL_DIR = ROOT_DIR / "game" / "c_mech2" / "mech2"
SUPPORTED_GAME_FILES = (
    {
        "name": "MW2.EXE",
        "expected_sha256": (
            "c4a42d0d448de50a75c7a41f40bb7146"
            "c6afa70d646cc18e6bacb7850737903f"
        ),
    },
    {
        "name": "MW2.PRJ",
        "expected_sha256": (
            "74ddb4f3721c0736f7ab59bdb2c07e16"
            "f6677e3605f77895e6efba489d2e8746"
        ),
    },
)


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _installation_state():
    files = []
    for supported_file in SUPPORTED_GAME_FILES:
        path = GAME_INSTALL_DIR / supported_file["name"]
        result = {
            "name": supported_file["name"],
            "path": str(path),
            "relative_path": str(path.relative_to(ROOT_DIR)),
            "exists": path.is_file(),
            "size": None,
            "sha256": None,
            "matches": False,
            "error": None,
        }
        if result["exists"]:
            try:
                result["size"] = path.stat().st_size
                result["sha256"] = _sha256_file(path)
                result["matches"] = (
                    result["sha256"] == supported_file["expected_sha256"]
                )
            except OSError as exc:
                result["error"] = str(exc)
        files.append(result)
    return {
        "ok": all(file_state["matches"] for file_state in files),
        "directory": str(GAME_INSTALL_DIR),
        "files": files,
    }


def _item(key, label=None, **layout):
    """Return visual metadata only; behavior comes from the canonical schema."""
    item = {"key": key}
    if label is not None:
        item["label"] = label
    item.update({name: value for name, value in layout.items() if value is not None})
    return item


def _curve_items(prefix):
    return [
        _item(f"{prefix}_curve_mode", "Response model",
              choice_labels=("Linear", "Power", "Blended")),
        _item(f"{prefix}_curve_k", "Curve blend", step=0.01, unit="%"),
        _item(f"{prefix}_curve_n", "Curve exponent", step=0.05),
    ]


def _response_curve(prefix):
    return {
        "mode": f"{prefix}_curve_mode",
        "blend": f"{prefix}_curve_k",
        "exponent": f"{prefix}_curve_n",
    }


def _group(group_id, title, subtitle, settings, **layout):
    return {
        "id": group_id,
        "title": title,
        "subtitle": subtitle,
        "settings": settings,
        **layout,
    }


SCHEMA = [
    {
        "id": "input",
        "config_section": "input",
        "label": "Input",
        "icon": "controls",
        "groups": [
            _group(
                "aiming_mode", "Turret aiming",
                "Choose direct position or relative rate control for turret yaw and pitch.",
                [
                    _item("joystick_input_enable", "Enable joystick input"),
                    _item("turret_aim_mode", "Aiming behavior",
                          choice_labels=("Direct position", "Relative rate")),
                ],
                featured=True,
                controller_selector=True,
            ),
            _group(
                "turret_yaw", "Turret yaw", "Horizontal torso aiming.",
                [
                    _item("invert_turret_yaw", "Reverse axis"),
                    _item("turret_yaw_deadzone", "Center deadzone",
                          step=0.001, unit="%"),
                    _item("turret_yaw_input_saturation", "Input saturation",
                          step=0.01, unit="%"),
                    _item("turret_yaw_output_saturation", "Output saturation",
                          step=0.01, unit="%"),
                    *_curve_items("direct_turret_yaw"),
                    _item("relative_turret_yaw_degrees_per_second",
                          "Maximum yaw rate", step=1.0, unit="°/s"),
                    *_curve_items("relative_turret_yaw"),
                ],
                input_axis=True,
                binding={"device": "turret_yaw_device", "axis": "turret_yaw_axis"},
                response_curves={
                    "direct": _response_curve("direct_turret_yaw"),
                    "relative": _response_curve("relative_turret_yaw"),
                },
            ),
            _group(
                "turret_pitch", "Turret pitch", "Vertical torso aiming.",
                [
                    _item("invert_turret_pitch", "Reverse axis"),
                    _item("turret_pitch_deadzone", "Center deadzone",
                          step=0.001, unit="%"),
                    _item("turret_pitch_input_saturation", "Input saturation",
                          step=0.01, unit="%"),
                    _item("turret_pitch_output_saturation", "Output saturation",
                          step=0.01, unit="%"),
                    *_curve_items("direct_turret_pitch"),
                    _item("relative_turret_pitch_degrees_per_second",
                          "Maximum pitch rate", step=1.0, unit="°/s"),
                    *_curve_items("relative_turret_pitch"),
                ],
                input_axis=True,
                binding={"device": "turret_pitch_device", "axis": "turret_pitch_axis"},
                response_curves={
                    "direct": _response_curve("direct_turret_pitch"),
                    "relative": _response_curve("relative_turret_pitch"),
                },
            ),
            _group(
                "chassis_turn", "Chassis turn",
                "Steering or rudder control for chassis rotation.",
                [
                    _item("invert_chassis_turn", "Reverse axis"),
                    _item("chassis_turn_input_deadzone", "Center deadzone",
                          step=0.001, unit="%"),
                    _item("chassis_turn_input_saturation", "Input saturation",
                          step=0.01, unit="%"),
                    _item("chassis_turn_output_saturation", "Output saturation",
                          step=0.01, unit="%"),
                    _item("chassis_turn_scale", "Turn sensitivity",
                          step=0.05, unit="×"),
                    *_curve_items("chassis"),
                ],
                input_axis=True,
                binding={"device": "chassis_turn_device", "axis": "chassis_turn_axis"},
                response_curves={"all": _response_curve("chassis")},
            ),
            _group(
                "throttle", "Throttle",
                "Uncentered power control with captured idle and full endpoints.",
                [
                    _item("invert_throttle", "Reverse axis"),
                    _item("throttle_raw_input_start", "Idle endpoint",
                          control="number", step=1, capture=True),
                    _item("throttle_raw_input_end", "Full-power endpoint",
                          control="number", step=1, capture=True),
                    _item("throttle_output_saturation", "Output saturation",
                          step=0.01, unit="%"),
                    *_curve_items("throttle"),
                ],
                input_axis=True,
                binding={"device": "throttle_device", "axis": "throttle_axis"},
                response_curves={"all": _response_curve("throttle")},
            ),
        ],
    },
    {
        "id": "renderer",
        "config_section": "renderer",
        "label": "Renderer",
        "icon": "display",
        "groups": [
            _group(
                "display_quality", "Display quality",
                "Core presentation and antialiasing controls.",
                [
                    _item("antialiasing", "Antialiasing",
                          choice_labels=("None", "4× SSAA")),
                    _item("ssaa_line_width", "SSAA line width", step=0.1, unit="px"),
                    _item("max_horizontal_fov_degrees", "Maximum horizontal FOV",
                          step=1.0, unit="°"),
                ],
            ),
            _group(
                "entity_detail", "Entity detail",
                "Choose and tune enhanced entity LOD selection.",
                [
                    _item("entity_lod_selection", "LOD selection",
                          choice_labels=("Native", "Projected size", "Force detail 0",
                                         "Force detail 1", "Force detail 2",
                                         "Force detail 3")),
                    _item("entity_lod_detail0_pixels", "Detail 0 threshold",
                          step=0.25, unit="px"),
                    _item("entity_lod_detail1_pixels", "Detail 1 threshold",
                          step=0.25, unit="px"),
                    _item("entity_lod_detail2_pixels", "Detail 2 threshold",
                          step=0.25, unit="px"),
                    _item("entity_lod_hysteresis", "LOD hysteresis",
                          step=0.01, unit="%"),
                    _item("enable_diagnostic_logging",
                          "Enable diagnostic logging"),
                ],
            ),
            _group(
                "enhanced_imaging", "Enhanced imaging",
                "Control wireframe reveal and textured effects.",
                [
                    _item("enhanced_enhanced_imaging", "Textured enhanced imaging"),
                    _item("enhanced_imaging_distance_ratio", "Reveal distance",
                          step=0.1, unit="×"),
                    _item("enhanced_imaging_reveal_time", "Reveal time",
                          step=0.1, unit="s"),
                ],
            ),
            _group(
                "geometry_treatments", "Geometry treatments",
                "Optional visual corrections for vehicles and terrain.",
                [
                    _item("enhanced_heli_rotors", "Enhanced helicopter rotors"),
                    _item("enhanced_aero_lift_fans", "Enhanced lift fans"),
                    _item("reduce_terrain_gaps", "Reduce terrain gaps"),
                ],
            ),
            _group(
                "texture_treatments", "Texture treatments",
                "Enhanced reconstruction and texture frequency.",
                [
                    _item("enhanced_mech_textures", "Enhanced mech textures"),
                    _item("enhanced_dropship_textures", "Enhanced dropship textures"),
                    _item("enhanced_mech_texture_uv_scale",
                          "Mech texture frequency", step=0.125, unit="×"),
                    _item("enhanced_dropship_texture_uv_scale",
                          "Dropship texture frequency", step=0.125, unit="×"),
                ],
            ),
        ],
    },
    {
        "id": "hud",
        "config_section": "HUD",
        "label": "HUD",
        "icon": "hud",
        "groups": [
            _group(
                "hud_scaling", "Resolution scaling",
                "Zero keeps native size; one follows viewport height.",
                [
                    _item("position_scaling", "Position scaling", step=0.05, unit="%"),
                    _item("panel_scaling", "Panel scaling", step=0.05, unit="%"),
                    _item("viewport_scaling", "Viewport scaling", step=0.05, unit="%"),
                    _item("font_scaling", "Font scaling", step=0.05, unit="%"),
                    _item("target_marker_scaling", "Target marker scaling",
                          step=0.05, unit="%"),
                ],
            ),
            _group(
                "hud_rendering", "Instrument rendering",
                "Choose native or enhanced rendering for cockpit instruments.",
                [
                    _item("compass_altimeter", "Compass and altimeter",
                          choice_labels=("Native", "Enhanced")),
                    _item("power_meters", "Power meters",
                          choice_labels=("Native", "Enhanced")),
                    _item("htal_meters", "Armor meters",
                          choice_labels=("Native", "Enhanced")),
                    _item("alt_htal_view", "Alternate HTAL view"),
                ],
            ),
            _group(
                "panel_placement", "Panel placement",
                "Position panel groups within widescreen displays.",
                [
                    _item("middle_panel_vertical_position",
                          "Middle panel vertical position",
                          step=1.0 / 768.0, unit="%"),
                    _item("top_panel_widescreen_position",
                          "Top panel widescreen position",
                          step=0.05, unit="%"),
                    _item("middle_panel_widescreen_position",
                          "Middle panel widescreen position",
                          step=0.05, unit="%"),
                    _item("bottom_panel_widescreen_position",
                          "Bottom panel widescreen position",
                          step=0.05, unit="%"),
                ],
            ),
            _group(
                "meter_tuning", "Meter shading",
                "Tune the enhanced meter lighting profile.",
                [
                    _item("meter_dark_offset", "Dark offset", step=1),
                    _item("meter_light_offset", "Light offset", step=1),
                    _item("meter_peak_offset", "Peak offset", step=1),
                    _item("meter_peak_position", "Peak position",
                          step=0.025, unit="%"),
                ],
            ),
            _group(
                "hud_layout", "Layout and radar",
                "Presentation preferences for cameras and instruments.",
                [
                    _item("rear_camera_mirror", "Mirror rear camera"),
                    _item("alt_throttle_indicator_position",
                          "Alternate throttle indicator position"),
                    _item("radar_stroke_width", "Radar stroke width",
                          step=0.1, unit="px"),
                ],
            ),
        ],
    },
    {
        "id": "debug",
        "config_section": "DEBUG",
        "label": "Advanced",
        "icon": "tune",
        "warning": (
            "These settings can reduce performance or intentionally disable rendering."
        ),
        "groups": [
            _group(
                "caches", "Caching", "Control steady-state resource reuse.",
                [
                    _item("cache_textures", "Cache textures"),
                    _item("cache_geometry_data", "Cache geometry data"),
                    _item("cache_static_geometry", "Cache static geometry"),
                ],
            ),
            _group(
                "diagnostics", "Diagnostic bypasses",
                "Isolate loading, extraction, upload, and drawing costs.",
                [
                    _item("disable_mission_texture_preload",
                          "Disable texture preload"),
                    _item("reuse_geometry_after_first",
                          "Reuse first geometry snapshot"),
                    _item("freeze_geometry_upload_after_first",
                          "Freeze uploads after first"),
                    _item("disable_geometry_upload", "Disable geometry upload"),
                    _item("disable_geometry_draw", "Disable geometry draw"),
                ],
            ),
            _group(
                "native_renderer", "Native renderer",
                "Control native scene suppression and vertex source.",
                [
                    _item("suppress_native_scene_raster",
                          "Suppress native scene raster"),
                    _item("entity_vertex_mode", "Entity vertex source",
                          choice_labels=("Matrix transform", "Game cached")),
                ],
            ),
        ],
    },
]


def _canonical_definition(record, layout=None):
    layout = dict(layout or {})
    value_type = {
        "boolean": "bool",
        "integer": "int",
        "number": "float",
        "string": "choice" if "choices" in record else "string",
    }[record["type"]]
    control = layout.get("control")
    if control is None:
        control = {
            "bool": "boolean",
            "choice": "choice",
            "int": "slider",
            "float": "slider",
            "string": "text",
        }[value_type]
    definition = {
        "key": record["name"],
        "label": layout.get("label", record["name"].replace("_", " ").title()),
        "control": control,
        "description": record["help"],
        "value_type": value_type,
        "config_file": record["file"],
        "config_section": record["section"],
    }
    for visual_name in ("step", "unit", "capture"):
        if visual_name in layout:
            definition[visual_name] = layout[visual_name]
    if "minimum" in record:
        definition["minimum"] = record["minimum"]
        definition["maximum"] = record["maximum"]
    if "choices" in record:
        choice_labels = layout.get("choice_labels", ())
        if choice_labels and len(choice_labels) != len(record["choices"]):
            raise RuntimeError(
                f"Choice labels do not match {record['section']}.{record['name']}"
            )
        definition["choices"] = [
            {
                "value": value,
                "label": choice_labels[index]
                if index < len(choice_labels)
                else str(value).replace("_", " ").title(),
            }
            for index, value in enumerate(record["choices"])
        ]
    return definition


def _apply_canonical_schema():
    represented = set()
    for section in SCHEMA:
        section_file = None
        for group in section["groups"]:
            canonical_settings = []
            for layout in group["settings"]:
                identity = (section["config_section"].lower(), layout["key"])
                record = CANONICAL_BY_SETTING.get(identity)
                if record is None:
                    raise RuntimeError(
                        f"Unknown layout setting {identity[0]}.{identity[1]}"
                    )
                canonical_settings.append(_canonical_definition(record, layout))
                if identity in represented:
                    raise RuntimeError(
                        f"Duplicate layout setting {identity[0]}.{identity[1]}"
                    )
                represented.add(identity)
                section_file = record["file"]
            group["settings"] = canonical_settings
            binding = group.get("binding")
            if binding:
                for key in binding.values():
                    identity = (section["config_section"].lower(), key)
                    record = CANONICAL_BY_SETTING.get(identity)
                    if record is None:
                        raise RuntimeError(
                            f"Unknown binding setting {identity[0]}.{identity[1]}"
                        )
                    if identity in represented:
                        raise RuntimeError(
                            f"Duplicate layout setting {identity[0]}.{identity[1]}"
                        )
                    represented.add(identity)
                    section_file = record["file"]
        section["config_file"] = section_file

    missing = set(CANONICAL_BY_SETTING) - represented
    if missing:
        names = ", ".join(f"{section}.{name}" for section, name in sorted(missing))
        raise RuntimeError(f"Configuration layout is missing: {names}")


_apply_canonical_schema()
SECTION_BY_ID = {section["id"]: section for section in SCHEMA}
SETTING_DEFS = {
    (section["id"], definition["key"]): definition
    for section in SCHEMA
    for group in section["groups"]
    for definition in group["settings"]
}


def _coerce_value(definition, raw_value):
    value_type = definition["value_type"]
    if value_type == "bool":
        if isinstance(raw_value, bool):
            return raw_value
        normalized = str(raw_value).strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
        raise ValueError("expected a boolean")
    if value_type == "choice":
        value = str(raw_value).strip().lower()
        choices = {str(choice["value"]) for choice in definition["choices"]}
        if value not in choices:
            raise ValueError("unsupported choice")
        return value
    if value_type == "string":
        return str(raw_value).strip()
    if value_type == "int":
        value = int(round(float(raw_value)))
    elif value_type == "float":
        value = float(raw_value)
    else:
        raise ValueError("unsupported setting type")

    minimum = definition.get("minimum")
    maximum = definition.get("maximum")
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return int(value) if value_type == "int" else float(value)


def _serialize_value(definition, value):
    if definition["value_type"] == "bool":
        return "true" if value else "false"
    if definition["value_type"] == "int":
        return str(int(value))
    if definition["value_type"] == "float":
        rendered = f"{float(value):.6f}".rstrip("0").rstrip(".")
        return rendered if "." in rendered else f"{rendered}.0"
    return str(value)


def _read_values():
    state = SimpleNamespace()
    conf = load_mod_config(state)
    values = {section["id"]: {} for section in SCHEMA}
    for section in SCHEMA:
        for group in section["groups"]:
            for definition in group["settings"]:
                values[section["id"]][definition["key"]] = getattr(
                    conf,
                    definition["key"],
                )
            for key in group.get("binding", {}).values():
                values[section["id"]][key] = getattr(conf, key)
    return values


def _replace_config_value(text, section_name, key, serialized_value):
    lines = text.splitlines(keepends=True)
    section_pattern = re.compile(r"^\s*\[([^]]+)]\s*(?:[#;].*)?$")
    key_pattern = re.compile(
        rf"^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)(.*?)([ \t]*(?:[#;].*)?)?(\n?)$",
        re.IGNORECASE,
    )
    section_start = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        match = section_pattern.match(line.rstrip("\r\n"))
        if not match:
            continue
        if section_start is None:
            if match.group(1).strip().lower() == section_name.lower():
                section_start = index
            continue
        section_end = index
        break

    if section_start is None:
        suffix = "" if not text or text.endswith("\n") else "\n"
        return (
            text
            + suffix
            + f"\n[{section_name}]\n{key} = {serialized_value}\n"
        )

    for index in range(section_start + 1, section_end):
        match = key_pattern.match(lines[index])
        if not match:
            continue
        comment = match.group(3) or ""
        newline = match.group(4) or "\n"
        lines[index] = f"{match.group(1)}{serialized_value}{comment}{newline}"
        return "".join(lines)

    insertion = section_end
    while insertion > section_start + 1 and not lines[insertion - 1].strip():
        insertion -= 1
    separator = "" if insertion < section_end else "\n"
    lines.insert(insertion, f"{key} = {serialized_value}\n{separator}")
    return "".join(lines)


def _initial_config_text(path):
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if path.name == "joystick.conf":
        example_path = path.with_name("joystick.example.conf")
        if example_path.is_file():
            return example_path.read_text(encoding="utf-8")
    return ""


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)


def _sdl_error(sdl2):
    message = sdl2.SDL_GetError()
    return message.decode("utf-8", "replace") if message else "unknown SDL error"


class JoystickService:
    def __init__(self, poll_hz=60.0):
        self._poll_interval = 1.0 / float(poll_hz)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._sdl2 = None
        self._devices = []
        self._snapshot = {"generation": 0, "devices": ()}

    def start(self):
        try:
            import sdl2
        except ImportError as exc:
            raise RuntimeError(
                "PySDL2 is not installed. Run 'uv sync' and try again."
            ) from exc

        self._sdl2 = sdl2
        if sdl2.SDL_InitSubSystem(sdl2.SDL_INIT_JOYSTICK) != 0:
            raise RuntimeError(f"Could not initialize SDL joysticks: {_sdl_error(sdl2)}")
        sdl2.SDL_JoystickEventState(sdl2.SDL_IGNORE)

        joystick_count = max(0, int(sdl2.SDL_NumJoysticks()))
        for enumeration_index in range(joystick_count):
            handle = sdl2.SDL_JoystickOpen(enumeration_index)
            if not handle:
                continue
            encoded_name = sdl2.SDL_JoystickNameForIndex(enumeration_index)
            name = (
                encoded_name.decode("utf-8", "replace")
                if encoded_name
                else f"SDL joystick {enumeration_index}"
            )
            axis_count = max(0, int(sdl2.SDL_JoystickNumAxes(handle)))
            self._devices.append(
                {
                    "name": name,
                    "handle": handle,
                    "axis_count": axis_count,
                    "enumeration_index": enumeration_index,
                    "duplicate": False,
                }
            )

        name_counts = {}
        for device in self._devices:
            name_counts[device["name"]] = name_counts.get(device["name"], 0) + 1
        for device in self._devices:
            device["duplicate"] = name_counts[device["name"]] > 1

        self._sample()
        self._thread = threading.Thread(
            target=self._poll,
            name="joystick-config-poll",
            daemon=True,
        )
        self._thread.start()

    def _poll(self):
        while not self._stop.wait(self._poll_interval):
            self._sample()

    def _sample(self):
        sdl2 = self._sdl2
        if sdl2 is None:
            return
        sdl2.SDL_JoystickUpdate()
        devices = tuple(
            {
                "name": device["name"],
                "axis_count": device["axis_count"],
                "duplicate": device["duplicate"],
                "axes": tuple(
                    int(sdl2.SDL_JoystickGetAxis(device["handle"], axis_index))
                    for axis_index in range(device["axis_count"])
                ),
            }
            for device in self._devices
        )
        with self._lock:
            self._snapshot = {
                "generation": self._snapshot["generation"] + 1,
                "devices": devices,
            }

    def snapshot(self):
        with self._lock:
            snapshot = self._snapshot
        return {
            "generation": snapshot["generation"],
            "devices": [
                {
                    "name": device["name"],
                    "axis_count": device["axis_count"],
                    "duplicate": device["duplicate"],
                    "axes": list(device["axes"]),
                }
                for device in snapshot["devices"]
            ],
        }

    def validate_binding(self, device_name, axis_index):
        matches = [
            device for device in self._devices
            if device["name"] == device_name
        ]
        if not matches:
            return "The selected SDL joystick is no longer connected."
        if len(matches) != 1 or matches[0]["duplicate"]:
            return "Multiple connected SDL joysticks have this name."
        if axis_index < 0 or axis_index >= matches[0]["axis_count"]:
            return "The selected physical axis is not available."
        return None

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._sdl2 is not None:
            for device in self._devices:
                self._sdl2.SDL_JoystickClose(device["handle"])
            self._devices.clear()
            self._sdl2.SDL_QuitSubSystem(self._sdl2.SDL_INIT_JOYSTICK)
            self._sdl2 = None


class ConfigApi:
    def __init__(self, joystick_service):
        self._lock = threading.Lock()
        self._joystick_service = joystick_service

    def get_state(self):
        with self._lock:
            values = _read_values()
        return {
            "schema": SCHEMA,
            "values": values,
            "installation": _installation_state(),
            "config_paths": {
                section["id"]: str(CONFIG_PATHS[section["config_file"]])
                for section in SCHEMA
            },
            "reset_files": RESET_FILE_PATHS,
            "joysticks": self._joystick_service.snapshot(),
        }

    def get_joystick_snapshot(self):
        return self._joystick_service.snapshot()

    def update_setting(self, section_id, key, raw_value):
        definition = SETTING_DEFS.get((section_id, key))
        if definition is None:
            return {"ok": False, "error": "Unknown configuration setting."}
        try:
            value = _coerce_value(definition, raw_value)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

        with self._lock:
            current = _read_values().get(section_id, {})
            candidate = dict(current)
            candidate[key] = value
            normalized = normalize_config_values(candidate)
            changed_values = {
                name: normalized[name]
                for name in normalized
                if name == key or normalized[name] != current.get(name)
            }
            path = CONFIG_PATHS[definition["config_file"]]
            text = _initial_config_text(path)
            for changed_name, changed_value in changed_values.items():
                changed_definition = SETTING_DEFS[(section_id, changed_name)]
                text = _replace_config_value(
                    text,
                    changed_definition["config_section"],
                    changed_name,
                    _serialize_value(changed_definition, changed_value),
                )
            _atomic_write(path, text)
        return {"ok": True, "values": changed_values}

    def reset_to_shipped_defaults(self):
        try:
            with self._lock:
                texts = {
                    file_name: _initial_config_text(path)
                    for file_name, path in CONFIG_PATHS.items()
                }
                for record in CANONICAL_RECORDS:
                    definition = SETTING_DEFS.get(
                        (record["section"].lower(), record["name"])
                    ) or _canonical_definition(record)
                    file_name = record["file"]
                    texts[file_name] = _replace_config_value(
                        texts[file_name],
                        record["section"],
                        record["name"],
                        _serialize_value(
                            definition, record["shipped_default"]
                        ),
                    )
                for file_name, text in texts.items():
                    _atomic_write(CONFIG_PATHS[file_name], text)
                values = _read_values()
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "values": values,
            "modified_files": RESET_FILE_PATHS,
        }

    def assign_axis(self, group_id, device_name, raw_axis_index):
        input_section = SECTION_BY_ID["input"]
        group = next(
            (
                candidate for candidate in input_section["groups"]
                if candidate["id"] == group_id and candidate.get("binding")
            ),
            None,
        )
        if group is None:
            return {"ok": False, "error": "Unknown joystick control."}
        try:
            axis_index = int(raw_axis_index)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid physical axis."}
        device_name = str(device_name).strip()
        error = self._joystick_service.validate_binding(device_name, axis_index)
        if error:
            return {"ok": False, "error": error}

        device_key = group["binding"]["device"]
        axis_key = group["binding"]["axis"]
        path = CONFIG_PATHS["joystick.conf"]
        with self._lock:
            text = _initial_config_text(path)
            text = _replace_config_value(text, "input", device_key, device_name)
            text = _replace_config_value(text, "input", axis_key, str(axis_index))
            _atomic_write(path, text)
        return {
            "ok": True,
            "device": device_name,
            "axis": axis_index,
            "device_key": device_key,
            "axis_key": axis_key,
        }

def _show_missing_dependency(message):
    print(message, file=sys.stderr)
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            "MechWarrior 2 Enhanced Renderer",
            0x10,
        )
    except Exception:
        pass


def main():
    try:
        import webview
    except ImportError:
        _show_missing_dependency(
            "pywebview is not installed in this Python environment.\n\n"
            "Run 'uv sync' from the project root, then launch configure.bat again."
        )
        return 1

    if not UI_PATH.is_file():
        _show_missing_dependency("Configuration application files are incomplete.")
        return 1

    joystick_service = JoystickService()
    try:
        joystick_service.start()
    except RuntimeError as exc:
        _show_missing_dependency(str(exc))
        return 1

    try:
        webview.create_window(
            "MechWarrior 2 Enhanced Renderer — Configuration",
            UI_PATH.as_uri(),
            js_api=ConfigApi(joystick_service),
            width=1500,
            height=900,
            min_size=(1080, 700),
            background_color="#081116",
            text_select=True,
        )
        webview.start(debug=False)
    finally:
        joystick_service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
