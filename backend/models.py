from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(32), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)  # stores a salted hash, never plaintext
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    likes = db.relationship("MusicLike", backref="user", cascade="all, delete-orphan")

    def set_password(self, raw: str) -> None:
        self.password = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password, raw)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "is_active": self.is_active,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }


class Music(db.Model):
    __tablename__ = "music"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    artist = db.Column(db.String(120), nullable=False, index=True)
    genre = db.Column(db.String(50), nullable=True, index=True)
    release_date = db.Column(db.Date, nullable=True)
    # Stored file names (generated, unique) inside UPLOAD_FOLDER/audio and UPLOAD_FOLDER/covers.
    audio_file = db.Column(db.String(255), nullable=False)
    cover_image = db.Column(db.String(255), nullable=True)
    duration = db.Column(db.Integer, nullable=True)  # seconds, read from the file on upload
    # Plain text, one line per lyric line (LRC "[mm:ss.xx]" timestamps are kept as-is).
    lyrics = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    likes = db.relationship("MusicLike", backref="music", cascade="all, delete-orphan")

    def to_dict(self, with_lyrics=True) -> dict:
        """with_lyrics=False keeps list responses small; `has_lyrics` is always present."""
        d = {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "genre": self.genre,
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "audio_file": self.audio_file,
            "audio_url": f"/media/audio/{self.audio_file}",
            "cover_image": self.cover_image,
            "cover_url": f"/media/covers/{self.cover_image}" if self.cover_image else None,
            "duration": self.duration,
            "has_lyrics": bool(self.lyrics),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if with_lyrics:
            d["lyrics"] = self.lyrics
        return d


class MusicLike(db.Model):
    """A user liking a track. The composite primary key makes each like unique per user."""
    __tablename__ = "music_likes"

    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    music_id = db.Column(db.Integer, db.ForeignKey("music.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)


class Comment(db.Model):
    """A comment on a target (e.g. a track), or a reply to another comment.

    Column names follow the requested schema (userId, targetType, ...); Python attributes are
    snake_case like the rest of the models. Replies copy their root's target so a whole thread can
    be fetched by (targetType, targetId). Nesting depth is unlimited: parentId points at any comment.
    targetId has no foreign key because it is polymorphic; comments are removed by the app when
    their target is deleted (see comments.delete_comment_trees).
    """
    __tablename__ = "comments"
    __table_args__ = (
        db.Index("ix_comments_target", "targetType", "targetId", "parentId"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column("userId", db.Integer, db.ForeignKey("users.user_id", ondelete="CASCADE"),
                        nullable=False, index=True)
    target_type = db.Column("targetType", db.String(20), nullable=False)
    target_id = db.Column("targetId", db.Integer, nullable=False)
    publish_time = db.Column("publishTime", db.DateTime, nullable=False, default=datetime.now)
    content = db.Column(db.Text, nullable=False)
    parent_id = db.Column("parentId", db.Integer, db.ForeignKey("comments.id", ondelete="CASCADE"),
                          nullable=True, index=True)

    def to_dict(self, username=None) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": username,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "parent_id": self.parent_id,
            "content": self.content,
            "publish_time": self.publish_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
