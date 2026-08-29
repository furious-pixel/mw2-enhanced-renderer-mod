"use strict";

const app = {
  schema: [],
  values: {},
  activeSection: "installation",
  installation: {ok: false, directory: "", files: []},
  saveTimers: new Map(),
  configPaths: {},
  resetFiles: [],
  joystickSnapshot: {generation: 0, devices: []},
  joystickPollTimer: null,
  detecting: null,
  expandedCalibration: new Set(),
  toastTimer: null,
  previewState: {mode: null, yaw: 0, pitch: 0, lastTime: null},
};

const PREVIEW_TURRET_RANGE_DEGREES = 64;

const INSTALLATION_SECTION = {
  id: "installation",
  label: "Game Installation",
  icon: "installation",
};

const icons = {
  installation: `<svg viewBox="0 0 24 24"><path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5zM4 7.5l8 4.5 8-4.5M12 12v9"/></svg>`,
  controls: `<svg viewBox="0 0 24 24"><path d="M5 5v14M19 5v14M5 9h5M14 15h5M10 7v4M14 13v4"/></svg>`,
  display: `<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>`,
  hud: `<svg viewBox="0 0 24 24"><path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4M8 12h8M12 8v8"/></svg>`,
  tune: `<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6"/></svg>`,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function currentSection() {
  if (app.activeSection === INSTALLATION_SECTION.id) return INSTALLATION_SECTION;
  return app.schema.find((section) => section.id === app.activeSection);
}

function navigationSections() {
  return [INSTALLATION_SECTION, ...app.schema];
}

function valueFor(key, sectionId = app.activeSection) {
  return app.values[sectionId]?.[key];
}

function setConnectionState(kind, text) {
  const dot = $("#sidebar-status-dot");
  dot.className = `status-dot ${kind || ""}`;
  $("#sidebar-status").textContent = text;
}

function setSaveState(kind, text) {
  const element = $("#save-state");
  element.className = `save-state ${kind || ""}`;
  $("span", element).textContent = text;
}

function showToast(message, kind = "") {
  const toast = $("#toast");
  toast.className = `toast ${kind}`;
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(app.toastTimer);
  app.toastTimer = setTimeout(() => toast.classList.remove("visible"), 4200);
}

function openResetDialog() {
  const list = $("#reset-file-list");
  list.innerHTML = app.resetFiles.map((path) => `
    <div class="reset-file" title="${escapeHtml(path)}">
      <strong>${escapeHtml(path.split(/[\\/]/).pop())}</strong>
      <span>${escapeHtml(path)}</span>
    </div>
  `).join("");
  $("#reset-modal").classList.remove("hidden");
  $("#reset-cancel-button").focus();
}

function closeResetDialog() {
  $("#reset-modal").classList.add("hidden");
  $("#reset-defaults-button").focus();
}

async function resetToShippedDefaults() {
  const confirmButton = $("#reset-confirm-button");
  confirmButton.disabled = true;
  confirmButton.textContent = "Resetting…";
  for (const timer of app.saveTimers.values()) clearTimeout(timer);
  app.saveTimers.clear();
  finishAxisDetection();
  setSaveState("saving", "Restoring shipped defaults…");
  try {
    const result = await window.pywebview.api.reset_to_shipped_defaults();
    if (!result.ok) throw new Error(result.error || "Could not reset configuration.");
    app.values = result.values;
    closeResetDialog();
    renderSection();
    setSaveState("", "Shipped defaults restored");
    setConnectionState("connected", "Configuration ready");
    showToast("Shipped defaults restored in mod.conf and joystick.conf.", "success");
  } catch (error) {
    setSaveState("error", "Could not reset configuration");
    setConnectionState("error", "Reset error");
    showToast(error.message || String(error));
  } finally {
    confirmButton.disabled = false;
    confirmButton.textContent = "Reset both files";
  }
}

function bindResetDialog() {
  $("#reset-defaults-button").addEventListener("click", openResetDialog);
  $("#reset-cancel-button").addEventListener("click", closeResetDialog);
  $("#reset-confirm-button").addEventListener("click", resetToShippedDefaults);
  $("#reset-modal").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeResetDialog();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#reset-modal").classList.contains("hidden")) {
      closeResetDialog();
    }
  });
}

function formatValue(definition, value) {
  if (definition.unit === "%") {
    const percent = Number(value) * 100;
    return `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`;
  }
  if (definition.value_type === "int") {
    return `${Math.round(Number(value))}${definition.unit || ""}`;
  }
  const step = Number(definition.step || 0.01);
  const decimals = step >= 1 ? 0 : Math.min(3, Math.max(1, String(step).split(".")[1]?.length || 1));
  return `${Number(value).toFixed(decimals)}${definition.unit || ""}`;
}

function rangeProgress(definition, value) {
  const minimum = Number(definition.minimum);
  const maximum = Number(definition.maximum);
  return `${((Number(value) - minimum) / (maximum - minimum)) * 100}%`;
}

function renderNavigation() {
  const nav = $("#section-nav");
  nav.innerHTML = navigationSections().map((section) => `
    <button class="nav-button ${section.id === app.activeSection ? "active" : ""}" data-section="${section.id}">
      <span class="nav-icon">${icons[section.icon] || icons.tune}</span>
      <span class="nav-label">${escapeHtml(section.label)}</span>
      ${section.id === "installation" ? `
        <span class="nav-result ${app.installation.ok ? "ok" : "warning"}" aria-label="${app.installation.ok ? "Installation verified" : "Installation warning"}">
          ${app.installation.ok ? "✓" : "!"}
        </span>` : ""}
    </button>
  `).join("");
  $$(".nav-button", nav).forEach((button) => {
    button.addEventListener("click", () => switchSection(button.dataset.section));
  });
}

