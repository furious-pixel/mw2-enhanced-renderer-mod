"""Unified direct/relative joystick input selected by joystick.conf."""

import math

from mod import modhook
from mod_init import load_mod_config


ADDR_CALLSITE_ENTITY_UPDATE_ALL = 0x0002CE84
ADDR_INPUT_TURRET_PITCH = 0x0A83F4
ADDR_INPUT_TURRET_YAW = 0x0A83F8
ADDR_INPUT_THROTTLE = 0x0A83FC
ADDR_INPUT_CHASSIS_TURN = 0x0A8404
ADDR_PLAYER_SLOT = 0x0A5918
ADDR_ENTITY_TABLE = 0x108B00

OFFSET_ENTITY_MECH_DATA = 0x20
OFFSET_MECH_MAX_YAW = 0xE0

INT16_NEGATIVE_MIN = 32768
INT16_POSITIVE_MAX = 32767
INT32_POSITIVE_MAX = 0x7FFF_FFFF
FIXED_16_16_SCALE = 65536.0

TURRET_PITCH_MAX = 0x003F_FFFF
TURRET_YAW_MAX = 0x003F_FFFF
CHASSIS_TURN_NEGATIVE_MAX = 0x0200_0000
CHASSIS_TURN_POSITIVE_MAX = 0x01FF_FFFF
CHASSIS_TURN_DEADZONE = 0x0001_0000
THROTTLE_MAX = 1024

TURRET_YAW_AXIS = 0
TURRET_PITCH_AXIS = 1
CHASSIS_TURN_AXIS = 2
THROTTLE_AXIS = 3
JOYSTICK_BINDING_CONFIG = (
    ("turret yaw", "turret_yaw_device", "turret_yaw_axis"),
    ("turret pitch", "turret_pitch_device", "turret_pitch_axis"),
    ("chassis turn", "chassis_turn_device", "chassis_turn_axis"),
    ("throttle", "throttle_device", "throttle_axis"),
)


def clamp_signed_16(value):
    return max(-INT16_NEGATIVE_MIN, min(INT16_POSITIVE_MAX, int(value)))


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def normalize_signed_axis(raw_value):
    raw_value = clamp_signed_16(raw_value)
    if raw_value >= 0:
        return raw_value / INT16_POSITIVE_MAX

    return raw_value / INT16_NEGATIVE_MIN


def curve_axis(x, mode, k, n):
    if mode == "power":
        return math.copysign(abs(x) ** n, x)
    if mode == "blended_curve":
        curved = math.copysign(abs(x) ** n, x)
        return (1.0 - k) * x + k * curved
    return x


def scale_normalized_signed_axis(normalized_value, negative_max, positive_max):
    normalized_value = clamp(float(normalized_value), -1.0, 1.0)
    if normalized_value >= 0.0:
        return int(normalized_value * positive_max)
    return -int((-normalized_value) * negative_max)


def normalize_throttle_axis(raw_value, invert, raw_start, raw_end):
    raw_value = clamp_signed_16(raw_value)
    if invert:
        raw_value = clamp_signed_16(-raw_value)
    if raw_value <= raw_start:
        return 0.0
    if raw_value >= raw_end:
        return 1.0

    return (raw_value - raw_start) / float(raw_end - raw_start)


def _input_config(modstate):
    conf = getattr(modstate, "conf", None)
    if conf is None:
        conf = load_mod_config(modstate)
    return conf


def _joystick_bound_axes(modstate, conf):
    bound = getattr(modstate, "joystick_input_bound_axes", None)
    if bound is not None:
        return bound

    requests = tuple(
        (getattr(conf, device_name), getattr(conf, axis_name))
        for _, device_name, axis_name in JOYSTICK_BINDING_CONFIG
    )
    statuses = tuple(modstate.bind_modjoystick_axes(requests))
    if len(statuses) != len(requests):
        raise RuntimeError("modjoy returned an invalid binding result")

    modstate.joystick_input_binding_statuses = statuses
    bound = tuple(status == "bound" for status in statuses)
    modstate.joystick_input_bound_axes = bound
    for (label, _, _), request, status in zip(
        JOYSTICK_BINDING_CONFIG,
        requests,
        statuses,
    ):
        if status != "bound":
            print(
                "joystick input: {} binding {!r} axis {}: {}".format(
                    label,
                    request[0],
                    request[1],
                    status,
                )
            )
    return bound


