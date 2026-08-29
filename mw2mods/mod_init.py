import configparser
import os
from types import SimpleNamespace


MOD_CONF_PATH = os.path.join(os.path.dirname(__file__), "mod.conf")
JOYSTICK_CONF_PATH = os.path.join(os.path.dirname(__file__), "joystick.conf")
# Missing or invalid settings fall back to the values declared here. The
# shipped mod.conf is a product profile and may intentionally choose others.
CONFIG_SCHEMA = {
    "DEBUG": {
        "cache_textures": True,
        "disable_mission_texture_preload": False,
        "cache_geometry_data": True,
        "cache_static_geometry": True,
        "reuse_geometry_after_first": False,
        "freeze_geometry_upload_after_first": False,
        "disable_geometry_upload": False,
        "disable_geometry_draw": False,
        "suppress_native_scene_raster": True,
        "entity_vertex_mode": ("matrix", ("matrix", "cached")),
    },
    "HUD": {
        "position_scaling": (1.0, 0.0, 1.0),
        "panel_scaling": (0.6, 0.0, 1.0),
        "viewport_scaling": (1.0, 0.0, 1.0),
        "font_scaling": (0.6, 0.0, 1.0),
        "target_marker_scaling": (0.0, 0.0, 1.0),
        "middle_panel_vertical_position": (357.0 / 768.0, 0.0, 1.0),
        "top_panel_widescreen_position": (0.0, 0.0, 1.0),
        "middle_panel_widescreen_position": (0.0, 0.0, 1.0),
        "bottom_panel_widescreen_position": (0.0, 0.0, 1.0),
        "compass_altimeter": ("enhanced", ("native", "enhanced")),
        "power_meters": ("enhanced", ("native", "enhanced")),
        "htal_meters": ("enhanced", ("native", "enhanced")),
        "alt_htal_view": True,
        "meter_dark_offset": (-2, -16, 16),
        "meter_light_offset": (-1, -16, 16),
        "meter_peak_offset": (0, -16, 16),
        "meter_peak_position": (0.375, 0.1, 0.9),
        "rear_camera_mirror": True,
        "alt_throttle_indicator_position": True,
        "radar_stroke_width": (1.5, 0.5, 8.0),
    },
    "renderer": {
        "antialiasing": ("none", ("none", "ssaa_4x")),
        "ssaa_line_width": (1.0, 0.5, 8.0),
        "max_horizontal_fov_degrees": (105.0, 30.0, 170.0),
        "entity_lod_selection": (
            "projected_size",
            (
                "native", "projected_size", "detail0", "detail1",
                "detail2", "detail3",
            ),
        ),
        "entity_lod_detail0_pixels": (16.0, 0.25, 4096.0),
        "entity_lod_detail1_pixels": (10.0, 0.25, 4096.0),
        "entity_lod_detail2_pixels": (5.0, 0.25, 4096.0),
        "entity_lod_hysteresis": (0.12, 0.0, 0.45),
        "enable_diagnostic_logging": False,
        "enhanced_enhanced_imaging": True,
        "enhanced_imaging_distance_ratio": (1.5, 0.1, 10.0),
        "enhanced_imaging_reveal_time": (0.3, 0.0, 30.0),
        "enhanced_heli_rotors": True,
        "enhanced_aero_lift_fans": True,
        "reduce_terrain_gaps": True,
        "enhanced_mech_textures": True,
        "enhanced_dropship_textures": True,
        "enhanced_mech_texture_uv_scale": (2.0, 0.125, 16.0),
        "enhanced_dropship_texture_uv_scale": (2.0, 0.125, 16.0),
    },
    "input": {
        "joystick_input_enable": False,
        "turret_aim_mode": ("direct", ("direct", "relative")),
        "turret_yaw_device": "",
        "turret_yaw_axis": 0,
        "turret_pitch_device": "",
        "turret_pitch_axis": 1,
        "chassis_turn_device": "",
        "chassis_turn_axis": 2,
        "throttle_device": "",
        "throttle_axis": 3,
        "invert_turret_yaw": False,
        "invert_turret_pitch": True,
        "invert_chassis_turn": False,
        "invert_throttle": True,
        "turret_yaw_deadzone": (0.001, 0.0, 0.5),
        "turret_yaw_input_saturation": (1.0, 0.05, 1.0),
        "turret_yaw_output_saturation": (1.0, 0.05, 1.0),
        "turret_pitch_deadzone": (0.001, 0.0, 0.5),
        "turret_pitch_input_saturation": (1.0, 0.05, 1.0),
        "turret_pitch_output_saturation": (1.0, 0.05, 1.0),
        "chassis_turn_input_deadzone": (0.0, 0.0, 0.5),
        "chassis_turn_input_saturation": (1.0, 0.05, 1.0),
        "chassis_turn_output_saturation": (1.0, 0.05, 1.0),
        "direct_turret_yaw_curve_mode": (
            "blended_curve", ("linear", "power", "blended_curve"),
        ),
        "direct_turret_yaw_curve_k": (0.75, 0.0, 1.0),
        "direct_turret_yaw_curve_n": (1.7, 0.01, 8.0),
        "direct_turret_pitch_curve_mode": (
            "blended_curve", ("linear", "power", "blended_curve"),
        ),
        "direct_turret_pitch_curve_k": (0.75, 0.0, 1.0),
        "direct_turret_pitch_curve_n": (1.7, 0.01, 8.0),
        "relative_turret_yaw_degrees_per_second": (256.0, 0.0, 10000.0),
        "relative_turret_pitch_degrees_per_second": (256.0, 0.0, 10000.0),
        "relative_turret_yaw_curve_mode": (
            "linear", ("linear", "power", "blended_curve"),
        ),
        "relative_turret_yaw_curve_k": (0.75, 0.0, 1.0),
        "relative_turret_yaw_curve_n": (1.7, 0.01, 8.0),
        "relative_turret_pitch_curve_mode": (
            "linear", ("linear", "power", "blended_curve"),
        ),
        "relative_turret_pitch_curve_k": (0.75, 0.0, 1.0),
        "relative_turret_pitch_curve_n": (1.7, 0.01, 8.0),
        "chassis_turn_scale": (2.0, 0.0, 16.0),
        "chassis_curve_mode": (
            "blended_curve", ("linear", "power", "blended_curve"),
        ),
        "chassis_curve_k": (0.75, 0.0, 1.0),
        "chassis_curve_n": (1.7, 0.01, 8.0),
        "throttle_raw_input_start": (-32768, -32768, 32766),
        "throttle_raw_input_end": (32767, -32767, 32767),
        "throttle_output_saturation": (1.0, 0.05, 1.0),
        "throttle_curve_mode": (
            "linear", ("linear", "power", "blended_curve"),
        ),
        "throttle_curve_k": (0.75, 0.0, 1.0),
        "throttle_curve_n": (1.7, 0.01, 8.0),
    },
}

