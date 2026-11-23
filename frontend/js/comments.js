// Comment section on the track detail page: post comments, reply to any comment (unlimited
// nesting), delete your own (admins: any), collapse threads and load older comments.
import { $, api, esc, state, toast } from "./core.js";
import { openAuth } from "./auth.js";

const MAX_LEN = 1000;
const MAX_INDENT = 4; // deeper replies are shown flat (with "▸ @user") so threads stay readable
const PAGE_SIZE = 10;

const cs = { targetId: null, roots: [], hasMore: false, count: 0, collapsed: new Set(), loading: false };
let seq = 0;

// ---------- data helpers ----------
function findNode(id, nodes = cs.roots, parent = null) {
  for (const n of nodes) {
    if (n.id === id) return { node: n, parent };
    const hit = findNode(id, n.replies, n);
    if (hit) return hit;
  }
  return null;
}

function recount(nodes) {
  for (const n of nodes) {
    recount(n.replies);
    n.reply_count = n.replies.reduce((s, r) => s + 1 + r.reply_count, 0);
  }
}

// ---------- rendering ----------
function editorHtml(placeholder, { reply = false } = {}) {
  return `
    <form class="comment-form${reply ? " reply" : ""}">
      <textarea name="content" maxlength="${MAX_LEN}" rows="${reply ? 2 : 3}" placeholder="${esc(placeholder)}"></textarea>
      <div class="comment-form-bar">
        <span class="small muted" data-counter>0 / ${MAX_LEN}</span>
        <span class="spacer"></span>
        ${reply ? `<button class="btn sm" type="button" data-act="cancel">Cancel</button>` : ""}
        <button class="btn primary sm" type="submit">${reply ? "Reply" : "Post"}</button>
      </div>
    </form>`;
}

function commentHtml(n, depth) {
  const me = state.user;
  const canDelete = me && (me.user_id === n.user_id || me.is_admin);
  const nested = depth < MAX_INDENT;
  const collapsed = cs.collapsed.has(n.id);
  // Name the person being answered when it isn't obvious from the indentation.
  const showReplyTo = n.reply_to && (depth >= 2 || !nested);
  const children = n.replies.map((r) => commentHtml(r, depth + 1)).join("");
  return `
    <div class="comment" data-id="${n.id}">
      <span class="avatar sm">${esc((n.username || "?")[0])}</span>
      <div class="comment-body">
        <div class="comment-head">
          <span class="comment-user">${esc(n.username || "Deleted user")}</span>
          ${showReplyTo ? `<span class="reply-to">▸ ${esc(n.reply_to.username || "?")}</span>` : ""}
          <span class="muted small">${esc(n.publish_time.slice(0, 16))}</span>
        </div>
        <div class="comment-text">${esc(n.content)}</div>
        <div class="comment-actions small">
          <a data-act="reply">Reply</a>
          ${nested && n.reply_count ? `<a data-act="toggle">${collapsed ? `Show ${n.reply_count} ${n.reply_count === 1 ? "reply" : "replies"}` : "Hide replies"}</a>` : ""}
          ${canDelete ? `<a data-act="delete" class="danger-link">Delete</a>` : ""}
        </div>
        <div class="reply-slot"></div>
        ${nested && children ? `<div class="comment-replies${collapsed ? " hidden" : ""}">${children}</div>` : ""}
      </div>
    </div>
    ${!nested ? children : ""}`;
}

function render() {
  $("#commentCount").textContent = cs.count ? `(${cs.count})` : "";
  $("#commentEditor").innerHTML = state.user
    ? editorHtml("Share your thoughts on this track…")
    : `<div class="comment-login muted">Log in to join the conversation. <button class="btn sm primary" data-act="login">Log in</button></div>`;
  $("#commentList").innerHTML = cs.roots.length
    ? cs.roots.map((n) => commentHtml(n, 0)).join("")
    : `<div class="empty">${cs.loading ? "Loading comments…" : "No comments yet — be the first!"}</div>`;
  $("#commentMore").classList.toggle("hidden", !cs.hasMore);
  $("#commentMore").disabled = cs.loading;
}

