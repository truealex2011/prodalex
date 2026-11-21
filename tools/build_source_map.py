#!/usr/bin/env python3
"""Собирает app/static/dev/source-map.json — карту «элемент интерфейса → код».

Сканирует три источника:
  * app/*.py                    — Python: AST даёт точные границы функций/классов
                                  и строки маршрутов (@bp.get/@bp.post/...);
  * app/templates/index.html    — id/class разметки с номерами строк;
  * app/static/js/*.js          — id/class в шаблонах и границы функций.

Далее ASSOCIATIONS связывает селектор элемента с символами бэкенда и
фронтенда. Перезапускать после изменений в коде:
    .venv\\Scripts\\python.exe tools\\build_source_map.py
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
OUT = APP / "static" / "dev" / "source-map.json"

# selectors / symbols -> описание и ссылки на код
ASSOCIATIONS: dict[str, dict] = {
    "#app": {
        "label": "Корневой контейнер приложения",
        "backend": ["__init__.create_app"],
        "frontend": ["#app", "app.show", "app.route"],
    },
    ".masthead": {
        "label": "Шапка сайта",
        "backend": ["__init__.create_app"],
        "frontend": [".masthead", "#app"],
    },
    ".brand": {
        "label": "Логотип AutoChoice",
        "backend": ["__init__.create_app"],
        "frontend": [".brand"],
    },
    "#acc-btn": {
        "label": "Кнопка аккаунта в шапке",
        "backend": ["auth.me", "auth.current_user"],
        "frontend": ["#acc-btn", "app.refreshUser", "app.openAuth"],
    },
    "#screen-mode": {
        "label": "Главный экран (описание + вход)",
        "backend": ["api.create_session"],
        "frontend": ["#screen-mode", "app.show", "app.route"],
    },
    ".hero": {
        "label": "Заголовок «Подберём машину для ВАС»",
        "backend": ["api.create_session"],
        "frontend": [".hero", "#screen-mode"],
    },
    "#home-levels": {
        "label": "Выбор режима «Новичок / Эксперт»",
        "backend": ["auth._check_mode", "api.create_session"],
        "frontend": ["#home-levels", "app.selectedHomeMode", "#start-btn"],
    },
    ".level-btn": {
        "label": "Кнопка уровня «Новичок / Эксперт»",
        "backend": ["auth._check_mode"],
        "frontend": [".level-btn", "#home-levels"],
    },
    "#home-describe": {
        "label": "Поле описания желаемой машины",
        "backend": ["api.describe", "nlu.parse", "llm.understand"],
        "frontend": ["#home-describe", "app.startWithDescription", "app.handleDescription"],
    },
    "#hint-chips": {
        "label": "Подсказки-чипы под полем описания",
        "backend": ["nlu.parse", "profile.chips"],
        "frontend": ["#hint-chips", "#describe-hint-chips", "app.chipsHtml"],
    },
    "#start-btn": {
        "label": "Кнопка «Дальше» на главной",
        "backend": ["api.create_session", "api.describe"],
        "frontend": ["#start-btn", "app.startWithDescription", "app.handleDescription"],
    },
    "#home-skip": {
        "label": "Ссылка «Не знаю — помогите вопросами»",
        "backend": ["api.create_session", "advisor.questionnaire"],
        "frontend": ["#home-skip", "app.startBlind", "app.openForm"],
    },
    "#home-auth": {
        "label": "Панель входа/регистрации на главной",
        "backend": ["auth.login", "auth.register", "auth.demo_login"],
        "frontend": ["#home-auth", "app.setHomeAuth", "app.refreshUser"],
    },
    "#home-auth-form": {
        "label": "Форма входа и регистрации",
        "backend": ["auth.login", "auth.register"],
        "frontend": ["#home-auth-form", "app.setHomeAuth"],
    },
    "#home-auth-seg": {
        "label": "Переключатель «Вход / Регистрация»",
        "backend": ["auth.login", "auth.register"],
        "frontend": ["#home-auth-seg", "app.setHomeAuth", "app.setSeg"],
    },
    "#home-auth-submit": {
        "label": "Кнопка отправки формы входа",
        "backend": ["auth.login", "auth.register"],
        "frontend": ["#home-auth-submit", "#home-auth-form"],
    },
    "#home-demo": {
        "label": "Кнопка демо-входа с анимацией",
        "backend": ["auth.demo_login"],
        "frontend": ["#home-demo", "app.demoMove", "app.refreshUser"],
    },
    "#screen-pick": {
        "label": "Экран выбора режима «Перед началом»",
        "backend": ["auth._check_mode"],
        "frontend": ["#screen-pick", "app.show", ".card-btn"],
    },
    ".card-btn": {
        "label": "Карточка выбора «Новичок / Эксперт»",
        "backend": ["auth._check_mode", "api.create_session"],
        "frontend": [".card-btn", "#screen-pick"],
    },
    "#screen-describe": {
        "label": "Экран описания машины",
        "backend": ["api.describe", "llm.understand"],
        "frontend": ["#screen-describe", "app.handleDescription"],
    },
    "#describe-go": {
        "label": "Кнопка «Дальше» на экране описания",
        "backend": ["api.describe"],
        "frontend": ["#describe-go", "app.handleDescription"],
    },
    "#screen-question": {
        "label": "Экран анкеты с вопросами",
        "backend": ["advisor.questionnaire", "api.advisor_questions"],
        "frontend": ["#screen-question", "app.openForm", "app.qaAsk"],
    },
    ".qa-card": {
        "label": "Карточка одного вопроса анкеты",
        "backend": ["advisor.questionnaire", "questions.question_dict", "api.advisor_questions"],
        "frontend": [".qa-card", "app.qaCard", "app.qaRead"],
    },
    ".qa-opt": {
        "label": "Вариант ответа в анкете",
        "backend": ["questions.question_dict", "api.answers"],
        "frontend": [".qa-opt", "app.qaCard", "app.qaSave"],
    },
    "#qa-finish": {
        "label": "Кнопка «Показать машину»",
        "backend": ["api.market", "engine.recommend_market"],
        "frontend": ["#qa-finish", "app.qaFinish", "app.showResult"],
    },
    "#qa-more": {
        "label": "Кнопка «Уточнить ещё»",
        "backend": ["api.advisor_questions", "advisor.questionnaire"],
        "frontend": ["#qa-more", "app.qaAsk"],
    },
    "#screen-loading": {
        "label": "Экран подбора с анимацией машины",
        "backend": ["api.market", "engine.recommend_market"],
        "frontend": ["#screen-loading", "app.showResult", ".ldr-car"],
    },
    "#screen-account": {
        "label": "Личный кабинет",
        "backend": ["auth.me", "auth.saved", "auth.history"],
        "frontend": ["#screen-account", "app.openAccount", "app.refreshUser"],
    },
    "#acc-tabs": {
        "label": "Вкладки кабинета «Сохранённые / История / Настройки»",
        "backend": ["auth.saved", "auth.history", "auth.update_me"],
        "frontend": ["#acc-tabs", "app.openAccount", "app.setSeg"],
    },
    ".car-card": {
        "label": "Карточка сохранённой машины",
        "backend": ["auth.saved", "catalog.fetch_cars", "auth.save_car"],
        "frontend": [".car-card", "app.carCard", "app.openAccount"],
    },
    "#btn-like": {
        "label": "Кнопка «Нравится»",
        "backend": ["api.feedback", "auth.save_car"],
        "frontend": ["#btn-like", "app.api"],
    },
    "#btn-dislike": {
        "label": "Кнопка «Не то»",
        "backend": ["api.feedback"],
        "frontend": ["#btn-dislike", "app.openSheet"],
    },
    "#stage": {
        "label": "Сцена просмотра машины (3D)",
        "backend": ["api.car", "api.model3d", "api.market"],
        "frontend": ["#stage", "viewer.CarViewer", "app.prepareCar"],
    },
    "#viewer": {
        "label": "3D-вьюер машины",
        "backend": ["api.model3d", "sketchfab.find_and_download"],
        "frontend": ["#viewer", "viewer.CarViewer", "app.prepareCar"],
    },
    "#photo-stage": {
        "label": "Фотографии машины вместо 3D",
        "backend": ["photos.find_photos", "api.car_media"],
        "frontend": ["#photo-stage", "app.gallery"],
    },
    ".callout": {
        "label": "Подпись к детали машины на 3D-сцене",
        "backend": ["engine.hotspots", "part_facts.part_texts"],
        "frontend": [".callout", "viewer.CarViewer", "app.onScroll"],
    },
    "#btn-moose": {
        "label": "Кнопка «Тест на лося»",
        "backend": ["api.moose", "api.moose_any", "moose.moose_test"],
        "frontend": ["#btn-moose", "app.openMoose", "app.runMoose"],
    },
    "#mm-report": {
        "label": "Отчёт о тесте на лося",
        "backend": ["moose.moose_test", "moose.collision"],
        "frontend": ["#mm-report", "app.renderMoose"],
    },
    "#sheet": {
        "label": "Шторка «Подробнее» о машине",
        "backend": ["api.car", "recommender.explain", "api.recommendation"],
        "frontend": ["#sheet", "app.openSheet", "app.renderDetails"],
    },
    "#profile-chips": {
        "label": "Условия подбора (чипы) в шторке",
        "backend": ["profile.chips", "api.patch_profile"],
        "frontend": ["#profile-chips", "app.chipsHtml", "app.removeChip"],
    },
    "#toast": {
        "label": "Всплывающее уведомление",
        "backend": [],
        "frontend": ["#toast", "app.toast"],
    },

    # --- мелкие элементы: можно выделить отдельно, а не только всю карточку ---
    ".h1-accent": {
        "label": "Слово «ВАС» в заголовке",
        "backend": [],
        "frontend": [".h1-accent"],
    },
    ".eyebrow": {
        "label": "Надзаголовок раздела",
        "backend": [],
        "frontend": [".eyebrow"],
    },
    ".level-btn > b": {
        "label": "Название уровня (Новичок / Эксперт)",
        "backend": ["auth._check_mode"],
        "frontend": [".level-btn"],
    },
    ".level-btn > span": {
        "label": "Пояснение под названием уровня",
        "backend": ["auth._check_mode"],
        "frontend": [".level-btn"],
    },
    ".card-ico": {
        "label": "Иконка в карточке выбора режима",
        "backend": [],
        "frontend": [".card-ico"],
    },
    ".card-arrow": {
        "label": "Стрелка в карточке выбора режима",
        "backend": [],
        "frontend": [".card-arrow"],
    },
    ".card-txt b": {
        "label": "Заголовок карточки выбора",
        "backend": [],
        "frontend": [".card-txt b"],
    },
    ".card-txt span": {
        "label": "Описание в карточке выбора",
        "backend": [],
        "frontend": [".card-txt span"],
    },
    ".modal-logo": {
        "label": "Иконка в форме входа",
        "backend": [],
        "frontend": [".modal-logo"],
    },
    ".field > span": {
        "label": "Подпись поля формы (Почта / Пароль)",
        "backend": [],
        "frontend": [".field"],
    },
    ".seg-btn": {
        "label": "Таб «Вход / Регистрация»",
        "backend": ["auth.login", "auth.register"],
        "frontend": [".seg-btn", "app.setHomeAuth"],
    },
    ".demo-spark": {
        "label": "Искра в кнопке демо-входа",
        "backend": [],
        "frontend": [".demo-spark"],
    },
    ".demo-label": {
        "label": "Текст кнопки демо-входа",
        "backend": [],
        "frontend": [".demo-label"],
    },
    ".demo-hint": {
        "label": "Подсказка у демо-входа",
        "backend": [],
        "frontend": [".demo-hint"],
    },
    ".avatar": {
        "label": "Аватар в шапке",
        "backend": ["auth.me", "auth.current_user"],
        "frontend": [".avatar", "app.refreshUser"],
    },
    ".acc-name": {
        "label": "Имя пользователя в шапке",
        "backend": ["auth.me"],
        "frontend": [".acc-name", "app.refreshUser"],
    },
    ".chip .x": {
        "label": "Крестик удаления условия",
        "backend": ["api.patch_profile", "profile.remove_chip"],
        "frontend": [".chip .x", "app.removeChip"],
    },
    ".q-number": {
        "label": "Номер вопроса в анкете",
        "backend": ["questions.question_dict"],
        "frontend": [".q-number", "app.qaCard"],
    },
    ".qa-question-text": {
        "label": "Текст вопроса анкеты",
        "backend": ["advisor.questionnaire", "llm.ask_questions"],
        "frontend": [".qa-question-text", "app.qaCard"],
    },
    ".qa-opt span": {
        "label": "Плашка варианта ответа",
        "backend": ["questions.question_dict"],
        "frontend": [".qa-opt span", "app.qaCard"],
    },
    ".qa-must .sw": {
        "label": "Переключатель «Обязательно»",
        "backend": ["questions.apply_answer"],
        "frontend": [".qa-must .sw", "app.qaRead"],
    },
    ".pill": {
        "label": "Бейдж типа кузова в карточке",
        "backend": ["catalog.fetch_cars"],
        "frontend": [".pill", "app.carCard"],
    },
    ".car-sil": {
        "label": "Силуэт автомобиля в карточке",
        "backend": ["catalog.fetch_cars"],
        "frontend": [".car-sil", "app.carCard"],
    },
    ".car-price": {
        "label": "Цена в карточке машины",
        "backend": ["catalog.fetch_cars"],
        "frontend": [".car-price", "app.carCard"],
    },
    ".spec b": {
        "label": "Значение характеристики в шторке",
        "backend": ["engine.physics_specs", "catalog.fetch_cars"],
        "frontend": [".spec b", "app.spec"],
    },
    ".block h3": {
        "label": "Заголовок блока «Почему подходит / Компромиссы»",
        "backend": ["recommender.explain"],
        "frontend": [".block h3", "app.renderDetails"],
    },

    # --- внутренности шторки «Подробнее» ---
    ".sheet-handle": {
        "label": "Ручка шторки «Подробнее»",
        "backend": [],
        "frontend": [".sheet-handle", "#sheet"],
    },
    ".sheet-close": {
        "label": "Кнопка закрытия шторки",
        "backend": [],
        "frontend": ["#sheet-close", "app.closeSheet"],
    },
    ".car-title": {
        "label": "Название машины в шторке",
        "backend": ["engine.recommend_market", "api.car"],
        "frontend": [".car-title", "app.renderDetails"],
    },
    ".car-meta": {
        "label": "Комплектация, год и цена в шторке",
        "backend": ["engine.recommend_market", "catalog.fetch_cars"],
        "frontend": [".car-meta", "app.renderDetails"],
    },
    ".specs-title": {
        "label": "Заголовок группы характеристик",
        "backend": ["engine.physics_specs"],
        "frontend": [".specs-title", "app.renderDetails"],
    },
    ".spec": {
        "label": "Плитка характеристики",
        "backend": ["engine.physics_specs", "part_facts.part_texts"],
        "frontend": [".spec", "app.renderDetails"],
    },
    ".block": {
        "label": "Блок с пояснениями в шторке",
        "backend": ["recommender.explain"],
        "frontend": [".block", "app.renderDetails"],
    },
    ".block ul li": {
        "label": "Пункт списка «Почему подходит / Компромиссы»",
        "backend": ["recommender.explain"],
        "frontend": [".block ul li", "app.renderDetails"],
    },
    "a.secondary": {
        "label": "Ссылка «Смотреть объявление»",
        "backend": ["engine.recommend_market"],
        "frontend": ["a.secondary", "app.renderDetails"],
    },
    ".link.alt-open": {
        "label": "Альтернативная машина в шторке",
        "backend": ["engine.recommend_market", "recommender.explain"],
        "frontend": [".alt-open", "#result-body"],
    },
    ".goal-note": {
        "label": "«Я понял так…» — пересказ запроса",
        "backend": ["llm.understand"],
        "frontend": ["#sheet-goal", "app.showGoal"],
    },
    ".label": {
        "label": "Подпись «Вот что я понял»",
        "backend": [],
        "frontend": [".label", "#sheet"],
    },
    ".chip": {
        "label": "Чип условия подбора",
        "backend": ["profile.chips", "api.patch_profile"],
        "frontend": [".chip", "app.chipsHtml"],
    },
    "#remark-box": {
        "label": "Блок «Что не так?»",
        "backend": ["api.feedback"],
        "frontend": ["#remark-box", "app.openSheet"],
    },
    "#remark-text": {
        "label": "Поле замечания о машине",
        "backend": ["api.feedback"],
        "frontend": ["#remark-text", "app.openSheet"],
    },
    "#remark-go": {
        "label": "Кнопка «Подобрать другую»",
        "backend": ["api.feedback", "api.market"],
        "frontend": ["#remark-go", "app.showResult"],
    },
    "#model-credit": {
        "label": "Атрибуция 3D-модели",
        "backend": ["sketchfab.find_and_download"],
        "frontend": ["#model-credit", "app.renderDetails"],
    },
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def loc(path: Path, start: int, end: int, note: str = "") -> dict:
    return {
        "file": rel(path),
        "dir": rel(path.parent) + "/",
        "name": path.name,
        "start": int(start),
        "end": int(end),
        "note": note[:180],
    }


def index_python() -> dict[str, dict]:
    index: dict[str, dict] = {}
    for path in sorted(APP.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def qualified(node: ast.AST) -> str:
            parts = []
            cur = node
            while cur is not None:
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    parts.append(cur.name)
                cur = parents.get(cur)
            return ".".join(reversed(parts))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            route = ""
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.args
                        and isinstance(dec.args[0], ast.Constant)):
                    route = f"{dec.func.attr.upper()} {dec.args[0].value}"
            note = route or f"{type(node).__name__} {node.name}"
            entry = loc(path, node.lineno, node.end_lineno or node.lineno, note)
            full = f"{path.stem}.{qualified(node)}"
            index[full] = entry
            index.setdefault(f"{path.stem}.{node.name}", entry)
    return index


def index_template() -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    path = APP / "templates" / "index.html"
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        for match in re.finditer(r'\bid="([^"]+)"', line):
            index.setdefault("#" + match.group(1), []).append(loc(path, number, number, stripped))
        for match in re.finditer(r'\bclass="([^"]+)"', line):
            for cls in match.group(1).split():
                index.setdefault("." + cls, []).append(loc(path, number, number, stripped))
    return index


def index_javascript() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    selectors: dict[str, list[dict]] = {}
    symbols: dict[str, dict] = {}
    pattern_fn = re.compile(r"^(\s*)(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
    pattern_arrow = re.compile(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
    pattern_class = re.compile(r"^class\s+([A-Za-z_$][\w$]*)")

    for path in sorted((APP / "static" / "js").glob("*.js")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            for match in re.finditer(r'''\bid=\\?["']([^"'\\]+)\\?["']''', line):
                selectors.setdefault("#" + match.group(1), []).append(loc(path, number, number, line.strip()))
            for match in re.finditer(r'''\bclass=\\?["']([^"'\\]+)\\?["']''', line):
                for cls in match.group(1).split():
                    selectors.setdefault("." + cls, []).append(loc(path, number, number, line.strip()))

        for number, line in enumerate(lines, 1):
            match = pattern_fn.match(line)
            kind = "fn"
            if match:
                indent = len(match.group(1))
                name = match.group(2)
            else:
                match = pattern_arrow.match(line)
                kind = "arrow"
                indent = 0
                if match:
                    name = match.group(1)
                else:
                    match = pattern_class.match(line)
                    kind = "class"
                    indent = 0
                    if match:
                        name = match.group(1)
            if not match:
                continue
            end = number
            if "{" not in line or (line.count("{") and line.count("}") >= line.count("{")):
                end = number
            else:
                for offset in range(number, len(lines)):
                    candidate = lines[offset]
                    if (candidate.strip() in ("}", "};")
                            and len(candidate) - len(candidate.lstrip()) == indent):
                        end = offset + 1
                        break
                else:
                    end = min(number + 80, len(lines))
            symbols[f"{path.stem}.{name}"] = loc(path, number, end, line.strip())
    return selectors, symbols


def main() -> None:
    py = index_python()
    html = index_template()
    js_sel, js_fn = index_javascript()

    entries: dict[str, dict] = {}
    for selector, spec in ASSOCIATIONS.items():
        frontend: list[dict] = []
        for key in spec["frontend"]:
            if key.startswith("#") or key.startswith("."):
                direct = html.get(key, []) + js_sel.get(key, [])
                if not direct:
                    # для составных селекторов (.level-btn > b, .qa-opt span)
                    # берём расположение последнего простого селектора
                    simple = re.findall(r"[#.][\w-]+", key)
                    if simple:
                        direct = html.get(simple[-1], []) + js_sel.get(simple[-1], [])
                frontend.extend(direct[:3])
            elif key in js_fn:
                frontend.append(js_fn[key])

        backend: list[dict] = []
        for key in spec["backend"]:
            if key in py:
                backend.append(py[key])

        entries[selector] = {
            "label": spec["label"],
            "backend": backend,
            "frontend": frontend,
        }

    # убираем дубли, сохраняя порядок
    for entry in entries.values():
        for side in ("backend", "frontend"):
            seen = set()
            unique = []
            for item in entry[side]:
                marker = (item["file"], item["start"], item["end"])
                if marker not in seen:
                    seen.add(marker)
                    unique.append(item)
            entry[side] = unique

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "tools/build_source_map.py",
        "entries": entries,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    filled = sum(1 for e in entries.values() if e["backend"] and e["frontend"])
    print(f"source-map: {OUT.relative_to(ROOT)}")
    print(f"selectors: {len(entries)}  fully mapped: {filled}")


if __name__ == "__main__":
    main()
