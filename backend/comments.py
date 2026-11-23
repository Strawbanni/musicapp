"""Comment API: comments on tracks, replies, and replies to replies (unlimited nesting).

Public:  GET /api/comments?target_type=music&target_id=<id>&page=&size=[&before_id=]
             Top-level comments, newest first, paginated. Pass `before_id` (the last root id you
             have) instead of `page` to load more without skips/duplicates when comments change. Each item carries its whole reply tree
             in `replies` (oldest first at every level) plus `reply_count` (all descendants).
             `total` counts top-level comments; `has_more` says whether older ones remain.
             Also returns `comment_count`: every comment on the target, replies included.
         GET /api/comments/<id>       One comment with its reply tree.
User:    POST /api/comments           JSON {target_type, target_id, content} for a new comment,
                                      or {parent_id, content} for a reply (target is inherited).
         DELETE /api/comments/<id>    Author or admin; removes the comment and all its replies.

Every comment dict has: id, user_id, username, target_type, target_id, parent_id, content,
publish_time, reply_to ({user_id, username} of the parent's author, or null), replies, reply_count.
"""
from flask import Blueprint, request

from .common import fail, is_admin, login_required, ok, paginate_args
from .models import Comment, Music, User, db

bp = Blueprint("comments", __name__, url_prefix="/api")

# targetType -> model whose primary key is targetId. Add entries here to make more things commentable.
TARGETS = {"music": Music}
MAX_CONTENT = 1000  # characters
_CHUNK = 500  # max ids per IN (...) clause


def _chunks(ids):
    ids = list(ids)
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


# ---------- reading trees ----------

def _rows(*criteria):
    """(Comment, username) pairs matching the criteria."""
    return (db.session.query(Comment, User.username)
            .join(User, User.user_id == Comment.user_id)
            .filter(*criteria).all())


def _build_trees(roots):
    """Turn [(Comment, username)] roots into dicts with nested `replies`, loading descendants level
    by level (one query per depth, so cost scales with the thread, not the whole target)."""
    nodes = {}
    for c, name in roots:
        nodes[c.id] = {**c.to_dict(name), "reply_to": None, "replies": []}
    frontier = list(nodes)
    while frontier:
        found = []
        for chunk in _chunks(frontier):
            found += _rows(Comment.parent_id.in_(chunk))
        found.sort(key=lambda r: r[0].id)  # oldest first (ids follow publish order)
        frontier = []
        for c, name in found:
            parent = nodes[c.parent_id]
            node = {**c.to_dict(name), "reply_to": {"user_id": parent["user_id"], "username": parent["username"]},
                    "replies": []}
            nodes[c.id] = node
            parent["replies"].append(node)
            frontier.append(c.id)

    def count(node):
        node["reply_count"] = sum(1 + count(r) for r in node["replies"])
        return node["reply_count"]

    trees = [nodes[c.id] for c, _ in roots]
    for t in trees:
        count(t)
    return trees


# ---------- deleting trees ----------

def delete_comment_trees(ids):
    """Delete the given comments and all their descendants. Returns the number of rows removed.

    Rows are deleted deepest level first so the parentId ON DELETE CASCADE never has to recurse
    (MySQL refuses cascades deeper than 15 levels). The caller commits.
    """
    parent_of = {}
    for chunk in _chunks(set(ids)):
        parent_of.update(db.session.query(Comment.id, Comment.parent_id).filter(Comment.id.in_(chunk)).all())
    frontier = list(parent_of)
    while frontier:
        new = []
        for chunk in _chunks(frontier):
            for cid, pid in db.session.query(Comment.id, Comment.parent_id).filter(Comment.parent_id.in_(chunk)):
                if cid not in parent_of:
                    parent_of[cid] = pid
                    new.append(cid)
        frontier = new

    # A reply is always inserted after its parent, so ascending ids visit parents first.
    depth = {}
    for cid in sorted(parent_of):
        pid = parent_of[cid]
        depth[cid] = depth[pid] + 1 if pid in depth else 0
    levels = {}
    for cid, d in depth.items():
        levels.setdefault(d, []).append(cid)
    for d in sorted(levels, reverse=True):
        for chunk in _chunks(levels[d]):
            Comment.query.filter(Comment.id.in_(chunk)).delete(synchronize_session=False)
    return len(parent_of)


def delete_target_comments(target_type, target_id):
    """Remove every comment on a target (call before deleting the target itself)."""
    ids = [cid for (cid,) in db.session.query(Comment.id).filter(
        Comment.target_type == target_type, Comment.target_id == target_id)]
    return delete_comment_trees(ids)


