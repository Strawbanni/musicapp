import redis

from backend import leaderboard
from test_comments import comment, reply  # noqa: F401  (helpers)
from test_music import admin, app, client, create, register  # noqa: F401  (fixtures)


def top(c, k=10):
    r = c.get("/api/leaderboard", query_string={"k": k})
    assert r.status_code == 200, r.json
    return [(x["music"]["id"], x["score"]) for x in r.json["data"]["items"]]


def zscores(app):
    key = app.config["LEADERBOARD_KEY"]
    return {int(m): int(s) for m, s in app.extensions["redis"].zrange(key, 0, -1, withscores=True)}


def test_create_adds_zero_and_delete_removes(admin, app):
    a = create(admin, title="A").json["data"]["id"]
    b = create(admin, title="B").json["data"]["id"]
    assert zscores(app) == {a: 0, b: 0}

    assert admin.delete(f"/api/music/{a}").status_code == 200
    assert zscores(app) == {b: 0}


def test_likes_and_comments_score(admin, app):
    a = create(admin, title="A").json["data"]["id"]
    b = create(admin, title="B").json["data"]["id"]
    bob = register(app.test_client(), "bob")

    assert bob.put(f"/api/music/{a}/like").status_code == 200
    assert bob.put(f"/api/music/{a}/like").status_code == 200  # idempotent: scored once
    admin.put(f"/api/music/{a}/like")
    assert zscores(app)[a] == 2

    root = comment(bob, b)              # +2
    r = reply(admin, root["id"])        # replies count too: +2
    assert zscores(app)[b] == 4
    admin.put(f"/api/comments/{root['id']}/like")  # liking a comment does not score the track
    assert zscores(app)[b] == 4

    bob.delete(f"/api/music/{a}/like")  # unlike: -1
    assert zscores(app)[a] == 1
    admin.delete(f"/api/comments/{r['id']}")
    assert zscores(app)[b] == 2
    admin.delete(f"/api/comments/{root['id']}")
    assert zscores(app)[b] == 0


def test_top_k(admin, app):
    ids = [create(admin, title=f"T{i}").json["data"]["id"] for i in range(4)]
    bob = register(app.test_client(), "bob")
    bob.put(f"/api/music/{ids[1]}/like")        # 1
    comment(bob, ids[2])                        # 2
    comment(bob, ids[3])
    bob.put(f"/api/music/{ids[3]}/like")        # 3

    anon = app.test_client()  # public
    assert top(anon, 3) == [(ids[3], 3), (ids[2], 2), (ids[1], 1)]
    items = anon.get("/api/leaderboard?k=2").json["data"]["items"]
    assert [x["rank"] for x in items] == [1, 2] and items[0]["music"]["title"] == "T3"
    assert "lyrics" not in items[0]["music"]
    assert len(top(anon)) == 4  # default k=10, fewer tracks than k

    for bad in ("0", "101", "abc"):
        assert anon.get(f"/api/leaderboard?k={bad}").status_code == 400


def test_deleting_user_takes_back_their_points(admin, app):
    a = create(admin).json["data"]["id"]
    bob = register(app.test_client(), "bob")
    bob.put(f"/api/music/{a}/like")
    root = comment(admin, a)
    reply(bob, root["id"])
    assert zscores(app)[a] == 5
    uid = bob.get("/api/auth/me").json["data"]["user_id"]
    assert admin.delete(f"/api/users/{uid}").status_code == 200
    assert zscores(app)[a] == 2


def test_rebuild_restores_scores(admin, app):
    a = create(admin).json["data"]["id"]
    comment(admin, a)
    admin.put(f"/api/music/{a}/like")
    r = app.extensions["redis"]
    r.delete(app.config["LEADERBOARD_KEY"])
    r.zadd(app.config["LEADERBOARD_KEY"], {"999": 50})  # stale entry for a track that no longer exists

    assert app.test_client().post("/api/leaderboard/rebuild").status_code == 401
    assert admin.post("/api/leaderboard/rebuild").status_code == 200
    assert zscores(app) == {a: 3}


def test_redis_down_does_not_break_requests(admin, app, monkeypatch):
    def boom(*a, **kw):
        raise redis.ConnectionError("down")
    monkeypatch.setattr(app.extensions["redis"], "zadd", boom)
    monkeypatch.setattr(app.extensions["redis"], "zrevrange", boom)

    assert create(admin).status_code == 201  # track still created
    assert admin.get("/api/leaderboard").status_code == 503
    monkeypatch.undo()
    with app.app_context():
        leaderboard.rebuild()
    assert len(zscores(app)) == 1
