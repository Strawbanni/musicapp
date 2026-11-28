"""User management API.

Auth routes:   /api/auth/register, /api/auth/login, /api/auth/logout, /api/auth/me
Self-service:  PUT /api/users/me, PUT /api/users/me/password
Admin routes:  GET /api/users, GET/PUT/DELETE /api/users/<id>, PATCH /api/users/<id>/status
"""
import re

from flask import Blueprint, request, session
from sqlalchemy import or_

from .comments import delete_user_comments
from .common import admin_required, current_user, fail, is_admin, login_required, ok, paginate_args
from .models import User, db
from .notifications import delete_user_notifications

bp = Blueprint("users", __name__, url_prefix="/api")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_一-龥]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- validation ----------

def validate_username(username):
    if not USERNAME_RE.match(username or ""):
        return "Username must be 3-32 characters: letters, digits, underscore or Chinese"


def validate_email(email):
    if not EMAIL_RE.match(email or "") or len(email) > 120:
        return "Invalid email address"


def validate_password(password):
    if not password or len(password) < 6 or len(password) > 64:
        return "Password must be 6-64 characters"


def user_payload(user):
    d = user.to_dict()
    d["is_admin"] = is_admin(user)
    return d


# ---------- auth ----------

@bp.post("/auth/register")
def register():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    err = validate_username(username) or validate_email(email) or validate_password(password)
    if err:
        return fail(err)
    if User.query.filter_by(username=username).first():
        return fail("Username already taken", 409)
    if User.query.filter_by(email=email).first():
        return fail("Email already registered", 409)

    user = User(username=username, email=email, is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    session.clear()
    session["user_id"] = user.user_id
    return ok(user_payload(user), "Registered", 201)


@bp.post("/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    account = (body.get("account") or "").strip()  # username or email
    password = body.get("password") or ""
    if not account or not password:
        return fail("Account and password are required")

    user = User.query.filter(
        or_(User.username == account, User.email == account.lower())
    ).first()
    if user is None or not user.check_password(password):
        return fail("Incorrect account or password", 401)
    if not user.is_active:
        return fail("This account has been deactivated", 403)

    session.clear()
    session["user_id"] = user.user_id
    session.permanent = bool(body.get("remember"))
    return ok(user_payload(user), "Logged in")


@bp.post("/auth/logout")
def logout():
    session.clear()
    return ok(msg="Logged out")


@bp.get("/auth/me")
def me():
    user = current_user()
    return ok(user_payload(user) if user else None)


# ---------- self-service ----------

@bp.put("/users/me")
@login_required
def update_me(user):
    body = request.get_json(silent=True) or {}
    if "email" in body:
        email = (body.get("email") or "").strip().lower()
        err = validate_email(email)
        if err:
            return fail(err)
        if User.query.filter(User.email == email, User.user_id != user.user_id).first():
            return fail("Email already registered", 409)
        user.email = email
    db.session.commit()
    return ok(user_payload(user), "Profile updated")


@bp.put("/users/me/password")
@login_required
def change_password(user):
    body = request.get_json(silent=True) or {}
    if not user.check_password(body.get("old_password") or ""):
        return fail("Current password is incorrect", 400)
    new = body.get("new_password") or ""
    err = validate_password(new)
    if err:
        return fail(err)
    user.set_password(new)
    db.session.commit()
    return ok(msg="Password changed")


# ---------- admin ----------

@bp.get("/users")
@admin_required
def list_users(_admin):
    page, size = paginate_args(request)
    keyword = (request.args.get("keyword") or "").strip()
    status = request.args.get("status")  # "active" | "inactive" | None

    q = User.query
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(User.username.ilike(like), User.email.ilike(like)))
    if status == "active":
        q = q.filter(User.is_active.is_(True))
    elif status == "inactive":
        q = q.filter(User.is_active.is_(False))

    pager = q.order_by(User.user_id.desc()).paginate(page=page, per_page=size, error_out=False)
    return ok({
        "items": [user_payload(u) for u in pager.items],
        "total": pager.total,
        "page": page,
        "size": size,
    })


@bp.post("/users")
@admin_required
def admin_create_user(_admin):
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    err = validate_username(username) or validate_email(email) or validate_password(password)
    if err:
        return fail(err)
    if User.query.filter_by(username=username).first():
        return fail("Username already taken", 409)
    if User.query.filter_by(email=email).first():
        return fail("Email already registered", 409)
    user = User(username=username, email=email, is_active=bool(body.get("is_active", True)))
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return ok(user_payload(user), "User created", 201)


@bp.get("/users/<int:uid>")
@admin_required
def get_user(_admin, uid):
    user = db.session.get(User, uid)
    if user is None:
        return fail("User not found", 404)
    return ok(user_payload(user))


@bp.put("/users/<int:uid>")
@admin_required
def admin_update_user(admin, uid):
    user = db.session.get(User, uid)
    if user is None:
        return fail("User not found", 404)
    body = request.get_json(silent=True) or {}

    if "username" in body:
        username = (body.get("username") or "").strip()
        err = validate_username(username)
        if err:
            return fail(err)
        if User.query.filter(User.username == username, User.user_id != uid).first():
            return fail("Username already taken", 409)
        user.username = username
    if "email" in body:
        email = (body.get("email") or "").strip().lower()
        err = validate_email(email)
        if err:
            return fail(err)
        if User.query.filter(User.email == email, User.user_id != uid).first():
            return fail("Email already registered", 409)
        user.email = email
    if body.get("password"):
        err = validate_password(body["password"])
        if err:
            return fail(err)
        user.set_password(body["password"])
    if "is_active" in body:
        if uid == admin.user_id and not body["is_active"]:
            return fail("You cannot deactivate your own account")
        user.is_active = bool(body["is_active"])

    db.session.commit()
    return ok(user_payload(user), "User updated")


@bp.patch("/users/<int:uid>/status")
@admin_required
def toggle_status(admin, uid):
    user = db.session.get(User, uid)
    if user is None:
        return fail("User not found", 404)
    body = request.get_json(silent=True) or {}
    active = bool(body.get("is_active"))
    if uid == admin.user_id and not active:
        return fail("You cannot deactivate your own account")
    user.is_active = active
    db.session.commit()
    return ok(user_payload(user), "Activated" if active else "Deactivated")


@bp.delete("/users/<int:uid>")
@admin_required
def delete_user(admin, uid):
    if uid == admin.user_id:
        return fail("You cannot delete your own account")
    user = db.session.get(User, uid)
    if user is None:
        return fail("User not found", 404)
    delete_user_comments(uid)  # with the replies under them; deepest first, see comments.py
    delete_user_notifications(uid)  # sent to or caused by the user
    db.session.delete(user)
    db.session.commit()
    return ok(msg="User deleted")
