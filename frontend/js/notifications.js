// Notification bell in the topbar: unread badge (polled), dropdown list, mark as read, and
// jumping to the track a notification is about.
import { $, api, esc, state, toast } from "./core.js";
import { openDetail } from "./detail.js";
import { currentTrack, playTrack } from "./player.js";

const POLL_MS = 30000;
const PAGE_SIZE = 15;
const ns = { unread: 0, items: [], page: 0, hasMore: false, loading: false, timer: null, userId: null, seq: 0 };

const bell = $("#notifBell");
const panel = $("#notifPanel");
const isOpen = () => !panel.classList.contains("hidden");

// ---------- badge ----------
function setUnread(n) {
  ns.unread = Math.max(0, n | 0);
  const badge = $("#notifBadge");
  badge.textContent = ns.unread > 99 ? "99+" : ns.unread;
  badge.classList.toggle("hidden", ns.unread === 0);
  bell.title = ns.unread ? `${ns.unread} unread notification${ns.unread > 1 ? "s" : ""}` : "Notifications";
  $("#notifReadAll").disabled = ns.unread === 0;
}

async function refreshCount() {
  if (!state.user || document.hidden) return;
  try {
    const { unread_count: n } = await api("GET", "/api/notifications/unread-count");
    const grew = n > ns.unread;
    setUnread(n);
    if (grew && isOpen()) loadList(true); // new arrivals while the panel is open
  } catch { /* logged out or offline: the next poll retries */ }
}

function startPolling() {
  stopPolling();
  refreshCount();
  ns.timer = setInterval(refreshCount, POLL_MS);
}
function stopPolling() {
  clearInterval(ns.timer);
  ns.timer = null;
}

// ---------- list ----------
function timeAgo(s) {
  const t = new Date(s.replace(" ", "T"));
  const sec = (Date.now() - t) / 1000;
  if (!Number.isFinite(sec)) return s;
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
  if (sec < 7 * 86400) return `${Math.floor(sec / 86400)} d ago`;
  return s.slice(0, 10);
}

function itemHtml(n) {
  const who = `<strong>${esc(n.actor.username)}</strong>`;
  const what = n.type === "like" ? "liked your comment" : "replied to your comment";
  const on = n.target.title ? ` on <strong>${esc(n.target.title)}</strong>` : "";
  return `
    <div class="notif-item ${n.is_read ? "read" : ""}" data-id="${n.id}">
      <span class="avatar sm">${esc(n.actor.username[0])}</span>
      <div class="notif-body">
        <div>${n.type === "like" ? "❤️ " : "💬 "}${who} ${what}${on}</div>
        <div class="notif-quote">“${esc(n.comment.content)}”</div>
        <div class="notif-meta" title="${esc(n.created_at)}">${timeAgo(n.created_at)}</div>
      </div>
      <span class="notif-dot"></span>
    </div>`;
}

function renderList() {
  $("#notifList").innerHTML = ns.items.length
    ? ns.items.map(itemHtml).join("")
    : `<div class="empty">${ns.loading ? "Loading…" : "No notifications yet"}</div>`;
  $("#notifMore").classList.toggle("hidden", !ns.hasMore);
  $("#notifMore").disabled = ns.loading;
}

async function loadList(reset = false) {
  if (ns.loading && !reset) return;
  ns.loading = true;
  const seq = ++ns.seq; // a newer reset supersedes this request
  const page = reset ? 1 : ns.page + 1;
  if (reset) { ns.items = []; renderList(); }
  try {
    const d = await api("GET", `/api/notifications?page=${page}&size=${PAGE_SIZE}`);
    if (seq !== ns.seq) return;
    const seen = new Set(ns.items.map((n) => n.id));
    ns.items = reset ? d.items : ns.items.concat(d.items.filter((n) => !seen.has(n.id)));
    ns.page = page;
    ns.hasMore = page * d.size < d.total;
    setUnread(d.unread_count);
  } catch (err) {
    if (seq === ns.seq) toast(err.message, "error");
  } finally {
    if (seq === ns.seq) {
      ns.loading = false;
      renderList();
    }
  }
}

// ---------- actions ----------
function openPanel() {
  panel.classList.remove("hidden");
  bell.setAttribute("aria-expanded", "true");
  loadList(true);
}
function closePanel() {
  panel.classList.add("hidden");
  bell.setAttribute("aria-expanded", "false");
}

async function markRead(n) {
  if (n.is_read) return;
  n.is_read = true;
  renderList();
  try {
    setUnread((await api("PUT", `/api/notifications/${n.id}/read`)).unread_count);
  } catch { /* stays read locally; the next poll corrects the badge */ }
}

async function openNotification(n) {
  markRead(n);
  closePanel();
  if (n.target.type !== "music") return;
  try {
    if (currentTrack()?.id !== n.target.id) {
      playTrack(await api("GET", `/api/music/${n.target.id}`));
    }
    openDetail(); // the track page shows the comment thread
  } catch (err) {
    toast(err.message, "error");
  }
}

bell.addEventListener("click", (e) => {
  e.stopPropagation();
  $("#userMenu")?.classList.add("hidden");
  isOpen() ? closePanel() : openPanel();
});
panel.addEventListener("click", (e) => {
  e.stopPropagation();
  const el = e.target.closest(".notif-item");
  const n = el && ns.items.find((x) => x.id === +el.dataset.id);
  if (n) openNotification(n);
});
$("#notifMore").onclick = () => loadList();
$("#notifReadAll").onclick = async () => {
  try {
    await api("PUT", "/api/notifications/read-all");
    ns.items.forEach((n) => { n.is_read = true; });
    setUnread(0);
    renderList();
  } catch (err) { toast(err.message, "error"); }
};
document.addEventListener("click", closePanel);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) closePanel(); });
// Catch up straight away when the tab becomes visible again (polling skips hidden tabs).
document.addEventListener("visibilitychange", refreshCount);

// Show the bell only while logged in; reset everything when the account changes.
document.addEventListener("user-changed", ({ detail: user }) => {
  $("#notifArea").classList.toggle("hidden", !user);
  const id = user?.user_id ?? null;
  if (id === ns.userId) return; // e.g. a profile edit: same account, keep state
  ns.userId = id;
  ns.seq++; // drop responses for the previous account
  ns.loading = false;
  closePanel();
  ns.items = [];
  ns.page = 0;
  ns.hasMore = false;
  setUnread(0);
  if (user) startPolling();
  else stopPolling();
});
