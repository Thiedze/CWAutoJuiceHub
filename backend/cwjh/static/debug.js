"use strict";

// Motor debug page: test and calibrate each bay. API: cwjh/debug_api.py.

const $ = (sel) => document.querySelector(sel);
// Every STEP pulse goes over I2C to the SX1509, which caps the real step rate.
const SX1509_LIMIT = 320;

const ui = { data: null, selected: 1, hold: false, testingAll: false, configDirty: false, positionSet: false };

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || res.statusText);
  }
  return data;
}

function toast(message) {
  const t = $("#toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 4000);
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

const bay = () => ui.data?.bays[ui.selected - 1];

// Runs a request that moves something; reports errors, then refreshes. The backend asks
// for a snapshot after every move (WS event "camera").
async function act(label, fn) {
  try {
    const result = await fn();
    await refresh();
    return result;
  } catch (e) {
    toast(`${label}: ${e.message}`);
    await refresh();
    return null;
  }
}

// Sliders: [name, label, min, max, step, unit, decimals, start value]

const MOVE_SLIDERS = [["target", "Position", -60, 60, 0.5, "°", 1, 0]];
const TEST_SLIDERS = [
  ["weight", "Test weight", 0, 1000, 10, "g", 0, 0],
  ["test_steps", "Steps out and back", 10, 3000, 10, "", 0, 200],
  ["test_dwell", "Wait at the far end", 0, 30, 0.5, "s", 1, 5],
  ["test_repeat", "Repeat", 1, 10, 1, "×", 0, 1],
];
const CONFIG_SLIDERS = [
  ["steps_per_degree", "Steps per degree", 0.5, 60, "any", "", 3],
  ["fill_degrees", "Fill tilt", 1, 30, 0.5, "°", 1],
  ["pour_open_steps", "Pour position", -3000, 3000, 10, "steps", 0],
  ["pour_flow_ml_s", "Pour flow", 0.5, 50, 0.5, "ml/s", 1],
  ["min_speed", "Start speed", 1, 500, 1, "steps/s", 0],
  ["full_load_g", "Full bottle weight", 100, 3000, 50, "g", 0],
  ["home_offset_steps", "Home offset", 0, 50, 1, "steps", 0],
];
const CURVE_SLIDERS = [
  ["speed", "Top speed", 10, 2000, 10, "steps/s", 0],
  ["start_ms", "Start curve", 0, 3000, 50, "ms", 0],
  ["end_ms", "End curve", 0, 3000, 50, "ms", 0],
];
const NUMBER_FIELDS = [
  ...CONFIG_SLIDERS.map((s) => s[0]),
  ...CURVE_SLIDERS.flatMap(([n]) => [`${n}_empty`, `${n}_full`]),
];

const fmt = (value, unit, decimals) => `${Number(value).toFixed(decimals)}${unit ? ` ${unit}` : ""}`;

function sliderInput(name, min, max, step, unit, decimals, value = min) {
  const input = el("input", { type: "range", name, min, max, step, value });
  const output = el("output", { textContent: fmt(value, unit, decimals) });
  input.addEventListener("input", () => (output.textContent = fmt(input.value, unit, decimals)));
  input.setValue = (v) => {
    input.value = v;
    output.textContent = fmt(v, unit, decimals);
  };
  return [input, output];
}

function buildSliders(container, specs) {
  container.replaceChildren(...specs.map(([name, label, min, max, step, unit, decimals, value = min]) =>
    el("label", { className: "slider" }, el("span", { textContent: label }), ...sliderInput(name, min, max, step, unit, decimals, value))
  ));
}

function buildPairs(container, specs) {
  container.replaceChildren(
    el("div", { className: "slider head" }, el("span"), el("span", { textContent: "empty bottle" }), el("span"),
      el("span", { textContent: "full bottle" }), el("span")),
    ...specs.map(([name, label, min, max, step, unit, decimals]) =>
      el("div", { className: "slider" }, el("span", { textContent: `${label} (${unit})` }),
        ...sliderInput(`${name}_empty`, min, max, step, "", decimals),
        ...sliderInput(`${name}_full`, min, max, step, "", decimals))
    )
  );
}

buildSliders($("#move-sliders"), MOVE_SLIDERS);
buildSliders($("#test-sliders"), TEST_SLIDERS);
buildSliders($("#config-sliders"), CONFIG_SLIDERS);
buildPairs($("#curve-sliders"), CURVE_SLIDERS);

const slider = (name) => document.querySelector(`input[name="${name}"]`);

// Live data

async function refresh() {
  try {
    ui.data = await api("/debug/bays");
  } catch (e) {
    ui.data = null;
  }
  render();
}

function bayLabel(b) {
  if (!b.wired) return `Bay ${b.bay}: no motor`;
  const bottle = b.ingredient ? `${b.ingredient}, ${Math.round(b.load_g)} g` : "empty";
  return `Bay ${b.bay}: ${bottle}`;
}

function render() {
  const data = ui.data;
  const badge = $("#hub-badge");
  const online = Boolean(data?.online);
  badge.textContent = online ? `hub online${data.fw ? ` (fw ${data.fw})` : ""}${data.busy ? ", moving" : ""}` : "hub offline";
  badge.classList.toggle("offline", !online);
  if (!data) return;

  const select = $("#bay-select");
  if (select.options.length !== data.bays.length) {
    select.replaceChildren(...data.bays.map((b) => el("option", { value: b.bay })));
  }
  data.bays.forEach((b, i) => (select.options[i].textContent = bayLabel(b)));
  select.value = String(ui.selected);

  const b = bay();
  const p = b.profile;
  $("#live").textContent = [
    b.wired ? `at ${b.position} steps = ${b.degrees}°` : "no motor wired",
    b.switch === null ? "no home switch" : `switch ${b.switch ? "pressed" : "released"}, ${b.homed ? "homed" : "not homed"}`,
    b.ingredient ? `${b.ingredient} ${b.volume_ml} ml = ${b.load_g} g` : "no bottle",
    `profile now: top ${p.max_speed}, start ${p.min_speed} steps/s, curves ${p.start_ms}/${p.end_ms} ms`,
  ].join("  ·  ");
  $("#bottle-g").textContent = Math.round(b.load_g);
  if (!ui.positionSet && b.degrees !== null) {
    slider("target").setValue(b.degrees);
    ui.positionSet = true;
  }
  if (!ui.configDirty) fillConfig(b.config);
  drawChart();
}

$("#bay-select").addEventListener("change", (event) => select(Number(event.target.value)));

function select(n) {
  ui.selected = n;
  ui.configDirty = false;
  ui.positionSet = false;
  ui.hold = false;
  $("#hold").textContent = "Hold: off";
  $("#config-status").textContent = "";
  render();
}

// Moving

function testLoad() {
  return $("#use-bottle").checked ? null : Number(slider("weight").value);
}

async function move(body) {
  const r = await act("Move", () => api(`/debug/bays/${ui.selected}/move`, { method: "POST", body: { ...body, load_g: testLoad() } }));
  ui.positionSet = false;  // show where the bay really is
  render();
  return r;
}

slider("target").addEventListener("change", (event) => move({ degrees: Number(event.target.value) }));
document.querySelectorAll("[data-jog]").forEach((button) =>
  button.addEventListener("click", () => move({ steps: Number(button.dataset.jog) }))
);
$("#rest").addEventListener("click", () => move({ position: 0 }));
$("#fill").addEventListener("click", () => move({ degrees: Number(slider("fill_degrees").value) }));

$("#home").addEventListener("click", async () => {
  const r = await act("Home", () => api(`/debug/bays/${ui.selected}/home`, { method: "POST" }));
  ui.positionSet = false;
  if (r) toast(r.homed ? "Homed on the switch" : "Home switch not found");
});
$("#zero").addEventListener("click", async () => {
  await act("Zero", () => api(`/debug/bays/${ui.selected}/zero`, { method: "POST" }));
  ui.positionSet = false;
});
$("#hold").addEventListener("click", async () => {
  const on = !ui.hold;
  const r = await act("Hold", () => api(`/debug/bays/${ui.selected}/hold`, { method: "POST", body: { on } }));
  if (r) {
    ui.hold = on;
    $("#hold").textContent = `Hold: ${on ? "on" : "off"}`;
  }
});

function testBody(withLoad = true) {
  return {
    to: $("#test-to").value,
    steps: Number(slider("test_steps").value),
    dwell_s: Number(slider("test_dwell").value),
    repeat: Number(slider("test_repeat").value),
    load_g: withLoad ? testLoad() : null,
  };
}

function syncTestTo() {
  slider("test_steps").closest(".slider").hidden = $("#test-to").value === "pour";
  drawChart();
}
$("#test-to").addEventListener("change", syncTestTo);

$("#test").addEventListener("click", async () => {
  $("#all-status").textContent = `Testing bay ${ui.selected} ...`;
  await act("Test", () => api(`/debug/bays/${ui.selected}/test`, { method: "POST", body: testBody() }));
  $("#all-status").textContent = "";
  ui.positionSet = false;
});

$("#test-all").addEventListener("click", async () => {
  const wired = (ui.data?.bays || []).filter((b) => b.wired);
  if (!wired.length) return toast("No wired bays");
  ui.testingAll = true;
  const body = testBody(false);  // each bay with its own bottle weight
  for (const b of wired) {
    if (!ui.testingAll) break;
    $("#all-status").textContent = `Testing bay ${b.bay} ...`;
    const r = await act(`Bay ${b.bay}`, () => api(`/debug/bays/${b.bay}/test`, { method: "POST", body }));
    if (!r) ui.testingAll = false;
  }
  $("#all-status").textContent = ui.testingAll ? `Tested ${wired.length} wired bay(s).` : "Stopped.";
  ui.testingAll = false;
});

$("#stop").addEventListener("click", async () => {
  ui.testingAll = false;
  try {
    await api("/debug/stop", { method: "POST" });
    toast("Stopped: every driver is off");
  } catch (e) {
    toast(`Stop: ${e.message}`);
  }
  ui.positionSet = false;
  refresh();
});

function syncWeight() {
  slider("weight").disabled = $("#use-bottle").checked;
  drawChart();
}
$("#use-bottle").addEventListener("change", syncWeight);
["weight", "test_steps"].forEach((name) => slider(name).addEventListener("input", drawChart));

// Calibration form

function fillConfig(config) {
  const form = $("#config");
  form.invert.checked = config.invert;
  NUMBER_FIELDS.forEach((name) => form[name].setValue(config[name]));
  slider("weight").max = config.full_load_g;
}

function readConfig() {
  const form = $("#config");
  const config = { invert: form.invert.checked };
  NUMBER_FIELDS.forEach((name) => (config[name] = Number(form[name].value)));
  return config;
}

$("#config").addEventListener("input", () => {
  ui.configDirty = true;
  $("#config-status").textContent = "not saved";
  drawChart();
});
$("#config-reset").addEventListener("click", () => {
  ui.configDirty = false;
  $("#config-status").textContent = "";
  render();
});
$("#config").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api(`/debug/bays/${ui.selected}/config`, { method: "PUT", body: readConfig() });
    ui.configDirty = false;
    ui.positionSet = false;
    $("#config-status").textContent = "saved";
  } catch (e) {
    $("#config-status").textContent = `not saved: ${e.message}`;
  }
  refresh();
});

