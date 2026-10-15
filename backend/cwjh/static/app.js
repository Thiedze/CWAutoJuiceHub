"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { alcoholic: null, recipe: null, hubOnline: false, cocktail: null, bays: [], ingredients: [] };

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 4000);
}

function show(view) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

// Home and recipe list

document.querySelectorAll(".tile").forEach((tile) =>
  tile.addEventListener("click", () => openList(tile.dataset.alcoholic === "true"))
);

async function openList(alcoholic) {
  state.alcoholic = alcoholic;
  $("#list-title").textContent = alcoholic ? "With alcohol" : "Without alcohol";
  const recipes = await api(`/recipes?alcoholic=${alcoholic}`);
  const list = $("#recipe-list");
  list.replaceChildren(
    ...recipes.map((r) => {
      const button = el("button", { disabled: !r.available },
        el("strong", { textContent: r.name }),
        el("span", { className: "why", textContent: r.available ? r.ingredients.map((i) => i.name).join(", ") : r.missing })
      );
      button.addEventListener("click", () => openCustomize(r));
      return el("li", {}, button);
    })
  );
  show("list");
}

// Customise

async function openCustomize(recipe) {
  state.recipe = recipe;
  $("#custom-title").textContent = recipe.name;
  $("#strength-row").hidden = !recipe.alcoholic;
  $("#strength").value = 1;
  await renderPlan();
  show("customize");
}

$("#strength").addEventListener("input", renderPlan);

async function renderPlan() {
  const strength = Number($("#strength").value);
  $("#strength-out").textContent = `${Math.round(strength * 100)}%`;
  try {
    const plan = await api(`/recipes/${state.recipe.id}/plan?strength=${strength}`);
    $("#plan-steps").replaceChildren(...plan.steps.map(stepItem));
    $("#plan-total").textContent = `${Math.round(plan.total_ml)} ml in total`;
    $("#start-btn").disabled = false;
  } catch (e) {
    $("#plan-steps").replaceChildren();
    $("#plan-total").textContent = e.message;
    $("#start-btn").disabled = true;
  }
}

function stepItem(step) {
  const amount = step.manual ? `${step.quantity}x` : `${Math.round(step.quantity)} ml`;
  const tag = step.manual ? "manual" : `automatic, bay ${step.bay}`;
  return el("li", { className: step.status }, `${amount} ${step.ingredient}`, el("span", { className: "tag", textContent: tag }));
}

$("#start-btn").addEventListener("click", async () => {
  try {
    await api("/cocktail/start", { method: "POST", body: { recipe_id: state.recipe.id, strength: Number($("#strength").value) } });
    show("run");
  } catch (e) {
    toast(e.message);
  }
});

// Running cocktail

const MESSAGES = {
  await_glass: "Put a glass on the scale, then tap Next.",
  init_glass: "Preparing the glass...",
  pouring: "Pouring...",
  releasing: "Almost done...",
  done: "Enjoy your drink! Take the glass and tap Next.",
};

function renderCocktail(status) {
  state.cocktail = status;
  if (status.phase === "idle" || !status.plan) {
    if (!$("#view-run").hidden) show("home");
    return;
  }
  if ($("#view-run").hidden && $("#view-settings").hidden) show("run");

  const step = status.step != null ? status.plan.steps[status.step] : null;
  $("#run-title").textContent = status.plan.recipe;
  let message = MESSAGES[status.phase] || "";
  if (status.phase === "pouring" && step) message = `Pouring ${step.ingredient}...`;
  if (status.phase === "await_manual" && step) message = `Add ${step.quantity}x ${step.ingredient}, then tap Next.`;
  if (status.phase === "error") message = `Something went wrong: ${status.error}`;
  $("#run-message").textContent = message;

  const progress = $("#run-progress");
  progress.hidden = status.phase !== "pouring";
  const { grams = 0, target = 0 } = status.progress || {};
  const pct = target ? Math.min(100, (grams / target) * 100) : 0;
  progress.querySelector(".bar").style.width = `${pct}%`;
  progress.querySelector(".label").textContent = target ? `${Math.round(grams)} / ${Math.round(target)} g` : "";

  $("#run-steps").replaceChildren(...status.plan.steps.map(stepItem));
  const waiting = ["await_glass", "await_manual", "done", "error"].includes(status.phase);
  $("#next-btn").disabled = !waiting;
  $("#next-btn").textContent = status.phase === "done" || status.phase === "error" ? "Finish" : "Next";
  $("#abort-btn").hidden = status.phase === "done";
}

