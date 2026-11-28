import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, send_from_directory
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from . import leaderboard
from .comments import bp as comments_bp
from .common import fail
from .models import User, db
from .music import bp as music_bp
from .notifications import bp as notifications_bp
from .users import bp as users_bp

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

# Pick up DATABASE_URL etc. from musicapp/.env regardless of the working directory.
load_dotenv(BASE_DIR / ".env")


def _database_uri():
    """Return the SQLAlchemy URI, normalising plain mysql:// URLs to use PyMySQL."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        return f"sqlite:///{BASE_DIR / 'musicapp.db'}"
    url = make_url(raw)
    if url.drivername == "mysql":
        url = url.set(drivername="mysql+pymysql")
    if url.drivername.startswith("mysql") and "charset" not in url.query:
        url = url.update_query_dict({"charset": "utf8mb4"})  # needed for Chinese usernames
    return url.render_as_string(hide_password=False)


def create_app(test_config=None):
    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
        SQLALCHEMY_DATABASE_URI=_database_uri(),
        # Avoid "MySQL server has gone away" on idle connections.
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 280},
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        ADMIN_USERNAMES={u.strip() for u in os.environ.get("ADMIN_USERNAMES", "admin").split(",")},
        ADMIN_DEFAULT_PASSWORD=os.environ.get("ADMIN_DEFAULT_PASSWORD", "admin123"),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "uploads")),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024,
        REDIS_URL=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        LEADERBOARD_KEY=os.environ.get("LEADERBOARD_KEY", leaderboard.DEFAULT_KEY),
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    leaderboard.init_app(app)
    app.register_blueprint(users_bp)
    app.register_blueprint(music_bp)
    app.register_blueprint(comments_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(leaderboard.bp)

    @app.errorhandler(413)
    def too_large(_e):
        mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return fail(f"Upload too large (max {mb} MB)", 413)

    # Make it obvious which database is in use (password masked).
    db_url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    app.logger.warning("Using database: %s", db_url.render_as_string(hide_password=True))
    if db_url.get_backend_name() == "sqlite" and not app.config.get("TESTING"):
        app.logger.warning("DATABASE_URL is not set - data is going to SQLite, not MySQL.")

    with app.app_context():
        db.create_all()
        _upgrade_schema(app)
        _seed_admin(app)
        _rebuild_leaderboard(app)

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    return app


# Columns added after a table was first created. db.create_all() never alters existing
# tables, so add them here (works on MySQL and SQLite). Format: (table, column, DDL type).
_ADDED_COLUMNS = [
    ("music", "duration", "INTEGER NULL"),
    ("music", "lyrics", "TEXT NULL"),  # TEXT = 64 KB on MySQL; lyrics are capped at 10,000 chars
]


def _upgrade_schema(app):
    insp = inspect(db.engine)
    for table, column, ddl in _ADDED_COLUMNS:
        if column not in {c["name"] for c in insp.get_columns(table)}:
            app.logger.warning("Adding missing column %s.%s", table, column)
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    _migrate_music_likes(app, insp)
    _migrate_notifications(app, insp)


def _migrate_music_likes(app, insp):
    """Move rows from the old music-only `music_likes` table into the shared `likes` table.

    Rows already present are skipped, so a run interrupted between the copy and the drop is safe
    to repeat (MySQL commits DDL implicitly, so both steps can't share one transaction).
    """
    if "music_likes" not in insp.get_table_names():
        return
    app.logger.warning("Migrating music_likes into likes")
    with db.engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO likes (user_id, target_type, target_id, created_at) "
            "SELECT ml.user_id, 'music', ml.music_id, ml.created_at FROM music_likes ml "
            "WHERE NOT EXISTS (SELECT 1 FROM likes l WHERE l.user_id = ml.user_id "
            "AND l.target_type = 'music' AND l.target_id = ml.music_id)"))
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE music_likes"))


# Earlier `notifications` layouts -> SELECT column names for
# (sourceUserId, targetUserId, operation, comment id, isRead, createTime).
# In both, the stored comment id of a "reply" row is the reply itself; the current layout stores
# the replied-to comment, so the copy looks up the reply's parentId.
_OLD_NOTIFICATION_LAYOUTS = {
    "kind": ("actor_id", "user_id", "kind", "comment_id", "is_read", "created_at"),
    "targetType": ("sourceUserId", "targetUserId", "targetType", "targetId", "isRead", "createTime"),
}


def _old_notification_layout(columns):
    if "operationType" in columns:
        return None  # current layout
    for marker, cols in _OLD_NOTIFICATION_LAYOUTS.items():
        if marker in columns:
            return cols
    return None


def _migrate_notifications(app, insp):
    """Convert an earlier `notifications` layout to the current one (sourceUserId, targetUserId,
    operationType, targetType, targetId, isRead, createTime): rename the old table, create the new
    one, copy the rows, drop the old table. A run interrupted after the rename resumes from
    `notifications_old`.
    """
    tables = insp.get_table_names()
    if "notifications" in tables and \
            _old_notification_layout({c["name"] for c in insp.get_columns("notifications")}):
        app.logger.warning("Migrating notifications to the new column layout")
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE notifications RENAME TO notifications_old"))
        tables.append("notifications_old")
    if "notifications_old" not in tables:
        return
    insp = inspect(db.engine)
    cols = _old_notification_layout({c["name"] for c in insp.get_columns("notifications_old")})
    # SQLite index names are database-wide and moved with the renamed table: drop them so they
    # can't clash with the new table's. (MySQL index names are per table, nothing to do.)
    if db.engine.dialect.name == "sqlite":
        for ix in insp.get_indexes("notifications_old"):
            with db.engine.begin() as conn:
                conn.execute(text(f'DROP INDEX IF EXISTS "{ix["name"]}"'))
    db.create_all()
    if cols:
        src, dst, op, cid, read, created = cols
        # Rows whose comment no longer exists are dropped (they could not be shown anyway).
        with db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO notifications (id, sourceUserId, targetUserId, operationType, targetType, "
                "targetId, isRead, createTime) "
                f"SELECT o.id, o.{src}, o.{dst}, o.{op}, 'comment', "
                f"CASE WHEN o.{op} = 'reply' THEN c.parentId ELSE o.{cid} END, o.{read}, o.{created} "
                f"FROM notifications_old o JOIN comments c ON c.id = o.{cid} "
                f"WHERE (o.{op} <> 'reply' OR c.parentId IS NOT NULL) "
                "AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.id = o.id)"))
    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE notifications_old"))


def _rebuild_leaderboard(app):
    """Bring the Redis leaderboard in line with the database (it may have missed updates while
    Redis was down, or Redis may be empty). The app still starts if Redis is unreachable."""
    try:
        leaderboard.rebuild()
    except Exception as e:  # redis.RedisError, or a bad REDIS_URL
        app.logger.warning("Leaderboard not rebuilt, Redis unavailable: %s", e)


def _seed_admin(app):
    """Create the default admin account on first run."""
    name = sorted(app.config["ADMIN_USERNAMES"])[0]
    if not User.query.filter_by(username=name).first():
        admin = User(username=name, email=f"{name}@musicapp.local", is_active=True)
        admin.set_password(app.config["ADMIN_DEFAULT_PASSWORD"])
        db.session.add(admin)
        db.session.commit()