// Speed curve, computed step by step like the firmware (firmware/src/motors.cpp)

function profileFor(config, loadG) {
  const f = Math.min(Math.max(loadG / config.full_load_g, 0), 1);
  const blend = (empty, full) => Math.round(empty + (full - empty) * f);
  return {
    max_speed: Math.max(blend(config.speed_empty, config.speed_full), config.min_speed),
    min_speed: config.min_speed,
    start_ms: blend(config.start_ms_empty, config.start_ms_full),
    end_ms: blend(config.end_ms_empty, config.end_ms_full),
  };
}

// Returns [[t, steps/s], ...] for one move; the real rate is capped by the SX1509.
function simulate(profile, steps) {
  const vMin = profile.min_speed;
  const tStart = profile.start_ms / 1000;
  const tEnd = profile.end_ms / 1000;
  let vPeak = profile.max_speed;
  if (tStart + tEnd > 0 && ((vPeak + vMin) / 2) * (tStart + tEnd) > steps) {
    vPeak = Math.max(vMin, (2 * steps) / (tStart + tEnd) - vMin);
  }
  const endSteps = ((vPeak + vMin) / 2) * tEnd;
  let t = 0;
  let v = tStart > 0 ? vMin : vPeak;
  let ending = false;
  let endStarted = 0;
  let vAtEnd = vPeak;
  const points = [[0, Math.min(v, SX1509_LIMIT)]];
  for (let done = 1; done <= steps; done++) {
    if (tEnd > 0 && steps - done <= endSteps) {
      if (!ending) {
        ending = true;
        endStarted = t;
        vAtEnd = v;
      }
      const x = Math.min(1, (t - endStarted) / tEnd);
      v = vMin + ((vAtEnd - vMin) * (1 + Math.cos(Math.PI * x))) / 2;
    } else if (tStart > 0) {
      const x = Math.min(1, t / tStart);
      v = vMin + ((vPeak - vMin) * (1 - Math.cos(Math.PI * x))) / 2;
    } else {
      v = vPeak;
    }
    const real = Math.min(Math.max(v, 1), SX1509_LIMIT);
    t += 1 / real;
    points.push([t, real]);
  }
  return points;
}

