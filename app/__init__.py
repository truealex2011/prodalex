import json
import logging
import sys
import time
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from . import metrics
from .api import bp as api_bp
from .auth import bp as auth_bp
from .catalog import load_seed
from .config import Config
from .database import ensure_schema
from .models import db


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"ts": round(record.created, 3), "level": record.levelname,
                   "event": record.getMessage(), "logger": record.name}
        for key in ("request_id", "path", "status", "duration_ms", "reason"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def create_app(overrides: dict | None = None) -> Flask:
    setup_logging()
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.update(overrides or {})
    app.json.ensure_ascii = False
    db.init_app(app)
    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)
    log = logging.getLogger("http")

    with app.app_context():
        ensure_schema()  # db/schema.sql; в проде — миграции Alembic, см. docs/DATABASE.md
        load_seed(app.config["CATALOG_PATH"])
    if app.config["MODEL_INDEX_PREWARM"] and not app.config.get("TESTING"):
        from . import media
        media.prewarm_index(app)

    @app.before_request
    def _start():
        g.t0 = time.perf_counter()
        g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]

    @app.after_request
    def _finish(resp):
        dt = time.perf_counter() - g.t0
        endpoint = request.url_rule.rule if request.url_rule else "unknown"
        metrics.HTTP_LATENCY.labels(endpoint=endpoint, status=resp.status_code).observe(dt)
        resp.headers["X-Request-Id"] = g.request_id
        if request.path != "/metrics":
            # текст пользователя в логи не пишем — только путь, статус и время
            log.info("request", extra={"request_id": g.request_id, "path": request.path,
                                       "status": resp.status_code, "duration_ms": round(dt * 1000, 1)})
        return resp

    @app.errorhandler(500)
    def _error(e):
        return jsonify(error={"code": "internal", "message": "Что-то пошло не так, попробуйте ещё раз",
                              "request_id": g.get("request_id")}), 500

    # адреса экранов одностраничного приложения: страницу отдаём везде, дальше маршрутизирует app.js
    @app.get("/")
    @app.get("/start")
    @app.get("/describe")
    @app.get("/question")
    @app.get("/empty")
    @app.get("/result")
    @app.get("/account")
    @app.get("/account/<tab>")
    @app.get("/car/<int:car_id>")
    def index(tab=None, car_id=None):
        return render_template("index.html")

    @app.get("/media/<path:key>")
    def media_file(key):
        """Файл из хранилища (S3 или локально). Сервер сам ходит в S3 и кэширует копию: бакет остаётся закрытым."""
        from flask import abort, send_file
        from .storage import content_type, get_storage
        if ".." in key or not key.startswith("models/"):
            abort(404)
        path = get_storage(app).fetch(key, Path(app.root_path).parent / "data" / "cache" / "s3")
        if path is None:
            abort(404)
        return send_file(path, mimetype=content_type(key), max_age=86400, conditional=True)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        try:
            db.session.execute(text("SELECT 1"))
            return {"status": "ready"}
        except Exception:
            return {"status": "db_unavailable"}, 503

    @app.get("/metrics")
    def prom():
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

    return app
