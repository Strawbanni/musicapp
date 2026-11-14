"""Helpers shared by all API blueprints: response envelope and auth guards."""
from functools import wraps

from flask import current_app, jsonify, session

from .models import User, db


def ok(data=None, msg="ok", status=200):
    return jsonify({"code": 0, "msg": msg, "data": data}), status


def fail(msg, status=400):
    return jsonify({"code": status, "msg": msg, "data": None}), status


def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    user = db.session.get(User, uid)
    if user is None or not user.is_active:
        session.clear()
        return None
    return user


def is_admin(user):
    return user is not None and user.username in current_app.config["ADMIN_USERNAMES"]


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return fail("Please log in first", 401)
        return fn(user, *args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return fail("Please log in first", 401)
        if not is_admin(user):
            return fail("Admin permission required", 403)
        return fn(user, *args, **kwargs)
    return wrapper


def paginate_args(request, default_size=10, max_size=100):
    page = max(request.args.get("page", 1, type=int), 1)
    size = min(max(request.args.get("size", default_size, type=int), 1), max_size)
    return page, size
