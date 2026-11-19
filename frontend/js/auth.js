// Header user area, login/register modal, and the "My Account" profile view.
import { $, $$, api, closeModal, esc, formData, navigate, openModal, registerView, setUser, state, toast } from "./core.js";

// ---------- header user area ----------
export function renderUser() {
  const area = $("#userArea");
  const u = state.user;
  $$(".admin-only").forEach((el) => el.classList.toggle("hidden", !(u && u.is_admin)));
  if (!u) {
    area.innerHTML = `<button class="btn primary" id="loginBtn">Log in</button>`;
    $("#loginBtn").onclick = () => openAuth("login");
    return;
  }
  area.innerHTML = `
    <span>${esc(u.username)}</span>
    <span class="avatar" id="avatarBtn">${esc(u.username[0])}</span>
    <div class="user-menu hidden" id="userMenu">
      <a data-go="profile">My Account</a>
      ${u.is_admin ? `<a data-go="music">Music Management</a><a data-go="users">User Management</a>` : ""}
      <a id="logoutBtn">Log out</a>
    </div>`;
  const menu = $("#userMenu");
  $("#avatarBtn").onclick = (e) => { e.stopPropagation(); menu.classList.toggle("hidden"); };
  $$("[data-go]", menu).forEach((a) => a.onclick = () => {
    menu.classList.add("hidden");
    navigate(a.dataset.go);
  });
  $("#logoutBtn").onclick = async () => {
    await api("POST", "/api/auth/logout");
    setUser(null);
    toast("Logged out");
  };
}
document.addEventListener("click", () => $("#userMenu")?.classList.add("hidden"));

// ---------- auth modal ----------
export function openAuth(tab = "login") {
  switchTab(tab);
  openModal("#authModal");
}
function switchTab(tab) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  $("#loginForm").classList.toggle("hidden", tab !== "login");
  $("#registerForm").classList.toggle("hidden", tab !== "register");
}
$$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = formData(e.target);
  try {
    const user = await api("POST", "/api/auth/login", {
      account: d.account, password: d.password, remember: !!d.remember,
    });
    e.target.reset();
    closeModal(e.target);
    setUser(user);
    toast(`Welcome back, ${user.username}`, "success");
  } catch (err) { toast(err.message, "error"); }
});

$("#registerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = formData(e.target);
  if (d.password !== d.confirm) return toast("Passwords do not match", "error");
  try {
    const user = await api("POST", "/api/auth/register", {
      username: d.username, email: d.email, password: d.password,
    });
    e.target.reset();
    closeModal(e.target);
    setUser(user);
    toast("Account created", "success");
  } catch (err) { toast(err.message, "error"); }
});

// ---------- profile view ----------
function renderProfile() {
  const u = state.user;
  $("#profileAvatar").textContent = u.username[0];
  $("#profileName").textContent = u.username;
  $("#profileId").textContent = u.user_id;
  $("#profileCreated").textContent = u.created_at;
  $("#emailForm").email.value = u.email;
}
registerView("profile", { auth: true, onShow: renderProfile });

$("#emailForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    setUser(await api("PUT", "/api/users/me", { email: e.target.email.value }));
    renderProfile();
    toast("Profile updated", "success");
  } catch (err) { toast(err.message, "error"); }
});

$("#passwordForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = formData(e.target);
  if (d.new_password !== d.confirm) return toast("Passwords do not match", "error");
  try {
    await api("PUT", "/api/users/me/password", { old_password: d.old_password, new_password: d.new_password });
    e.target.reset();
    toast("Password changed", "success");
  } catch (err) { toast(err.message, "error"); }
});