def _centered_axis_input(raw_value, invert, deadzone, input_saturation):
    normalized = normalize_signed_axis(raw_value)
    if invert:
        normalized = -normalized

    magnitude = abs(normalized)
    if magnitude <= deadzone:
        return 0.0
    span = max(0.000001, float(input_saturation) - float(deadzone))
    calibrated = clamp((magnitude - deadzone) / span, 0.0, 1.0)
    return math.copysign(calibrated, normalized)


def _centered_axis_response(
    raw_value,
    invert,
    deadzone,
    input_saturation,
    output_saturation,
    curve_mode,
    curve_k,
    curve_n,
):
    calibrated = _centered_axis_input(
        raw_value,
        invert,
        deadzone,
        input_saturation,
    )
    curved = curve_axis(calibrated, curve_mode, curve_k, curve_n)
    return clamp(curved * output_saturation, -1.0, 1.0)


def _unsigned_axis_curve(value, mode, k, n):
    value = clamp(float(value), 0.0, 1.0)
    if mode == "power":
        return value**n
    if mode == "blended_curve":
        return (1.0 - k) * value + k * (value**n)
    return value


def _read_player_max_yaw(gamemem):
    try:
        player_slot = int(gamemem.read_reloc_u32(ADDR_PLAYER_SLOT))
        entity = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_TABLE + player_slot * 4)
        )
        if entity == 0:
            return None

        mech_data = int(
            gamemem.read_runtime_u32(entity + OFFSET_ENTITY_MECH_DATA)
        )
        if mech_data == 0:
            return None

        raw_max = int(
            gamemem.read_runtime_i32(mech_data + OFFSET_MECH_MAX_YAW)
        )
    except Exception:
        return None

    max_yaw = abs(raw_max)
    if max_yaw == 0 or max_yaw > INT32_POSITIVE_MAX:
        return None
    return max_yaw


def _direct_turret_values(axes, conf, bound):
    turret_yaw = None
    if bound[TURRET_YAW_AXIS]:
        yaw_input = _centered_axis_response(
            axes[TURRET_YAW_AXIS],
            conf.invert_turret_yaw,
            conf.turret_yaw_deadzone,
            conf.turret_yaw_input_saturation,
            conf.turret_yaw_output_saturation,
            conf.direct_turret_yaw_curve_mode,
            conf.direct_turret_yaw_curve_k,
            conf.direct_turret_yaw_curve_n,
        )
        turret_yaw = scale_normalized_signed_axis(
            yaw_input,
            TURRET_YAW_MAX,
            TURRET_YAW_MAX,
        )

    turret_pitch = None
    if bound[TURRET_PITCH_AXIS]:
        pitch_input = _centered_axis_response(
            axes[TURRET_PITCH_AXIS],
            conf.invert_turret_pitch,
            conf.turret_pitch_deadzone,
            conf.turret_pitch_input_saturation,
            conf.turret_pitch_output_saturation,
            conf.direct_turret_pitch_curve_mode,
            conf.direct_turret_pitch_curve_k,
            conf.direct_turret_pitch_curve_n,
        )
        turret_pitch = scale_normalized_signed_axis(
            pitch_input,
            TURRET_PITCH_MAX,
            TURRET_PITCH_MAX,
        )
    return turret_yaw, turret_pitch


