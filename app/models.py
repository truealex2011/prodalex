"""ORM-модели таблиц сессий и выдачи. Схема создаётся db/schema.sql (см. app/database.py),
поэтому здесь create_all() не используется: модели только описывают готовые таблицы.

Каталог (бренды, комплектации, характеристики, 3D) читается SQL-запросом через v_cars —
см. app/catalog.py: ему не нужен ORM.
"""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

USER_MODE = ENUM("novice", "expert", name="user_mode", create_type=False)
DIALOG_KIND = ENUM("describe", "answer", "skip", "remark", "chip_removed", "mode_changed",
                   name="dialog_kind", create_type=False)


class UserSession(db.Model):
    """Анонимная сессия: никаких персональных данных, только профиль подбора.

    Версию профиля ведёт триггер БД: любое изменение profile даёт новую версию и снимок
    в profile_versions, поэтому приложение profile_version не трогает.
    """

    __tablename__ = "sessions"

    id = db.Column(UUID(as_uuid=False), primary_key=True)
    mode = db.Column(USER_MODE, nullable=False, default="novice")
    profile = db.Column(JSONB, nullable=False)
    profile_version = db.Column(db.Integer, nullable=False, default=1)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"))
    asked = db.Column("asked_slots", ARRAY(db.Text), nullable=False, default=list)

    @property
    def excluded(self) -> list[int]:
        rows = db.session.execute(select(SessionExcluded.car_id).where(SessionExcluded.session_id == self.id))
        return [r[0] for r in rows]

    def exclude(self, car_id: int) -> None:
        db.session.merge(SessionExcluded(session_id=self.id, car_id=car_id))


class SessionExcluded(db.Model):
    __tablename__ = "session_excluded_cars"
    session_id = db.Column(UUID(as_uuid=False), db.ForeignKey("sessions.id"), primary_key=True)
    car_id = db.Column(db.Integer, primary_key=True)


class DialogEvent(db.Model):
    """Что человек написал/ответил и как мы это поняли."""

    __tablename__ = "dialog_events"
    id = db.Column(db.BigInteger, primary_key=True)
    session_id = db.Column(UUID(as_uuid=False), db.ForeignKey("sessions.id"), nullable=False)
    kind = db.Column(DIALOG_KIND, nullable=False)
    slot = db.Column(db.Text)
    user_text = db.Column(db.Text)
    value = db.Column(JSONB)
    parsed = db.Column(JSONB)
    nlu_engine = db.Column(db.Text)
    profile_version = db.Column(db.Integer)


class Recommendation(db.Model):
    __tablename__ = "recommendations"
    id = db.Column(db.BigInteger, primary_key=True)
    session_id = db.Column(UUID(as_uuid=False), db.ForeignKey("sessions.id"), nullable=False)
    profile_version = db.Column(db.Integer, nullable=False)
    catalog_version = db.Column(db.Text)
    is_empty = db.Column(db.Boolean, nullable=False, default=False)
    counterfactuals = db.Column(JSONB)


class RecommendationItem(db.Model):
    __tablename__ = "recommendation_items"
    recommendation_id = db.Column(db.BigInteger, db.ForeignKey("recommendations.id"), primary_key=True)
    rank = db.Column(db.SmallInteger, primary_key=True)
    car_id = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Numeric(5, 4), nullable=False)
    explanation = db.Column(JSONB, nullable=False, default=list)


class Feedback(db.Model):
    __tablename__ = "feedback"
    id = db.Column(db.BigInteger, primary_key=True)
    session_id = db.Column(UUID(as_uuid=False), db.ForeignKey("sessions.id"), nullable=False)
    car_id = db.Column(db.Integer, nullable=False)
    recommendation_id = db.Column(db.BigInteger)
    liked = db.Column(db.Boolean, nullable=False)
    remark = db.Column(db.Text)


class FunnelEvent(db.Model):
    __tablename__ = "funnel_events"
    id = db.Column(db.BigInteger, primary_key=True)
    session_id = db.Column(UUID(as_uuid=False))
    step = db.Column(db.Text, nullable=False)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.BigInteger, primary_key=True)
    email = db.Column(db.Text, nullable=False, unique=True)
    password_hash = db.Column(db.Text, nullable=False)
    display_name = db.Column(db.Text)
    mode = db.Column(USER_MODE)  # NULL — ещё не выбран, спросим при первом поиске


class SavedCar(db.Model):
    __tablename__ = "saved_cars"
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), primary_key=True)
    car_id = db.Column(db.Integer, primary_key=True)
    saved_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
