"""Audio management API.

Public:  GET /api/music, GET /api/music/genres, GET /api/music/<id>
         GET /media/audio/<file>, GET /media/covers/<file>
User:    PUT /api/music/<id>/like, DELETE /api/music/<id>/like (idempotent like / unlike, see likes.py)
Comments on a track: see comments.py (target_type="music"); they are removed with the track.
Admin:   POST /api/music/inspect, POST /api/music, PUT /api/music/<id>, DELETE /api/music/<id>

Create/update take multipart/form-data with fields
  title, artist, genre, release_date (YYYY-MM-DD), lyrics (plain/LRC text, max 10,000 chars),
  audio_file (file), cover_image (file),
  remove_cover ("1" to drop the existing cover on update),
  use_embedded_cover ("0" to ignore cover art embedded in the audio file; default is to use it).

Metadata embedded in the audio file (see audiometa.py) is used as follows:
  - duration is always taken from the file;
  - on create, a blank title/artist falls back to the file's tags (title finally to the file name);
  - if the track ends up without a cover, the embedded cover art is saved as its cover.
POST /api/music/inspect returns the tags without saving anything, so the UI can pre-fill the form.
Embedded lyrics are only returned by inspect (for the form to pre-fill), never saved implicitly.

GET /api/music omits `lyrics` (each item has `has_lyrics`); GET /api/music/<id> includes them,
plus `like_count` and `liked` (whether the logged-in user likes it; false when logged out).
To edit only the lyrics, PUT /api/music/<id> with just the `lyrics` field (empty clears them).
"""
import base64
import uuid
from datetime import date
from pathlib import Path

from flask import Blueprint, abort, current_app, request, send_from_directory
from sqlalchemy import or_

from . import leaderboard
from .audiometa import read_metadata
from .comments import delete_target_comments
from .common import admin_required, current_user, fail, login_required, ok, paginate_args
from .likes import delete_likes, like_info, like_route
from .models import Music, db

bp = Blueprint("music", __name__)

