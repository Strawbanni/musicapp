// Music: home "new releases", public library, and admin music management.
import {
  $, $$, api, bindPager, closeModal, coverStyle, esc, fmtTime, navigate, openModal, registerView, renderPager, toast,
} from "./core.js";
import { highlightPlaying, playQueue, playTrack } from "./player.js";

// ---------- shared ----------
let genres = [];

async function loadGenres() {
  try { genres = await api("GET", "/api/music/genres"); } catch { genres = []; }
  // Refresh every genre <select> (keep the current choice) and the editor's suggestions.
  $$("select[data-genre-select]").forEach((sel) => {
    const cur = sel.value;
    sel.innerHTML = `<option value="">All genres</option>` +
      genres.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("");
    sel.value = genres.includes(cur) ? cur : "";
  });
  $("#genreList").innerHTML = genres.map((g) => `<option value="${esc(g)}"></option>`).join("");
  return genres;
}

const fetchMusic = (params) => api("GET", `/api/music?${new URLSearchParams(params)}`);

/** Delegate play clicks inside `root` to the track list returned by getList(). */
function bindPlay(root, getList) {
  root.addEventListener("click", (e) => {
    const el = e.target.closest("[data-play]");
    if (!el) return;
    const list = getList();
    const track = list.find((t) => t.id === +el.closest("[data-track-id]").dataset.trackId);
    if (track) playTrack(track, list);
  });
}

const playIcon = `<span class="play-icon"></span>`;

// Re-highlight the visible lists whenever playback changes.
document.addEventListener("track-changed", () => {
  ["#homeGrid", "#libraryTbody", "#musicTbody"].forEach((s) => highlightPlaying($(s)));
});

// ---------- home ----------
const home = { items: [] };

async function loadHome() {
  try {
    const [data] = await Promise.all([fetchMusic({ size: 12, sort: "newest" }), loadGenres()]);
    home.items = data.items;
  } catch (err) { toast(err.message, "error"); return; }

  $("#homeGrid").innerHTML = home.items.length
    ? home.items.map((t) => `
      <div class="card" data-track-id="${t.id}">
        <div class="cover-lg" style="${esc(coverStyle(t))}">
          <button class="card-play" data-play title="Play">${playIcon}</button>
        </div>
        <div class="name ellipsis" title="${esc(t.title)}">${esc(t.title)}</div>
        <div class="muted small ellipsis">${esc(t.artist)}</div>
      </div>`).join("")
    : `<div class="empty">No music yet. An admin can add tracks under Music Management.</div>`;
  highlightPlaying($("#homeGrid"));

  $("#homeGenres").innerHTML = genres.map((g) => `<button class="chip" data-genre="${esc(g)}">${esc(g)}</button>`).join("");
  $("#homeGenreSection").classList.toggle("hidden", !genres.length);
}
registerView("home", { onShow: loadHome });

bindPlay($("#homeGrid"), () => home.items);
$("#homePlayAll").onclick = () => playQueue(home.items);
$("#homeGenres").addEventListener("click", (e) => {
  const g = e.target.dataset.genre;
  if (g === undefined) return;
  openLibrary({ genre: g });
});

// ---------- library (public) ----------
const lib = { page: 1, size: 20, items: [] };

async function loadLibrary() {
  const params = {
    page: lib.page, size: lib.size, sort: $("#libSort").value,
    keyword: $("#libKeyword").value.trim(), genre: $("#libGenre").value,
  };
  try {
    const data = await fetchMusic(params);
    lib.items = data.items;
    const offset = (data.page - 1) * data.size;
    $("#libraryTbody").innerHTML = data.items.length
      ? data.items.map((t, i) => `
        <tr data-track-id="${t.id}">
          <td class="idx"><span class="num">${offset + i + 1}</span><button class="row-play" data-play title="Play">${playIcon}</button></td>
          <td><div class="track-cell"><div class="thumb" style="${esc(coverStyle(t))}"></div>
            <span class="ellipsis" title="${esc(t.title)}">${esc(t.title)}</span></div></td>
          <td>${esc(t.artist)}</td>
          <td>${t.genre ? `<span class="tag genre">${esc(t.genre)}</span>` : '<span class="muted">—</span>'}</td>
          <td>${t.release_date || '<span class="muted">—</span>'}</td>
          <td class="muted">${t.duration ? fmtTime(t.duration) : "—"}</td>
        </tr>`).join("")
      : `<tr><td colspan="6" class="empty">No tracks found</td></tr>`;
    highlightPlaying($("#libraryTbody"));
    renderPager($("#libraryPager"), data);
  } catch (err) { toast(err.message, "error"); }
}
registerView("library", { onShow: () => { loadGenres(); loadLibrary(); } });

