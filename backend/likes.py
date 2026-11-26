"""Likes shared by every likeable target (tracks, comments, ...).

A like is a (user, target_type, target_id) row; liking is idempotent. The routes live with their
resources and only call into this module:
    PUT/DELETE /api/music/<id>/like       (music.py)
    PUT/DELETE /api/comments/<id>/like    (comments.py)
Both return {like_count, liked}. A user can like any number of tracks and comments.

Targets have no foreign key from the likes table, so whoever deletes a target must call
delete_likes() for it (music.delete_music and comments.delete_comment_trees do).
"""
from sqlalchemy.exc import IntegrityError

from .common import fail, ok
from .models import Like, db

_CHUNK = 500  # max ids per IN (...) clause


def _chunks(ids):
    ids = list(ids)
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


def like_infos(target_type, ids, user):
    """{target_id: {"like_count": n, "liked": bool}} for many targets at once (2 queries per chunk)."""
    ids = list(dict.fromkeys(ids))
    info = {tid: {"like_count": 0, "liked": False} for tid in ids}
    for chunk in _chunks(ids):
        rows = (db.session.query(Like.target_id, db.func.count())
                .filter(Like.target_type == target_type, Like.target_id.in_(chunk))
                .group_by(Like.target_id))
        for tid, n in rows:
            info[tid]["like_count"] = n
        if user is not None:
            mine = db.session.query(Like.target_id).filter(
                Like.user_id == user.user_id, Like.target_type == target_type, Like.target_id.in_(chunk))
            for (tid,) in mine:
                info[tid]["liked"] = True
    return info


def like_info(target_type, target_id, user):
    return like_infos(target_type, [target_id], user)[target_id]


def set_like(user, target_type, target_id, liked):
    """Like or unlike a target that the caller has verified exists. Returns the API response."""
    key = (user.user_id, target_type, target_id)
    existing = db.session.get(Like, key)
    if liked and existing is None:
        db.session.add(Like(user_id=user.user_id, target_type=target_type, target_id=target_id))
    elif not liked and existing is not None:
        db.session.delete(existing)
    try:
        db.session.commit()
    except IntegrityError:  # a concurrent request already liked it; the end state is the same
        db.session.rollback()
    return ok(like_info(target_type, target_id, user))


def like_route(model, target_type, not_found):
    """Build the PUT/DELETE handler body shared by every likeable resource."""
    def handle(user, target_id, liked):
        if db.session.get(model, target_id) is None:
            return fail(not_found, 404)
        return set_like(user, target_type, target_id, liked)
    return handle


def delete_likes(target_type, ids):
    """Remove all likes on the given targets (call when deleting them). The caller commits."""
    for chunk in _chunks(set(ids)):
        Like.query.filter(Like.target_type == target_type, Like.target_id.in_(chunk)) \
            .delete(synchronize_session=False)
