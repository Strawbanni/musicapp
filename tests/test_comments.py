import pytest

from backend.models import Comment, Like
from test_music import admin, app, client, create, register  # noqa: F401  (fixtures)


@pytest.fixture
def track(admin):
    return create(admin).json["data"]["id"]


def post(c, content, **kw):
    return c.post("/api/comments", json={"content": content, **kw})


def comment(c, mid, content="hi"):
    r = post(c, content, target_type="music", target_id=mid)
    assert r.status_code == 201, r.json
    return r.json["data"]


def reply(c, parent_id, content="re"):
    r = post(c, content, parent_id=parent_id)
    assert r.status_code == 201, r.json
    return r.json["data"]


def listing(c, mid, **params):
    r = c.get("/api/comments", query_string={"target_type": "music", "target_id": mid, **params})
    assert r.status_code == 200, r.json
    return r.json["data"]


def test_comment_and_nested_replies(admin, app, track):
    bob = register(app.test_client(), "bob")
    root = comment(admin, track, "  great song \r\n line2 ")
    assert root["content"] == "great song \n line2" and root["username"] == "admin"
    assert root["parent_id"] is None and root["reply_to"] is None

    r1 = reply(bob, root["id"], "agree")
    assert r1["target_type"] == "music" and r1["target_id"] == track
    assert r1["reply_to"]["username"] == "admin"
    r2 = reply(admin, r1["id"], "thanks bob")
    r3 = reply(bob, r2["id"], "deep")
    reply(bob, root["id"], "second reply")

    data = listing(app.test_client(), track)  # listing is public
    assert data["total"] == 1 and data["comment_count"] == 5
    tree = data["items"][0]
    assert tree["reply_count"] == 4
    assert [x["content"] for x in tree["replies"]] == ["agree", "second reply"]  # oldest first
    lvl2 = tree["replies"][0]["replies"][0]
    assert lvl2["id"] == r2["id"] and lvl2["reply_to"]["username"] == "bob"
    assert lvl2["replies"][0]["id"] == r3["id"] and lvl2["replies"][0]["reply_to"]["username"] == "admin"

    single = app.test_client().get(f"/api/comments/{r2['id']}").json["data"]
    assert single["reply_to"]["username"] == "bob" and single["reply_count"] == 1

    with app.app_context():  # stored with the requested column names
        assert {c.name for c in Comment.__table__.columns} == {
            "id", "userId", "targetType", "targetId", "publishTime", "content", "parentId"}


def test_roots_paginated_newest_first(admin, track):
    for i in range(5):
        comment(admin, track, f"c{i}")
    page1 = listing(admin, track, size=2)
    assert page1["total"] == 5 and [x["content"] for x in page1["items"]] == ["c4", "c3"]
    last = listing(admin, track, size=2, page=3)
    assert [x["content"] for x in last["items"]] == ["c0"] and not last["has_more"]

    # Cursor paging is unaffected by comments posted after the first page was loaded.
    comment(admin, track, "new")
    page2 = listing(admin, track, size=2, before_id=page1["items"][-1]["id"])
    assert [x["content"] for x in page2["items"]] == ["c2", "c1"] and page2["has_more"]
    page3 = listing(admin, track, size=2, before_id=page2["items"][-1]["id"])
    assert [x["content"] for x in page3["items"]] == ["c0"] and not page3["has_more"]


def test_validation_and_permissions(admin, app, track):
    anon = app.test_client()
    assert post(anon, "x", target_type="music", target_id=track).status_code == 401
    assert post(admin, "   ", target_type="music", target_id=track).status_code == 400
    assert post(admin, "x" * 1001, target_type="music", target_id=track).status_code == 400
    assert post(admin, "x", target_type="video", target_id=track).status_code == 400
    assert post(admin, "x", target_type="music", target_id="abc").status_code == 400
    assert post(admin, "x", target_type="music", target_id=9999).status_code == 404
    assert post(admin, "x", parent_id=9999).status_code == 404
    root = comment(admin, track)
    other = create(admin, title="Other").json["data"]["id"]
    assert post(admin, "x", parent_id=root["id"], target_type="music", target_id=other).status_code == 400
    assert anon.get("/api/comments?target_type=music&target_id=9999").status_code == 404
    assert anon.get("/api/comments/9999").status_code == 404

    bob = register(app.test_client(), "bob")
    assert bob.delete(f"/api/comments/{root['id']}").status_code == 403
    assert anon.delete(f"/api/comments/{root['id']}").status_code == 401