/** Navigate to the library with a preset search/filter (used by topbar search and genre chips). */
export function openLibrary({ keyword = "", genre = "" } = {}) {
  $("#libKeyword").value = keyword;
  // The genre option may not exist yet if genres haven't loaded; add it so the value sticks.
  const sel = $("#libGenre");
  if (genre && ![...sel.options].some((o) => o.value === genre)) sel.add(new Option(genre, genre));
  sel.value = genre;
  lib.page = 1;
  navigate("library");
}

bindPlay($("#libraryTbody"), () => lib.items);
$("#libraryTbody").addEventListener("dblclick", (e) => {
  const row = e.target.closest("[data-track-id]");
  if (row) playQueue(lib.items, lib.items.findIndex((t) => t.id === +row.dataset.trackId));
});
bindPager($("#libraryPager"), (p) => { lib.page = p; loadLibrary(); });
const libSearch = () => { lib.page = 1; loadLibrary(); };
$("#libSearchBtn").onclick = libSearch;
$("#libKeyword").addEventListener("keydown", (e) => { if (e.key === "Enter") libSearch(); });
$("#libGenre").onchange = libSearch;
$("#libSort").onchange = libSearch;
$("#libPlayAll").onclick = () => playQueue(lib.items);

// ---------- admin: music management ----------
const adm = { page: 1, size: 10, items: [] };

async function loadAdminMusic() {
  const params = {
    page: adm.page, size: adm.size, keyword: $("#musicKeyword").value.trim(), genre: $("#musicGenre").value,
  };
  try {
    const data = await fetchMusic(params);
    adm.items = data.items;
    $("#musicTbody").innerHTML = data.items.length
      ? data.items.map((t) => `
        <tr data-track-id="${t.id}">
          <td>${t.id}</td>
          <td><div class="track-cell"><button class="thumb playable" data-play title="Preview" style="${esc(coverStyle(t))}">${playIcon}</button>
            <span class="ellipsis" title="${esc(t.title)}">${esc(t.title)}</span>
            ${t.has_lyrics ? '<span class="tag lyrics" title="Has lyrics">Lyrics</span>' : ""}</div></td>
          <td>${esc(t.artist)}</td>
          <td>${t.genre ? `<span class="tag genre">${esc(t.genre)}</span>` : '<span class="muted">—</span>'}</td>
          <td>${t.release_date || '<span class="muted">—</span>'}</td>
          <td class="muted">${t.duration ? fmtTime(t.duration) : "—"}</td>
          <td class="muted small">${t.created_at}</td>
          <td class="actions">
            <button class="btn sm" data-act="edit">Edit</button>
            <button class="btn sm danger" data-act="delete">Delete</button>
          </td>
        </tr>`).join("")
      : `<tr><td colspan="8" class="empty">No tracks found</td></tr>`;
    highlightPlaying($("#musicTbody"));
    renderPager($("#musicPager"), data);
  } catch (err) { toast(err.message, "error"); }
}
registerView("music", { admin: true, onShow: () => { loadGenres(); loadAdminMusic(); } });

bindPlay($("#musicTbody"), () => adm.items);
bindPager($("#musicPager"), (p) => { adm.page = p; loadAdminMusic(); });
const admSearch = () => { adm.page = 1; loadAdminMusic(); };
$("#musicSearchBtn").onclick = admSearch;
$("#musicKeyword").addEventListener("keydown", (e) => { if (e.key === "Enter") admSearch(); });
$("#musicGenre").onchange = admSearch;
$("#musicAddBtn").onclick = () => openMusicEditor(null);

