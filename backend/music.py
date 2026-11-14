"""Audio management API.

Public:  GET /api/music, GET /api/music/genres, GET /api/music/<id>
         GET /media/audio/<file>, GET /media/covers/<file>
Admin:   POST /api/music, PUT /api/music/<id>, DELETE /api/music/<id>

Create/update take multipart/form-data with fields
  title, artist, genre, release_date (YYYY-MM-DD), audio_file (file), cover_image (file),
  remove_cover ("1" to drop the existing cover on update).
"""
import uuid
from datetime import date
from pathlib import Path

from flask import Blueprint, abort, current_app, request, send_from_directory
from sqlalchemy import or_

from .common import admin_required, fail, ok, paginate_args
from .models import Music, db

bp = Blueprint("music", __name__)

AUDIO_EXTS = {"mp3", "wav", "ogg", "oga", "flac", "m4a", "aac", "webm"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
MEDIA_KINDS = {"audio": AUDIO_EXTS, "covers": IMAGE_EXTS}


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
        v = (form.get(key) or "").strip()
        if required and not v:
            return f"{label} is required"
        if len(v) > max_len:
            return f"{label} must be at most {max_len} characters"
        values[key] = v or None

    err = (text("title", "Title", 200, True)
           or text("artist", "Artist", 120, True)
           or text("genre", "Genre", 50, False))
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
        "items": [m.to_dict() for m in pager.items],
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
    return ok(music.to_dict())


# ---------- admin ----------

@bp.post("/api/music")
@admin_required
def create_music(_admin):
    values, err = parse_fields(request.form, partial=False)
    if err:
        return fail(err)

    audio = request.files.get("audio_file")
    cover = request.files.get("cover_image")
    if not audio or not audio.filename:
        return fail("Audio file is required")
    err = check_upload(audio, "audio") or (cover and cover.filename and check_upload(cover, "covers"))
    if err:
        return fail(err)

    music = Music(**values)
    music.audio_file = save_upload(audio, "audio")
    if cover and cover.filename:
        music.cover_image = save_upload(cover, "covers")
    db.session.add(music)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        delete_upload(music.audio_file, "audio")
        delete_upload(music.cover_image, "covers")
        raise
    return ok(music.to_dict(), "Track created", 201)


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
    if has_audio:
        music.audio_file = save_upload(audio, "audio")
    if has_cover:
        music.cover_image = save_upload(cover, "covers")
    elif request.form.get("remove_cover") == "1":
        music.cover_image = None

    db.session.commit()
    # Remove replaced files only after the DB points at the new ones.
    if music.audio_file != old_audio:
        delete_upload(old_audio, "audio")
    if music.cover_image != old_cover:
        delete_upload(old_cover, "covers")
    return ok(music.to_dict(), "Track updated")


@bp.delete("/api/music/<int:mid>")
@admin_required
def delete_music(_admin, mid):
    music = db.session.get(Music, mid)
    if music is None:
        return fail("Track not found", 404)
    audio, cover = music.audio_file, music.cover_image
    db.session.delete(music)
    db.session.commit()
    delete_upload(audio, "audio")
    delete_upload(cover, "covers")
    return ok(msg="Track deleted")
