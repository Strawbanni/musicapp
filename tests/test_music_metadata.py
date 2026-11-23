"""Auto-filling track data from the audio file's own tags (mutagen)."""
import base64
import io
import sqlite3
import wave

import pytest
from mutagen.id3 import APIC, TCON, TDRC, TIT2, TPE1, USLT
from mutagen.wave import WAVE

from backend import create_app

PNG = base64.b64decode(  # 1x1 PNG
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")


def make_wav(tmp_path, seconds=3, cover=None, **tags):
    """A real WAV file of `seconds` length with optional ID3 tags / embedded cover."""
    path = tmp_path / f"src_{len(list(tmp_path.iterdir()))}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * seconds)
    if tags or cover:
        f = WAVE(path)
        f.add_tags()
        frames = {"title": TIT2, "artist": TPE1, "genre": TCON, "date": TDRC}
        for k, v in tags.items():
            if k == "lyrics":
                f.tags.add(USLT(encoding=3, lang="eng", desc="", text=v))
            else:
                f.tags.add(frames[k](encoding=3, text=v))
        if cover:
            f.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=cover))
        f.save()
    return path.read_bytes()


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                       "UPLOAD_FOLDER": str(tmp_path / "uploads")})


@pytest.fixture
def admin(app):
    c = app.test_client()
    assert c.post("/api/auth/login", json={"account": "admin", "password": "admin123"}).status_code == 200
    return c


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    return d


def post(c, url, **form):
    data = {k: (io.BytesIO(v[1]), v[0]) if isinstance(v, tuple) else v for k, v in form.items()}
    return c.post(url, data=data, content_type="multipart/form-data")


def test_inspect_reads_tags_duration_and_cover(admin, src):
    wav = make_wav(src, seconds=3, cover=PNG, title="Blue Sky", artist="Nova", genre="Pop", date="2023-07-14")
    r = post(admin, "/api/music/inspect", audio_file=("blue.wav", wav))
    assert r.status_code == 200, r.json
    d = r.json["data"]
    assert (d["title"], d["artist"], d["genre"]) == ("Blue Sky", "Nova", "Pop")
    assert (d["release_date"], d["release_year"], d["duration"]) == ("2023-07-14", 2023, 3)
    assert d["cover"] == "data:image/png;base64," + base64.b64encode(PNG).decode()


def test_inspect_year_only_and_untagged(admin, src):
    d = post(admin, "/api/music/inspect", audio_file=("y.wav", make_wav(src, date="1999"))).json["data"]
    assert d["release_date"] is None and d["release_year"] == 1999

    d = post(admin, "/api/music/inspect", audio_file=("plain.wav", make_wav(src, seconds=2))).json["data"]
    assert d["title"] is None and d["cover"] is None and d["duration"] == 2

    # Garbage with an audio extension is not an error, just no metadata.
    d = post(admin, "/api/music/inspect", audio_file=("junk.mp3", b"not audio")).json["data"]
    assert d["duration"] is None


def test_inspect_requires_admin(app, src):
    c = app.test_client()
    assert post(c, "/api/music/inspect", audio_file=("a.wav", make_wav(src))).status_code == 401


def test_create_falls_back_to_tags_and_embedded_cover(admin, src):
    wav = make_wav(src, seconds=4, cover=PNG, title="Tagged Title", artist="Tagged Artist")
    r = post(admin, "/api/music", title="", artist="", audio_file=("x.wav", wav))
    assert r.status_code == 201, r.json
    m = r.json["data"]
    assert (m["title"], m["artist"], m["duration"]) == ("Tagged Title", "Tagged Artist", 4)
    assert m["cover_url"] and admin.get(m["cover_url"]).data == PNG

    # Form values win over tags; use_embedded_cover=0 skips the embedded art.
    r = post(admin, "/api/music", title="Mine", artist="Me", use_embedded_cover="0", audio_file=("x.wav", wav))
    m = r.json["data"]
    assert (m["title"], m["artist"], m["cover_url"]) == ("Mine", "Me", None)


def test_create_untagged_uses_filename_and_cleans_up_on_error(admin, src, tmp_path):
    wav = make_wav(src)
    r = post(admin, "/api/music", title="", artist="Someone", audio_file=("My Song.wav", wav))
    assert r.status_code == 201 and r.json["data"]["title"] == "My Song"

    audio_dir = tmp_path / "uploads" / "audio"
    before = set(audio_dir.iterdir())
    r = post(admin, "/api/music", title="", artist="", audio_file=("x.wav", wav))
    assert r.status_code == 400 and "Artist" in r.json["msg"]
    assert set(audio_dir.iterdir()) == before  # rejected upload was removed


def test_update_new_audio_refreshes_duration_and_fills_missing_cover(admin, src):
    m = post(admin, "/api/music", title="T", artist="A", audio_file=("a.wav", make_wav(src, seconds=2))).json["data"]
    assert m["duration"] == 2 and m["cover_url"] is None

    r = admin.put(f"/api/music/{m['id']}", data={"audio_file": (io.BytesIO(make_wav(src, seconds=5, cover=PNG)), "b.wav")},
                  content_type="multipart/form-data")
    u = r.json["data"]
    assert u["duration"] == 5 and u["cover_url"] and u["title"] == "T"  # tags don't overwrite on edit


def test_schema_upgrade_adds_duration_column(tmp_path):
    db_file = tmp_path / "old.db"
    con = sqlite3.connect(db_file)
    con.execute("""CREATE TABLE music (id INTEGER PRIMARY KEY, title VARCHAR(200) NOT NULL,
        artist VARCHAR(120) NOT NULL, genre VARCHAR(50), release_date DATE, audio_file VARCHAR(255) NOT NULL,
        cover_image VARCHAR(255), created_at DATETIME NOT NULL)""")
    con.execute("INSERT INTO music VALUES (1,'Old','A',NULL,NULL,'x.mp3',NULL,'2024-01-01 00:00:00')")
    con.commit()
    con.close()

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file.as_posix()}",
                      "UPLOAD_FOLDER": str(tmp_path / "up")})
    m = app.test_client().get("/api/music/1").json["data"]
    assert m["title"] == "Old" and m["duration"] is None and m["lyrics"] is None


def test_inspect_reads_embedded_lyrics_but_create_does_not_save_them(admin, src):
    wav = make_wav(src, title="Song", artist="A", lyrics="Line one\rLine two\r")
    d = post(admin, "/api/music/inspect", audio_file=("s.wav", wav)).json["data"]
    assert d["lyrics"] == "Line one\nLine two"
    # Saving is explicit: the form pre-fills from inspect; a blank field means "no lyrics".
    m = post(admin, "/api/music", audio_file=("s.wav", wav)).json["data"]
    assert m["lyrics"] is None