# Shipped settings normally match the safe fallbacks above. Keep intentional
# product-profile differences explicit so configuration tools can restore the
# shipped profile without maintaining another defaults table.
CONFIG_SHIPPED_OVERRIDES = {
    "renderer": {
        "antialiasing": "ssaa_4x",
    },
}


# User-facing setting help has the same section/key shape as CONFIG_SCHEMA so
# shipped configuration comments and configuration tools can use one source.
CONFIG_HELP = {
    "DEBUG": {
        "cache_textures": "Cache resolved CEL pixels while continuing to resolve live descriptor state each frame.",
        "disable_mission_texture_preload": "Skip mission-wide texture preload and load textures on demand.",
        "cache_geometry_data": "Cache parsed face topology while recomputing transforms and vertices.",
        "cache_static_geometry": "Cache final geometry for blocks the game marks as static.",
        "reuse_geometry_after_first": "Reuse the first complete geometry snapshot for diagnostics.",
        "freeze_geometry_upload_after_first": "Continue extraction but stop refreshing GPU geometry after the first upload.",
        "disable_geometry_upload": "Skip enhanced geometry uploads to isolate extraction cost.",
        "disable_geometry_draw": "Skip enhanced geometry drawing to isolate rendering cost.",
        "suppress_native_scene_raster": "Suppress native main-scene geometry after a complete enhanced frame is available.",
        "entity_vertex_mode": "Choose matrix-transformed local vertices or game-cached world vertices.",
    },
    "HUD": {
        "position_scaling": "Scale HUD positions from native layout toward viewport-height scaling.",
        "panel_scaling": "Scale ordinary HUD artwork from native size toward viewport-height scaling.",
        "viewport_scaling": "Scale camera panes from native size toward viewport-height scaling.",
        "font_scaling": "Scale renderer text from native size toward viewport-height scaling.",
        "target_marker_scaling": "Scale the center reticle, NAV circle, and offscreen target arrows.",
        "middle_panel_vertical_position": "Position middle HUD panels vertically, from the top (0) to the bottom (1) of the reference HUD area.",
        "top_panel_widescreen_position": "Move top HUD panels from the centered 4:3 layout (0) toward the widescreen edges (1).",
        "middle_panel_widescreen_position": "Move middle HUD panels from the centered 4:3 layout (0) toward the widescreen edges (1).",
        "bottom_panel_widescreen_position": "Move bottom HUD panels from the centered 4:3 layout (0) toward the widescreen edges (1).",
        "compass_altimeter": "Choose native strips or corrected generated compass and altimeter rendering.",
        "power_meters": "Choose native bands or smooth live-palette power-meter shading.",
        "htal_meters": "Choose native bands or smooth live-palette armor-meter shading.",
        "alt_htal_view": "Show the damage wireframe above lowered HTAL meters in the right MFD.",
        "meter_dark_offset": "Offset the dark palette shade used by enhanced meters.",
        "meter_light_offset": "Offset the light palette shade used by enhanced meters.",
        "meter_peak_offset": "Offset the highlight palette shade used by enhanced meters.",
        "meter_peak_position": "Place the enhanced meter highlight within each filled segment.",
        "rear_camera_mirror": "Mirror the rear-camera image horizontally like a vehicle rear-view mirror.",
        "alt_throttle_indicator_position": "Place throttle, speed, and MASC in the alternate right-center layout.",
        "radar_stroke_width": "Set antialiased radar-circle and field-of-view line thickness in output pixels.",
    },
    "renderer": {
        "antialiasing": "Choose native scene rendering or four-sample SSAA.",
        "ssaa_line_width": "Set resolved scene-geometry line width while SSAA is active.",
        "max_horizontal_fov_degrees": "Cap the main scene horizontal field of view on wide displays.",
        "entity_lod_selection": "Select entity detail by projected size, native choice, or a forced detail level.",
        "entity_lod_detail0_pixels": "Set the projected-radius threshold for the highest entity detail.",
        "entity_lod_detail1_pixels": "Set the projected-radius threshold for entity detail level 1.",
        "entity_lod_detail2_pixels": "Set the projected-radius threshold for entity detail level 2.",
        "entity_lod_hysteresis": "Set the fractional LOD threshold margin that prevents rapid detail switching.",
        "enable_diagnostic_logging": "Write renderer diagnostics and performance telemetry, including entity LOD decisions, to renderer_debug.log.",
        "enhanced_enhanced_imaging": "Keep supported effects textured during enhanced imaging.",
        "enhanced_imaging_distance_ratio": "Set enhanced-imaging visibility distance as a multiple of the native far distance.",
        "enhanced_imaging_reveal_time": "Set the time for enhanced-imaging reveal to reach maximum distance.",
        "enhanced_heli_rotors": "Replace helicopter rotor stand-ins with smooth motion-blurred rotors.",
        "enhanced_aero_lift_fans": "Apply motion-blurred textures to enclosed aeroplane lift fans.",
        "reduce_terrain_gaps": "Reposition known terrain blocks to reduce quantization seams.",
        "enhanced_mech_textures": "Use reconstructed seamless textures for known mech camouflage.",
        "enhanced_dropship_textures": "Use reconstructed seamless textures for known dropship surfaces.",
        "enhanced_mech_texture_uv_scale": "Set the texture frequency for reconstructed mech camouflage.",
        "enhanced_dropship_texture_uv_scale": "Set the texture frequency for reconstructed dropship surfaces.",
    },
    "input": {
        "joystick_input_enable": "Enable joystick polling and applying joystick input to the game.",
        "turret_aim_mode": "Choose direct stick-position aiming or relative rate aiming.",
        "turret_yaw_device": "Select the uniquely named SDL joystick used for turret yaw.",
        "turret_yaw_axis": "Select the physical device axis used for turret yaw.",
        "turret_pitch_device": "Select the uniquely named SDL joystick used for turret pitch.",
        "turret_pitch_axis": "Select the physical device axis used for turret pitch.",
        "chassis_turn_device": "Select the uniquely named SDL joystick used for chassis turn.",
        "chassis_turn_axis": "Select the physical device axis used for chassis turn.",
        "throttle_device": "Select the uniquely named SDL joystick used for throttle.",
        "throttle_axis": "Select the physical device axis used for throttle.",
        "invert_turret_yaw": "Reverse the physical turret-yaw direction.",
        "invert_turret_pitch": "Reverse the physical turret-pitch direction.",
        "invert_chassis_turn": "Reverse the physical chassis-turn direction.",
        "invert_throttle": "Reverse the physical throttle direction before endpoint calibration.",
        "turret_yaw_deadzone": "Ignore small turret-yaw movement around the physical center.",
        "turret_yaw_input_saturation": "Set the turret-yaw travel required to reach full calibrated input.",
        "turret_yaw_output_saturation": "Cap the maximum turret-yaw command produced by the axis.",
        "turret_pitch_deadzone": "Ignore small turret-pitch movement around the physical center.",
        "turret_pitch_input_saturation": "Set the turret-pitch travel required to reach full calibrated input.",
        "turret_pitch_output_saturation": "Cap the maximum turret-pitch command produced by the axis.",
        "chassis_turn_input_deadzone": "Ignore small chassis-turn movement around the physical center.",
        "chassis_turn_input_saturation": "Set the chassis-turn travel required to reach full calibrated input.",
        "chassis_turn_output_saturation": "Cap the maximum chassis-turn command produced by the axis.",
        "direct_turret_yaw_curve_mode": "Choose the response curve used for direct turret yaw.",
        "direct_turret_yaw_curve_k": "Set the power-curve blend used for direct turret yaw.",
        "direct_turret_yaw_curve_n": "Set the power exponent used for direct turret yaw.",
        "direct_turret_pitch_curve_mode": "Choose the response curve used for direct turret pitch.",
        "direct_turret_pitch_curve_k": "Set the power-curve blend used for direct turret pitch.",
        "direct_turret_pitch_curve_n": "Set the power exponent used for direct turret pitch.",
        "relative_turret_yaw_degrees_per_second": "Set the turret-yaw target rate at full stick deflection.",
        "relative_turret_pitch_degrees_per_second": "Set the turret-pitch target rate at full stick deflection.",
        "relative_turret_yaw_curve_mode": "Choose the response curve used for relative turret yaw.",
        "relative_turret_yaw_curve_k": "Set the power-curve blend used for relative turret yaw.",
        "relative_turret_yaw_curve_n": "Set the power exponent used for relative turret yaw.",
        "relative_turret_pitch_curve_mode": "Choose the response curve used for relative turret pitch.",
        "relative_turret_pitch_curve_k": "Set the power-curve blend used for relative turret pitch.",
        "relative_turret_pitch_curve_n": "Set the power exponent used for relative turret pitch.",
        "chassis_turn_scale": "Set chassis-turn gain before the game's turn limit is applied.",
        "chassis_curve_mode": "Choose the response curve used for chassis turn.",
        "chassis_curve_k": "Set the power-curve blend used for chassis turn.",
        "chassis_curve_n": "Set the power exponent used for chassis turn.",
        "throttle_raw_input_start": "Set the post-inversion raw throttle value that produces idle.",
        "throttle_raw_input_end": "Set the post-inversion raw throttle value that produces full input.",
        "throttle_output_saturation": "Cap the maximum throttle command produced by the axis.",
        "throttle_curve_mode": "Choose the response curve used across calibrated throttle travel.",
        "throttle_curve_k": "Set the power-curve blend used for throttle response.",
        "throttle_curve_n": "Set the power exponent used for throttle response.",
    },
}