function drawChart() {
  const b = bay();
  if (!b) return;
  const config = readConfig();
  if (NUMBER_FIELDS.some((n) => !Number.isFinite(config[n])) || config.full_load_g <= 0 || config.min_speed < 1) return;
  const toPour = $("#test-to").value === "pour";
  const steps = Math.max(1, Math.round(Math.abs(toPour ? config.pour_open_steps : Number(slider("test_steps").value)) || 1));
  const load = $("#use-bottle").checked ? b.load_g : Number(slider("weight").value);
  const curves = [
    { cls: "empty", label: "empty", points: simulate(profileFor(config, 0), steps) },
    { cls: "full", label: `full (${config.full_load_g} g)`, points: simulate(profileFor(config, config.full_load_g), steps) },
    { cls: "current", label: `${Math.round(load)} g`, points: simulate(profileFor(config, load), steps) },
  ];

  const W = 640, H = 260, L = 44, R = 12, T = 12, B = 30;
  const tMax = Math.max(...curves.map((c) => c.points.at(-1)[0]), 0.1);
  const vMax = Math.max(SX1509_LIMIT * 1.1, ...curves.flatMap((c) => c.points.map((p) => p[1])));
  const x = (t) => L + (t / tMax) * (W - L - R);
  const y = (v) => H - B - (v / vMax) * (H - T - B);
  const svg = (tag, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([k, val]) => node.setAttribute(k, val));
    return node;
  };
  const path = (points) => {
    const every = Math.max(1, Math.floor(points.length / 400));
    return points.filter((_, i) => i % every === 0 || i === points.length - 1)
      .map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join("");
  };

  const chart = $("#chart");
  chart.replaceChildren(
    svg("line", { class: "axis", x1: L, y1: H - B, x2: W - R, y2: H - B }),
    svg("line", { class: "axis", x1: L, y1: T, x2: L, y2: H - B }),
    svg("line", { class: "limit", x1: L, y1: y(SX1509_LIMIT), x2: W - R, y2: y(SX1509_LIMIT) }),
    Object.assign(svg("text", { x: W - R, y: y(SX1509_LIMIT) - 4, "text-anchor": "end" }), { textContent: "SX1509 limit" }),
    Object.assign(svg("text", { x: L - 6, y: T + 8, "text-anchor": "end" }), { textContent: Math.round(vMax) }),
    Object.assign(svg("text", { x: L - 6, y: H - B, "text-anchor": "end" }), { textContent: "0" }),
    Object.assign(svg("text", { x: W - R, y: H - 8, "text-anchor": "end" }), { textContent: `${tMax.toFixed(2)} s` }),
    Object.assign(svg("text", { x: L, y: H - 8 }), { textContent: "steps/s over time" }),
    ...curves.map((c) => svg("path", { class: c.cls, d: path(c.points) }))
  );
  $("#chart-legend").textContent = curves
    .map((c) => `${c.cls === "current" ? "━" : c.cls === "full" ? "╍" : "┄"} ${c.label}: ${c.points.at(-1)[0].toFixed(2)} s`)
    .join("   ");
}

