# Промпт: скачать модели Sketchfab, разбить на части и проверить (для ИИ-агента с доступом к терминалу)

Нужен агент, который умеет запускать команды (Claude Code, Codex и т. п.), открывать интернет и работать в папке проекта `autochoice`.
Обычный чат без терминала этого сделать не может. Приложите файл со ссылками и (если есть) папку `labels/` от прошлого шага.

```
Ты работаешь в проекте AutoChoice (папка autochoice, Python 3, numpy; зависимости в requirements.txt).
Задача: для каждой ссылки Sketchfab из приложенного файла (формат https://skfb.ly/XXXXX, блоки разделены пустой строкой)
скачать 3D-модель, автоматически разбить её на детали готовым скриптом, проверить качество и выдать подписи для БД.
Всё делай сам, запуская команды. Ничего не выдумывай: каждое поле — из страницы Sketchfab, из самого файла GLB или из вывода скрипта.

ШАГ 1. Метаданные (со страницы модели на sketchfab.com, можно через https://api.sketchfab.com/v3/models/<uid>)
Для каждой ссылки: uid (32 hex), название, автор, ссылка на автора, лицензия и её ссылка, downloadable, faceCount,
дата публикации. Лицензия: ok — CC0 / CC BY / CC BY-SA; nc — есть NonCommercial; blocked — есть NoDerivatives,
Sketchfab Standard, Editorial, платная; unknown. Не скачиваемые и blocked — пропусти, причину запиши в отчёт.

ШАГ 2. Скачивание (по правилам Sketchfab, без обхода защиты)
- Если задана переменная SKETCHFAB_TOKEN — через официальный Download API: GET https://api.sketchfab.com/v3/models/<uid>/download
  с заголовком "Authorization: Token <токен>", формат glTF/GLB. Токен нигде не печатай и не сохраняй в файлы.
- Если токена нет — используй готовый код проекта: функции app/sketchfab.py (download_glb, mirror_paths — копия в зеркале Objaverse)
  или файлы, которые лежат в data/models_inbox/<uid>.glb (их положил человек вручную).
- Не получилось скачать — запиши status "not_downloaded" и причину, иди дальше. Файлы кладёшь в data/models_inbox/<uid>.glb.
- Убедись, что файл — настоящий GLB (app.sketchfab.verify_glb), а не страница с ошибкой. Сжатые Draco/meshopt модели пропускай (скрипт их не читает).

ШАГ 3. Разбиение на части (готовый скрипт, не пиши свой алгоритм)
  python tools/split_glb.py data/models_inbox/<uid>.glb --out data/split/<uid> [--length-mm <длина машины в мм, если известна>]
Результат: car_parts.glb и parts.json. Из вывода/parts.json возьми: время, число деталей, список id деталей, measured (колёсная база,
колея, длина, ширина, высота). Если скрипт сказал «не удалось разбить» — status "split_failed" и причина дословно из вывода.
Разметку не правь вручную и не дорисовывай детали, которых скрипт не нашёл.

ШАГ 4. Проверка качества (число считай сам по GLB)
  python -c "from app.splitter import quality_stats, low_quality_reason; import sys; q=quality_stats(open(sys.argv[1],'rb').read()); print(q, low_quality_reason(q))" data/models_inbox/<uid>.glb
Правила (как на сайте): меньше 60 000 треугольников — «low_poly»; самая большая текстура меньше 512 px — «low_res»; в названии есть
scan, photogrammetry, wreck, rust, 6x6, monster, drift, rally, police, taxi, tuned, custom, low poly, cartoon — «bad_variant».
Модель в БД не заводим, если сработало любое из них, либо в parts.json нет четырёх колёс (wheel_front_left / right, wheel_rear_left / right),
либо нет капота или дверей. Прямо перечисли, какие детали найдены и каких не хватает.

ШАГ 5. Привязка к машине
Определи марку, модель, поколение, год и тип кузова (sedan | hatch | suv | wagon | minivan) по названию и описанию страницы;
чего нет — null. Если есть файл каталога (data/catalog/*.json или таблица cars в БД) — укажи id совпавшей машины, иначе null.

ВЫХОД (создай папку labels_split/)
1) labels_split/<short_code>.json на каждую ссылку:
{
  "short_code": "", "uid": "", "status": "ok | not_downloaded | split_failed | rejected | blocked_license",
  "reject_reason": null,
  "title": "", "author": "", "license": "", "license_url": "", "license_class": "ok|nc|blocked|unknown", "source_url": "",
  "car": {"brand": null, "model": null, "generation": null, "year_from": null, "year_to": null, "body": null, "catalog_car_id": null},
  "stats": {"triangles": 0, "texture_px": null, "split_seconds": 0, "parts_found": [], "parts_missing": [], "measured": {}},
  "files": {"glb": "data/split/<uid>/car_parts.glb", "parts_json": "data/split/<uid>/parts.json"},
  "credit": "«название» — автор (Sketchfab), лицензия",
  "caption_ru": "Марка Модель, год · модель автора, CC BY 4.0"
}
2) labels_split/index.json — все записи одним массивом.
3) labels_split/report.md — сколько ссылок, сколько ok, и по каждой отклонённой причина (одной строкой, из вывода команд).

ПРАВИЛА
- Каждое число и каждую деталь бери из вывода команд, не из головы. Если команда упала — приведи её последнюю строку ошибки в reject_reason.
- Не меняй код проекта и не удаляй файлы. Только читай, запускай скрипты и создавай файлы в data/models_inbox, data/split, labels_split.
- Не скачивай и не публикуй ничего, что запрещено лицензией. Не обходи ограничения Sketchfab.
- В конце ответь коротко: сколько моделей ok, сколько отклонено и главные причины.
```

---

## Что дальше

Папку `labels_split/` вместе с `data/split/` присылайте мне: я заведу модели со статусом `ok` в БД и в хранилище (`tools/import_models.py`).
Скачивание без `SKETCHFAB_TOKEN` у многих моделей не сработает (токен берётся в личном кабинете Sketchfab). Тогда скачайте GLB вручную в `data/models_inbox/`, и агент сделает остальное.
