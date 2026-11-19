// Admin: user management view.
import {
  $, api, bindPager, closeModal, esc, openModal, registerView, renderPager, setUser, state, toast,
} from "./core.js";

const view = { page: 1, size: 10, items: {} };

async function loadUsers() {
  const params = new URLSearchParams({
    page: view.page, size: view.size, keyword: $("#userKeyword").value.trim(), status: $("#userStatus").value,
  });
  try {
    renderUserTable(await api("GET", `/api/users?${params}`));
  } catch (err) { toast(err.message, "error"); }
}
registerView("users", { admin: true, onShow: loadUsers });

function renderUserTable(data) {
  const { items } = data;
  const tbody = $("#userTbody");
  view.items = Object.fromEntries(items.map((u) => [u.user_id, u]));
  tbody.innerHTML = !items.length
    ? `<tr><td colspan="6" class="empty">No users found</td></tr>`
    : items.map((u) => {
      const self = u.user_id === state.user.user_id;
      return `
      <tr data-id="${u.user_id}">
        <td>${u.user_id}</td>
        <td>${esc(u.username)}${u.is_admin ? '<span class="tag admin">admin</span>' : ""}</td>
        <td>${esc(u.email)}</td>
        <td><span class="tag ${u.is_active ? "on" : "off"}">${u.is_active ? "Active" : "Inactive"}</span></td>
        <td>${u.created_at}</td>
        <td class="actions">
          <button class="btn sm" data-act="edit">Edit</button>
          ${self ? "" : `<button class="btn sm" data-act="toggle">${u.is_active ? "Deactivate" : "Activate"}</button>
          <button class="btn sm danger" data-act="delete">Delete</button>`}
        </td>
      </tr>`;
    }).join("");
  renderPager($("#userPager"), data);
}

bindPager($("#userPager"), (p) => { view.page = p; loadUsers(); });
$("#userSearchBtn").onclick = () => { view.page = 1; loadUsers(); };
$("#userKeyword").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#userSearchBtn").click(); });
$("#userStatus").onchange = () => $("#userSearchBtn").click();

$("#userTbody").addEventListener("click", async (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  const id = +e.target.closest("tr").dataset.id;
  const u = view.items[id];
  try {
    if (act === "edit") return openEdit(u);
    if (act === "toggle") {
      await api("PATCH", `/api/users/${id}/status`, { is_active: !u.is_active });
      toast(u.is_active ? "User deactivated" : "User activated", "success");
    }
    if (act === "delete") {
      if (!confirm(`Delete user "${u.username}"? This cannot be undone.`)) return;
      await api("DELETE", `/api/users/${id}`);
      toast("User deleted", "success");
    }
    loadUsers();
  } catch (err) { toast(err.message, "error"); }
});

// ---------- create / edit ----------
function openEdit(u) {
  const f = $("#editForm");
  f.reset();
  $("#editTitle").textContent = u ? "Edit user" : "New user";
  f.user_id.value = u ? u.user_id : "";
  f.username.value = u ? u.username : "";
  f.email.value = u ? u.email : "";
  f.password.placeholder = u ? "Leave blank to keep unchanged" : "Password (6+ chars)";
  f.password.required = !u;
  f.is_active.checked = u ? u.is_active : true;
  f.is_active.disabled = !!u && u.user_id === state.user.user_id;
  openModal("#editModal");
}
$("#userAddBtn").onclick = () => openEdit(null);

$("#editForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { username: f.username.value.trim(), email: f.email.value.trim() };
  if (f.password.value) body.password = f.password.value;
  if (!f.is_active.disabled) body.is_active = f.is_active.checked;
  try {
    if (f.user_id.value) {
      const updated = await api("PUT", `/api/users/${f.user_id.value}`, body);
      if (updated.user_id === state.user.user_id) setUser(updated);
      toast("User updated", "success");
    } else {
      await api("POST", "/api/users", body);
      toast("User created", "success");
    }
    closeModal(f);
    loadUsers();
  } catch (err) { toast(err.message, "error"); }
});
