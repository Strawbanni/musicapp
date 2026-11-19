"""Read metadata (tags, duration, embedded cover art) from audio files with mutagen.

Supports ID3 (MP3, WAV, AIFF), MP4/M4A/AAC, FLAC and Ogg (Vorbis/Opus/FLAC) tags.
Everything is best-effort: an unreadable or untagged file yields empty values, never an error.
"""
import base64
import re
from dataclasses import asdict, dataclass
from datetime import date

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

MAX_LEN = {"title": 200, "artist": 120, "genre": 50, "lyrics": 10_000}


@dataclass
class AudioMeta:
    title: str | None = None
    artist: str | None = None
    genre: str | None = None
    release_date: str | None = None   # YYYY-MM-DD, only when the tag has a full date
    release_year: int | None = None   # whenever a year can be found
    duration: int | None = None       # whole seconds
    lyrics: str | None = None         # unsynchronised lyrics (ID3 USLT / MP4 ©lyr / Vorbis LYRICS)
    cover: bytes | None = None
    cover_ext: str | None = None      # jpg / png / gif / webp

    def public(self) -> dict:
        d = asdict(self)
        d.pop("cover")
        return d


def read_metadata(fileobj) -> AudioMeta:
    """`fileobj` is a path or a seekable binary file object."""
    meta = AudioMeta()
    try:
        audio = mutagen.File(fileobj)
    except Exception:  # corrupt / unsupported file
        return meta
    if audio is None:
        return meta

    length = getattr(getattr(audio, "info", None), "length", None)
    if length and length > 0:
        meta.duration = int(round(length))

    tags = audio.tags
    title = artist = genre = date = lyrics = None
    pictures = []  # (picture_type, bytes); type 3 = front cover

    if isinstance(tags, ID3):  # MP3, WAV, AIFF
        title = _id3_text(tags, "TIT2")
        artist = _id3_text(tags, "TPE1") or _id3_text(tags, "TPE2")
        tcon = tags.get("TCON")
        genre = tcon.genres[0] if tcon and tcon.genres else None  # resolves "(13)" -> "Pop"
        date = _id3_text(tags, "TDRC") or _id3_text(tags, "TYER") or _id3_text(tags, "TDRL")
        pictures = [(p.type, p.data) for p in tags.getall("APIC")]
        lyrics = next((f.text for f in tags.getall("USLT") if f.text and f.text.strip()), None)
    elif isinstance(audio, MP4) and tags is not None:  # M4A / AAC in MP4
        title = _first(tags.get("\xa9nam"))
        artist = _first(tags.get("\xa9ART")) or _first(tags.get("aART"))
        genre = _first(tags.get("\xa9gen"))
        date = _first(tags.get("\xa9day"))
        lyrics = _first(tags.get("\xa9lyr"))
        pictures = [(3, bytes(c)) for c in tags.get("covr", [])]
    elif tags is not None and hasattr(tags, "get"):  # Vorbis comments: FLAC, Ogg Vorbis/Opus
        title = _first(tags.get("title"))
        artist = _first(tags.get("artist")) or _first(tags.get("albumartist"))
        genre = _first(tags.get("genre"))
        date = _first(tags.get("date")) or _first(tags.get("year"))
        lyrics = _first(tags.get("lyrics")) or _first(tags.get("unsyncedlyrics"))
        for raw in tags.get("metadata_block_picture", []) or []:
            try:
                p = Picture(base64.b64decode(raw))
                pictures.append((p.type, p.data))
            except Exception:
                pass
    for p in getattr(audio, "pictures", []) or []:  # FLAC native picture blocks
        pictures.append((p.type, p.data))

    meta.title = _clean(title, "title")
    meta.artist = _clean(artist, "artist")
    meta.genre = _clean(genre, "genre")
    meta.lyrics = _clean((lyrics or "").replace("\r\n", "\n").replace("\r", "\n"), "lyrics")
    meta.release_date, meta.release_year = _parse_date(date)

    # Prefer the front cover, else the first picture whose format we accept.
    pictures.sort(key=lambda p: p[0] != 3)
    for _type, data in pictures:
        ext = image_ext(data)
        if ext:
            meta.cover, meta.cover_ext = data, ext
            break
    return meta


def image_ext(data: bytes):
    """Identify an image by its magic bytes (tag mime types are often wrong)."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _id3_text(tags, key):
    frame = tags.get(key)
    return str(frame.text[0]) if frame is not None and getattr(frame, "text", None) else None


def _first(v):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    return str(v) if v is not None else None


def _clean(v, field):
    v = (v or "").replace("\x00", "").strip()
    return v[:MAX_LEN[field]] or None


_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")


def _parse_date(raw):
    m = _DATE_RE.match((raw or "").strip())
    if not m:
        return None, None
    year = int(m.group(1))
    if m.group(2) and m.group(3):
        try:
            return date(year, int(m.group(2)), int(m.group(3))).isoformat(), year
        except ValueError:
            pass
    return None, year
