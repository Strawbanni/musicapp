// Bottom player bar: a single <audio> element driving a play queue.
import { $, coverStyle, fmtTime, toast } from "./core.js";

const audio = new Audio();
audio.preload = "metadata";
const player = { queue: [], index: -1 };

export const currentTrack = () => player.queue[player.index] || null;
export const isPlaying = () => !!currentTrack() && !audio.paused;
export const currentTime = () => audio.currentTime;

/** Jump to `sec` in the current track. */
export function seek(sec) {
  if (currentTrack() && Number.isFinite(sec)) audio.currentTime = sec;
}

/** Subscribe to playback position updates (fires on timeupdate / seek). */
export function onProgress(fn) {
  audio.addEventListener("timeupdate", fn);
  audio.addEventListener("seeked", fn);
}

/** Replace the queue with `tracks` and start playing at `index`. */
export function playQueue(tracks, index = 0) {
  if (!tracks.length) return;
  player.queue = tracks.slice();
  playAt(index);
}

/** Play `track` within `list`; toggles pause if it is already the current track. */
export function playTrack(track, list = [track]) {
  const cur = currentTrack();
  if (cur && cur.id === track.id) { togglePlay(); return; }
  const i = list.findIndex((t) => t.id === track.id);
  playQueue(i >= 0 ? list : [track], Math.max(i, 0));
}

function playAt(i) {
  if (i < 0 || i >= player.queue.length) return;
  player.index = i;
  const t = player.queue[i];
  audio.src = t.audio_url;
  audio.play().catch(() => {});
  renderNowPlaying();
}

export function togglePlay() {
  if (!currentTrack()) return;
  audio.paused ? audio.play().catch(() => {}) : audio.pause();
}

function next() {
  if (player.index >= player.queue.length - 1) return false;
  playAt(player.index + 1);
  return true;
}
function prev() {
  // Like most players: restart the song if we're more than 3s in, otherwise go back.
  if (audio.currentTime > 3 || player.index <= 0) audio.currentTime = 0;
  else playAt(player.index - 1);
}

// ---------- rendering ----------
function renderNowPlaying() {
  const t = currentTrack();
  const cover = $("#npCover");
  cover.setAttribute("style", t ? coverStyle(t) : "");
  cover.classList.toggle("clickable", !!t);
  cover.title = t ? "Open track details" : "";
  $("#npTitle").textContent = t ? t.title : "Not playing";
  $("#npArtist").textContent = t ? t.artist : "—";
  document.title = t ? `${t.title} · ${t.artist} — MusicApp` : "MusicApp";
  notify();
}

/** Let track lists highlight the current track and its play state. */
function notify() {
  document.dispatchEvent(new CustomEvent("track-changed", {
    detail: { track: currentTrack(), playing: !audio.paused },
  }));
}

/** Mark rows/cards (elements with data-track-id inside `root`) that match the current track. */
export function highlightPlaying(root) {
  const t = currentTrack();
  root.querySelectorAll("[data-track-id]").forEach((el) => {
    const on = !!t && +el.dataset.trackId === t.id;
    el.classList.toggle("playing", on);
    el.classList.toggle("paused", on && audio.paused);
  });
}

function renderProgress() {
  const d = audio.duration;
  $("#curTime").textContent = fmtTime(audio.currentTime);
  $("#durTime").textContent = fmtTime(d);
  $("#progressBar").style.width = Number.isFinite(d) && d > 0 ? `${(audio.currentTime / d) * 100}%` : "0";
}

// ---------- wiring ----------
audio.addEventListener("play", () => { $("#playBtn").textContent = "⏸"; notify(); });
audio.addEventListener("pause", () => { $("#playBtn").textContent = "▶"; notify(); });
audio.addEventListener("timeupdate", renderProgress);
audio.addEventListener("loadedmetadata", renderProgress);
audio.addEventListener("ended", () => { if (!next()) renderProgress(); });
audio.addEventListener("error", () => {
  if (currentTrack()) toast(`Cannot play "${currentTrack().title}"`, "error");
});

$("#playBtn").onclick = togglePlay;
$("#nextBtn").onclick = next;
$("#prevBtn").onclick = prev;

$("#progress").addEventListener("click", (e) => {
  if (!Number.isFinite(audio.duration)) return;
  const r = e.currentTarget.getBoundingClientRect();
  audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
});

const vol = $("#volume");
audio.volume = +(localStorage.getItem("volume") ?? vol.value);
vol.value = audio.volume;
vol.addEventListener("input", () => {
  audio.volume = +vol.value;
  localStorage.setItem("volume", vol.value);
});

// Space toggles playback when not typing in a field.
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.target.closest("input, textarea, select, button")) return;
  if (!currentTrack()) return;
  e.preventDefault();
  togglePlay();
});