CONFIG_SECTION_FILES = {
    section_name: os.path.basename(MOD_CONF_PATH)
    for section_name in CONFIG_SCHEMA
}
CONFIG_SECTION_FILES["input"] = os.path.basename(JOYSTICK_CONF_PATH)
CONFIG_FILE_PATHS = {
    os.path.basename(MOD_CONF_PATH): MOD_CONF_PATH,
    os.path.basename(JOYSTICK_CONF_PATH): JOYSTICK_CONF_PATH,
}
CONFIG_SCALAR_TYPES = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
}


CONFIG_FALLBACKS = {
    name: spec[0] if isinstance(spec, tuple) else spec
    for schema in CONFIG_SCHEMA.values()
    for name, spec in schema.items()
}


def config_keys_for_sections(*section_names):
    return tuple(
        name
        for section_name in section_names
        for name in CONFIG_SCHEMA[section_name]
    )


def config_schema_records():
    """Return detached, serialization-ready metadata in stable schema order."""
    records = []
    for section_name, schema in CONFIG_SCHEMA.items():
        for name, spec in schema.items():
            fallback = spec[0] if isinstance(spec, tuple) else spec
            record = {
                "file": CONFIG_SECTION_FILES[section_name],
                "section": section_name,
                "name": name,
                "type": CONFIG_SCALAR_TYPES[type(fallback)],
                "fallback": fallback,
                "shipped_default": CONFIG_SHIPPED_OVERRIDES.get(
                    section_name, {}
                ).get(name, fallback),
                "help": CONFIG_HELP[section_name][name],
            }
            if isinstance(spec, tuple):
                if len(spec) == 2:
                    record["choices"] = list(spec[1])
                else:
                    record["minimum"] = spec[1]
                    record["maximum"] = spec[2]
            records.append(record)
    return tuple(records)