// Camera: a live picture of the machine, and snapshots for debugging

const camera = { stream: null };

async function startCamera(deviceId) {
  stopCamera();
  try {
    camera.stream = await navigator.mediaDevices.getUserMedia({
      video: deviceId ? { deviceId: { exact: deviceId }, width: { ideal: 1920 } } : { width: { ideal: 1920 } },
      audio: false,
    });
  } catch (e) {
    $("#camera-status").textContent = `Camera not available: ${e.message}`;
    return;
  }
  const video = $("#camera");
  video.srcObject = camera.stream;
  video.hidden = false;
  await video.play();
  $("#camera-toggle").textContent = "Stop camera";
  $("#camera-snap").disabled = false;

  await listCameras();
  $("#camera-status").textContent = "";
}

// Device names are only readable once access was granted. An iPhone (Continuity Camera)
// may only show up later, so the list follows devicechange.
async function listCameras() {
  const current = camera.stream?.getVideoTracks()[0]?.getSettings().deviceId;
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  if (!devices.some((d) => d.label)) return;
  $("#camera-select").replaceChildren(...devices.map((d) =>
    el("option", { value: d.deviceId, textContent: d.label || "camera", selected: d.deviceId === current })
  ));
}
navigator.mediaDevices?.addEventListener("devicechange", listCameras);

