import uuid
import json
from queue import Queue
from threading import Thread

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from . import advisor, engine, metrics, nlu
from .catalog import catalog_version, fetch_cars
from .models import (DialogEvent, Feedback, FunnelEvent, Recommendation, RecommendationItem,
                     UserSession, db)
from .moose import moose_test
from .profile import chips, empty_profile, merge, remove_chip
from .questions import apply_answer, next_question
from .recommender import recommend

bp = Blueprint("api", __name__, url_prefix="/api/v1")


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


@bp.errorhandler(ApiError)
def handle_api_error(e: ApiError):
    return jsonify(error={"code": e.code, "message": e.message}), e.status


def _cfg():
    return current_app.config


def _valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _session(sid: str) -> UserSession:
    s = db.session.get(UserSession, sid) if _valid_uuid(sid) else None
    if s is None:
        raise ApiError("session_not_found", "Сессия не найдена, начните заново", 404)
    return s


def _cars() -> list[dict]:
    return fetch_cars(_cfg()["MEDIA_BASE_URL"])


def _one_car(car_id) -> dict | None:
    if not isinstance(car_id, int):
        return None
    found = fetch_cars(_cfg()["MEDIA_BASE_URL"], car_id=car_id)
    return found[0] if found else None


def _funnel(s: UserSession | None, step: str) -> None:
    """Шаг воронки: счётчик Prometheus (оперативно) + строка в БД (переживает рестарт)."""
    metrics.FUNNEL.labels(step=step).inc()
    db.session.add(FunnelEvent(session_id=s.id if s else None, step=step))


def _log(s: UserSession, kind: str, **fields) -> None:
    db.session.add(DialogEvent(session_id=s.id, kind=kind, **fields))


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("bad_request", "Ожидался JSON-объект")
    return data


def _save(s: UserSession, profile: dict):
    s.profile = profile  # версию и снимок профиля создаёт триггер БД
    db.session.commit()
    db.session.refresh(s)


def _state(s: UserSession) -> dict:
    return {"session_id": s.id, "mode": s.mode, "profile": s.profile,
            "profile_version": s.profile_version, "chips": chips(s.profile)}


@bp.post("/sessions")
def create_session():
    from .auth import current_user  # импорт здесь: auth сам импортирует api
    user = current_user()
    body = _body()
    mode = (user.mode if user and user.mode else body.get("mode", "novice"))
    if mode not in ("novice", "expert"):
        raise ApiError("bad_mode", "Режим должен быть novice или expert")
    if user and not user.mode:  # первый поиск: запоминаем выбор в аккаунте
        user.mode = mode
    s = UserSession(id=str(uuid.uuid4()), mode=mode, profile=empty_profile(), asked=[],
                    user_id=user.id if user else None)
    db.session.add(s)
    db.session.flush()
    _funnel(s, "mode")
    db.session.commit()
    db.session.refresh(s)
    return jsonify(_state(s)), 201


@bp.get("/sessions/<sid>/profile")
def get_profile(sid):
    return jsonify(_state(_session(sid)))


@bp.patch("/sessions/<sid>/profile")
def patch_profile(sid):
    """Правка сводки: удалить чип или сменить режим. Пересчёт — на следующем GET рекомендации."""
    s, data = _session(sid), _body()
    profile = s.profile
    if "remove" in data:
        profile = remove_chip(profile, str(data["remove"]))
        _log(s, "chip_removed", value=str(data["remove"]))
    if "budget_max" in data:
        profile = {**profile, "hard": {**profile["hard"], "budget_max": int(data["budget_max"])}}
    if data.get("mode") in ("novice", "expert"):
        s.mode = data["mode"]
        _log(s, "mode_changed", value=data["mode"])
    _save(s, profile)
    return jsonify(_state(s))


def _inference(fn, *args):
    """Run pure inference off-thread; only public stage labels cross into the response."""
    from . import llm
    events = Queue()
    def work():
        llm.progress.report = lambda message: events.put(('stage', message))
        try:
            events.put(('done', fn(*args)))
        except Exception as exc:
            events.put(('error', exc))
        finally:
            del llm.progress.report
    Thread(target=work, daemon=True).start()
    while True:
        kind, value = events.get()
        if kind == 'done':
            return value
        if kind == 'error':
            raise value
        yield value


