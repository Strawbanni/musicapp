import pytest

from backend.models import Notification
from test_comments import comment, reply, track  # noqa: F401  (fixture + helpers)
from test_music import admin, app, client, register  # noqa: F401  (fixtures)


def unread(c):
    r = c.get("/api/notifications/unread-count")
    assert r.status_code == 200, r.json
    return r.json["data"]["unread_count"]


def inbox(c, **params):
    r = c.get("/api/notifications", query_string=params)
    assert r.status_code == 200, r.json
    return r.json["data"]


@pytest.fixture
def bob(app):
    return register(app.test_client(), "bob")


def test_reply_and_like_notify_comment_author(admin, bob, track):
    root = comment(admin, track, "admin's comment")
    assert unread(admin) == 0

    r = reply(bob, root["id"], "nice one")
    assert bob.put(f"/api/comments/{root['id']}/like").status_code == 200
    assert bob.put(f"/api/comments/{root['id']}/like").status_code == 200  # idempotent: no duplicate

    data = inbox(admin)
    assert data["unread_count"] == 2 and data["total"] == 2
    like, rep = data["items"]  # newest first
    assert like["type"] == "like" and like["comment"]["id"] == root["id"]
    # The operated target of a reply is the replied-to comment, not the reply.
    assert rep["type"] == "reply" and rep["comment"]["id"] == root["id"]
    assert rep["comment"]["content"] == "admin's comment"
    with admin.application.app_context():
        rows = {(n.operation_type, n.target_type, n.target_id) for n in Notification.query}
    assert rows == {("like", "comment", root["id"]), ("reply", "comment", root["id"])}
    assert r["parent_id"] == root["id"]
    for n in (like, rep):
        assert n["actor"]["username"] == "bob" and n["is_read"] is False
        assert n["target"] == {"type": "music", "id": track, "title": "Song A"}
    assert unread(bob) == 0


def test_own_actions_and_track_likes_do_not_notify(admin, bob, track):
    root = comment(admin, track)
    reply(admin, root["id"])
    admin.put(f"/api/comments/{root['id']}/like")
    bob.put(f"/api/music/{track}/like")  # tracks have no owner
    comment(bob, track)  # top-level comment on a track: nobody to notify
    assert unread(admin) == 0 and unread(bob) == 0


def test_unlike_withdraws_like_notification(admin, bob, track):
    root = comment(admin, track)
    bob.put(f"/api/comments/{root['id']}/like")
    assert unread(admin) == 1
    bob.delete(f"/api/comments/{root['id']}/like")
    assert unread(admin) == 0 and inbox(admin)["total"] == 0
    bob.put(f"/api/comments/{root['id']}/like")  # liking again notifies once more
    assert unread(admin) == 1


def test_mark_read(admin, bob, app, track):
    root = comment(admin, track)
    reply(bob, root["id"], "a")
    reply(bob, root["id"], "b")
    first, second = inbox(admin)["items"]

    assert bob.put(f"/api/notifications/{first['id']}/read").status_code == 404  # not bob's
    r = admin.put(f"/api/notifications/{first['id']}/read")
    assert r.status_code == 200 and r.json["data"]["unread_count"] == 1
    only_unread = inbox(admin, unread_only=1)
    assert only_unread["total"] == 1 and only_unread["items"][0]["id"] == second["id"]

    r = admin.put("/api/notifications/read-all")
    assert r.json["data"] == {"unread_count": 0, "updated": 1}
    assert all(n["is_read"] for n in inbox(admin)["items"])


def test_login_required(client):
    assert client.get("/api/notifications").status_code == 401
    assert client.get("/api/notifications/unread-count").status_code == 401
    assert client.put("/api/notifications/read-all").status_code == 401


def test_deleting_comments_tracks_and_users_cleans_up(admin, bob, app, track):
    root = comment(admin, track)
    r = reply(bob, root["id"])
    bob.put(f"/api/comments/{root['id']}/like")
    assert unread(admin) == 2

    bob.delete(f"/api/comments/{r['id']}")  # bob removes his reply
    assert unread(admin) == 1

    carol = register(app.test_client(), "carol")
    carol.put(f"/api/comments/{root['id']}/like")
    bob_id = bob.get("/api/auth/me").json["data"]["user_id"]
    assert admin.delete(f"/api/users/{bob_id}").status_code == 200
    assert inbox(admin)["items"][0]["actor"]["username"] == "carol" and unread(admin) == 1

    assert admin.delete(f"/api/music/{track}").status_code == 200  # removes the comment too
    with app.app_context():
        assert Notification.query.count() == 0


