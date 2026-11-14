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
    assert create(admin, title="  ").status_code == 400
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
