"""Аккаунты: регистрация, вход, профиль, сохранённые машины, история поиска.

Вход — подписанная cookie сессии Flask (HttpOnly, SameSite=Lax). Пароль хранится только хешем.
"""
import re

from flask import Blueprint, jsonify, request, session as cookie
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .api import ApiError, _body, _cfg, handle_api_error  # noqa: F401  (обработчик ошибок общий)
from .catalog import fetch_cars
from .models import SavedCar, User, db
from .profile import chips

bp = Blueprint("auth", __name__, url_prefix="/api/v1")
bp.register_error_handler(ApiError, handle_api_error)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_HASH = generate_password_hash("timing-equalizer")  # чтобы вход с неизвестной почтой шёл столько же


def current_user() -> User | None:
    uid = cookie.get("uid")
    return db.session.get(User, uid) if uid else None


def _need_user() -> User:
    u = current_user()
    if u is None:
        raise ApiError("auth_required", "Войдите в аккаунт", 401)
    return u


def _public(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.display_name, "mode": u.mode}


def _login(u: User) -> None:
    cookie.clear()
    cookie["uid"] = u.id
    cookie.permanent = True


def _check_mode(mode) -> str:
    if mode not in ("novice", "expert"):
        raise ApiError("bad_mode", "Выберите, как вы разбираетесь в машинах: новичок или эксперт")
    return mode


def _check_name(name) -> str | None:
    name = (str(name).strip() if name is not None else "")
    if name and not 2 <= len(name) <= 40:
        raise ApiError("bad_name", "Имя — от 2 до 40 символов")
    return name or None


@bp.post("/auth/register")
def register():
    d = _body()
    email = str(d.get("email", "")).strip().lower()
    password = str(d.get("password", ""))
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ApiError("bad_email", "Проверьте адрес почты")
    if not 8 <= len(password) <= 72:
        raise ApiError("bad_password", "Пароль — от 8 до 72 символов")
    if not (re.search(r"[A-Za-zА-Яа-яЁё]", password) and re.search(r"\d", password)):
        raise ApiError("bad_password", "В пароле должны быть и буквы, и цифры")
    u = User(email=email, password_hash=generate_password_hash(password),
             display_name=_check_name(d.get("name")), mode=_check_mode(d["mode"]) if d.get("mode") else None)
    db.session.add(u)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ApiError("email_taken", "Эта почта уже зарегистрирована — попробуйте войти", 409)
    _login(u)
    return jsonify(_public(u)), 201


@bp.post("/auth/login")
def login():
    d = _body()
    u = User.query.filter_by(email=str(d.get("email", "")).strip().lower()).first()
    ok = check_password_hash(u.password_hash if u else _DUMMY_HASH, str(d.get("password", "")))
    if not (u and ok):
        raise ApiError("bad_credentials", "Неверная почта или пароль", 401)
    _login(u)
    return jsonify(_public(u))


@bp.post("/auth/logout")
def logout():
    cookie.clear()
    return jsonify(ok=True)


@bp.get("/me")
def me():
    u = current_user()
    return jsonify(user=_public(u) if u else None)


@bp.patch("/me")
def update_me():
    u, d = _need_user(), _body()
    if "mode" in d:
        u.mode = _check_mode(d["mode"])
    if "name" in d:
        u.display_name = _check_name(d["name"])
    db.session.commit()
    return jsonify(_public(u))


# ───────────── сохранённые ─────────────
def _cards(ids: list[int]) -> dict[int, dict]:
    return {c["id"]: c for c in fetch_cars(_cfg()["MEDIA_BASE_URL"]) if c["id"] in ids}


@bp.get("/me/saved")
def saved():
    u = _need_user()
    rows = SavedCar.query.filter_by(user_id=u.id).order_by(SavedCar.saved_at.desc()).all()
    cars = _cards([r.car_id for r in rows])
    return jsonify(items=[{"saved_at": r.saved_at.isoformat(), "car": cars[r.car_id]}
                          for r in rows if r.car_id in cars])


@bp.put("/me/saved/<int:car_id>")
def save_car(car_id):
    u = _need_user()
    if not _cards([car_id]):
        raise ApiError("car_not_found", "Машина не найдена", 404)
    db.session.merge(SavedCar(user_id=u.id, car_id=car_id))
    db.session.commit()
    return jsonify(ok=True)


@bp.delete("/me/saved/<int:car_id>")
def unsave_car(car_id):
    u = _need_user()
    SavedCar.query.filter_by(user_id=u.id, car_id=car_id).delete()
    db.session.commit()
    return jsonify(ok=True)


@bp.get("/me/saved/ids")
def saved_ids():
    u = _need_user()
    return jsonify(ids=[r.car_id for r in SavedCar.query.filter_by(user_id=u.id)])


# ───────────── история поиска ─────────────
HISTORY_SQL = text("""
    SELECT s.id, s.created_at, s.mode, s.profile,
           (SELECT d.user_text FROM dialog_events d
             WHERE d.session_id = s.id AND d.kind = 'describe' ORDER BY d.id LIMIT 1) AS query,
           (SELECT ri.car_id FROM recommendation_items ri JOIN recommendations r ON r.id = ri.recommendation_id
             WHERE r.session_id = s.id AND ri.rank = 1 ORDER BY r.id DESC LIMIT 1) AS car_id
      FROM sessions s
     WHERE s.user_id = :u
     ORDER BY s.created_at DESC LIMIT 50""")


@bp.get("/me/history")
def history():
    u = _need_user()
    rows = [r for r in db.session.execute(HISTORY_SQL, {"u": u.id}).mappings() if r["query"] or r["car_id"]]
    cars = _cards([r["car_id"] for r in rows if r["car_id"]])
    return jsonify(items=[{
        "session_id": str(r["id"]), "created_at": r["created_at"].isoformat(), "query": r["query"],
        "chips": [c["label"] for c in chips(r["profile"])],
        "car": ({"id": r["car_id"], "title": cars[r["car_id"]]["title"], "trim": cars[r["car_id"]]["trim"],
                 "price": cars[r["car_id"]]["price"]} if r["car_id"] in cars else None),
    } for r in rows])
