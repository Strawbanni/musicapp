// Shared infrastructure: API client, DOM helpers, toast/modal, app state, view router, pager.

// ---------- DOM utils ----------
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const formData = (form) => Object.fromEntries(new FormData(form));

export function fmtTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// ---------- state ----------
export const state = { user: null, view: "home" };

/** Update the logged-in user and notify listeners (header, router, ...). */
export function setUser(user) {
  state.user = user;
  document.dispatchEvent(new CustomEvent("user-changed", { detail: user }));
}

// ---------- API ----------
/** Call the JSON API. `body` may be a plain object (sent as JSON) or a FormData (multipart upload). */
export async function api(method, url, body) {
  const isForm = body instanceof FormData;
  const res = await fetch(url, {
    method,
    headers: body && !isForm ? { "Content-Type": "application/json" } : {},
    body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
    credentials: "same-origin",
  });
  const json = await res.json().catch(() => ({ code: res.status, msg: "Network error" }));
  if (json.code !== 0) {
    if (res.status === 401 && state.user) setUser(null);
    throw new Error(json.msg || "Request failed");
  }
  return json.data;
}

// ---------- toast ----------
let toastTimer;
export function toast(msg, type = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2200);
}

// ---------- modal ----------
export function openModal(id) { $(id).classList.remove("hidden"); }
export function closeModal(el) { el.closest(".modal").classList.add("hidden"); }
$$("[data-close]").forEach((b) => b.addEventListener("click", () => closeModal(b)));
$$(".modal").forEach((m) => m.addEventListener("mousedown", (e) => { if (e.target === m) m.classList.add("hidden"); }));

// ---------- view router ----------
// Each view: { auth?: bool, admin?: bool, onShow?: () => void }
const views = {};
let requestLogin = () => {};

export function registerView(name, opts = {}) { views[name] = opts; }
export function onLoginRequired(fn) { requestLogin = fn; }

export function navigate(name, { navEl } = {}) {
  if (!views[name]) name = "home";
  if (!canAccess(name)) {
    if (!state.user) { requestLogin(); return; }
    name = "home";
  }
  state.view = name;
  $$(".view").forEach((el) => el.classList.toggle("hidden", el.id !== `view-${name}`));
  // Keep the sidebar highlight in sync even when navigating from code (menu, search, ...).
  const nav = navEl || $(`.nav-item[data-view="${name}"]`);
  if (nav) {
    $$(".nav-item").forEach((x) => x.classList.toggle("active", x === nav));
  }
  views[name]?.onShow?.();
}

/** Whether the current user may see view `name`. */
export function canAccess(name) {
  const v = views[name] || {};
  if ((v.auth || v.admin) && !state.user) return false;
  return !(v.admin && !state.user.is_admin);
}

// ---------- pager ----------
export function renderPager(el, { total, page, size }) {
  const pages = Math.max(1, Math.ceil(total / size));
  el.innerHTML = `
    <span class="muted">Total ${total}</span>
    <button class="btn sm" ${page <= 1 ? "disabled" : ""} data-page="${page - 1}">‹ Prev</button>
    <span>${page} / ${pages}</span>
    <button class="btn sm" ${page >= pages ? "disabled" : ""} data-page="${page + 1}">Next ›</button>`;
}

export function bindPager(el, onPage) {
  el.addEventListener("click", (e) => {
    const p = e.target.dataset.page;
    if (p && !e.target.disabled) onPage(+p);
  });
}

// ---------- cover art ----------
const gradients = ["#31c27c,#1d7d8c", "#ff7a59,#c2185b", "#5b8def,#6c3fc4", "#f5b041,#e67e22", "#1abc9c,#2c3e50", "#e84393,#6c5ce7"];

/** CSS `background` for a track cover: the uploaded image, or a stable gradient placeholder. */
export function coverStyle(track) {
  if (track?.cover_url) return `background-image:url('${encodeURI(track.cover_url)}')`;
  const g = gradients[(track?.id || 0) % gradients.length];
  return `background:linear-gradient(135deg,${g})`;
}
