"""Mission view-distance panel. The UI never writes renderer memory directly."""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))
from level_tweak.overrides import Catalog, PRESETS, RADIAL_SCALE, SLIDER_MAX, metres_to_fixed
from level_tweak.protocol import Command, Mapping, fresh, now_ms, signal_ready


class PreviewApi:
    def __init__(self, mapping, missions=None, catalog=None):
        self._mapping = mapping
        self._catalog = catalog or Catalog()
        self._missions = missions or {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._command = Command(session=secrets.randbits(64) or 1, preset=2)
        self._last_intent = 0
        self._saving = False
        self._closed = False
        self._error = ""
        self._worker = threading.Thread(target=self._heartbeat, daemon=True, name="level-tweak-heartbeat")
        self._worker.start()

    def _heartbeat(self):
        while not self._stop.is_set():
            try:
                with self._lock:
                    self._command.heartbeat_ms = now_ms()
                    self._mapping.write_command(self._command)
                    self._error = ""
            except (OSError, RuntimeError, TimeoutError) as exc:
                self._error = str(exc)
            self._stop.wait(0.25)

    def _close(self):
        self._stop.set()
        self._worker.join()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._mapping.write_command(Command())
            except (OSError, RuntimeError, TimeoutError):
                pass
            self._mapping.close()

    def _status(self):
        if self._closed:
            raise RuntimeError("Level tweak window is closing")
        return self._mapping.read_status()

    @staticmethod
    def _scope(status):
        return dict(renderer=str(status.renderer), mission=str(status.mission), scn=status.scn.decode("ascii"))

    def _require_scope(self, scope):
        status = self._status()
        if not fresh(status.heartbeat_ms) or not status.renderer or not status.in_mission:
            raise ValueError("Renderer is idle or disconnected; load a mission")
        if scope != self._scope(status):
            raise ValueError("Mission changed; wait for the panel to refresh")
        return status

    def _farpatcher(self, scn):
        row = self._missions.get(scn, {})
        value = row.get("community_view_metres")
        if value is None:
            value = (row.get("community_view_fixed") or 0) / 100
        return float(value) if value and 0 < float(value) < SLIDER_MAX else None

    def _resolved(self, scn):
        result = self._catalog.resolve(scn)
        result.update(farpatcher_metres=self._farpatcher(scn), farpatcher_available=self._farpatcher(scn) is not None)
        return result

    def get_bootstrap(self):
        signal_ready(self._mapping.name)
        return dict(ok=True, user_path=str(self._catalog.user_path), slider_max_metres=SLIDER_MAX,
                    status=self.get_status())

    def get_status(self):
        with self._lock:
            try:
                status = self._status()
                scope = self._scope(status)
            except (OSError, RuntimeError, TimeoutError, UnicodeError) as exc:
                return dict(ok=False, connected=False, in_mission=False, message=str(exc))
            scn = scope["scn"]
            meta = self._missions.get(scn, {})
            resolved = self._resolved(scn)
            native_fixed = int(meta.get("native_view_fixed") or status.live_view_fixed)
            native = float(meta.get("native_view_metres") or native_fixed / 100)
            applied = status.view_fixed / 100 * (RADIAL_SCALE if status.preset == 4 else 1)
            return dict(ok=True, connected=bool(status.renderer and fresh(status.heartbeat_ms)),
                        in_mission=bool(status.in_mission), scope=scope, scn=scn,
                        code=scn.split("scn", 1)[0].rstrip("_-").upper(), title=str(meta.get("title") or ""),
                        applied_preset=PRESETS[status.preset], applied_metres=applied,
                        live_metres=status.live_view_fixed / 100, native_metres=native,
                        radial_metres=native * RADIAL_SCALE, shipped_metres=resolved["shipped_metres"],
                        farpatcher_metres=resolved["farpatcher_metres"],
                        shipped_available=resolved["shipped_available"], farpatcher_available=resolved["farpatcher_available"],
                        resolved_source=resolved["source"], resolved=resolved, slider_max_metres=SLIDER_MAX,
                        live_disagrees_with_catalog=bool(native_fixed and status.live_view_fixed and native_fixed != status.live_view_fixed),
                        saving=self._saving, message=self._error)

    def _values(self, preset, metres, status):
        if preset not in PRESETS:
            raise ValueError("Unknown preset")
        scn = status.scn.decode("ascii")
        if preset == "shipped":
            metres = self._catalog.resolve(scn)["shipped_metres"]
        elif preset == "farpatcher":
            metres = self._farpatcher(scn)
        elif preset in ("game", "game-radial"):
            metres = status.live_view_fixed / 100
        elif preset == "max":
            metres = 0
        if metres is None:
            raise ValueError("This preset is unavailable for the mission")
        return PRESETS.index(preset), metres_to_fixed(metres)

    def _set(self, preset, fixed, status, save=False):
        self._command.renderer = status.renderer
        self._command.mission = status.mission
        self._command.scn = status.scn
        self._command.preset = preset
        self._command.view_fixed = fixed
        self._command.revision += 1
        self._command.save_revision = self._command.revision if save else 0
        self._command.heartbeat_ms = now_ms()
        self._mapping.write_command(self._command)

    def _intent(self, intent):
        if type(intent) is not int or not self._last_intent < intent <= 2**53 - 1:
            raise ValueError("Superseded panel request")
        self._last_intent = intent

    def set_command(self, scope, intent, preset, metres):
        try:
            with self._lock:
                if self._saving:
                    raise ValueError("Wait for the current save")
                status = self._require_scope(scope)
                self._intent(intent)
                preset, fixed = self._values(preset, metres, status)
                self._set(preset, fixed, status)
            return dict(ok=True)
        except (ValueError, TypeError, OverflowError, OSError, RuntimeError) as exc:
            return dict(ok=False, error=str(exc))

    def save_override(self, scope, intent, payload):
        path = None
        owns_save = False
        try:
            with self._lock:
                if self._saving or not isinstance(payload, dict):
                    raise ValueError("Another save is in progress or payload is invalid")
                status = self._require_scope(scope)
                self._intent(intent)
                name = payload.get("preset")
                preset, fixed = self._values(name, payload.get("metres"), status)
                self._saving = True
                owns_save = True
            # Disk work never holds the channel mutex or blocks the heartbeat.
            scn = status.scn.decode("ascii")
            path = self._catalog.save(scn, name, fixed, payload.get("comment", ""), payload.get("critical", False),
                                     scn.split("scn", 1)[0].upper(), self._missions.get(scn, {}).get("title", ""))
            with self._lock:
                self._require_scope(scope)
                self._set(preset, fixed, status, save=True)
                revision, session = self._command.revision, self._command.session
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not self._stop.wait(0.03):
                with self._lock:
                    current = self._require_scope(scope)
                    if current.command_session == session and current.save_ack == revision:
                        return dict(ok=True, path=str(path))
            raise TimeoutError("Renderer did not acknowledge this save")
        except (ValueError, TypeError, OverflowError, OSError, RuntimeError) as exc:
            suffix = " Saved to disk; restart the game to load it." if path else " Nothing was saved."
            return dict(ok=False, saved=bool(path), error=str(exc) + suffix)
        finally:
            if owns_save:
                with self._lock:
                    self._saving = False


def main():
    mapping = api = None
    try:
        import webview
        payload = json.loads((TOOLS / "mission_index.json").read_text(encoding="utf-8"))
        missions = {row["scn"].lower(): row for row in payload["missions"]}
        mapping = Mapping()
        api = PreviewApi(mapping, missions)
        options = dict(width=int(os.getenv("MW2_LEVEL_TWEAK_WIDTH", "1200")),
                       height=int(os.getenv("MW2_LEVEL_TWEAK_HEIGHT", "260")))
        if os.getenv("MW2_LEVEL_TWEAK_X") and os.getenv("MW2_LEVEL_TWEAK_Y"):
            options.update(x=int(os.environ["MW2_LEVEL_TWEAK_X"]), y=int(os.environ["MW2_LEVEL_TWEAK_Y"]))
        webview.create_window("MW2 Level Tweak", (TOOLS / "ui/index.html").as_uri(), js_api=api,
                              min_size=(640, 220), background_color="#081116", text_select=True, **options)
        webview.start(debug=False)
        return 0
    except Exception as exc:
        C.windll.user32.MessageBoxW(0, str(exc), "MW2 Level Tweak", 0x10)
        return 1
    finally:
        if api:
            api._close()
        elif mapping:
            mapping.close()


if __name__ == "__main__":
    raise SystemExit(main())