def _config_value(section, name, spec):
    default = spec[0] if isinstance(spec, tuple) else spec
    raw_value = section.get(name)
    if isinstance(default, bool):
        normalized = str(raw_value).strip().lower()
        return {
            "1": True, "true": True, "yes": True, "on": True,
            "0": False, "false": False, "no": False, "off": False,
        }.get(normalized, default)
    if isinstance(default, str) and not isinstance(spec, tuple):
        return str(default if raw_value is None else raw_value).strip()
    if not isinstance(spec, tuple):
        try:
            return type(default)(default if raw_value is None else raw_value)
        except (TypeError, ValueError):
            return default
    if len(spec) == 2:
        value = str(default if raw_value is None else raw_value).strip().lower()
        return value if value in spec[1] else default
    value_type = int if isinstance(default, int) else float
    try:
        value = value_type(default if raw_value is None else raw_value)
    except (TypeError, ValueError):
        value = default
    return max(spec[1], min(spec[2], value))


def _config_values(section, schema):
    return {
        name: _config_value(section, name, spec)
        for name, spec in schema.items()
    }


def normalize_config_values(values):
    """Return a copy with cross-setting configuration rules applied."""
    values = dict(values)
    for deadzone_name, saturation_name in (
        ("turret_yaw_deadzone", "turret_yaw_input_saturation"),
        ("turret_pitch_deadzone", "turret_pitch_input_saturation"),
        ("chassis_turn_input_deadzone", "chassis_turn_input_saturation"),
    ):
        if deadzone_name not in values or saturation_name not in values:
            continue
        values[saturation_name] = max(
            values[saturation_name],
            min(1.0, values[deadzone_name] + 0.01),
        )
    if (
        "throttle_raw_input_start" in values
        and "throttle_raw_input_end" in values
        and values["throttle_raw_input_end"]
        <= values["throttle_raw_input_start"]
    ):
        values["throttle_raw_input_start"] = CONFIG_FALLBACKS[
            "throttle_raw_input_start"
        ]
        values["throttle_raw_input_end"] = CONFIG_FALLBACKS[
            "throttle_raw_input_end"
        ]
    return values