function switchSection(sectionId) {
  if (!navigationSections().some((section) => section.id === sectionId)) return;
  app.activeSection = sectionId;
  const configPath = sectionId === "installation"
    ? app.installation.directory
    : app.configPaths[sectionId] || "";
  $("#config-path").textContent = configPath;
  $("#config-path").title = configPath;
  renderNavigation();
  renderSection();
  $("#content-scroll").scrollTop = 0;
}

function renderSection() {
  const section = currentSection();
  if (!section) return;
  $("#breadcrumb-section").textContent = section.label.toUpperCase();
  const warning = $("#warning-banner");
  warning.textContent = section.warning || "";
  warning.classList.toggle("hidden", !section.warning);

  if (section.id === "installation") {
    renderInstallation();
    return;
  }

  const grid = $("#groups-grid");
  grid.classList.toggle("single-column", section.id !== "input");
  grid.innerHTML = section.groups.map((group) => {
    if (group.input_axis) return renderInputAxisCard(section, group);
    return renderSettingsCard(section, group);
  }).join("");
  bindControls(section);
  refreshCurves();
  refreshJoystickStatus();
}

function formatFileSize(size) {
  if (!Number.isFinite(Number(size))) return "Unavailable";
  const bytes = Number(size);
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function installationFileStatus(file) {
  if (file.matches) return {kind: "ok", label: "verified"};
  if (!file.exists) return {kind: "error", label: "missing"};
  if (file.error) return {kind: "error", label: "could not read"};
  return {kind: "warning", label: "modified or unsupported"};
}

function renderInstallation() {
  const installation = app.installation;
  const grid = $("#groups-grid");
  grid.classList.add("single-column");
  grid.innerHTML = `
    <article class="settings-card verification-card ${installation.ok ? "verified" : "warning"}">
      <header class="compact-panel-heading">
        <h2>File verification</h2>
        <span class="verification-result ${installation.ok ? "ok" : "warning"}">${installation.ok ? "✓ Supported files verified" : "! Installation warning"}</span>
      </header>
      <div class="installation-files">
        ${installation.files.map((file) => {
          const status = installationFileStatus(file);
          return `
            <section class="fingerprint-file">
              <div class="fingerprint-summary">
                <strong>${escapeHtml(file.name)}</strong>
                <span>(${formatFileSize(file.size)}, <code>${escapeHtml(file.relative_path || file.path)}</code>)</span>
              </div>
              <div class="fingerprint-hash">
                <span>SHA-256:</span>
                <code>${escapeHtml(file.sha256 || "Not available")}</code>
                <span class="inline-file-status ${status.kind}">(${escapeHtml(status.label)}).</span>
              </div>
              ${file.error ? `<p class="fingerprint-error">${escapeHtml(file.error)}</p>` : ""}
            </section>`;
        }).join("")}
      </div>
      <footer class="installation-note">
        Files are checked in the supported directory shown above. A different DOSBox mount path is not discovered automatically.
      </footer>
    </article>
    <article class="settings-card installation-guide">
      <header class="compact-panel-heading">
        <h2>Installation</h2>
      </header>
      <section class="installation-help">
        <p>
          This mod supports only <strong>MechWarrior 2: 31st Century Combat for DOS</strong>,
          updated with the official <strong>version 1.1</strong> patch. Other editions are not supported.
        </p>
        <div class="installation-step-heading">
          <span>1</span>
          <div><h3>Copy the game files</h3><p>Place your CD image and complete DOS game installation into the release's <code>game</code> directory.</p></div>
        </div>
        <pre>game/
├── MECH2_16B.BIN
├── MECH2_16B.CUE
└── c_mech2/
    └── <strong class="game-install-directory">mech2/  ← complete installed game directory</strong>
        ├── MW2.EXE
        ├── MW2.PRJ
        └── … all other installed game files</pre>
        <p class="optional-step">
          <strong>Optional:</strong> If your DOS copy is not already patched, the
          <a href="https://www.pcgamingwiki.com/wiki/MechWarrior_2%3A_31st_Century_Combat#Patches" target="_blank" rel="noopener noreferrer">PCGamingWiki patches section</a>
          links to the correct DOS v1.1 patch.
        </p>
        <div class="installation-step-heading">
          <span>2</span>
          <div><h3>Configure the DOS game</h3></div>
        </div>
        <p>In combat variable, set the <strong>"Detail section"</strong> as follows.</p>
        <p>
          The mod is verified to work only with all effects enabled and the game resolution set to
          <strong>1024×768</strong>.
        </p>
        <div class="required-settings" aria-label="Required MechWarrior 2 detail settings">
          <div><span>Object Textures</span><strong>On</strong></div>
          <div><span>Terrain Textures</span><strong>On</strong></div>
          <div><span>Display Detail</span><strong>High</strong></div>
          <div><span>Object Density</span><strong>High</strong></div>
          <div><span>Chunky Explosions</span><strong>On</strong></div>
          <div><span>Resolution</span><strong>1024×768</strong></div>
        </div>
      </section>
    </article>`;
}

function renderSettingsCard(section, group) {
  const settings = `
    <div class="card-body">
      ${group.settings.map((definition) => renderSetting(section, definition)).join("")}
    </div>`;
  return `
    <article class="settings-card ${group.featured ? "featured" : ""}">
      <header class="card-heading">
        <div><h2>${escapeHtml(group.title)}</h2><p>${escapeHtml(group.subtitle || "")}</p></div>
      </header>
      ${group.controller_selector
        ? `<div class="aiming-overview">${settings}${renderControlPreview()}</div>${renderControllerSelector()}`
        : settings}
    </article>`;
}

function renderControlPreview() {
  const relative = valueFor("turret_aim_mode", "input") === "relative";
  return `
    <section class="control-preview" aria-label="Live in-game control preview">
      <header class="control-preview-heading">
        <div>
          <span class="summary-label">Live control preview</span>
          <strong>${relative ? "Simulated turret position" : "Turret position"}</strong>
        </div>
        <button class="small-button preview-center ${relative ? "" : "hidden"}" type="button" data-center-preview>Center simulation</button>
      </header>
      <p class="control-preview-note">Setup aid only — use it to verify that each joystick direction matches the game.</p>
      <canvas class="cockpit-preview" data-control-preview aria-label="Turret, chassis turn, and throttle command preview"></canvas>
    </section>`;
}

function activeInputSettings(group) {
  const aimingMode = valueFor("turret_aim_mode", "input");
  return group.settings.filter((definition) => {
    if (definition.key.startsWith("direct_")) return aimingMode === "direct";
    if (definition.key.startsWith("relative_")) return aimingMode === "relative";
    return true;
  });
}

function activeResponseCurve(group) {
  if (!group?.response_curves) return null;
  const aimingMode = valueFor("turret_aim_mode", "input");
  return group.response_curves[aimingMode] || group.response_curves.all || null;
}

function responseSummary(group) {
  const curve = activeResponseCurve(group);
  if (!curve) return "Linear";
  const mode = valueFor(curve.mode, "input");
  const labels = {linear: "Linear", power: "Power curve", blended_curve: "Blended curve"};
  const aimingMode = valueFor("turret_aim_mode", "input");
  if (group.id === "turret_yaw" || group.id === "turret_pitch") {
    return `${aimingMode === "direct" ? "Direct" : "Relative"} · ${labels[mode] || mode}`;
  }
  return labels[mode] || mode;
}

function joystickBindingState(group) {
  const deviceName = String(valueFor(group.binding.device, "input") || "");
  const axisIndex = Number(valueFor(group.binding.axis, "input"));
  if (!deviceName) return {kind: "unassigned", text: "Not assigned"};
  const matches = app.joystickSnapshot.devices.filter((device) => device.name === deviceName);
  if (!matches.length) return {kind: "missing", text: "Device missing"};
  if (matches.length !== 1 || matches[0].duplicate) {
    return {kind: "error", text: "Duplicate name unsupported"};
  }
  if (!Number.isInteger(axisIndex) || axisIndex < 0 || axisIndex >= matches[0].axis_count) {
    return {kind: "error", text: "Axis unavailable"};
  }
  return {kind: "connected", text: "Connected"};
}

function boundAxisSample(group) {
  const deviceName = String(valueFor(group.binding.device, "input") || "");
  const axisIndex = Number(valueFor(group.binding.axis, "input"));
  const matches = app.joystickSnapshot.devices.filter((device) => device.name === deviceName);
  if (matches.length !== 1 || matches[0].duplicate) return null;
  const rawValue = matches[0].axes[axisIndex];
  if (!Number.isFinite(rawValue)) return null;
  return {
    device: matches[0],
    axisIndex,
    rawValue: Number(rawValue),
    normalized: rawAxisToNormalized(Number(rawValue)),
  };
}

function renderInputAxisCard(section, group) {
  const deviceName = String(valueFor(group.binding.device, "input") || "");
  const axisIndex = Number(valueFor(group.binding.axis, "input"));
  const bindingState = joystickBindingState(group);
  const expanded = app.expandedCalibration.has(group.id);
  const curve = activeResponseCurve(group);
  return `
    <article class="settings-card input-axis-card">
      <header class="card-heading input-axis-heading">
        <div><h2>${escapeHtml(group.title)}</h2><p>${escapeHtml(group.subtitle || "")}</p></div>
        <div class="axis-actions">
          <button class="small-button assign-button" type="button" data-detect="${group.id}">Assign</button>
          <button class="small-button calibrate-button ${expanded ? "active" : ""}" type="button" data-toggle-calibration="${group.id}">${expanded ? "Done" : "Calibrate"}</button>
        </div>
      </header>
      <div class="axis-summary">
        <div class="axis-identity">
          <span class="summary-label">Assigned input</span>
          <strong title="${escapeHtml(deviceName)}">${escapeHtml(deviceName || "Unassigned")}</strong>
          <span class="axis-binding-detail ${bindingState.kind}">${escapeHtml(bindingState.text)}${deviceName ? ` · Axis ${axisIndex}` : ""}</span>
        </div>
        <div class="axis-response-name">
          <span class="summary-label">Response</span>
          <strong>${escapeHtml(responseSummary(group))}</strong>
        </div>
        ${curve ? `<div class="axis-graph-block">
          <canvas class="curve-canvas axis-curve" data-curve-group="${group.id}" data-axis-graph="${group.id}" aria-label="Calibrated input response graph"></canvas>
          <div class="axis-response-readout">
            <span><i class="input-swatch"></i>Input <b data-axis-input-value>0.000</b></span>
            <span><i class="output-swatch"></i>Response <b data-axis-output-value>0.000</b></span>
          </div>
        </div>` : ""}
      </div>
      <div class="calibration-drawer ${expanded ? "" : "hidden"}">
        <div class="calibration-intro">Move the assigned control while adjusting these values. Every change is written to joystick.conf immediately.</div>
        <div class="card-body">
          ${activeInputSettings(group).map((definition) => renderSetting(section, definition)).join("")}
        </div>
      </div>
    </article>`;
}

function renderControllerSelector() {
  return `
    <div class="controller-strip">
      <div><span class="summary-label">SDL joystick service</span><strong id="joystick-state">Scanning for joysticks…</strong></div>
      <div class="controller-device-list" id="controller-device-list"></div>
    </div>`;
}

function renderSetting(section, definition) {
  const value = app.values[section.id][definition.key];
  const description = definition.description;
  return `
    <div class="setting-row">
      <div>
        <div class="setting-label">${escapeHtml(definition.label)}</div>
        ${description ? `<div class="setting-description">${escapeHtml(description)}</div>` : ""}
      </div>
      <div class="control-wrap">${renderControl(definition, value)}</div>
    </div>`;
}

function renderControl(definition, value) {
  const common = `data-key="${definition.key}"`;
  if (definition.control === "boolean") {
    return `<div class="segmented-control" data-choice-key="${definition.key}">
      <button type="button" data-choice-value="true" class="${value ? "active" : ""}">true</button>
      <button type="button" data-choice-value="false" class="${value ? "" : "active"}">false</button>
    </div>`;
  }
  if (definition.control === "choice") {
    if (definition.choices.length === 2) {
      return `<div class="segmented-control" data-choice-key="${definition.key}">
        ${definition.choices.map((choice) => `<button type="button" data-choice-value="${escapeHtml(choice.value)}" class="${choice.value === value ? "active" : ""}">${escapeHtml(choice.label)}</button>`).join("")}
      </div>`;
    }
    return `<select class="select-control" ${common}>
      ${definition.choices.map((choice) => `<option value="${escapeHtml(choice.value)}" ${choice.value === value ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}
    </select>`;
  }
  if (definition.control === "number") {
    const input = `<input class="number-control" type="number" ${common} value="${value}" min="${definition.minimum}" max="${definition.maximum}" step="${definition.step}">`;
    return definition.capture
      ? `<div class="number-with-action">${input}<button class="capture-button" type="button" data-capture="${definition.key}">Use live</button></div>`
      : input;
  }
  return `<div class="slider-control">
    <input class="range-input" type="range" ${common} value="${value}" min="${definition.minimum}" max="${definition.maximum}" step="${definition.step}" style="--range-progress:${rangeProgress(definition, value)}">
    <span class="range-value" data-range-value="${definition.key}">${formatValue(definition, value)}</span>
  </div>`;
}

function definitionFor(section, key) {
  for (const group of section.groups) {
    const found = group.settings.find((setting) => setting.key === key);
    if (found) return found;
  }
  return null;
}

function bindControls(section) {
  $$(`[data-key]`, $("#groups-grid")).forEach((control) => {
    const definition = definitionFor(section, control.dataset.key);
    if (!definition) return;
    const eventName = control.type === "range" ? "input" : "change";
    control.addEventListener(eventName, () => {
      let value;
      if (definition.value_type === "bool") value = control.value === "true";
      else if (definition.value_type === "int") value = Math.round(Number(control.value));
      else if (definition.value_type === "float") value = Number(control.value);
      else value = control.value;
      app.values[section.id][definition.key] = value;
      if (control.type === "range") {
        control.style.setProperty("--range-progress", rangeProgress(definition, value));
        const display = $(`[data-range-value="${definition.key}"]`);
        if (display) display.textContent = formatValue(definition, value);
      }
      refreshCurves();
      scheduleSave(section.id, definition, value, control.type !== "range");
    });
    if (control.type === "range") {
      control.addEventListener("change", () => flushSave(section.id, definition, Number(control.value)));
    }
  });
  $$(`[data-choice-key]`, $("#groups-grid")).forEach((control) => {
    const definition = definitionFor(section, control.dataset.choiceKey);
    $$(`[data-choice-value]`, control).forEach((button) => {
      button.addEventListener("click", () => {
        const rawValue = button.dataset.choiceValue;
        const value = definition.value_type === "bool" ? rawValue === "true" : rawValue;
        app.values[section.id][definition.key] = value;
        scheduleSave(section.id, definition, value, true);
        if (definition.key === "turret_aim_mode" || definition.key.endsWith("_curve_mode")) renderSection();
        else {
          $$(`[data-choice-value]`, control).forEach((candidate) => candidate.classList.toggle("active", candidate === button));
          refreshCurves();
        }
      });
    });
  });
  $$(`[data-detect]`, $("#groups-grid")).forEach((button) => {
    button.addEventListener("click", () => detectAxis(button.dataset.detect, button));
  });
  $$(`[data-capture]`, $("#groups-grid")).forEach((button) => {
    button.addEventListener("click", () => captureThrottleEndpoint(button.dataset.capture));
  });
  $$(`[data-toggle-calibration]`, $("#groups-grid")).forEach((button) => {
    button.addEventListener("click", () => {
      const groupId = button.dataset.toggleCalibration;
      if (app.expandedCalibration.has(groupId)) app.expandedCalibration.delete(groupId);
      else app.expandedCalibration.add(groupId);
      renderSection();
    });
  });
  const centerPreview = $("[data-center-preview]", $("#groups-grid"));
  if (centerPreview) centerPreview.addEventListener("click", centerControlPreview);
}

function configFileName(sectionId) {
  const path = app.configPaths[sectionId] || "configuration";
  return path.split(/[\\/]/).pop();
}

function scheduleSave(sectionId, definition, value, immediate) {
  const timerKey = `${sectionId}:${definition.key}`;
  clearTimeout(app.saveTimers.get(timerKey));
  setSaveState("saving", `Updating ${configFileName(sectionId)}…`);
  if (immediate) {
    flushSave(sectionId, definition, value);
    return;
  }
  app.saveTimers.set(timerKey, setTimeout(() => flushSave(sectionId, definition, value), 160));
}

async function flushSave(sectionId, definition, value) {
  const timerKey = `${sectionId}:${definition.key}`;
  clearTimeout(app.saveTimers.get(timerKey));
  app.saveTimers.delete(timerKey);
  try {
    const result = await window.pywebview.api.update_setting(sectionId, definition.key, value);
    if (!result.ok) throw new Error(result.error || "Could not update configuration.");
    Object.assign(app.values[sectionId], result.values);
    const updatedSection = app.schema.find((section) => section.id === sectionId);
    Object.entries(result.values).forEach(([changedKey, changedValue]) => {
      const changedDefinition = definitionFor(updatedSection, changedKey);
      if (changedDefinition) syncControlValue(changedDefinition, changedValue);
    });
    setSaveState("", "Saved automatically");
    setConnectionState("connected", "Configuration ready");
  } catch (error) {
    setSaveState("error", "Could not save change");
    setConnectionState("error", "Save error");
    showToast(error.message || String(error));
  }
}

function syncControlValue(definition, value) {
  const segmented = $(`[data-choice-key="${definition.key}"]`);
  if (segmented) {
    $$(`[data-choice-value]`, segmented).forEach((button) => {
      button.classList.toggle("active", button.dataset.choiceValue === String(value));
    });
    return;
  }
  const control = $(`[data-key="${definition.key}"]`);
  if (!control) return;
  if (definition.value_type === "bool") control.value = value ? "true" : "false";
  else control.value = value;
  if (control.type === "range") {
    control.style.setProperty("--range-progress", rangeProgress(definition, value));
    const display = $(`[data-range-value="${definition.key}"]`);
    if (display) display.textContent = formatValue(definition, value);
  }
}

function curveValue(x, mode, blend, exponent) {
  if (mode === "power") return x ** exponent;
  if (mode === "blended_curve") return (1 - blend) * x + blend * (x ** exponent);
  return x;
}

function clampValue(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, Number(value)));
}

function signedCurveValue(value, mode, blend, exponent) {
  const sign = value < 0 ? -1 : 1;
  return sign * curveValue(Math.abs(value), mode, blend, exponent);
}

function normalizedToRawAxis(value) {
  value = clampValue(value, -1, 1);
  return value >= 0 ? Math.round(value * 32767) : Math.round(value * 32768);
}

function rawAxisToNormalized(value) {
  value = clampValue(value, -32768, 32767);
  return value >= 0 ? value / 32767 : value / 32768;
}

function centeredAxisResponse(rawInput, names, curve, extraScale = 1) {
  let normalized = clampValue(rawInput, -1, 1);
  if (valueFor(names.invert, "input")) normalized = -normalized;
  const deadzone = Number(valueFor(names.deadzone, "input"));
  const inputSaturation = Number(valueFor(names.inputSaturation, "input"));
  const magnitude = Math.abs(normalized);
  let calibrated = 0;
  if (magnitude > deadzone) {
    const span = Math.max(0.000001, inputSaturation - deadzone);
    calibrated = Math.sign(normalized) * clampValue((magnitude - deadzone) / span, 0, 1);
  }
  const curved = signedCurveValue(
    calibrated,
    valueFor(curve.mode, "input"),
    Number(valueFor(curve.blend, "input")),
    Number(valueFor(curve.exponent, "input")),
  );
  const outputSaturation = Number(valueFor(names.outputSaturation, "input"));
  return clampValue(curved * outputSaturation * extraScale, -1, 1);
}

function responseForInputGroup(groupId, rawInput) {
  const section = app.schema.find((candidate) => candidate.id === "input");
  const group = section?.groups.find((candidate) => candidate.id === groupId);
  const curve = activeResponseCurve(group);
  if (!group || !curve) return rawInput;
  if (groupId === "turret_yaw" || groupId === "turret_pitch") {
    return centeredAxisResponse(rawInput, {
      invert: `invert_${groupId}`,
      deadzone: `${groupId}_deadzone`,
      inputSaturation: `${groupId}_input_saturation`,
      outputSaturation: `${groupId}_output_saturation`,
    }, curve);
  }
  if (groupId === "chassis_turn") {
    return centeredAxisResponse(rawInput, {
      invert: "invert_chassis_turn",
      deadzone: "chassis_turn_input_deadzone",
      inputSaturation: "chassis_turn_input_saturation",
      outputSaturation: "chassis_turn_output_saturation",
    }, curve, Number(valueFor("chassis_turn_scale", "input")));
  }

  let calibratedRaw = normalizedToRawAxis(rawInput);
  if (valueFor("invert_throttle", "input")) {
    calibratedRaw = clampValue(-calibratedRaw, -32768, 32767);
  }
  const start = Number(valueFor("throttle_raw_input_start", "input"));
  const end = Number(valueFor("throttle_raw_input_end", "input"));
  let normalizedThrottle;
  if (calibratedRaw <= start) normalizedThrottle = 0;
  else if (calibratedRaw >= end) normalizedThrottle = 1;
  else normalizedThrottle = (calibratedRaw - start) / Math.max(1, end - start);
  const curved = curveValue(
    normalizedThrottle,
    valueFor(curve.mode, "input"),
    Number(valueFor(curve.blend, "input")),
    Number(valueFor(curve.exponent, "input")),
  );
  return clampValue(
    curved * Number(valueFor("throttle_output_saturation", "input")),
    0,
    1,
  );
}

function centerControlPreview() {
  app.previewState.yaw = 0;
  app.previewState.pitch = 0;
  app.previewState.lastTime = performance.now();
  updateLiveJoystick();
}

function directionalValue(value, negative, positive, multiplier = 100, suffix = "%") {
  if (!Number.isFinite(value)) return "Unavailable";
  const magnitude = Math.abs(value) * multiplier;
  if (magnitude < 0.5) return "Centered";
  return `${value < 0 ? negative : positive} ${Math.round(magnitude)}${suffix}`;
}

function resizeCanvas(canvas, width, height) {
  const ratio = window.devicePixelRatio || 1;
  const pixelWidth = Math.round(width * ratio);
  const pixelHeight = Math.round(height * ratio);
  if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
  if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return ctx;
}

function updateControlPreview(outputs) {
  const canvas = $("[data-control-preview]");
  if (!canvas) {
    app.previewState.lastTime = null;
    return;
  }

  const now = performance.now();
  const mode = valueFor("turret_aim_mode", "input");
  const state = app.previewState;
  if (state.mode !== mode) {
    state.mode = mode;
    state.yaw = 0;
    state.pitch = 0;
    state.lastTime = now;
  }

  const yaw = outputs.turret_yaw;
  const pitch = outputs.turret_pitch;
  const yawRate = Number.isFinite(yaw)
    ? yaw * Number(valueFor("relative_turret_yaw_degrees_per_second", "input"))
    : NaN;
  const pitchRate = Number.isFinite(pitch)
    ? pitch * Number(valueFor("relative_turret_pitch_degrees_per_second", "input"))
    : NaN;
  if (mode === "direct") {
    state.yaw = Number.isFinite(yaw) ? yaw : 0;
    state.pitch = Number.isFinite(pitch) ? pitch : 0;
  } else {
    const delta = state.lastTime === null
      ? 0
      : clampValue((now - state.lastTime) / 1000, 0, 0.1);
    state.yaw = clampValue(
      state.yaw + (Number.isFinite(yawRate) ? yawRate : 0) * delta
        / PREVIEW_TURRET_RANGE_DEGREES,
      -1,
      1,
    );
    state.pitch = clampValue(
      state.pitch + (Number.isFinite(pitchRate) ? pitchRate : 0) * delta
        / PREVIEW_TURRET_RANGE_DEGREES,
      -1,
      1,
    );
  }
  state.lastTime = now;

  const throttle = Number.isFinite(outputs.throttle)
    ? clampValue(outputs.throttle, 0, 1)
    : 0;
  const turn = Number.isFinite(outputs.chassis_turn)
    ? clampValue(outputs.chassis_turn, -1, 1)
    : 0;
  let aimText;
  if (mode === "relative") {
    aimText = `YAW ${directionalValue(yawRate, "LEFT", "RIGHT", 1, "°/s")} · PITCH ${directionalValue(pitchRate, "UP", "DOWN", 1, "°/s")}`;
  } else {
    aimText = `YAW ${directionalValue(yaw, "LEFT", "RIGHT")} · PITCH ${directionalValue(pitch, "UP", "DOWN")}`;
  }
  const turnText = directionalValue(outputs.chassis_turn, "LEFT", "RIGHT");
  const throttleText = Number.isFinite(outputs.throttle)
    ? `${Math.round(throttle * 100)}%`
    : "Unavailable";

  const width = Math.max(320, Math.round(canvas.clientWidth || 360));
  const height = 210;
  const ctx = resizeCanvas(canvas, width, height);

  const left = 14;
  const right = width - 58;
  const top = 10;
  const bottom = 137;
  const centerX = (left + right) / 2;
  const centerY = (top + bottom) / 2;
  ctx.fillStyle = "rgba(3,11,14,.64)";
  ctx.strokeStyle = "rgba(85,214,194,.18)";
  ctx.fillRect(left, top, right - left, bottom - top);
  ctx.strokeRect(left + 0.5, top + 0.5, right - left - 1, bottom - top - 1);

  ctx.strokeStyle = "rgba(143,165,171,.1)";
  ctx.lineWidth = 1;
  for (let index = 1; index < 5; index++) {
    const x = left + (right - left) * index / 5;
    const y = top + (bottom - top) * index / 5;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
  }
  ctx.setLineDash([3, 4]);
  ctx.strokeStyle = "rgba(143,165,171,.24)";
  ctx.beginPath(); ctx.moveTo(centerX, top); ctx.lineTo(centerX, bottom); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(left, centerY); ctx.lineTo(right, centerY); ctx.stroke();

  const reticleX = centerX + state.yaw * ((right - left) / 2 - 24);
  const reticleY = centerY + state.pitch * ((bottom - top) / 2 - 18);
  ctx.strokeStyle = "rgba(230,183,92,.32)";
  ctx.beginPath(); ctx.moveTo(centerX, centerY); ctx.lineTo(reticleX, reticleY); ctx.stroke();
  ctx.setLineDash([]);
  ctx.strokeStyle = "#e6b75c";
  ctx.fillStyle = "#e6b75c";
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.arc(reticleX, reticleY, 10, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(reticleX - 18, reticleY); ctx.lineTo(reticleX - 7, reticleY);
  ctx.moveTo(reticleX + 7, reticleY); ctx.lineTo(reticleX + 18, reticleY);
  ctx.moveTo(reticleX, reticleY - 18); ctx.lineTo(reticleX, reticleY - 7);
  ctx.moveTo(reticleX, reticleY + 7); ctx.lineTo(reticleX, reticleY + 18);
  ctx.stroke();
  ctx.beginPath(); ctx.arc(reticleX, reticleY, 2, 0, Math.PI * 2); ctx.fill();

  const throttleX = width - 37;
  const throttleTop = 28;
  const throttleHeight = 109;
  ctx.fillStyle = "rgba(143,165,171,.08)";
  ctx.strokeStyle = "rgba(143,165,171,.28)";
  ctx.fillRect(throttleX, throttleTop, 16, throttleHeight);
  ctx.strokeRect(throttleX + 0.5, throttleTop + 0.5, 15, throttleHeight - 1);
  ctx.fillStyle = "#55d6c2";
  ctx.fillRect(throttleX + 3, throttleTop + 3 + (throttleHeight - 6) * (1 - throttle), 10, (throttleHeight - 6) * throttle);
  ctx.fillStyle = "#8fa5ab";
  ctx.font = "700 10px Consolas, monospace";
  ctx.textAlign = "center";
  ctx.fillText("THR", throttleX + 8, 18);
  ctx.fillText("100", throttleX - 12, throttleTop + 5);
  ctx.fillText("0", throttleX - 8, throttleTop + throttleHeight);
  ctx.fillText(throttleText, throttleX + 8, 153);

  const turnY = 164;
  ctx.strokeStyle = "rgba(143,165,171,.34)";
  ctx.beginPath(); ctx.moveTo(left + 16, turnY); ctx.lineTo(right - 6, turnY); ctx.stroke();
  const turnX = centerX + turn * ((right - left) / 2 - 22);
  ctx.fillStyle = "#e6b75c";
  ctx.beginPath();
  ctx.moveTo(turnX, turnY - 7); ctx.lineTo(turnX + 7, turnY);
  ctx.lineTo(turnX, turnY + 7); ctx.lineTo(turnX - 7, turnY); ctx.closePath(); ctx.fill();
  ctx.fillStyle = "#5e747a";
  ctx.font = "700 10px Consolas, monospace";
  ctx.textAlign = "left"; ctx.fillText("L", left, turnY + 3);
  ctx.textAlign = "right"; ctx.fillText("R", right + 7, turnY + 3);
  ctx.textAlign = "center"; ctx.fillText(`CHASSIS ${turnText}`, centerX, 181);

  ctx.font = "600 10px Consolas, monospace";
  ctx.fillStyle = "#8fa5ab";
  ctx.textAlign = "left"; ctx.fillText(aimText, left, 202);
}

function throttleEndpointPosition(settingKey) {
  let rawValue = Number(valueFor(settingKey, "input"));
  if (valueFor("invert_throttle", "input")) {
    rawValue = clampValue(-rawValue, -32768, 32767);
  }
  return 50 + rawAxisToNormalized(rawValue) * 50;
}

function refreshCurves() {
  const section = currentSection();
  if (!section) return;
  $$(`[data-curve-group]`).forEach((canvas) => {
    const group = section.groups.find((candidate) => candidate.id === canvas.dataset.curveGroup);
    if (!activeResponseCurve(group)) return;
    drawCurve(canvas, group, null);
  });
}

function drawCurve(canvas, group, liveInput) {
  const width = Math.max(120, Math.round(canvas.clientWidth || 120));
  const height = Math.max(72, Math.round(canvas.clientHeight || 72));
  const ctx = resizeCanvas(canvas, width, height);
  const padLeft = 24;
  const padRight = 10;
  const padTop = 12;
  const padBottom = 19;
  const drawWidth = width - padLeft - padRight;
  const drawHeight = height - padTop - padBottom;
  ctx.strokeStyle = "rgba(143,165,171,.13)";
  ctx.lineWidth = 1;
  for (let index = 0; index <= 4; index++) {
    const x = padLeft + drawWidth * index / 4;
    const y = padTop + drawHeight * index / 4;
    ctx.beginPath(); ctx.moveTo(x, padTop); ctx.lineTo(x, height - padBottom); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(padLeft, y); ctx.lineTo(width - padRight, y); ctx.stroke();
  }
  ctx.setLineDash([3, 3]);
  ctx.strokeStyle = "rgba(143,165,171,.32)";
  const zeroY = group.id === "throttle"
    ? height - padBottom
    : padTop + drawHeight / 2;
  ctx.beginPath(); ctx.moveTo(padLeft, zeroY); ctx.lineTo(width - padRight, zeroY); ctx.stroke();
  const zeroX = padLeft + drawWidth / 2;
  ctx.beginPath(); ctx.moveTo(zeroX, padTop); ctx.lineTo(zeroX, height - padBottom); ctx.stroke();
  ctx.setLineDash([]);

  if (group.id === "throttle") {
    for (const [settingKey, label] of [
      ["throttle_raw_input_start", "IDLE"],
      ["throttle_raw_input_end", "MAX"],
    ]) {
      const endpointX = padLeft + throttleEndpointPosition(settingKey) * drawWidth / 100;
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = "rgba(230,183,92,.62)";
      ctx.beginPath(); ctx.moveTo(endpointX, padTop); ctx.lineTo(endpointX, height - padBottom); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(230,183,92,.9)";
      ctx.font = "700 9px Consolas, monospace";
      ctx.textAlign = endpointX < padLeft + drawWidth * 0.15 ? "left" : endpointX > padLeft + drawWidth * 0.85 ? "right" : "center";
      ctx.fillText(label, endpointX, padTop + 9);
    }
  }

  ctx.strokeStyle = "#65dfcc";
  ctx.lineWidth = 2;
  ctx.shadowColor = "rgba(85,214,194,.35)";
  ctx.shadowBlur = 5;
  ctx.beginPath();
  for (let step = 0; step <= 160; step++) {
    const input = -1 + step / 80;
    const output = responseForInputGroup(group.id, input);
    const x = padLeft + (input + 1) / 2 * drawWidth;
    const y = group.id === "throttle"
      ? padTop + (1 - output) * drawHeight
      : padTop + (1 - (output + 1) / 2) * drawHeight;
    if (step === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.shadowBlur = 0;

  ctx.fillStyle = "rgba(143,165,171,.72)";
  ctx.font = "9px Consolas, monospace";
  ctx.textAlign = "right";
  ctx.fillText(group.id === "throttle" ? "1" : "+1", padLeft - 4, padTop + 3);
  if (group.id === "throttle") {
    ctx.fillText("0", padLeft - 4, height - padBottom + 3);
  } else {
    ctx.fillText("0", padLeft - 4, padTop + drawHeight / 2 + 3);
    ctx.fillText("-1", padLeft - 4, height - padBottom + 3);
  }
  ctx.textAlign = "left";
  ctx.fillText("-1", padLeft, height - 6);
  ctx.textAlign = "center";
  ctx.fillText("PHYSICAL INPUT", padLeft + drawWidth / 2, height - 6);
  ctx.textAlign = "right";
  ctx.fillText("+1", width - padRight, height - 6);

  if (Number.isFinite(liveInput)) {
    const live = responseForInputGroup(group.id, liveInput);
    const x = padLeft + (liveInput + 1) * drawWidth / 2;
    const y = group.id === "throttle"
      ? padTop + (1 - live) * drawHeight
      : padTop + (1 - (live + 1) / 2) * drawHeight;
    ctx.strokeStyle = "rgba(85,214,194,.72)";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, padTop); ctx.lineTo(x, height - padBottom); ctx.stroke();
    ctx.fillStyle = "#e6b75c";
    ctx.shadowColor = "rgba(230,183,92,.75)";
    ctx.shadowBlur = 8;
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
  }
}

function refreshJoystickStatus() {
  const state = $("#joystick-state");
  const list = $("#controller-device-list");
  if (!state || !list) return;
  const devices = app.joystickSnapshot.devices;
  state.textContent = devices.length
    ? `${devices.length} connected`
    : "No joystick detected";
  const catalogKey = devices.map(
    (device) => `${device.name}\u0000${device.axis_count}\u0000${device.duplicate}`,
  ).join("\u0001");
  if (list.dataset.catalogKey === catalogKey) return;
  list.dataset.catalogKey = catalogKey;
  list.innerHTML = devices.length
    ? devices.map((device) => `
        <span class="controller-device ${device.duplicate ? "error" : ""}" title="${escapeHtml(device.name)}">
          ${escapeHtml(device.name)} · ${device.axis_count} axes${device.duplicate ? " · duplicate name" : ""}
        </span>`).join("")
    : `<span class="controller-device missing">Connect joysticks before assigning axes.</span>`;
}

function updateLiveJoystick() {
  const inputSection = app.schema.find((candidate) => candidate.id === "input");
  const outputs = {};
  $$(`[data-axis-graph]`).forEach((canvas) => {
    const group = inputSection?.groups.find(
      (candidate) => candidate.id === canvas.dataset.axisGraph,
    );
    if (!group) return;
    const sample = boundAxisSample(group);
    const input = sample?.normalized;
    drawCurve(canvas, group, input ?? null);
    const block = canvas.closest(".axis-graph-block");
    const inputValue = $("[data-axis-input-value]", block);
    const outputValue = $("[data-axis-output-value]", block);
    if (Number.isFinite(input)) {
      outputs[group.id] = responseForInputGroup(group.id, input);
      inputValue.textContent = input.toFixed(3);
      outputValue.textContent = outputs[group.id].toFixed(3);
    } else {
      outputs[group.id] = null;
      inputValue.textContent = "N/A";
      outputValue.textContent = "N/A";
    }
  });
  updateControlPreview(outputs);
}

async function pollJoystickSnapshot() {
  try {
    app.joystickSnapshot = await window.pywebview.api.get_joystick_snapshot();
    refreshJoystickStatus();
    updateLiveJoystick();
  } catch (error) {
    setConnectionState("error", "Joystick service error");
  } finally {
    app.joystickPollTimer = setTimeout(pollJoystickSnapshot, 33);
  }
}

function finishAxisDetection(message = "") {
  if (!app.detecting) return;
  const {button, idleLabel} = app.detecting;
  button.classList.remove("listening");
  button.textContent = idleLabel;
  app.detecting = null;
  if (message) showToast(message);
}

function detectAxis(groupId, button) {
  const usableDevices = app.joystickSnapshot.devices.filter(
    (device) => !device.duplicate && device.axis_count > 0,
  );
  if (!usableDevices.length) {
    showToast("Connect a uniquely named joystick before assigning an axis.");
    return;
  }
  finishAxisDetection();
  const idleLabel = button.dataset.idleLabel || button.textContent;
  button.dataset.idleLabel = idleLabel;
  const baseline = new Map(
    usableDevices.map((device) => [device.name, [...device.axes]]),
  );
  button.classList.add("listening");
  button.textContent = "Move axis…";
  const token = Symbol(groupId);
  app.detecting = {groupId, button, idleLabel, token};
  const started = performance.now();

  async function sample() {
    if (app.detecting?.token !== token) return;
    if (performance.now() - started > 5000) {
      finishAxisDetection("No axis movement detected.");
      return;
    }
    let best = null;
    for (const device of app.joystickSnapshot.devices) {
      const initialAxes = baseline.get(device.name);
      if (!initialAxes || device.duplicate) continue;
      device.axes.forEach((rawValue, axisIndex) => {
        const delta = Math.abs(Number(rawValue) - Number(initialAxes[axisIndex] || 0));
        if (!best || delta > best.delta) {
          best = {deviceName: device.name, axisIndex, delta};
        }
      });
    }
    if (best && best.delta >= 11468) {
      try {
        const result = await window.pywebview.api.assign_axis(
          groupId,
          best.deviceName,
          best.axisIndex,
        );
        if (!result.ok) throw new Error(result.error || "Could not assign joystick axis.");
        app.values.input[result.device_key] = result.device;
        app.values.input[result.axis_key] = result.axis;
        finishAxisDetection();
        renderSection();
      } catch (error) {
        finishAxisDetection(error.message || String(error));
      }
      return;
    }
    requestAnimationFrame(sample);
  }
  requestAnimationFrame(sample);
}

function captureThrottleEndpoint(settingKey) {
  const inputSection = app.schema.find((candidate) => candidate.id === "input");
  const throttleGroup = inputSection?.groups.find((candidate) => candidate.id === "throttle");
  const sample = boundAxisSample(throttleGroup);
  if (!sample) {
    showToast("Assign an available throttle axis before capturing an endpoint.");
    return;
  }
  let raw = sample.rawValue;
  if (valueFor("invert_throttle", "input")) {
    raw = clampValue(-raw, -32768, 32767);
  }
  const section = app.schema.find((candidate) => candidate.id === "input");
  const definition = definitionFor(section, settingKey);
  app.values.input[settingKey] = raw;
  syncControlValue(definition, raw);
  flushSave("input", definition, raw);
}

async function initialize() {
  try {
    const state = await window.pywebview.api.get_state();
    app.schema = state.schema;
    app.values = state.values;
    app.installation = state.installation || {ok: false, directory: "", files: []};
    app.configPaths = state.config_paths || {};
    app.resetFiles = state.reset_files || [];
    app.joystickSnapshot = state.joysticks || {generation: 0, devices: []};
    if (!navigationSections().some((section) => section.id === app.activeSection)) {
      app.activeSection = "installation";
    }
    const configPath = app.activeSection === "installation"
      ? app.installation.directory
      : app.configPaths[app.activeSection] || "";
    $("#config-path").textContent = configPath;
    $("#config-path").title = configPath;
    setConnectionState("connected", "Configuration ready");
    setSaveState("", "Changes save automatically");
    renderNavigation();
    bindResetDialog();
    renderSection();
    updateLiveJoystick();
    pollJoystickSnapshot();
  } catch (error) {
    setConnectionState("error", "Connection failed");
    setSaveState("error", "Could not read configuration");
    showToast(error.message || String(error));
  }
}

window.addEventListener("pywebviewready", initialize);