$("#musicTbody").addEventListener("click", async (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  const id = +e.target.closest("tr").dataset.trackId;
  const t = adm.items.find((x) => x.id === id);
  if (act === "edit") return openMusicEditor(t);
  if (act === "delete") {
    if (!confirm(`Delete "${t.title}" by ${t.artist}? The audio and cover files will be removed.`)) return;
    try {
      await api("DELETE", `/api/music/${id}`);
      toast("Track deleted", "success");
      document.dispatchEvent(new Event("music-changed"));
      // Step back a page if we just removed the last row on it.
      if (adm.items.length === 1 && adm.page > 1) adm.page -= 1;
      loadAdminMusic();
      loadGenres();
    } catch (err) { toast(err.message, "error"); }
  }
});

// ---------- admin: create / edit modal ----------
const mf = $("#musicForm");
const F = mf.elements;
const TEXT_FIELDS = ["title", "artist", "genre", "release_date", "lyrics"];
const AUDIO_HINT = "MP3, WAV, OGG, FLAC, M4A, AAC or WEBM — title, artist, genre, date, lyrics and cover are read from the file's tags";

let editing = null;        // track being edited, or null when creating
let pickedCoverUrl = null; // object URL for a cover image picked by the user
let embeddedCover = null;  // data URL of cover art embedded in the newly picked audio file
let inspectSeq = 0;        // ignore stale tag-reading responses when the file is changed quickly
const autoFilled = new Set(); // fields filled from tags that the user hasn't edited since

/**
 * Show the cover that will actually be saved. Mirrors the server's rules:
 * picked image > (removed) > existing cover > art embedded in the audio file.
 */
function refreshCover() {
  let style = null;
  let label = "";
  if (pickedCoverUrl) style = `background-image:url('${pickedCoverUrl}')`;
  else if (F.remove_cover.checked) style = null;
  else if (editing?.cover_url) style = coverStyle(editing);
  else if (embeddedCover) { style = `background-image:url('${embeddedCover}')`; label = "From audio file"; }

  const box = $("#coverPreview");
  box.setAttribute("style", style || "");
  box.classList.toggle("empty-cover", !style);
  $("#coverSource").textContent = label;
  $("#removeCoverRow").classList.toggle("hidden", !(editing?.cover_url || embeddedCover));
}

function setAudioHint(text, cls = "muted") {
  const h = $("#audioHint");
  h.textContent = text;
  h.className = `small ${cls}`;
}

const currentAudioHint = (t) =>
  `Current: ${t.audio_file}${t.duration ? ` (${fmtTime(t.duration)})` : ""} — leave empty to keep it`;

async function openMusicEditor(t) {
  // List rows don't carry lyrics; load the full track before editing.
  if (t) {
    try { t = await api("GET", `/api/music/${t.id}`); } catch (err) { toast(err.message, "error"); return; }
  }
  editing = t;
  mf.reset();
  inspectSeq++;
  if (pickedCoverUrl) { URL.revokeObjectURL(pickedCoverUrl); pickedCoverUrl = null; }
  embeddedCover = null;
  autoFilled.clear();
  $("#musicEditTitle").textContent = t ? "Edit track" : "New track";
  for (const k of TEXT_FIELDS) {
    F[k].value = t?.[k] ?? "";
    F[k].classList.remove("autofilled");
  }
  F.audio_file.required = !t;
  setAudioHint(t ? currentAudioHint(t) : AUDIO_HINT);
  refreshCover();
  setSaving(false);
  openModal("#musicModal");
  (t ? F.title : F.audio_file).focus();
}

F.cover_image.addEventListener("change", () => {
  if (pickedCoverUrl) { URL.revokeObjectURL(pickedCoverUrl); pickedCoverUrl = null; }
  const file = F.cover_image.files[0];
  if (file) {
    pickedCoverUrl = URL.createObjectURL(file);
    F.remove_cover.checked = false;
  }
  refreshCover();
});