$("#next-btn").addEventListener("click", () => api("/cocktail/next", { method: "POST" }).catch((e) => toast(e.message)));
$("#abort-btn").addEventListener("click", () => api("/cocktail/abort", { method: "POST" }).catch((e) => toast(e.message)));

// Settings: bays

$("#settings-btn").addEventListener("click", openSettings);
$("#home-btn").addEventListener("click", () => {
  const busy = state.cocktail && state.cocktail.phase !== "idle";
  show(busy ? "run" : "home");
});

async function openSettings() {
  [state.bays, state.ingredients] = await Promise.all([api("/bays"), api("/ingredients")]);
  renderBays();
  show("settings");
}

function renderBays() {
  $("#bay-grid").replaceChildren(
    ...state.bays.map((bay) => {
      const button = el("button", { className: `bay ${bay.filled ? "filled" : "empty"}` },
        el("span", { className: "num", textContent: `Bay ${bay.id}` }),
        el("strong", { className: "name", textContent: bay.filled ? bay.ingredient.name : "empty" }),
        el("span", { className: "muted", textContent: bay.filled ? `~${Math.round(bay.volume_ml)} ml` : "tap to fill" })
      );
      button.addEventListener("click", () => (bay.filled ? emptyBay(bay) : fillBay(bay)));
      return button;
    })
  );
}

async function emptyBay(bay) {
  if (!confirm(`Empty bay ${bay.id} (${bay.ingredient.name})?`)) return;
  await api(`/bays/${bay.id}/empty`, { method: "POST" }).catch((e) => toast(e.message));
  openSettings();
}

async function fillBay(bay) {
  const dialog = $("#bay-dialog");
  $("#bay-dialog-title").textContent = `Fill bay ${bay.id}`;
  $("#bay-dialog-text").textContent = "Tilting the bay...";
  $("#bay-confirm").disabled = true;
  $("#bay-ingredient").replaceChildren(
    ...state.ingredients.filter((i) => i.type !== "SOLID").map((i) => el("option", { value: i.id, textContent: i.name }))
  );
  dialog.showModal();
  try {
    await api(`/bays/${bay.id}/fill/start`, { method: "POST" });
    $("#bay-dialog-text").textContent = "Insert the bottle, choose the liquid, and confirm.";
    $("#bay-confirm").disabled = false;
  } catch (e) {
    $("#bay-dialog-text").textContent = e.message;
  }
  dialog.onclose = async () => {
    try {
      if (dialog.returnValue === "confirm") {
        await api(`/bays/${bay.id}/fill/confirm`, {
          method: "POST",
          body: { ingredient_id: Number($("#bay-ingredient").value), volume_ml: Number($("#bay-volume").value) },
        });
      } else {
        await api(`/bays/${bay.id}/fill/cancel`, { method: "POST" });
      }
    } catch (e) {
      toast(e.message);
    }
    openSettings();
  };
}

// Live updates

function setOnline(online) {
  state.hubOnline = online;
  const badge = $("#hub-badge");
  badge.textContent = online ? "hub online" : "hub offline";
  badge.classList.toggle("offline", !online);
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`);
  ws.onmessage = (event) => {
    const { type, data } = JSON.parse(event.data);
    if (type === "online") setOnline(data);
    if (type === "state") $("#scale-g").textContent = Math.round(data.scale_g);
    if (type === "cocktail") renderCocktail(data);
  };
  ws.onclose = () => {
    setOnline(false);
    setTimeout(connect, 2000);
  };
}

connect();
