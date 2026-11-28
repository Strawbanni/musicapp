"""Track leaderboard kept in a Redis sorted set (ZSet).

Key: config LEADERBOARD_KEY (default "musicapp:leaderboard:music"); member = track id (string),
score = popularity:
    a track is added with score 0 when it is created and removed when it is deleted;
    each like on the track            +1  (unlike: -1)
    each comment on the track         +2  (replies count too: they live on the same track;
                                            deleting a comment takes back 2 per removed comment)
Likes on comments do not change a track's score.

Public:  GET  /api/leaderboard?k=10        Top-K tracks (1 <= k <= 100), highest score first:
                                           {k, items: [{rank, score, music}]}; music is the usual
                                           track dict without lyrics.
Admin:   POST /api/leaderboard/rebuild     Recompute every score from the database.

The database is the source of truth. Hooks run after the database commit and never fail the
request: if Redis is unreachable the change is logged and skipped, and the next rebuild (done at
every app start, or via the admin route) restores the exact scores. Increments use
ZADD XX INCR, so a late increment can never re-create a track that was already removed.
"""
import functools

import redis
from flask import Blueprint, current_app, request

from .common import admin_required, fail, ok
from .models import Comment, Like, Music, db

bp = Blueprint("leaderboard", __name__, url_prefix="/api")

LIKE_POINTS = 1
COMMENT_POINTS = 2
TARGET = "music"  # likes/comments with this target_type score for tracks
MAX_K = 100
DEFAULT_KEY = "musicapp:leaderboard:music"


def init_app(app):
    """Attach the Redis client (tests pass a fake one as REDIS_CLIENT)."""
    client = app.config.get("REDIS_CLIENT")
    if client is None:
        client = redis.Redis.from_url(app.config["REDIS_URL"], decode_responses=True,
                                      socket_connect_timeout=2, socket_timeout=2)
    app.extensions["redis"] = client
    app.config.setdefault("LEADERBOARD_KEY", DEFAULT_KEY)


def _redis():
    return current_app.extensions["redis"]


def _key():
    return current_app.config["LEADERBOARD_KEY"]


def _best_effort(fn):
    """Run a Redis update; on a Redis error log it and return None instead of failing the request."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except redis.RedisError as e:
            current_app.logger.warning("Leaderboard update %s%s skipped: %s", fn.__name__, args, e)
            return None
    return wrapper


# ---------- updates (call after the database commit) ----------

@_best_effort
def track_added(music_id):
    _redis().zadd(_key(), {str(music_id): 0})


@_best_effort
def track_removed(music_id):
    _redis().zrem(_key(), str(music_id))


@_best_effort
def add_points(music_id, points):
    """Change a track's score; does nothing if the track is not on the leaderboard."""
    if points:
        _redis().zadd(_key(), {str(music_id): points}, xx=True, incr=True)


def like_changed(music_id, liked):
    add_points(music_id, LIKE_POINTS if liked else -LIKE_POINTS)


def comments_added(music_id, count=1):
    add_points(music_id, COMMENT_POINTS * count)


def comments_removed(music_id, count):
    add_points(music_id, -COMMENT_POINTS * count)


# ---------- exact scores from the database ----------

def db_scores(music_ids=None):
    """{music_id: score} computed from the likes and comments tables (all tracks if ids is None)."""
    q = db.session.query(Music.id)
    if music_ids is not None:
        q = q.filter(Music.id.in_(list(music_ids)))
    scores = {mid: 0 for (mid,) in q}
    if not scores:
        return scores
    for model, target_type, target_id, points in (
            (Like, Like.target_type, Like.target_id, LIKE_POINTS),
            (Comment, Comment.target_type, Comment.target_id, COMMENT_POINTS)):
        rows = (db.session.query(target_id, db.func.count()).select_from(model)
                .filter(target_type == TARGET, target_id.in_(list(scores)))
                .group_by(target_id))
        for mid, n in rows:
            scores[mid] += points * n
    return scores


@_best_effort
def refresh(music_ids):
    """Set the exact scores of some tracks (used when one change touches many tracks)."""
    music_ids = set(music_ids)
    if not music_ids:
        return
    scores = db_scores(music_ids)
    pipe = _redis().pipeline()
    if scores:
        pipe.zadd(_key(), {str(mid): s for mid, s in scores.items()})
    gone = music_ids - set(scores)
    if gone:
        pipe.zrem(_key(), *map(str, gone))
    pipe.execute()


def rebuild():
    """Replace the whole ZSet with scores computed from the database (atomic MULTI/EXEC).
    Returns the number of tracks; raises redis.RedisError if Redis is unreachable."""
    scores = db_scores()
    pipe = _redis().pipeline(transaction=True)
    pipe.delete(_key())
    if scores:
        pipe.zadd(_key(), {str(mid): s for mid, s in scores.items()})
    pipe.execute()
    return len(scores)


# ---------- routes ----------

@bp.get("/leaderboard")
def top_k():
    raw = request.args.get("k", "10").strip()
    k = int(raw) if raw.isdigit() else 0  # type=int would silently fall back to the default on "abc"
    if not 1 <= k <= MAX_K:
        return fail(f"k must be an integer between 1 and {MAX_K}")
    try:
        entries = _redis().zrevrange(_key(), 0, k - 1, withscores=True)
    except redis.RedisError as e:
        current_app.logger.warning("Leaderboard read failed: %s", e)
        return fail("Leaderboard is temporarily unavailable", 503)

    ids = [int(member) for member, _ in entries]
    tracks = {m.id: m for m in Music.query.filter(Music.id.in_(ids))} if ids else {}
    items = []
    for member, score in entries:
        music = tracks.get(int(member))
        if music is None:
            continue  # deleted while Redis was unreachable; dropped by the next rebuild
        items.append({"rank": len(items) + 1, "score": int(score), "music": music.to_dict(with_lyrics=False)})
    return ok({"k": k, "items": items})


@bp.post("/leaderboard/rebuild")
@admin_required
def rebuild_route(_admin):
    try:
        count = rebuild()
    except redis.RedisError as e:
        return fail(f"Redis is unavailable: {e}", 503)
    return ok({"tracks": count}, "Leaderboard rebuilt")
