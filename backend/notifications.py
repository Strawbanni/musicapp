"""System notifications: tell users when someone likes or replies to their comments.

Created automatically (never through the API):
    like   - someone likes your comment (removed again if they unlike it)
    reply  - someone replies to your comment
Acting on your own comment never notifies you. Tracks have no owner, so liking a track or posting a
top-level comment on it notifies nobody.

Stored as (sourceUserId, targetUserId, operationType, targetType, targetId): operationType is
"like" or "reply" and (targetType, targetId) is the operated target, ("comment", <the liked or
replied-to comment>). A reply's own id is not stored; deleting the reply removes the oldest
matching notification created no earlier than the reply.

User:  GET /api/notifications?page=&size=[&unread_only=1]
           Newest first: {items, total, unread_count, page, size}. Each item has id, type (the
           operationType), is_read, created_at, actor {user_id, username}, comment {id, content,
           parent_id} (the operated comment: your liked / replied-to comment) and
           target {type, id, title} (what that comment is on, e.g. a track).
       GET /api/notifications/unread-count   {unread_count}  (cheap; the bell polls this)
       PUT /api/notifications/<id>/read      Mark one as read; returns {unread_count}
       PUT /api/notifications/read-all       Mark all as read; returns {unread_count: 0, updated}
"""
from flask import Blueprint, request
from sqlalchemy.orm import aliased

from .common import fail, login_required, ok, paginate_args
from .models import Comment, Music, Notification, User, db

bp = Blueprint("notifications", __name__, url_prefix="/api")

LIKE, REPLY = "like", "reply"  # operationType values
COMMENT = "comment"  # targetType of comments
SNIPPET = 120  # characters of comment text included in a notification
_CHUNK = 500  # max ids per IN (...) clause
# targetType -> (model, title attribute) used to name what a comment is on.
_TARGET_TITLES = {"music": (Music, "title")}


def _chunks(ids):
    ids = list(ids)
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


# ---------- creating / removing (called by likes.py, comments.py, users.py; the caller commits) ----------

def notify(target_user_id, source_user_id, operation_type, target_type, target_id):
    if target_user_id is None or target_user_id == source_user_id:
        return
    db.session.add(Notification(target_user_id=target_user_id, source_user_id=source_user_id,
                                operation_type=operation_type, target_type=target_type,
                                target_id=target_id))


def like_changed(actor, target_type, target_id, liked):
    """Hook for likes.set_like: notify a comment's author on like, retract it on unlike."""
    if target_type != COMMENT:
        return  # only comments have an author to notify
    if liked:
        comment = db.session.get(Comment, target_id)
        if comment is not None:
            notify(comment.user_id, actor.user_id, LIKE, COMMENT, target_id)
    else:
        Notification.query.filter_by(source_user_id=actor.user_id, operation_type=LIKE,
                                     target_type=COMMENT, target_id=target_id) \
            .delete(synchronize_session=False)


def reply_posted(reply, parent):
    notify(parent.user_id, reply.user_id, REPLY, COMMENT, parent.id)


def delete_comment_notifications(comment_ids):
    """Call before deleting comments (whole subtrees): removes notifications whose target is one
    of them, and the reply notification of each deleted reply whose parent survives."""
    ids = set(comment_ids)
    for chunk in _chunks(ids):
        Notification.query.filter(Notification.target_type == COMMENT,
                                  Notification.target_id.in_(chunk)).delete(synchronize_session=False)
        replies = db.session.query(Comment.user_id, Comment.parent_id, Comment.publish_time) \
            .filter(Comment.id.in_(chunk), Comment.parent_id.isnot(None)).all()
        for user_id, parent_id, published in replies:
            if parent_id in ids:
                continue  # its notification targeted a deleted comment and is already gone
            # The reply's id isn't stored: drop the oldest matching notification made after it.
            n = (Notification.query
                 .filter_by(source_user_id=user_id, operation_type=REPLY, target_type=COMMENT,
                            target_id=parent_id)
                 .filter(Notification.create_time >= published)
                 .order_by(Notification.create_time, Notification.id).first())
            if n is not None:
                db.session.delete(n)


def delete_user_notifications(user_id):
    """Remove notifications sent to or caused by a user (call when deleting the user)."""
    Notification.query.filter(db.or_(Notification.target_user_id == user_id,
                                     Notification.source_user_id == user_id)) \
        .delete(synchronize_session=False)


# ---------- reading ----------

def _unread_count(user):
    return Notification.query.filter_by(target_user_id=user.user_id, is_read=False).count()


def _snippet(text):
    return text if len(text) <= SNIPPET else text[:SNIPPET - 1].rstrip() + "…"


def _titles(comments):
    """{(target_type, target_id): title} for the targets of the given comments."""
    wanted = {}
    for c in comments:
        wanted.setdefault(c.target_type, set()).add(c.target_id)
    titles = {}
    for target_type, ids in wanted.items():
        if target_type not in _TARGET_TITLES:
            continue
        model, attr = _TARGET_TITLES[target_type]
        pk = model.__mapper__.primary_key[0]
        for chunk in _chunks(ids):
            for tid, title in db.session.query(pk, getattr(model, attr)).filter(pk.in_(chunk)):
                titles[(target_type, tid)] = title
    return titles


@bp.get("/notifications")
@login_required
def list_notifications(user):
    page, size = paginate_args(request, default_size=20, max_size=50)
    unread_only = request.args.get("unread_only", "").lower() in ("1", "true", "yes")

    actor = aliased(User)
    q = (db.session.query(Notification, actor.username, Comment)
         .join(actor, actor.user_id == Notification.source_user_id)
         .join(Comment, Comment.id == Notification.target_id)
         .filter(Notification.target_user_id == user.user_id,
                 Notification.target_type == COMMENT))
    if unread_only:
        q = q.filter(Notification.is_read.is_(False))
    total = q.count()
    rows = q.order_by(Notification.id.desc()).offset((page - 1) * size).limit(size).all()

    titles = _titles(c for _, _, c in rows)
    items = [{
        "id": n.id,
        "type": n.operation_type,
        "is_read": n.is_read,
        "created_at": n.create_time.strftime("%Y-%m-%d %H:%M:%S"),
        "actor": {"user_id": n.source_user_id, "username": name},
        "comment": {"id": c.id, "content": _snippet(c.content), "parent_id": c.parent_id},
        "target": {"type": c.target_type, "id": c.target_id,
                   "title": titles.get((c.target_type, c.target_id))},
    } for n, name, c in rows]
    return ok({"items": items, "total": total, "unread_count": _unread_count(user),
               "page": page, "size": size})


@bp.get("/notifications/unread-count")
@login_required
def unread_count(user):
    return ok({"unread_count": _unread_count(user)})


@bp.put("/notifications/<int:nid>/read")
@login_required
def mark_read(user, nid):
    n = db.session.get(Notification, nid)
    if n is None or n.target_user_id != user.user_id:
        return fail("Notification not found", 404)
    if not n.is_read:
        n.is_read = True
        db.session.commit()
    return ok({"unread_count": _unread_count(user)})


@bp.put("/notifications/read-all")
@login_required
def mark_all_read(user):
    updated = Notification.query.filter_by(target_user_id=user.user_id, is_read=False) \
        .update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    return ok({"unread_count": 0, "updated": updated})