def test_delete_removes_subtree(admin, app, track):
    bob = register(app.test_client(), "bob")
    root = comment(admin, track)
    keep = comment(admin, track, "keep")
    parent = root
    for i in range(20):  # deeper than MySQL's 15-level cascade limit
        parent = reply(bob if i % 2 else admin, parent["id"], f"r{i}")

    mid_reply = reply(bob, root["id"], "bob's")
    reply(admin, mid_reply["id"], "under bob's")
    assert bob.delete(f"/api/comments/{mid_reply['id']}").json["data"] == {"deleted": 2}

    assert admin.delete(f"/api/comments/{root['id']}").json["data"] == {"deleted": 21}  # admin may delete
    data = listing(admin, track)
    assert data["comment_count"] == 1 and data["items"][0]["id"] == keep["id"]


def test_comments_removed_with_track_and_user(admin, app, track):
    bob = register(app.test_client(), "bob")
    bob_id = bob.get("/api/auth/me").json["data"]["user_id"]
    a = comment(admin, track, "admin root")
    b = comment(bob, track, "bob root")
    reply(admin, b["id"], "admin under bob")     # goes with bob's thread
    reply(bob, a["id"], "bob under admin")       # bob's own reply goes too
    survivor = reply(admin, a["id"], "admin under admin")

    assert admin.delete(f"/api/users/{bob_id}").status_code == 200
    data = listing(admin, track)
    assert data["comment_count"] == 2
    assert [r["id"] for r in data["items"][0]["replies"]] == [survivor["id"]]

    assert admin.delete(f"/api/music/{track}").status_code == 200
    with app.app_context():
        assert Comment.query.count() == 0


# ---------- likes ----------

def test_like_multiple_comments(admin, app, track):
    bob = register(app.test_client(), "bob")
    root = comment(admin, track, "first")
    other = comment(admin, track, "second")
    r1 = reply(bob, root["id"])
    assert root["like_count"] == 0 and root["liked"] is False

    # A user can like any number of comments, including replies; liking twice is a no-op.
    for cid in (root["id"], other["id"], r1["id"]):
        r = bob.put(f"/api/comments/{cid}/like")
        assert r.status_code == 200 and r.json["data"] == {"like_count": 1, "liked": True}
    assert bob.put(f"/api/comments/{root['id']}/like").json["data"] == {"like_count": 1, "liked": True}
    assert admin.put(f"/api/comments/{root['id']}/like").json["data"] == {"like_count": 2, "liked": True}
    assert admin.delete(f"/api/comments/{root['id']}/like").json["data"] == {"like_count": 1, "liked": False}
    assert admin.delete(f"/api/comments/{root['id']}/like").json["data"] == {"like_count": 1, "liked": False}

    # Listings carry the count and the viewer's own `liked` on every node of the tree.
    items = {c["id"]: c for c in listing(bob, track)["items"]}
    assert items[root["id"]]["liked"] and items[other["id"]]["liked"]
    assert items[root["id"]]["replies"][0]["like_count"] == 1 and items[root["id"]]["replies"][0]["liked"]
    anon = {c["id"]: c for c in listing(app.test_client(), track)["items"]}
    assert anon[root["id"]]["like_count"] == 1 and anon[root["id"]]["liked"] is False
    assert bob.get(f"/api/comments/{r1['id']}").json["data"]["liked"] is True

    # Comment likes don't touch the track's likes, and vice versa.
    assert bob.get(f"/api/music/{track}").json["data"]["like_count"] == 0


def test_comment_like_requires_login_and_existing_comment(admin, app, track):
    c = comment(admin, track)
    assert app.test_client().put(f"/api/comments/{c['id']}/like").status_code == 401
    assert admin.put("/api/comments/9999/like").status_code == 404


def test_comment_likes_removed_with_comment_track_and_user(admin, app, track):
    bob = register(app.test_client(), "bob")
    root = comment(admin, track)
    child = reply(bob, root["id"])
    keep = comment(bob, track)
    for cid in (root["id"], child["id"], keep["id"]):
        admin.put(f"/api/comments/{cid}/like")
        bob.put(f"/api/comments/{cid}/like")
    admin.put(f"/api/music/{track}/like")

    assert admin.delete(f"/api/comments/{root['id']}").status_code == 200  # with its reply
    with app.app_context():
        assert {lk.target_id for lk in Like.query.filter_by(target_type="comment")} == {keep["id"]}

    bob_id = bob.get("/api/auth/me").json["data"]["user_id"]
    assert admin.delete(f"/api/users/{bob_id}").status_code == 200  # bob's comment and likes go
    with app.app_context():
        assert Like.query.filter_by(target_type="comment").count() == 0
        assert Like.query.count() == 1  # admin's like on the track
    last = comment(admin, track)
    admin.put(f"/api/comments/{last['id']}/like")

    assert admin.delete(f"/api/music/{track}").status_code == 200
    with app.app_context():
        assert Like.query.count() == 0
