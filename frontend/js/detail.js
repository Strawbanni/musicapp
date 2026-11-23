// Track detail page: opened by clicking the cover in the player bar. Shows the current track's
// info, like button/count, lyrics and comments (comments.js); LRC lyrics follow playback and can be clicked to seek.
import { $, api, coverStyle, esc, fmtTime, navigate, registerView, state, toast } from "./core.js";
import { openAuth } from "./auth.js";
import { showComments } from "./comments.js";
import { currentTime, currentTrack, isPlaying, onProgress, seek, togglePlay } from "./player.js";

const detail = { track: null, lines: [], synced: false, active: -1, backTo: "home" };
const cache = new Map(); // id -> full track (with lyrics and like info for the current user)
let loadSeq = 0;
let liking = false;

// ---------- lyrics ----------
const TIME_TAG = /\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]/g;

/**
 * Parse lyrics into [{ time, text }]. LRC lines may carry several timestamps; metadata tags
 * such as [ar:...] are dropped. Without any timestamps, lines are returned as plain text.
 */
export function parseLyrics(text) {
  const raw = String(text || "").split(/\r?\n/);
  const timed = [];
  const plain = [];
  for (const line of raw) {
    const stamps = [...line.matchAll(TIME_TAG)];
    const body = line.replace(TIME_TAG, "").trim();
    if (stamps.length) {
      for (const m of stamps) timed.push({ time: +m[1] * 60 + parseFloat(m[2].replace(":", ".")), text: body });
    } else if (!/^\[[a-z]+:.*\]$/i.test(line.trim())) {
      plain.push({ time: null, text: line.trim() });
    }
  }
  if (timed.length) return { synced: true, lines: timed.sort((a, b) => a.time - b.time) };
  // Trim leading/trailing blank lines of plain lyrics.
  while (plain.length && !plain[0].text) plain.shift();
  while (plain.length && !plain[plain.length - 1].text) plain.pop();
  return { synced: false, lines: plain };
}

function renderLyrics() {
  const box = $("#detailLyrics");
  detail.active = -1;
  box.classList.toggle("synced", detail.synced);
  box.innerHTML = detail.lines.length
    ? detail.lines.map((l, i) =>
      `<p data-i="${i}"${l.time != null ? ` title="Jump to ${fmtTime(l.time)}"` : ""}>${esc(l.text) || "&nbsp;"}</p>`).join("")
    : `<div class="empty">No lyrics for this track</div>`;
  syncLyrics();
}

/** Highlight the lyric line for the current playback position and keep it in view. */
function syncLyrics() {
  if (state.view !== "detail" || !detail.synced || currentTrack()?.id !== detail.track?.id) return;
  const t = currentTime() + 0.2; // small lead so lines don't appear late
  let i = -1;
  while (i + 1 < detail.lines.length && detail.lines[i + 1].time <= t) i++;
  if (i === detail.active) return;
  const box = $("#detailLyrics");
  box.querySelector("p.active")?.classList.remove("active");
  detail.active = i;
  const el = i >= 0 && box.querySelector(`p[data-i="${i}"]`);
  if (el) {
    el.classList.add("active");
    box.scrollTo({ top: el.offsetTop - box.clientHeight / 2 + el.clientHeight / 2, behavior: "smooth" });
  }
}

// ---------- likes ----------
function renderLike() {
  const t = detail.track;
  const btn = $("#detailLike");
  const known = t && "like_count" in t; // queue entries lack it until the full track loads
  btn.disabled = !known || liking;
  btn.classList.toggle("liked", !!t?.liked);
  btn.setAttribute("aria-pressed", String(!!t?.liked));
  btn.title = !state.user ? "Log in to like" : t?.liked ? "Unlike" : "Like";
  $("#detailLikeCount").textContent = known ? t.like_count : "–";
}

async function toggleLike() {
  const t = detail.track;
  if (!t || liking) return;
  if (!state.user) { openAuth("login"); return; }
  liking = true;
  renderLike();
  try {
    const res = await api(t.liked ? "DELETE" : "PUT", `/api/music/${t.id}/like`);
    const cached = cache.get(t.id);
    if (cached) Object.assign(cached, res);
    if (detail.track?.id === t.id) Object.assign(detail.track, res);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    liking = false;
    renderLike();
  }
}

// ---------- page ----------
function renderPlayButton() {
  const playing = isPlaying() && currentTrack()?.id === detail.track?.id;
  $("#detailPlay").textContent = playing ? "⏸ Pause" : "▶ Play";
}

function renderDetail() {
  const t = detail.track;
  if (!t) return;
  $("#detailCover").setAttribute("style", coverStyle(t));
  $("#detailTitle").textContent = t.title;
  $("#detailArtist").textContent = t.artist;
  const meta = [
    ["Genre", t.genre],
    ["Release date", t.release_date],
    ["Length", t.duration ? fmtTime(t.duration) : null],
  ];
  $("#detailMeta").innerHTML = meta.map(([k, v]) =>
    `<div><dt>${k}</dt><dd>${v ? esc(v) : '<span class="muted">—</span>'}</dd></div>`).join("");
  renderPlayButton();
  renderLike();
}

/** Show `track` immediately (from the queue), then load its full record for lyrics. */
async function showTrack(track) {
  const seq = ++loadSeq;
  detail.track = cache.get(track.id) || track;
  renderDetail();
  showComments(track.id);
  if (!cache.has(track.id)) {
    $("#detailLyrics").innerHTML = `<div class="empty">Loading lyrics…</div>`;
    try {
      cache.set(track.id, await api("GET", `/api/music/${track.id}`));
    } catch {
      if (seq === loadSeq) $("#detailLyrics").innerHTML = `<div class="empty">Couldn't load lyrics</div>`;
      return;
    }
    if (seq !== loadSeq) return;
    detail.track = cache.get(track.id);
    renderDetail();
  }
  ({ lines: detail.lines, synced: detail.synced } = parseLyrics(detail.track.lyrics));
  renderLyrics();
}

registerView("detail", {
  onShow: () => {
    const t = currentTrack();
    if (t) showTrack(t);
    else navigate(detail.backTo);
  },
});

/** Open the detail page for the track that is currently loaded in the player. */
export function openDetail() {
  if (!currentTrack()) return;
  if (state.view !== "detail") detail.backTo = state.view;
  navigate("detail");
}

// ---------- wiring ----------
$("#npCover").addEventListener("click", openDetail);
$("#detailBack").onclick = () => navigate(detail.backTo);
$("#detailPlay").onclick = () => {
  const t = currentTrack();
  if (t && t.id === detail.track?.id) togglePlay();
};
$("#detailLike").onclick = toggleLike;
$("#detailLyrics").addEventListener("click", (e) => {
  const p = e.target.closest("p[data-i]");
  const line = p && detail.lines[+p.dataset.i];
  if (line?.time != null && currentTrack()?.id === detail.track?.id) seek(line.time);
});

document.addEventListener("track-changed", ({ detail: { track } }) => {
  if (state.view !== "detail") return;
  // Follow the player when it moves to another track (next/prev, end of song).
  if (track && track.id !== detail.track?.id) showTrack(track);
  else renderPlayButton();
});
onProgress(syncLyrics);
// Admin edits change the cached lyrics/info; drop the cache when music is saved.
document.addEventListener("music-changed", () => cache.clear());
// `liked` depends on who is logged in: reload after login / logout.
document.addEventListener("user-changed", () => {
  cache.clear();
  if (state.view === "detail" && detail.track) showTrack(detail.track);
});
