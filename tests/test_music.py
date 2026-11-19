import io

import pytest

from backend import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(client):
    assert client.post("/api/auth/login", json={"account": "admin", "password": "admin123"}).status_code == 200
    return client


def upload(name, data=b"fake-bytes"):
    return (io.BytesIO(data), name)


def create(c, **over):
    form = {
        "title": "Song A", "artist": "Artist A", "genre": "Pop", "release_date": "2024-05-01",
        "audio_file": upload("a.mp3", b"ID3audio"), "cover_image": upload("c.png", b"PNG"),
    }
    form.update(over)
    return c.post("/api/music", data=form, content_type="multipart/form-data")


def test_create_list_and_stream(admin, app, tmp_path):
    r = create(admin)
    assert r.status_code == 201, r.json
    m = r.json["data"]
    assert m["title"] == "Song A" and m["release_date"] == "2024-05-01"
    assert m["audio_url"].startswith("/media/audio/") and m["cover_url"].startswith("/media/covers/")

    audio = admin.get(m["audio_url"])
    assert audio.status_code == 200 and audio.data == b"ID3audio"
    ranged = admin.get(m["audio_url"], headers={"Range": "bytes=0-2"})
    assert ranged.status_code == 206 and ranged.data == b"ID3"

    create(admin, title="Other", artist="Someone", genre="Rock", audio_file=upload("b.ogg"), cover_image=upload("", b""))
    admin.post("/api/auth/logout")  # listing is public
    assert admin.get("/api/music").json["data"]["total"] == 2
    assert admin.get("/api/music?keyword=someone").json["data"]["total"] == 1
    assert admin.get("/api/music?genre=Pop").json["data"]["items"][0]["id"] == m["id"]
    assert admin.get("/api/music/genres").json["data"] == ["Pop", "Rock"]


def test_validation(admin):
    r = create(admin, artist="  ")  # untagged file: nothing to fall back to
    assert r.status_code == 400 and "Artist" in r.json["msg"]
    assert create(admin, release_date="2024-13-01").status_code == 400
    assert create(admin, audio_file=upload("evil.exe")).status_code == 400
    assert create(admin, cover_image=upload("x.mp3")).status_code == 400
    r = admin.post("/api/music", data={"title": "T", "artist": "A"}, content_type="multipart/form-data")
    assert r.status_code == 400 and "Audio" in r.json["msg"]


def test_permissions(client):
    assert create(client).status_code == 401
    client.post("/api/auth/register", json={"username": "bob", "email": "b@x.com", "password": "secret1"})
    assert create(client).status_code == 403
    assert client.delete("/api/music/1").status_code == 403


def test_update_replaces_files_and_delete_cleans_up(admin, tmp_path):
    m = create(admin).json["data"]
    audio_dir, cover_dir = tmp_path / "uploads" / "audio", tmp_path / "uploads" / "covers"
    old_audio = audio_dir / m["audio_file"]
    assert old_audio.exists()

    r = admin.put(f"/api/music/{m['id']}", data={
        "title": "Renamed", "audio_file": upload("new.flac"), "remove_cover": "1",
    }, content_type="multipart/form-data")
    assert r.status_code == 200, r.json
    u = r.json["data"]
    assert u["title"] == "Renamed" and u["artist"] == "Artist A"  # untouched field kept
    assert u["cover_url"] is None and not old_audio.exists()
    assert not list(cover_dir.iterdir())

    assert admin.put(f"/api/music/{m['id']}", data={"artist": ""},
                     content_type="multipart/form-data").status_code == 400

    assert admin.delete(f"/api/music/{m['id']}").status_code == 200
    assert not list(audio_dir.iterdir())
    assert admin.get(f"/api/music/{m['id']}").status_code == 404


def test_media_rejects_bad_paths(client):
    assert client.get("/media/secret/x.mp3").status_code == 404
    assert client.get("/media/audio/../../app.py").status_code == 404


