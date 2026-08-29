from __future__ import annotations

import struct
from dataclasses import dataclass


ADDR_GAME_TICK = 0x000A58C4
ADDR_SHORT_MESSAGE_TOP = 0x000A5600
ADDR_SHORT_MESSAGE_BOTTOM = 0x000A5624
SHORT_MESSAGE_SIZE = 0x24
SHORT_MESSAGE_TEXT_MAX = 256
MESSAGE_TEXT_X_OFFSET = 6

@dataclass(frozen=True, slots=True)
class HudShortMessage:
    slot: int
    text: str
    left: int
    top: int
    right: int
    bottom: int
    text_x: int
    text_y: int


@dataclass(frozen=True, slots=True)
class HudShortMessages:
    messages: tuple[HudShortMessage, ...]


EMPTY_HUD_SHORT_MESSAGES = HudShortMessages(())


def _string(gamemem, runtime_address):
    if runtime_address == 0:
        return ""
    raw = gamemem.read_runtime_bytes(runtime_address, SHORT_MESSAGE_TEXT_MAX)
    return bytes(raw).split(b"\x00", 1)[0].decode("cp437", errors="replace")


def _window_rect(gamemem, runtime_address):
    if runtime_address == 0:
        return None
    data = bytes(gamemem.read_runtime_bytes(runtime_address + 0x04, 0x10))
    left, top, right, bottom = struct.unpack("<4i", data)
    if right < left or bottom < top:
        return None
    return left, top, right, bottom


def _snapshot_record(gamemem, slot, record_address, current_tick):
    record = bytes(gamemem.read_reloc_bytes(record_address, SHORT_MESSAGE_SIZE))
    text_pointer, x, y, active, _priority, _font, _backdrop, expiry, window = (
        struct.unpack("<Iiiiiiiii", record)
    )
    if active == 0 or current_tick >= expiry:
        return None
    text = _string(gamemem, text_pointer)
    if not text:
        return None
    pane = _window_rect(gamemem, window)
    if pane is None:
        pane = (0, 0, 1022, 48) if slot == 0 else (0, 720, 1022, 768)
    left, top, right, bottom = pane
    return HudShortMessage(
        slot=int(slot),
        text=text,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        text_x=left + MESSAGE_TEXT_X_OFFSET + x,
        text_y=top + y,
    )


def snapshot_short_messages(gamemem):
    current_tick = int(gamemem.read_reloc_i32(ADDR_GAME_TICK))
    messages = []
    for slot, record_address in (
        (0, ADDR_SHORT_MESSAGE_TOP),
        (1, ADDR_SHORT_MESSAGE_BOTTOM),
    ):
        message = _snapshot_record(gamemem, slot, record_address, current_tick)
        if message is not None:
            messages.append(message)
    if not messages:
        return EMPTY_HUD_SHORT_MESSAGES
    return HudShortMessages(tuple(messages))