def _reply(steps):
    def packets():
        while True:
            try:
                yield {'stage': next(steps)}
            except StopIteration as done:
                yield {'result': done.value}
                return
    if request.accept_mimetypes.best != 'application/x-ndjson':
        for packet in packets():
            if 'result' in packet:
                return jsonify(packet['result'])
    def lines():
        for packet in packets():
            yield json.dumps(packet, ensure_ascii=False) + '\n'
    return Response(stream_with_context(lines()), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@bp.post("/sessions/<sid>/describe")
def describe(sid):
    s, data = _session(sid), _body()
    text = str(data.get("text", "")).strip()
    if not text:
        raise ApiError("empty_text", "Опишите хотя бы парой слов, какая машина нужна")
    def steps():
        yield 'Ищу условия в вашем описании.'
        delta, engine = yield from _inference(nlu.parse_ex, text)
        yield 'Сохраняю распознанные условия.'
        if nlu.understood(delta):
            _save(s, merge(s.profile, delta))
        _log(s, 'describe', user_text=text[:1000], parsed=delta.model_dump(mode='json'),
             nlu_engine=engine, profile_version=s.profile_version)
        _funnel(s, 'describe')
        db.session.commit()
        return _state(s)
    return _reply(steps())


@bp.get("/sessions/<sid>/question")
def question(sid):
    s = _session(sid)
    q = next_question(s.profile, s.mode, s.asked, _cars())
    return jsonify({"question": q, "done": q is None})


def _description(s: UserSession) -> str:
    ev = (DialogEvent.query.filter_by(session_id=s.id, kind="describe").order_by(DialogEvent.id.desc()).first())
    return ev.user_text if ev and ev.user_text else ""


@bp.get("/sessions/<sid>/advisor")
def advisor_questions(sid):
    """Анкета: пачка уточняющих вопросов (phase=initial) или проверка, нужны ли ещё (phase=more)."""
    s = _session(sid)
    phase = "more" if request.args.get("phase") == "more" else "initial"
    skipped = bool(s.asked) and set(s.asked) <= set(s.profile.get("skipped", []))
    def steps():
        yield 'Сверяю профиль с каталогом и предыдущими ответами.'
        description = _description(s)
        res = yield from _inference(advisor.questionnaire, s.profile, s.mode, s.asked, _cars(), description, phase, skipped)
        # prefetch_suggest отключён: локальная модель предлагала один и тот же
        # список для любых описаний. Модели выбираются из каталога по профилю.
        return res
    return _reply(steps())


@bp.post("/sessions/<sid>/answers")
def answers(sid):
    """Ответы на пачку вопросов анкеты: [{slot, value | custom_text, must}] — применяются по порядку."""
    s, data = _session(sid), _body()
    profile, done = s.profile, list(s.asked)
    for a in data.get("answers") or []:
        slot = a.get("slot")
        if slot in done or not (slot in advisor.SLOTS or str(slot).startswith("custom_")):
            continue
        custom = str(a.get("custom_text") or "").strip()[:300]
        if custom:
            delta, engine = nlu.parse_ex(custom)
            if not nlu.understood(delta):
                db.session.rollback()
                raise ApiError('clarify_answer', f'Я не понял ответ «{custom[:80]}». Переформулируйте его или выберите готовый вариант.', 422)
            profile = merge(profile, delta)
            _log(s, "answer", slot=slot, user_text=custom, parsed=delta.model_dump(mode="json"), nlu_engine=engine)
        else:
            profile = apply_answer(profile, slot, a.get("value"), bool(a.get("must")))
            _log(s, "answer", slot=slot, value=a.get("value"))
        done.append(slot)
        _funnel(s, f"answer_{len(done)}")
    s.asked = done
    _save(s, profile)
    return jsonify(_state(s))


@bp.post("/sessions/<sid>/answer")
def answer(sid):
    s, data = _session(sid), _body()
    slot = data.get("slot")
    if slot in s.asked:
        raise ApiError("already_answered", "На этот вопрос уже ответили")
    profile = s.profile
    parsed, custom, engine = None, None, None
    if data.get("custom_text"):  # «свой ответ» разбирается тем же парсером, что и описание
        custom = str(data["custom_text"])[:1000]
        delta, engine = nlu.parse_ex(custom)
        if not nlu.understood(delta):
            raise ApiError('clarify_answer', 'Я не понял, что вы имели в виду. Уточните ответ или выберите готовый вариант.', 422)
        parsed = delta.model_dump(mode="json")
        profile = merge(profile, delta)
    else:
        profile = apply_answer(profile, slot, data.get("value"), bool(data.get("must")))
    s.asked = [*s.asked, slot]
    _save(s, profile)
    _log(s, "answer", slot=slot, value=None if custom else data.get("value"), user_text=custom,
         parsed=parsed, nlu_engine=engine if parsed else None, profile_version=s.profile_version)
    _funnel(s, f"answer_{len(s.asked)}")
    db.session.commit()
    return jsonify(_state(s))


def _recommendation(s: UserSession) -> dict:
    result = recommend(_cars(), s.profile, s.excluded)
    if result["empty"]:
        metrics.EMPTY_RESULTS.inc()
    version = catalog_version(_cfg()["CATALOG_PATH"])
    rec = Recommendation(session_id=s.id, profile_version=s.profile_version, catalog_version=version,
                         is_empty=result["empty"], counterfactuals=result.get("counterfactuals"))
    db.session.add(rec)
    db.session.flush()
    if not result["empty"]:
        shown = [(result["car"]["id"], result["score"],
                  {"reasons": result["reasons"], "tradeoffs": result["tradeoffs"]})]
        shown += [(a["id"], a["score"], {"diff": a["diff"]}) for a in result["alternatives"]]
        for rank, (car_id, score, why) in enumerate(shown, 1):
            db.session.add(RecommendationItem(recommendation_id=rec.id, rank=rank, car_id=car_id,
                                              score=score, explanation=why))
    db.session.commit()
    return {**result, "recommendation_id": rec.id, "profile_version": s.profile_version,
            "chips": chips(s.profile), "catalog_version": version}


@bp.get("/sessions/<sid>/recommendation")
def recommendation(sid):
    s = _session(sid)
    _funnel(s, "result")
    return jsonify(_recommendation(s))


def _finish_market(s, res):
    hard = s.profile.get("hard", {})
    if not res and not engine.available():
        return {**_recommendation(s), "source": "catalog", "notice": "Сервис объявлений сейчас недоступен — показан демо-каталог"}
    if not res and (hard.get("models") or hard.get("brands")):
        named = ", ".join(chips_named(s.profile))
        return {"car": None, "alternatives": [], "uncertain": [], "empty": True, "counterfactuals": [], "source": "market",
                "why_empty": [f"Сейчас нет подходящих объявлений: {named}. Уберите условие или измените бюджет, чтобы увидеть другие варианты."],
                "profile_version": s.profile_version, "chips": chips(s.profile)}
    if not res:
        return {**_recommendation(s), "source": "catalog"}
    return {**res, "profile_version": s.profile_version, "chips": chips(s.profile)}


@bp.get("/sessions/<sid>/market")
def market(sid):
    """Подбор по живым объявлениям (движок «парсинка»): модели под профиль -> объявления -> ранжирование.
    Если движок недоступен или ничего не нашёл — обычный подбор по каталогу."""
    s = _session(sid)
    _funnel(s, "result")
    exclude = {x for x in request.args.get("exclude", "").split(",") if x}
    if request.accept_mimetypes.best == 'application/x-ndjson':
        def steps():
            yield 'Проверяю подходящие модели и живые объявления.'
            result = yield from _inference(engine.recommend_market, s.profile, s.mode, exclude, _description(s))
            return _finish_market(s, result)
        return _reply(steps())
    try:
        res = engine.recommend_market(s.profile, s.mode, exclude, _description(s))
    except Exception:
        current_app.logger.exception("подбор по объявлениям")
        res = None
    return jsonify(_finish_market(s, res))


def chips_named(profile: dict) -> list[str]:
    return [c["label"] for c in chips(profile) if c["key"] in ("hard.models", "hard.brands")][:1] or ["указанная марка"]


@bp.get("/model3d")
def model3d():
    """3D-модель машины из объявления по названию (поиск на Sketchfab, загрузка GLB, автоматическое разбиение на детали)."""
    from . import media
    name = request.args.get("name", "").strip()
    if not 2 <= len(name) <= 100:
        raise ApiError("bad_name", "Некорректное название модели")
    length = request.args.get("length")
    return jsonify(media.resolve_name(name, request.args.get("year"), request.args.get("body") or None,
                                      int(length) if (length or "").isdigit() else None, _cfg()["MEDIA_BASE_URL"]))


@bp.post("/moose")
def moose_any():
    """Тест на лося для машины из объявления: данные объявления, каталога Drom и размеры 3D-модели."""
    data = _body()
    car, speed = data.get("car") or {}, data.get("speed", 60)
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        raise ApiError("bad_speed", "Скорость должна быть числом")
    if not 30 <= speed <= 90 or not isinstance(car, dict) or not car.get("title"):
        raise ApiError("bad_request", "Нужны машина и скорость от 30 до 90 км/ч")
    car = {**car, "specs": engine.physics_specs(car), "body": car.get("body") or "sedan"}
    res = moose_test(car, speed)
    res["swerve"]["factors"].extend(car["specs"]["physics_sources"])
    return jsonify(res)


@bp.post("/sessions/<sid>/feedback")
def feedback(sid):
    """Понравилось — фиксируем. Есть замечания — разбираем их, исключаем машину и ищем снова."""
    s, data = _session(sid), _body()
    liked = bool(data.get("liked"))
    metrics.FEEDBACK.labels(liked=str(liked).lower()).inc()
    text = str(data.get("text", "")).strip()
    car = _one_car(data.get("car_id"))
    if car:
        last = Recommendation.query.filter_by(session_id=s.id).order_by(Recommendation.id.desc()).first()
        db.session.add(Feedback(session_id=s.id, car_id=car["id"], liked=liked, remark=text[:1000] or None,
                                recommendation_id=last.id if last else None))
    if liked:
        db.session.commit()
        return jsonify({"ok": True})
    profile = s.profile
    if text:
        delta = nlu.parse(text)
        _log(s, "remark", user_text=text[:1000], parsed=delta.model_dump(mode="json"), nlu_engine="rules")
        # «дорого» без суммы -> бюджет ниже цены отвергнутой машины
        if car and car.get("price") and "cheap" in delta.nice and not delta.hard.budget_max:
            delta.hard.budget_max = int(car["price"] * 0.9)
        profile = merge(profile, delta)
    if car:
        s.exclude(car["id"])
    _save(s, profile)
    return jsonify(_recommendation(s))


@bp.get("/cars/<int:car_id>")
def car(car_id):
    c = _one_car(car_id)
    if c is None:
        raise ApiError("car_not_found", "Машина не найдена", 404)
    return jsonify(c)


@bp.get("/cars/random")
def random_car():
    """Return one random catalog car for the demo viewer."""
    found = fetch_cars(_cfg()["MEDIA_BASE_URL"], random=True)
    if not found:
        raise ApiError("car_not_found", "В каталоге нет машин", 404)
    return jsonify(found[0])


@bp.get("/cars/<int:car_id>/media")
def car_media(car_id):
    """3D-модель (файл GLB) или, если модели нет, фото машины. Найденное сохраняется: повторные вызовы мгновенные."""
    from . import media  # импорт здесь: модуль тянет сетевые зависимости
    c = _one_car(car_id)
    if c is None:
        raise ApiError("car_not_found", "Машина не найдена", 404)
    return jsonify(media.resolve(c, _cfg()["MEDIA_BASE_URL"]))


@bp.get("/cars/<int:car_id>/moose")
def moose(car_id):
    """Тест на лося: объезд и удар на заданной скорости (50–60 км/ч)."""
    c = _one_car(car_id)
    if c is None:
        raise ApiError("car_not_found", "Машина не найдена", 404)
    try:
        speed = float(request.args.get("speed", 60))
    except ValueError:
        raise ApiError("bad_speed", "Скорость должна быть числом")
    if not 30 <= speed <= 90:
        raise ApiError("bad_speed", "Скорость теста — от 30 до 90 км/ч")
    if not c["specs"].get("track_mm"):
        raise ApiError("no_specs", "Для этой машины нет данных для теста", 422)
    sid = request.args.get("session")
    _funnel(db.session.get(UserSession, sid) if _valid_uuid(sid) else None, "moose")
    db.session.commit()
    return jsonify(moose_test(c, speed))