def test_table_columns(app):
    from sqlalchemy import inspect

    from backend.models import db
    with app.app_context():
        cols = [c["name"] for c in inspect(db.engine).get_columns("notifications")]
    assert cols == ["id", "sourceUserId", "targetUserId", "operationType", "targetType", "targetId",
                    "isRead", "createTime"]


def test_deleting_one_of_several_replies_removes_one_notification(admin, bob, app, track):
    root = comment(admin, track)
    r1 = reply(bob, root["id"], "first")
    reply(bob, root["id"], "second")
    nested = reply(admin, r1["id"], "admin answers bob")  # notifies bob, targets r1
    assert unread(admin) == 2 and unread(bob) == 1

    assert bob.delete(f"/api/comments/{r1['id']}").status_code == 200  # takes `nested` with it
    assert unread(admin) == 1 and unread(bob) == 0
    with app.app_context():
        left = Notification.query.one()
        assert (left.operation_type, left.target_id) == ("reply", root["id"])
    assert nested["parent_id"] == r1["id"]


_OLD_SCHEMA = """
    CREATE TABLE users (user_id INTEGER PRIMARY KEY, username VARCHAR(32) NOT NULL UNIQUE,
        password VARCHAR(255) NOT NULL, email VARCHAR(120) NOT NULL UNIQUE,
        is_active BOOLEAN NOT NULL, created_at DATETIME NOT NULL);
    INSERT INTO users VALUES (1, 'admin', 'x', 'a@x', 1, '2026-01-01 00:00:00'),
                             (2, 'bob', 'x', 'b@x', 1, '2026-01-01 00:00:00');
    CREATE TABLE comments (id INTEGER PRIMARY KEY, userId INTEGER NOT NULL,
        targetType VARCHAR(20) NOT NULL, targetId INTEGER NOT NULL, publishTime DATETIME NOT NULL,
        content TEXT NOT NULL, parentId INTEGER);
    INSERT INTO comments VALUES (3, 1, 'music', 1, '2026-02-03 04:00:00', 'root', NULL),
                                (4, 2, 'music', 1, '2026-02-03 04:05:00', 'reply', 3);
"""


@pytest.mark.parametrize("notifications_ddl", [
    # the first layout
    """CREATE TABLE notifications (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
           actor_id INTEGER NOT NULL, kind VARCHAR(20) NOT NULL, comment_id INTEGER NOT NULL,
           is_read BOOLEAN NOT NULL, created_at DATETIME NOT NULL);
       CREATE INDEX ix_notifications_user_read ON notifications (user_id, is_read);
       CREATE INDEX ix_notifications_actor_id ON notifications (actor_id);""",
    # the layout without operationType (targetType held like/reply)
    """CREATE TABLE notifications (id INTEGER PRIMARY KEY, sourceUserId INTEGER NOT NULL,
           targetUserId INTEGER NOT NULL, targetType VARCHAR(20) NOT NULL, targetId INTEGER NOT NULL,
           isRead BOOLEAN NOT NULL, createTime DATETIME NOT NULL);
       CREATE INDEX ix_notifications_target_user_read ON notifications (targetUserId, isRead);
       CREATE INDEX ix_notifications_target ON notifications (targetType, targetId);""",
])
def test_old_notifications_table_is_migrated(tmp_path, notifications_ddl):
    import sqlite3

    from sqlalchemy import inspect

    from backend import create_app
    from backend.models import db

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    # Old rows: (id, recipient, actor, operation, comment id = the reply / liked comment, read, time)
    # bob (2) replied to admin's comment 3 with comment 4; admin liked comment 4; 9 is orphaned.
    rows = [(7, 1, 2, "reply", 4, 0, "2026-02-03 04:05:06"),
            (8, 2, 1, "like", 4, 1, "2026-02-03 04:06:00"),
            (9, 1, 2, "like", 99, 0, "2026-02-03 04:07:00")]
    if "kind" not in notifications_ddl:  # that layout stores the actor before the recipient
        rows = [(i, actor, to, *rest) for i, to, actor, *rest in rows]
    con.executescript(_OLD_SCHEMA + notifications_ddl)
    con.executemany("INSERT INTO notifications VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
                      "UPLOAD_FOLDER": str(tmp_path / "uploads")})
    with app.app_context():
        rows = [(n.id, n.source_user_id, n.target_user_id, n.operation_type, n.target_type, n.target_id,
                 n.is_read, n.create_time.isoformat(sep=" "))
                for n in Notification.query.order_by(Notification.id)]
        assert rows == [
            (7, 2, 1, "reply", "comment", 3, False, "2026-02-03 04:05:06"),  # now targets the parent
            (8, 1, 2, "like", "comment", 4, True, "2026-02-03 04:06:00"),
        ]  # 9 referenced a missing comment and is dropped
        assert "notifications_old" not in inspect(db.engine).get_table_names()