def _relative_turret_values(modstate, gamemem, axes, conf, bound):
    frame_delta = max(0.0, float(getattr(modstate, "frame_delta", 0.0)))

    turret_yaw = None
    if bound[TURRET_YAW_AXIS]:
        max_yaw = _read_player_max_yaw(gamemem)
        if max_yaw is not None:
            yaw_input = _centered_axis_response(
                axes[TURRET_YAW_AXIS],
                conf.invert_turret_yaw,
                conf.turret_yaw_deadzone,
                conf.turret_yaw_input_saturation,
                conf.turret_yaw_output_saturation,
                conf.relative_turret_yaw_curve_mode,
                conf.relative_turret_yaw_curve_k,
                conf.relative_turret_yaw_curve_n,
            )
            yaw_state = float(
                getattr(
                    modstate,
                    "joystick_input_relative_turret_yaw",
                    0.0,
                )
            )
            yaw_state = clamp(
                yaw_state
                + yaw_input
                * conf.relative_turret_yaw_degrees_per_second
                * FIXED_16_16_SCALE
                * frame_delta,
                -float(max_yaw),
                float(max_yaw),
            )
            modstate.joystick_input_relative_turret_yaw = yaw_state
            turret_yaw = int(clamp(yaw_state, -max_yaw, max_yaw))

    turret_pitch = None
    if bound[TURRET_PITCH_AXIS]:
        pitch_input = _centered_axis_response(
            axes[TURRET_PITCH_AXIS],
            conf.invert_turret_pitch,
            conf.turret_pitch_deadzone,
            conf.turret_pitch_input_saturation,
            conf.turret_pitch_output_saturation,
            conf.relative_turret_pitch_curve_mode,
            conf.relative_turret_pitch_curve_k,
            conf.relative_turret_pitch_curve_n,
        )
        pitch_state = float(
            getattr(
                modstate,
                "joystick_input_relative_turret_pitch",
                0.0,
            )
        )
        pitch_state = clamp(
            pitch_state
            + pitch_input
            * conf.relative_turret_pitch_degrees_per_second
            * FIXED_16_16_SCALE
            * frame_delta,
            -float(TURRET_PITCH_MAX),
            float(TURRET_PITCH_MAX),
        )
        modstate.joystick_input_relative_turret_pitch = pitch_state
        turret_pitch = int(
            clamp(pitch_state, -TURRET_PITCH_MAX, TURRET_PITCH_MAX)
        )
    return turret_yaw, turret_pitch


def _chassis_turn_value(raw_value, conf):
    chassis_input = _centered_axis_response(
        raw_value,
        conf.invert_chassis_turn,
        conf.chassis_turn_input_deadzone,
        conf.chassis_turn_input_saturation,
        conf.chassis_turn_output_saturation,
        conf.chassis_curve_mode,
        conf.chassis_curve_k,
        conf.chassis_curve_n,
    )
    chassis_turn = scale_normalized_signed_axis(
        clamp(chassis_input * conf.chassis_turn_scale, -1.0, 1.0),
        CHASSIS_TURN_NEGATIVE_MAX,
        CHASSIS_TURN_POSITIVE_MAX,
    )
    if abs(chassis_turn) < CHASSIS_TURN_DEADZONE:
        return 0
    return chassis_turn


@modhook("MW2.EXE", ADDR_CALLSITE_ENTITY_UPDATE_ALL, "call")
def feed_joystick_input(modstate, gamemem):
    conf = _input_config(modstate)
    if not conf.joystick_input_enable:
        return

    bound = _joystick_bound_axes(modstate, conf)
    if not any(bound):
        return

    axes = modstate.get_modjoystick_axes()
    if len(axes) != len(JOYSTICK_BINDING_CONFIG):
        raise RuntimeError("modjoy returned an invalid axis snapshot")

    if conf.turret_aim_mode == "relative":
        turret_yaw, turret_pitch = _relative_turret_values(
            modstate,
            gamemem,
            axes,
            conf,
            bound,
        )
    else:
        turret_yaw, turret_pitch = _direct_turret_values(
            axes,
            conf,
            bound,
        )

    chassis_turn = None
    if bound[CHASSIS_TURN_AXIS]:
        chassis_turn = _chassis_turn_value(
            axes[CHASSIS_TURN_AXIS],
            conf,
        )

    throttle = None
    if bound[THROTTLE_AXIS]:
        throttle_input = normalize_throttle_axis(
            axes[THROTTLE_AXIS],
            conf.invert_throttle,
            conf.throttle_raw_input_start,
            conf.throttle_raw_input_end,
        )
        throttle_input = _unsigned_axis_curve(
            throttle_input,
            conf.throttle_curve_mode,
            conf.throttle_curve_k,
            conf.throttle_curve_n,
        )
        throttle = int(
            clamp(
                throttle_input * conf.throttle_output_saturation,
                0.0,
                1.0,
            )
            * THROTTLE_MAX
            + 0.5
        )

    if turret_yaw is not None:
        gamemem.write_reloc_i32(ADDR_INPUT_TURRET_YAW, turret_yaw)
    if turret_pitch is not None:
        gamemem.write_reloc_i32(ADDR_INPUT_TURRET_PITCH, turret_pitch)
    if chassis_turn is not None:
        gamemem.write_reloc_i32(ADDR_INPUT_CHASSIS_TURN, chassis_turn)
    if throttle is not None:
        gamemem.write_reloc_i32(ADDR_INPUT_THROTTLE, throttle)