def delete_user_comments(user_id):
    """Remove a user's comments together with the replies under them."""
    ids = [cid for (cid,) in db.session.query(Comment.id).filter(Comment.user_id == user_id)]
    return delete_comment_trees(ids)


# ---------- routes ----------

def _parse_target(target_type, raw_id):
    """Validate a (target_type, target_id) pair. Returns (target_id, error_response)."""
    model = TARGETS.get(target_type)
    if model is None:
        return None, fail(f"target_type must be one of: {', '.join(sorted(TARGETS))}")
    try:
        target_id = int(raw_id)
    except (TypeError, ValueError):
        return None, fail("target_id must be an integer")
    if db.session.get(model, target_id) is None:
        return None, fail("Target not found", 404)
    return target_id, None


@bp.get("/comments")
def list_comments():
    target_type = (request.args.get("target_type") or "").strip()
    target_id, err = _parse_target(target_type, request.args.get("target_id"))
    if err:
        return err
    page, size = paginate_args(request, default_size=10, max_size=50)
    before_id = request.args.get("before_id", type=int)

    on_target = (Comment.target_type == target_type, Comment.target_id == target_id)
    total = Comment.query.filter(*on_target, Comment.parent_id.is_(None)).count()
    roots_q = (db.session.query(Comment, User.username)
               .join(User, User.user_id == Comment.user_id)
               .filter(*on_target, Comment.parent_id.is_(None))
               .order_by(Comment.id.desc()))  # newest first
    if before_id is not None:
        roots = roots_q.filter(Comment.id < before_id).limit(size).all()
        has_more = roots_q.filter(Comment.id < before_id).offset(size).limit(1).count() > 0
    else:
        roots = roots_q.offset((page - 1) * size).limit(size).all()
        has_more = page * size < total
    comment_count = db.session.query(db.func.count(Comment.id)).filter(*on_target).scalar()
    return ok({
        "items": _build_trees(roots),
        "total": total,
        "has_more": has_more,
        "comment_count": comment_count,
        "page": page,
        "size": size,
    })


@bp.get("/comments/<int:cid>")
def get_comment(cid):
    rows = _rows(Comment.id == cid)
    if not rows:
        return fail("Comment not found", 404)
    tree = _build_trees(rows)[0]
    parent_id = rows[0][0].parent_id
    if parent_id is not None:
        parent = _rows(Comment.id == parent_id)[0]
        tree["reply_to"] = {"user_id": parent[0].user_id, "username": parent[1]}
    return ok(tree)


@bp.post("/comments")
@login_required
def create_comment(user):
    body = request.get_json(silent=True) or {}
    content = str(body.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return fail("Comment cannot be empty")
    if len(content) > MAX_CONTENT:
        return fail(f"Comment must be at most {MAX_CONTENT} characters")

    parent = None
    if body.get("parent_id") is not None:
        try:
            parent = db.session.get(Comment, int(body["parent_id"]))
        except (TypeError, ValueError):
            return fail("parent_id must be an integer")
        if parent is None:
            return fail("The comment you are replying to no longer exists", 404)
        # A reply lives on the same target as its parent; reject explicit mismatches.
        if ("target_type" in body and body["target_type"] != parent.target_type) or \
                ("target_id" in body and str(body["target_id"]) != str(parent.target_id)):
            return fail("Reply target does not match the parent comment")
        target_type, target_id = parent.target_type, parent.target_id
    else:
        target_type = str(body.get("target_type") or "").strip()
        target_id, err = _parse_target(target_type, body.get("target_id"))
        if err:
            return err

    comment = Comment(user_id=user.user_id, target_type=target_type, target_id=target_id,
                      content=content, parent_id=parent.id if parent else None)
    db.session.add(comment)
    db.session.commit()

    data = {**comment.to_dict(user.username), "replies": [], "reply_count": 0, "reply_to": None}
    if parent is not None:
        parent_author = db.session.get(User, parent.user_id)
        data["reply_to"] = {"user_id": parent.user_id,
                            "username": parent_author.username if parent_author else None}
    return ok(data, "Reply posted" if parent else "Comment posted", 201)


@bp.delete("/comments/<int:cid>")
@login_required
def delete_comment(user, cid):
    comment = db.session.get(Comment, cid)
    if comment is None:
        return fail("Comment not found", 404)
    if comment.user_id != user.user_id and not is_admin(user):
        return fail("You can only delete your own comments", 403)
    removed = delete_comment_trees([cid])
    db.session.commit()
    return ok({"deleted": removed}, "Comment deleted")
