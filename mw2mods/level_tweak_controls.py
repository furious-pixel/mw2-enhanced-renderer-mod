"""Apply temporary native game controls on the emulation thread."""
import os
import sys
from pathlib import Path

from mod import modhook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from level_tweak.protocol import Command, Controls, Mapping, fresh, now_ms

COMPRESSION = 0x000A58D8
EXPANSION = 0x000A58DC
TIMING_ENABLED = 0x000A58F8
TIME_FLAGS = ((0, 0), (0, 1), (1, 0))


def read_flags(memory):
    return memory.read_reloc_u32(COMPRESSION), memory.read_reloc_u32(EXPANSION)


def write_flags(memory, flags):
    previous = read_flags(memory)
    memory.write_reloc_u32(COMPRESSION, flags[0])
    try:
        memory.write_reloc_u32(EXPANSION, flags[1])
    except Exception:
        memory.write_reloc_u32(COMPRESSION, previous[0])
        raise


class GameControls:
    def __init__(self):
        self.mapping = None
        self.next_poll = self.next_attach = 0
        self.scope = self.blocked_scope = None
        self.accepted = Command()
        self.original = self.written = None

    def __del__(self):
        if self.mapping:
            self.mapping.close()

    def restore(self, memory):
        if self.original is not None:
            # Do not undo a newer setting made through the game's own cheats.
            if read_flags(memory) == self.written:
                write_flags(memory, self.original)
            self.original = self.written = None

    def reset(self, memory):
        self.restore(memory)
        self.blocked_scope = self.scope
        self.accepted = Command()
        self.next_poll = 0

    def poll(self, memory):
        now = now_ms()
        if now < self.next_poll:
            return
        self.next_poll = now + 100
        if not fresh(self.accepted.heartbeat_ms):
            self.restore(memory)
        try:
            if self.mapping is None:
                if now < self.next_attach:
                    return
                self.next_attach = now + 1000
                self.mapping = Mapping(role="controls")
            block = self.mapping.read_game_command()
            status, command = block.status, block.command
            scope = (status.renderer, status.mission, bytes(status.scn))
            if (not status.renderer or status.in_mission != 1 or not fresh(status.heartbeat_ms)
                    or not scope[2] or scope == self.blocked_scope):
                self.restore(memory)
                self.mapping.write_controls(Controls(heartbeat_ms=now))
                return
            if scope != self.scope:
                self.restore(memory)
                self.scope = scope
                self.accepted = Command()
            flags = read_flags(memory)
            available = bool(memory.read_reloc_u32(TIMING_ENABLED))
            valid = (command.session and command.control_revision and command.time_mode < 3
                     and command.control_reserved == 0 and fresh(command.heartbeat_ms)
                     and (command.renderer, command.mission, bytes(command.scn)) == scope)
            if valid and available:
                previous = self.accepted
                newer = (command.session != previous.session
                         or command.control_revision > previous.control_revision)
                same = (command.session == previous.session
                        and command.control_revision == previous.control_revision
                        and command.time_mode == previous.time_mode)
                if newer:
                    if self.original is None or flags != self.written:
                        self.original = flags
                    write_flags(memory, TIME_FLAGS[command.time_mode])
                    self.written = flags = TIME_FLAGS[command.time_mode]
                if newer or same:
                    self.accepted = command
            elif not valid:
                self.restore(memory)
                flags = read_flags(memory)
            active = self.original is not None
            self.mapping.write_controls(Controls(
                session=self.accepted.session if active else 0,
                renderer=status.renderer, mission=status.mission,
                revision=self.accepted.control_revision if active else 0,
                heartbeat_ms=now, time_mode=2 if flags[0] else 1 if flags[1] else 0,
                available=available))
        except TimeoutError:
            # Never wait for IPC on the emulation thread. Expiry still runs.
            pass
        except (OSError, RuntimeError, ValueError):
            self.restore(memory)
            if self.mapping:
                self.mapping.close()
                self.mapping = None


def reset_controls(modstate, memory):
    controls = getattr(modstate, "level_tweak_controls", None)
    if controls is not None:
        controls.reset(memory)


def update_controls(modstate, memory):
    controls = getattr(modstate, "level_tweak_controls", None)
    if controls is None:
        controls = modstate.level_tweak_controls = GameControls()
    controls.poll(memory)


# Disabled launches register no hooks and perform no per-frame work.
if os.environ.get("MW2_LEVEL_TWEAK") == "1":
    modhook("MW2.EXE", 0x0002CD11, "call")(reset_controls)
    modhook("MW2.EXE", 0x0002CE84, "call")(update_controls)