F.remove_cover.addEventListener("change", () => {
  if (F.remove_cover.checked && pickedCoverUrl) {
    F.cover_image.value = "";
    URL.revokeObjectURL(pickedCoverUrl);
    pickedCoverUrl = null;
  }
  refreshCover();
});

// A field the user types in is theirs: a later file pick must not overwrite it.
TEXT_FIELDS.forEach((k) => F[k].addEventListener("input", () => {
  autoFilled.delete(k);
  F[k].classList.remove("autofilled");
}));

/**
 * Fill `field` with `value` unless the user has entered something themselves.
 * A value auto-filled from a previously picked file is cleared if the new file lacks it.
 */
function autofill(field, value) {
  const el = F[field];
  if (el.value.trim() && !autoFilled.has(field)) return false;
  if (!value) {
    if (autoFilled.delete(field)) {
      el.value = "";
      el.classList.remove("autofilled");
    }
    return false;
  }
  el.value = value;
  autoFilled.add(field);
  el.classList.add("autofilled");
  return true;
}

// Picking an audio file: read its tags on the server and pre-fill the form.
F.audio_file.addEventListener("change", async () => {
  const file = F.audio_file.files[0];
  const seq = ++inspectSeq;
  embeddedCover = null;
  refreshCover();
  if (!file) {
    setSaving(false);
    setAudioHint(editing ? currentAudioHint(editing) : AUDIO_HINT);
    return;
  }

  setAudioHint("Reading tags…");
  setSaving(true, "Reading tags…");
  let meta;
  try {
    const fd = new FormData();
    fd.append("audio_file", file);
    meta = await api("POST", "/api/music/inspect", fd);
  } catch (err) {
    if (seq !== inspectSeq) return;
    setSaving(false);
    setAudioHint(err.message, "error-text");
    return;
  }
  if (seq !== inspectSeq) return; // a newer file was picked meanwhile
  setSaving(false);

  const filled = [];
  const stem = file.name.replace(/\.[^.]+$/, "");
  // On edit only empty fields are filled, so saved data is never replaced by tags.
  if (autofill("title", meta.title || (editing ? null : stem))) filled.push(meta.title ? "title" : "title (file name)");
  if (autofill("artist", meta.artist)) filled.push("artist");
  if (autofill("genre", meta.genre)) filled.push("genre");
  const date = meta.release_date || (meta.release_year ? `${meta.release_year}-01-01` : null);
  if (autofill("release_date", date)) filled.push(meta.release_date ? "release date" : "release year");
  if (autofill("lyrics", meta.lyrics)) filled.push("lyrics");
  embeddedCover = meta.cover;
  refreshCover();

  const parts = [file.name];
  if (meta.duration) parts.push(fmtTime(meta.duration));
  parts.push(filled.length ? `filled from tags: ${filled.join(", ")}` : "no usable tags found");
  if (meta.cover) parts.push("embedded cover found");
  setAudioHint(parts.join(" · "));
});

function setSaving(on, label = "Uploading…") {
  const btn = $("#musicSaveBtn");
  btn.disabled = on;
  btn.textContent = on ? label : "Save";
}

mf.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  for (const k of TEXT_FIELDS) fd.append(k, F[k].value.trim());
  if (F.audio_file.files[0]) fd.append("audio_file", F.audio_file.files[0]);
  if (F.cover_image.files[0]) fd.append("cover_image", F.cover_image.files[0]);
  if (F.remove_cover.checked) {
    if (editing) fd.append("remove_cover", "1");
    fd.append("use_embedded_cover", "0");
  }

  setSaving(true);
  try {
    if (editing) {
      await api("PUT", `/api/music/${editing.id}`, fd);
      toast("Track updated", "success");
    } else {
      await api("POST", "/api/music", fd);
      adm.page = 1;
      toast("Track created", "success");
    }
    closeModal(mf);
    document.dispatchEvent(new Event("music-changed"));
    loadAdminMusic();
    loadGenres();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setSaving(false);
  }
});
