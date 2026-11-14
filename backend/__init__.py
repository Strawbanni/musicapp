import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, send_from_directory
from sqlalchemy.engine import make_url

from .common import fail
from .models import User, db
from .music import bp as music_bp
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
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    app.register_blueprint(users_bp)
    app.register_blueprint(music_bp)

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
        _seed_admin(app)

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    return app


def _seed_admin(app):
    """Create the default admin account on first run."""
    name = sorted(app.config["ADMIN_USERNAMES"])[0]
    if not User.query.filter_by(username=name).first():
        admin = User(username=name, email=f"{name}@musicapp.local", is_active=True)
        admin.set_password(app.config["ADMIN_DEFAULT_PASSWORD"])
        db.session.add(admin)
        db.session.commit()