def test_lyrics_create_update_clear(admin):
    lyrics = "[00:01.00]First line\r\n[00:05.50]第二行\r\n"
    m = create(admin, lyrics=lyrics).json["data"]
    assert m["lyrics"] == "[00:01.00]First line\n[00:05.50]第二行" and m["has_lyrics"] is True

    # The list stays light: only a flag; the detail endpoint carries the text.
    item = admin.get("/api/music").json["data"]["items"][0]
    assert "lyrics" not in item and item["has_lyrics"] is True
    assert admin.get(f"/api/music/{m['id']}").json["data"]["lyrics"].endswith("第二行")

    # Lyrics-only update leaves everything else alone.
    r = admin.put(f"/api/music/{m['id']}", data={"lyrics": "New words"}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.json["data"]["lyrics"] == "New words" and r.json["data"]["title"] == "Song A"
    # Updates without the field keep the lyrics.
    r = admin.put(f"/api/music/{m['id']}", data={"genre": "Rock"}, content_type="multipart/form-data")
    assert r.json["data"]["lyrics"] == "New words"
    # An empty value clears them.
    r = admin.put(f"/api/music/{m['id']}", data={"lyrics": "  "}, content_type="multipart/form-data")
    assert r.json["data"]["lyrics"] is None and r.json["data"]["has_lyrics"] is False


def test_lyrics_length_limit(admin):
    r = create(admin, lyrics="x" * 10_001)
    assert r.status_code == 400 and "Lyrics" in r.json["msg"]
    assert create(admin, lyrics="x" * 10_000).status_code == 201


# ---------- likes ----------

def register(c, name):
    r = c.post("/api/auth/register", json={
        "username": name, "email": f"{name}@x.com", "password": "secret1", "confirm": "secret1"})
    assert r.status_code in (200, 201), r.json
    return c


def test_like_unlike_and_count(admin, app):
    mid = create(admin).json["data"]["id"]
    d = admin.get(f"/api/music/{mid}").json["data"]
    assert d["like_count"] == 0 and d["liked"] is False

    r = admin.put(f"/api/music/{mid}/like")
    assert r.status_code == 200 and r.json["data"] == {"like_count": 1, "liked": True}
    # Liking twice is a no-op.
    assert admin.put(f"/api/music/{mid}/like").json["data"] == {"like_count": 1, "liked": True}

    other = register(app.test_client(), "bob")
    assert other.put(f"/api/music/{mid}/like").json["data"] == {"like_count": 2, "liked": True}
    assert other.delete(f"/api/music/{mid}/like").json["data"] == {"like_count": 1, "liked": False}
    assert other.delete(f"/api/music/{mid}/like").json["data"] == {"like_count": 1, "liked": False}

    # The count is public; `liked` is per user.
    anon = app.test_client().get(f"/api/music/{mid}").json["data"]
    assert anon["like_count"] == 1 and anon["liked"] is False
    assert admin.get(f"/api/music/{mid}").json["data"]["liked"] is True


def test_like_requires_login_and_existing_track(admin, app):
    mid = create(admin).json["data"]["id"]
    assert app.test_client().put(f"/api/music/{mid}/like").status_code == 401
    assert admin.put("/api/music/9999/like").status_code == 404


def test_likes_removed_with_track_and_user(admin, app):
    from backend.models import MusicLike
    mid = create(admin).json["data"]["id"]
    bob = register(app.test_client(), "bob")
    bob.put(f"/api/music/{mid}/like")
    admin.put(f"/api/music/{mid}/like")
    bob_id = bob.get("/api/auth/me").json["data"]["user_id"]

    assert admin.delete(f"/api/users/{bob_id}").status_code == 200
    assert admin.get(f"/api/music/{mid}").json["data"]["like_count"] == 1
    assert admin.delete(f"/api/music/{mid}").status_code == 200
    with app.app_context():
        assert MusicLike.query.count() == 0