AUDIO_EXTS = {"mp3", "wav", "ogg", "oga", "flac", "m4a", "aac", "webm"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
IMAGE_MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
MEDIA_KINDS = {"audio": AUDIO_EXTS, "covers": IMAGE_EXTS}
MAX_LYRICS = 10_000  # characters; fits a MySQL TEXT column even at 4 bytes/char


# ---------- file storage ----------

def media_dir(kind) -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_ext(storage):
    name = storage.filename or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def check_upload(storage, kind):
    """Return an error message if the uploaded file is unacceptable, else None."""
    ext = file_ext(storage)
    if ext not in MEDIA_KINDS[kind]:
        label = "Audio" if kind == "audio" else "Cover image"
        return f"{label} must be one of: {', '.join(sorted(MEDIA_KINDS[kind]))}"


def save_upload(storage, kind) -> str:
    # Never trust the client file name: store under a random name, keep only the extension.
    name = f"{uuid.uuid4().hex}.{file_ext(storage)}"
    storage.save(media_dir(kind) / name)
    return name


def save_bytes(data: bytes, ext, kind) -> str:
    name = f"{uuid.uuid4().hex}.{ext}"
    (media_dir(kind) / name).write_bytes(data)
    return name


def delete_upload(name, kind):
    if not name:
        return
    path = media_dir(kind) / Path(name).name
    try:
        path.unlink(missing_ok=True)
    except OSError:
        current_app.logger.warning("Could not delete %s", path)


@bp.get("/media/<kind>/<path:filename>")
def media(kind, filename):
    if kind not in MEDIA_KINDS:
        abort(404)
    # send_from_directory rejects path traversal and supports Range requests (audio seeking).
    return send_from_directory(media_dir(kind), filename, max_age=86400)


# ---------- validation ----------

def parse_fields(form, partial):
    """Validate text fields. Returns (values, error). With partial=True, absent fields are skipped."""
    values = {}

    def text(key, label, max_len, required):
        if partial and key not in form:
            return None
        v = (form.get(key) or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if required and not v:
            return f"{label} is required"
        if len(v) > max_len:
            return f"{label} must be at most {max_len} characters"
        values[key] = v or None

    err = (text("title", "Title", 200, True)
           or text("artist", "Artist", 120, True)
           or text("genre", "Genre", 50, False)
           or text("lyrics", "Lyrics", MAX_LYRICS, False))
    if err:
        return None, err

    if not (partial and "release_date" not in form):
        raw = (form.get("release_date") or "").strip()
        if raw:
            try:
                values["release_date"] = date.fromisoformat(raw)
            except ValueError:
                return None, "Release date must be in YYYY-MM-DD format"
        else:
            values["release_date"] = None
    return values, None


# ---------- public ----------

@bp.get("/api/music")
def list_music():
    page, size = paginate_args(request, default_size=20)
    keyword = (request.args.get("keyword") or "").strip()
    genre = (request.args.get("genre") or "").strip()
    sort = request.args.get("sort", "newest")

    q = Music.query
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(Music.title.ilike(like), Music.artist.ilike(like)))
    if genre:
        q = q.filter(Music.genre == genre)

    order = {
        "newest": [Music.id.desc()],
        "title": [Music.title.asc(), Music.id.asc()],
        "artist": [Music.artist.asc(), Music.title.asc()],
        "release": [Music.release_date.desc(), Music.id.desc()],
    }.get(sort, [Music.id.desc()])

    pager = q.order_by(*order).paginate(page=page, per_page=size, error_out=False)
    return ok({
        "items": [m.to_dict(with_lyrics=False) for m in pager.items],
        "total": pager.total,
        "page": page,
        "size": size,
    })


@bp.get("/api/music/genres")
def list_genres():
    rows = (db.session.query(Music.genre)
            .filter(Music.genre.isnot(None), Music.genre != "")
            .distinct().order_by(Music.genre).all())
    return ok([r[0] for r in rows])


@bp.get("/api/music/<int:mid>")
def get_music(mid):
    music = db.session.get(Music, mid)
    if music is None:
        return fail("Track not found", 404)
    return ok({**music.to_dict(), **like_info("music", music.id, current_user())})


# ---------- likes ----------

_set_like = like_route(Music, "music", "Track not found")


@bp.put("/api/music/<int:mid>/like")
@login_required
def like_music(user, mid):
    return _set_like(user, mid, True)


@bp.delete("/api/music/<int:mid>/like")
@login_required
def unlike_music(user, mid):
    return _set_like(user, mid, False)


# ---------- admin ----------

@bp.post("/api/music/inspect")
@admin_required
def inspect_audio(_admin):
    """Read tags from an audio file without storing it (used to pre-fill the upload form)."""
    audio = request.files.get("audio_file")
    if not audio or not audio.filename:
        return fail("Audio file is required")
    err = check_upload(audio, "audio")
    if err:
        return fail(err)
    meta = read_metadata(audio.stream)
    data = meta.public()
    data["cover"] = (f"data:{IMAGE_MIME[meta.cover_ext]};base64,{base64.b64encode(meta.cover).decode()}"
                     if meta.cover else None)
    return ok(data)


def wants_embedded_cover(form):
    return form.get("use_embedded_cover", "1") != "0" and form.get("remove_cover") != "1"


@bp.post("/api/music")
@admin_required
def create_music(_admin):
    audio = request.files.get("audio_file")
    cover = request.files.get("cover_image")
    has_cover = bool(cover and cover.filename)
    if not audio or not audio.filename:
        return fail("Audio file is required")
    err = check_upload(audio, "audio") or (has_cover and check_upload(cover, "covers"))
    if err:
        return fail(err)

    audio_name = save_upload(audio, "audio")
    saved = [(audio_name, "audio")]
    try:
        meta = read_metadata(media_dir("audio") / audio_name)

        # Blank title/artist fall back to the file's tags (title finally to the file name).
        form = request.form.to_dict()
        if not (form.get("title") or "").strip():
            form["title"] = meta.title or Path(audio.filename).stem[:200]
        if not (form.get("artist") or "").strip() and meta.artist:
            form["artist"] = meta.artist
        values, err = parse_fields(form, partial=False)
        if err:
            raise _Reject(err)

        music = Music(**values, audio_file=audio_name, duration=meta.duration)
        if has_cover:
            music.cover_image = save_upload(cover, "covers")
        elif meta.cover and wants_embedded_cover(request.form):
            music.cover_image = save_bytes(meta.cover, meta.cover_ext, "covers")
        if music.cover_image:
            saved.append((music.cover_image, "covers"))

        db.session.add(music)
        db.session.commit()
    except _Reject as e:
        _discard(saved)
        return fail(str(e))
    except Exception:
        db.session.rollback()
        _discard(saved)
        raise
    leaderboard.track_added(music.id)  # score 0
    return ok(music.to_dict(), "Track created", 201)


class _Reject(Exception):
    """Validation failure after files were already written (they get cleaned up)."""


def _discard(saved):
    for name, kind in saved:
        delete_upload(name, kind)


@bp.put("/api/music/<int:mid>")
@admin_required
def update_music(_admin, mid):
    music = db.session.get(Music, mid)
    if music is None:
        return fail("Track not found", 404)

    values, err = parse_fields(request.form, partial=True)
    if err:
        return fail(err)

    audio = request.files.get("audio_file")
    cover = request.files.get("cover_image")
    has_audio = bool(audio and audio.filename)
    has_cover = bool(cover and cover.filename)
    err = (has_audio and check_upload(audio, "audio")) or (has_cover and check_upload(cover, "covers"))
    if err:
        return fail(err)

    for k, v in values.items():
        setattr(music, k, v)

    old_audio, old_cover = music.audio_file, music.cover_image
    if has_cover:
        music.cover_image = save_upload(cover, "covers")
    elif request.form.get("remove_cover") == "1":
        music.cover_image = None
    if has_audio:
        music.audio_file = save_upload(audio, "audio")
        meta = read_metadata(media_dir("audio") / music.audio_file)
        music.duration = meta.duration
        # A replacement file can supply the cover if the track has none.
        if not music.cover_image and meta.cover and wants_embedded_cover(request.form):
            music.cover_image = save_bytes(meta.cover, meta.cover_ext, "covers")

    # Capture before commit: after a rollback the instance is expired and would reload old values.
    new_audio, new_cover = music.audio_file, music.cover_image
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        if new_audio != old_audio:
            delete_upload(new_audio, "audio")
        if new_cover and new_cover != old_cover:
            delete_upload(new_cover, "covers")
        raise
    # Remove replaced files only after the DB points at the new ones.
    if new_audio != old_audio:
        delete_upload(old_audio, "audio")
    if new_cover != old_cover:
        delete_upload(old_cover, "covers")
    return ok(music.to_dict(), "Track updated")


@bp.delete("/api/music/<int:mid>")
@admin_required
def delete_music(_admin, mid):
    music = db.session.get(Music, mid)
    if music is None:
        return fail("Track not found", 404)
    audio, cover = music.audio_file, music.cover_image
    # Comments and likes have no FK to the track (their target id is polymorphic).
    delete_target_comments("music", mid)  # also removes the likes on those comments
    delete_likes("music", [mid])
    db.session.delete(music)
    db.session.commit()
    leaderboard.track_removed(mid)
    delete_upload(audio, "audio")
    delete_upload(cover, "covers")
    return ok(msg="Track deleted")
