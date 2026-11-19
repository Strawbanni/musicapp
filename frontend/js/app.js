// Entry point: wires navigation, the top search bar and session bootstrap.
import { $, $$, api, canAccess, navigate, onLoginRequired, setUser, state } from "./core.js";
import { openAuth, renderUser } from "./auth.js";
import { openLibrary } from "./music.js";
import "./player.js";
import "./detail.js";
import "./users.js";

onLoginRequired(() => openAuth("login"));

$$(".nav-item").forEach((a) => a.addEventListener("click", () => navigate(a.dataset.view, { navEl: a })));

$("#globalSearch").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  openLibrary({ keyword: e.target.value.trim() });
});

document.addEventListener("user-changed", () => {
  renderUser();
  // Leave protected pages after logout / session expiry, and admin pages if no longer admin.
  if (!canAccess(state.view)) navigate("home");
});

// ---------- boot ----------
(async () => {
  let user = null;
  try { user = await api("GET", "/api/auth/me"); } catch { /* not logged in */ }
  setUser(user);
  navigate("home");
})();
