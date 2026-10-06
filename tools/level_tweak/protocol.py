"""Version 4 Windows IPC. Layout matches renderer/level_tweak_protocol.h.

Every row copy uses an OS mutex. The renderer always tries with timeout zero;
the panel uses a bounded wait. Owner events enforce one process of each role.
An abandoned mutex invalidates all rows, never treating torn bytes as a save.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from contextlib import contextmanager
import os
import re

MAGIC, VERSION = 0x3454574D, 4
TIMEOUT_MS = 2000


class Command(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        "session", "renderer", "mission", "revision", "heartbeat_ms", "save_revision"
    )] + [("view_fixed", C.c_uint32), ("preset", C.c_uint32), ("scn", C.c_char * 32),
          ("control_revision", C.c_uint64), ("time_mode", C.c_uint32), ("control_reserved", C.c_uint32)]


class Status(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        "renderer", "mission", "heartbeat_ms", "command_session", "command_revision", "save_ack"
    )] + [(name, C.c_uint32) for name in (
        "preset", "view_fixed", "live_view_fixed", "in_mission"
    )] + [("scn", C.c_char * 32)]


class Controls(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        "session", "renderer", "mission", "revision", "heartbeat_ms"
    )] + [("time_mode", C.c_uint32), ("available", C.c_uint32)]


class Shared(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ("magic", "version", "size", "reserved")] + [
        ("command", Command), ("status", Status), ("controls", Controls)]


assert C.sizeof(Command) == 104 and C.sizeof(Status) == 96
assert Shared.command.offset == 16 and Shared.status.offset == 120
assert Shared.controls.offset == 216 and C.sizeof(Shared) == 264

K = C.WinDLL("kernel32", use_last_error=True)
for name, args, result in (
    ("CreateEventW", [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE),
    ("OpenEventW", [W.DWORD, W.BOOL, W.LPCWSTR], W.HANDLE),
    ("SetEvent", [W.HANDLE], W.BOOL),
    ("CreateMutexW", [C.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
    ("CreateFileMappingW", [W.HANDLE, C.c_void_p, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR], W.HANDLE),
    ("OpenFileMappingW", [W.DWORD, W.BOOL, W.LPCWSTR], W.HANDLE),
    ("MapViewOfFile", [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.c_size_t], C.c_void_p),
    ("UnmapViewOfFile", [C.c_void_p], W.BOOL),
    ("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD),
    ("ReleaseMutex", [W.HANDLE], W.BOOL),
    ("CloseHandle", [W.HANDLE], W.BOOL),
    ("GetTickCount64", [], C.c_uint64),
):
    fn = getattr(K, name)
    fn.argtypes, fn.restype = args, result


def now_ms():
    return K.GetTickCount64()


def fresh(stamp):
    now = now_ms()
    return 0 < stamp <= now and now - stamp <= TIMEOUT_MS


def checked(handle):
    if not handle:
        raise C.WinError(C.get_last_error())
    return handle


def signal_ready(channel_name):
    # Optional launcher handshake: signal only after the web UI can call its API.
    event = K.OpenEventW(2, False, channel_name + ".ready")
    if event:
        try:
            checked(K.SetEvent(event))
        finally:
            K.CloseHandle(event)


@contextmanager
def mutex_lock(handle, timeout=100):
    result = K.WaitForSingleObject(handle, timeout)
    if result == 0x102:
        raise TimeoutError("Level tweak channel is busy")
    if result not in (0, 0x80):
        raise C.WinError(C.get_last_error())
    try:
        yield result == 0x80
    finally:
        checked(K.ReleaseMutex(handle))


class Mapping:
    def __init__(self, channel=None, *, role="sidecar"):
        if role not in ("sidecar", "controls"):
            raise ValueError("Invalid channel owner")
        token = channel if channel is not None else os.environ.get("MW2_LEVEL_TWEAK_SHM", "")
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("Start the panel with launch_level_tweak.bat (missing channel id)")
        self.name = "Local\\mw2_level_tweak_v4_" + token
        self.timeout = 0 if role == "controls" else 100
        self.lease = self.mutex = self.mapping = self.address = None
        try:
            self.lease = checked(K.CreateEventW(None, True, False, self.name + "." + role))
            if C.get_last_error() == 183:
                raise RuntimeError("Another " + role + " owner already uses this channel")
            self.mutex = checked(K.CreateMutexW(None, False, self.name + ".data"))
            if role == "sidecar":
                self.mapping = checked(K.CreateFileMappingW(W.HANDLE(-1), None, 4, 0, C.sizeof(Shared), self.name))
                created = C.get_last_error() != 183
            else:
                self.mapping = checked(K.OpenFileMappingW(0xF001F, False, self.name))
                created = False
            self.address = checked(K.MapViewOfFile(self.mapping, 0xF001F, 0, 0, C.sizeof(Shared)))
            with mutex_lock(self.mutex, self.timeout) as abandoned:
                if created:
                    block = Shared(MAGIC, VERSION, C.sizeof(Shared), 0)
                    C.memmove(self.address, C.byref(block), C.sizeof(block))
                self._validate()
                if abandoned:
                    C.memset(self.address + Shared.command.offset, 0, C.sizeof(Shared) - 16)
        except BaseException:
            self.close()
            raise

    def _validate(self):
        header = (C.c_uint32 * 4).from_buffer_copy(C.string_at(self.address, 16))
        if tuple(header) != (MAGIC, VERSION, C.sizeof(Shared), 0):
            raise RuntimeError("Incompatible level tweak channel; restart this launch")

    @contextmanager
    def locked(self):
        if not self.address:
            raise RuntimeError("Level tweak channel is closed")
        with mutex_lock(self.mutex, self.timeout) as abandoned:
            self._validate()
            if abandoned:
                C.memset(self.address + Shared.command.offset, 0, C.sizeof(Shared) - 16)
            yield

    def read_status(self):
        with self.locked():
            status = Status.from_buffer_copy(C.string_at(self.address + Shared.status.offset, C.sizeof(Status)))
        if status.preset >= 6 or status.in_mission > 1:
            raise RuntimeError("Invalid renderer status")
        if len(status.scn) >= 32 or (status.in_mission and not re.fullmatch(rb"[a-z0-9_.-]{1,31}", status.scn)):
            raise RuntimeError("Invalid renderer mission name")
        return status

    def write_command(self, command):
        with self.locked():
            C.memmove(self.address + Shared.command.offset, C.byref(command), C.sizeof(Command))

    def read_controls(self):
        with self.locked():
            row = Controls.from_buffer_copy(C.string_at(self.address + Shared.controls.offset, C.sizeof(Controls)))
        if row.time_mode > 2 or row.available > 1:
            raise RuntimeError("Invalid game-control status")
        return row

    def read_game_command(self):
        with self.locked():
            return Shared.from_buffer_copy(C.string_at(self.address, C.sizeof(Shared)))

    def write_controls(self, controls):
        with self.locked():
            C.memmove(self.address + Shared.controls.offset, C.byref(controls), C.sizeof(Controls))

    def close(self):
        if self.address:
            K.UnmapViewOfFile(self.address)
            self.address = None
        for field in ("mapping", "mutex", "lease"):
            handle = getattr(self, field)
            if handle:
                K.CloseHandle(handle)
                setattr(self, field, None)
