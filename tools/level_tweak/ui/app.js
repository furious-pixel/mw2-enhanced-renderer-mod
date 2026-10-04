"use strict";

const $ = (id) => document.getElementById(id);
const bridge = () => window.pywebview.api;
const state = { scope: null, preset: "max", metres: 8000, status: null,
  intent: 0, pending: null, sending: false, saving: false, timer: null };
const sameScope = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const active = () => state.status?.connected && state.status?.in_mission;

function controls() {
  const disabled = !active() || state.saving;
  for (const element of document.querySelectorAll("button, input, textarea")) element.disabled = disabled;
  for (const button of document.querySelectorAll(".preset")) {
    const preset = button.dataset.preset;
    button.classList.toggle("active", preset === state.preset);
    if (preset === "shipped") button.disabled ||= !state.status?.shipped_available;
    if (preset === "farpatcher") button.disabled ||= !state.status?.farpatcher_available;
  }
  $("save-button").disabled ||= state.preset === "shipped";
}

function presetMetres(preset, status) {
  if (preset === "game") return status.native_metres;
  if (preset === "game-radial") return status.radial_metres;
  if (preset === "shipped") return status.shipped_metres;
  if (preset === "farpatcher") return status.farpatcher_metres;
  if (preset === "max") return 8000;
  return state.metres;
}

function render(status) {
  state.status = status;
  if (!status?.ok) {
    $("connection").textContent = status?.message || "Waiting for renderer";
    controls();
    return;
  }
  if (!sameScope(state.scope, status.scope)) {
    state.scope = status.scope;
    state.pending = null;
    clearTimeout(state.timer);
    state.preset = status.resolved.preset;
    state.metres = status.resolved.metres;
    $("comment").value = status.resolved.comment;
    $("critical").checked = status.resolved.critical;
    $("custom-metres").value = state.preset === "custom" ? state.metres.toFixed(2) : "";
    $("save-status").textContent = "Live preview does not persist until Save.";
  }
  $("connection").textContent = !status.connected ? "Renderer idle or disconnected" :
    status.in_mission ? "In mission" : "Load a mission";
  $("mission-title").textContent = status.in_mission ? status.title : "Waiting for mission";
  $("mission-code").textContent = status.in_mission ? `${status.code}  ${status.scn}` : "----";
  $("source").textContent = `source ${status.resolved_source}`;
  const label = status.applied_preset === "max" ? "unconstrained" : `${status.applied_metres.toFixed(2)} m`;
  $("live-metres").textContent = label;
  $("applied").textContent = `Applied: ${status.applied_preset} (${label}).` +
    (status.live_disagrees_with_catalog ? " Live VIEW disagrees with catalog." : "");
  $("applied").classList.toggle("warn", status.live_disagrees_with_catalog);
  if (document.activeElement !== $("metres"))
    $("metres").value = String(Math.min(8000, presetMetres(state.preset, status) || 8000));
  $("ticks").replaceChildren();
  for (const preset of ["game", "game-radial", "shipped", "farpatcher", "max"]) {
    const metres = presetMetres(preset, status);
    if (!Number.isFinite(metres) || metres <= 0) continue;
    const tick = document.createElement("div");
    tick.className = `tick tick-${preset}`;
    tick.style.left = `${Math.min(100, metres / 80)}%`;
    $("ticks").appendChild(tick);
  }
  controls();
}

async function sendPending() {
  if (state.sending || state.saving || !active()) return;
  state.sending = true;
  try {
    while (state.pending && !state.saving && active()) {
      const command = state.pending;
      state.pending = null;
      const result = await bridge().set_command(command.scope, command.intent, command.preset, command.metres);
      if (!result.ok && sameScope(command.scope, state.scope)) $("save-status").textContent = result.error;
    }
  } catch (error) {
    $("save-status").textContent = `Preview failed: ${error}`;
  } finally { state.sending = false; }
}

function preview(delay = 0) {
  if (!active() || state.saving) return;
  state.pending = { scope: state.scope, intent: ++state.intent, preset: state.preset, metres: state.metres };
  clearTimeout(state.timer);
  state.timer = setTimeout(sendPending, delay);
  controls();
}

async function save() {
  if (!active() || state.saving) return;
  state.saving = true;
  state.pending = null;
  clearTimeout(state.timer);
  const scope = state.scope;
  const intent = ++state.intent;
  controls();
  $("save-status").textContent = "Saving and waiting for the renderer…";
  try {
    const result = await bridge().save_override(scope, intent, { preset: state.preset, metres: state.metres,
      comment: $("comment").value, critical: $("critical").checked });
    $("save-status").textContent = result.ok ? `Saved to ${result.path}` : result.error;
  } catch (error) { $("save-status").textContent = `Save failed: ${error}`; }
  finally { state.saving = false; controls(); }
}

async function poll() {
  try { render(await bridge().get_status()); }
  catch (error) { render({ ok: false, message: `Status failed: ${error}` }); }
  setTimeout(poll, 250); // One outstanding poll, including slow bridge calls.
}

window.addEventListener("pywebviewready", async () => {
  for (const button of document.querySelectorAll(".preset")) {
    button.addEventListener("click", () => {
      state.preset = button.dataset.preset;
      if (state.preset === "custom") {
        const value = $("custom-metres").valueAsNumber;
        state.metres = Number.isFinite(value) && value >= 0 && value <= 42949672.95 ? value : state.metres;
        $("custom-metres").value = state.metres.toFixed(2);
      } else state.metres = presetMetres(state.preset, state.status) || 0;
      $("metres").value = String(Math.min(8000, state.metres));
      preview();
    });
  }
  $("metres").addEventListener("input", () => {
    state.preset = "custom";
    state.metres = Number($("metres").value);
    $("custom-metres").value = state.metres.toFixed(2);
    preview(80);
  });
  $("custom-metres").addEventListener("input", () => {
    const value = $("custom-metres").valueAsNumber;
    if (!Number.isFinite(value) || value < 0 || value > 42949672.95) {
      $("save-status").textContent = "Enter a finite, nonnegative distance within the supported range.";
      return;
    }
    state.preset = "custom"; state.metres = value; preview(150);
  });
  $("save-button").addEventListener("click", save);
  try {
    const bootstrap = await bridge().get_bootstrap();
    render(bootstrap.status);
    $("save-status").textContent = `Save writes ${bootstrap.user_path}`;
  } catch (error) { $("connection").textContent = String(error); }
  poll();
});

controls();