function stopCamera() {
  camera.stream?.getTracks().forEach((t) => t.stop());
  camera.stream = null;
  $("#camera").hidden = true;
  $("#camera-toggle").textContent = "Start camera";
  $("#camera-snap").disabled = true;
}

async function snapshot(label) {
  const video = $("#camera");
  if (!camera.stream || !video.videoWidth) return;
  const canvas = el("canvas", { width: video.videoWidth, height: video.videoHeight });
  canvas.getContext("2d").drawImage(video, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  const tag = String(label).replace(/[^A-Za-z0-9_-]/g, "");
  try {
    const res = await fetch(`/api/debug/camera?label=${encodeURIComponent(tag)}`, {
      method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob,
    });
    const data = await res.json();
    $("#camera-status").textContent = res.ok ? `Snapshot sent: ${data.saved}` : `Snapshot failed: ${data.detail}`;
  } catch (e) {
    $("#camera-status").textContent = `Snapshot failed: ${e.message}`;
  }
}

// The backend asks for snapshots after moves (reason "move") and on request (reason
// "request", e.g. while debugging remotely). After a move, give the picture a moment.
function onCameraEvent({ label, reason }) {
  if (!camera.stream) return;
  if (reason === "request") snapshot(label);
  else if ($("#camera-auto").checked) setTimeout(() => snapshot(label), 400);
}

$("#camera-toggle").addEventListener("click", () => (camera.stream ? stopCamera() : startCamera($("#camera-select").value)));
$("#camera-select").addEventListener("change", (event) => camera.stream && startCamera(event.target.value));
$("#camera-snap").addEventListener("click", () => snapshot(`bay${ui.selected}-manual`));

// Raw requests and the serial log

$("#raw").addEventListener("submit", async (event) => {
  event.preventDefault();
  const line = $("#raw-line").value.trim();
  if (!line) return;
  const out = $("#raw-out");
  out.prepend(el("li", { className: "out", textContent: `> ${line}` }));
  try {
    const r = await api("/debug/raw", { method: "POST", body: { line } });
    out.prepend(el("li", { className: r.reply.startsWith("ERR") ? "err" : "", textContent: r.reply }));
  } catch (e) {
    out.prepend(el("li", { className: "err", textContent: e.message }));
  }
  refresh();
});

function isPing(line) {
  const op = line.split(/\s+/)[1];
  return ["Q", "STATE", "W", "SW"].includes(op);
}

function addSerial(entry) {
  if (isPing(entry.line)) return;
  const list = $("#serial");
  const time = new Date(entry.t * 1000).toTimeString().slice(0, 8);
  list.append(el("li", {
    className: entry.line.split(/\s+/)[1] === "ERR" ? "err" : entry.dir === "out" ? "out" : "",
    textContent: `${time} ${entry.dir === "out" ? "Pi → Nano" : "Nano → Pi"}  ${entry.line}`,
  }));
  while (list.children.length > 200) list.firstElementChild.remove();
  list.scrollTop = list.scrollHeight;
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`);
  ws.onmessage = (event) => {
    const { type, data } = JSON.parse(event.data);
    if (type === "serial") addSerial(data);
    if (type === "camera") onCameraEvent(data);
  };
  ws.onopen = async () => {
    $("#serial").replaceChildren();
    try { (await api("/serial")).forEach(addSerial); } catch {}
  };
  ws.onclose = () => setTimeout(connect, 2000);
}

connect();
syncWeight();
syncTestTo();
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 1000);