// ---------- loading ----------
/** Show the comments for a track (called by the detail page). */
export async function showComments(trackId) {
  const my = ++seq;
  Object.assign(cs, { targetId: trackId, roots: [], hasMore: false, count: 0, loading: true });
  cs.collapsed.clear();
  render();
  try {
    const data = await api("GET", `/api/comments?target_type=music&target_id=${trackId}&size=${PAGE_SIZE}`);
    if (my !== seq) return;
    Object.assign(cs, { roots: data.items, hasMore: data.has_more, count: data.comment_count });
  } catch (err) {
    if (my !== seq) return;
    toast(`Couldn't load comments: ${err.message}`, "error");
  }
  cs.loading = false;
  render();
}

async function loadMore() {
  if (cs.loading || !cs.roots.length) return;
  const my = seq;
  cs.loading = true;
  render();
  try {
    const before = cs.roots[cs.roots.length - 1].id;
    const data = await api("GET",
      `/api/comments?target_type=music&target_id=${cs.targetId}&size=${PAGE_SIZE}&before_id=${before}`);
    if (my !== seq) return;
    cs.roots.push(...data.items);
    cs.hasMore = data.has_more;
    cs.count = data.comment_count;
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (my === seq) { cs.loading = false; render(); }
  }
}

// ---------- actions ----------
async function submit(form, parentId) {
  const box = form.content;
  const content = box.value.trim();
  if (!content) { box.focus(); return; }
  const btn = form.querySelector("[type=submit]");
  btn.disabled = true;
  const my = seq;
  try {
    const body = parentId ? { parent_id: parentId, content } : { target_type: "music", target_id: cs.targetId, content };
    const c = await api("POST", "/api/comments", body);
    if (my !== seq) return;
    if (parentId) {
      const hit = findNode(parentId);
      if (hit) {
        hit.node.replies.push(c);
        cs.collapsed.delete(parentId);
      }
    } else {
      cs.roots.unshift(c);
    }
    recount(cs.roots);
    cs.count += 1;
    render();
    toast(parentId ? "Reply posted" : "Comment posted", "success");
  } catch (err) {
    toast(err.message, "error");
    btn.disabled = false;
  }
}

async function remove(id) {
  const hit = findNode(id);
  if (!hit) return;
  const extra = hit.node.reply_count ? ` and its ${hit.node.reply_count} ${hit.node.reply_count === 1 ? "reply" : "replies"}` : "";
  if (!confirm(`Delete this comment${extra}?`)) return;
  const my = seq;
  try {
    const { deleted } = await api("DELETE", `/api/comments/${id}`);
    if (my !== seq) return;
    const again = findNode(id); // the tree may have changed while waiting
    if (again) {
      const list = again.parent ? again.parent.replies : cs.roots;
      list.splice(list.indexOf(again.node), 1);
    }
    recount(cs.roots);
    cs.count = Math.max(0, cs.count - deleted);
    render();
    toast("Comment deleted");
  } catch (err) {
    toast(err.message, "error");
  }
}

function openReply(commentEl) {
  if (!state.user) { openAuth("login"); return; }
  const slot = commentEl.querySelector(":scope > .comment-body > .reply-slot");
  if (slot.firstElementChild) { slot.querySelector("textarea").focus(); return; }
  const name = commentEl.querySelector(":scope > .comment-body .comment-user").textContent;
  slot.innerHTML = editorHtml(`Reply to ${name}…`, { reply: true });
  slot.querySelector("textarea").focus();
}

// ---------- wiring ----------
const section = $("#commentSection");

section.addEventListener("click", (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!act) return;
  const el = e.target.closest(".comment");
  const id = el ? +el.dataset.id : null;
  if (act === "login") openAuth("login");
  else if (act === "reply") openReply(el);
  else if (act === "cancel") e.target.closest(".reply-slot").innerHTML = "";
  else if (act === "delete") remove(id);
  else if (act === "toggle") {
    if (cs.collapsed.has(id)) cs.collapsed.delete(id); else cs.collapsed.add(id);
    render();
  }
});

section.addEventListener("submit", (e) => {
  e.preventDefault();
  const el = e.target.closest(".comment");
  submit(e.target, el ? +el.dataset.id : null);
});

section.addEventListener("input", (e) => {
  if (e.target.name !== "content") return;
  const counter = e.target.form.querySelector("[data-counter]");
  counter.textContent = `${e.target.value.length} / ${MAX_LEN}`;
});

section.addEventListener("keydown", (e) => {
  // Ctrl/Cmd+Enter posts.
  if (e.target.name !== "content") return;
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) e.target.form.requestSubmit();
});

$("#commentMore").onclick = loadMore;