def load_mod_config(modstate):
    parsers = {}
    for file_name, path in CONFIG_FILE_PATHS.items():
        parser = configparser.ConfigParser(
            inline_comment_prefixes=("#", ";")
        )
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error:
            parser = configparser.ConfigParser()
        parsers[file_name] = parser

    values = {}
    for section_name, schema in CONFIG_SCHEMA.items():
        parser = parsers[CONFIG_SECTION_FILES[section_name]]
        section = (
            parser[section_name] if parser.has_section(section_name) else {}
        )
        values.update(_config_values(section, schema))
    values = normalize_config_values(values)
    modstate.conf = SimpleNamespace(
        **values,
        path=MOD_CONF_PATH,
        joystick_path=JOYSTICK_CONF_PATH,
    )
    return modstate.conf


MOD_INIT = {
    "executables": [
        {
            "name": "MW2.EXE",
            "landmark": {
                "string": "Framerate",
                "reloc": 0x000A3330,
            },
            # scan_range is not thoroughly verified; may not work on other installs
            # consider omitting it to force a full linear scan.
            "scan_range": {
                "start": 0x170000,
                "end": 0xA00000,
            },
            "frame_start": 0x0002CE60,  # callsite for game's frame timing near the start of the game loop - must have a call instruction at this address for this to work.
            "scene_raster_suppression": {
                "phase_enter": 0x0002CEB1,
                "phase_leave": 0x0002CEB7,
                "sites": [
                    {
                        "callsite": 0x00054A20,
                        "target": 0x00054B90,
                    },
                    {
                        "callsite": 0x00054AB5,
                        "target": 0x00054B90,
                    },
                ],
            },
        }
    ]
}
