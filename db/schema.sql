-- AutoChoice — схема PostgreSQL (совместима с PG 12+).
-- Применение:  psql -v ON_ERROR_STOP=1 -f db/schema.sql <база>
-- Принципы:
--   * каталог нормализован: бренд → модель → комплектация; характеристики, цвета, теги — отдельно;
--   * внешние факты (краш-тесты, лось) хранятся с источником и ссылкой;
--   * 3D-ассеты имеют лицензию (CC BY требует указать автора);
--   * пользователь анонимен: только профиль подбора, никаких персональных данных;
--   * история диалога и версий профиля — для воспроизводимости выдачи и разбора качества NLU.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ───────────── перечисления ─────────────
CREATE TYPE body_type      AS ENUM ('sedan', 'hatch', 'suv', 'wagon', 'minivan');
CREATE TYPE drive_type     AS ENUM ('fwd', 'rwd', 'awd');
CREATE TYPE gearbox_type   AS ENUM ('manual', 'auto');
CREATE TYPE fuel_type      AS ENUM ('petrol', 'diesel', 'hybrid', 'electric');
CREATE TYPE user_mode      AS ENUM ('novice', 'expert');
CREATE TYPE asset_kind     AS ENUM ('model_glb', 'parts_json', 'image', 'thumbnail');
CREATE TYPE dialog_kind    AS ENUM ('describe', 'answer', 'skip', 'remark', 'chip_removed', 'mode_changed');
CREATE TYPE maneuver_type  AS ENUM ('swerve', 'hit');
CREATE TYPE moose_verdict  AS ENUM ('pass', 'limit', 'fail');

-- ───────────── каталог ─────────────
CREATE TABLE brands (
    id       smallserial PRIMARY KEY,
    name     text NOT NULL UNIQUE,
    country  char(2)                                   -- ISO 3166-1 alpha-2
);

CREATE TABLE car_models (
    id        serial PRIMARY KEY,
    brand_id  smallint NOT NULL REFERENCES brands(id) ON DELETE RESTRICT,
    name      text NOT NULL,
    UNIQUE (brand_id, name)
);

-- Комплектация = то, что выдаётся пользователю («объявление» каталога)
CREATE TABLE cars (
    id                integer PRIMARY KEY,
    model_id          integer NOT NULL REFERENCES car_models(id) ON DELETE RESTRICT,
    trim              text NOT NULL,
    year              smallint CHECK (year BETWEEN 1990 AND 2100),
    body              body_type NOT NULL,
    seats             smallint CHECK (seats BETWEEN 1 AND 12),
    trunk_l           integer  CHECK (trunk_l > 0),
    power_hp          integer  CHECK (power_hp > 0),
    consumption_l100  numeric(4,1) CHECK (consumption_l100 > 0),
    drive             drive_type,
    transmission      gearbox_type,
    fuel              fuel_type,
    price_rub         integer  CHECK (price_rub > 0),
    listing_url       text,
    is_active         boolean NOT NULL DEFAULT true,       -- скрыть из выдачи, не удаляя историю
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_id, trim, year)
);
CREATE INDEX cars_filter_idx ON cars (price_rub, seats) WHERE is_active;
CREATE INDEX cars_body_idx   ON cars (body) WHERE is_active;

-- Расширенные характеристики 1:1 (данные для сравнения, подсказок и теста на лося)
CREATE TABLE car_specs (
    car_id             integer PRIMARY KEY REFERENCES cars(id) ON DELETE CASCADE,
    accel_0_100        numeric(4,1) CHECK (accel_0_100 > 0),
    top_speed_kmh      smallint,
    clearance_mm       smallint,
    length_mm          smallint,
    width_mm           smallint,
    height_mm          smallint,
    weight_kg          smallint,
    track_mm           smallint,              -- колея: нужна модели лосиного теста
    tank_l             smallint,
    tires              text,
    airbags            smallint CHECK (airbags >= 0),
    esp                boolean,
    led_headlights     boolean,
    led_taillights     boolean,
    electric_handbrake boolean,
    power_mirrors      boolean,
    screen_in          numeric(3,1),
    climate            text,
    chassis            text,                  -- basic | sport | ... (расширяемо, поэтому не enum)
    extra              jsonb NOT NULL DEFAULT '{}'::jsonb   -- всё, что пока не вынесено в колонки
);

CREATE TABLE colors (
    code     text PRIMARY KEY,                -- red, white, ...
    name_ru  text NOT NULL,
    hex      char(7) NOT NULL CHECK (hex ~ '^#[0-9a-fA-F]{6}$')
);
CREATE TABLE car_colors (
    car_id     integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    color_code text    NOT NULL REFERENCES colors(code),
    PRIMARY KEY (car_id, color_code)
);

CREATE TABLE tags (
    code      text PRIMARY KEY,               -- family, city, first-car, ...
    label_ru  text NOT NULL
);
CREATE TABLE car_tags (
    car_id    integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    tag_code  text    NOT NULL REFERENCES tags(code),
    PRIMARY KEY (car_id, tag_code)
);
CREATE INDEX car_tags_tag_idx ON car_tags (tag_code);

-- ───────────── внешние факты: безопасность ─────────────
CREATE TABLE test_sources (
    id    smallserial PRIMARY KEY,
    name  text NOT NULL UNIQUE,               -- Euro NCAP, ARCAP, km77, Teknikens Värld ...
    kind  text NOT NULL CHECK (kind IN ('crash', 'moose')),
    url   text
);

-- Реальный результат лосиного теста (если опубликован); иначе подсказка считается моделью
CREATE TABLE moose_results (
    id         serial PRIMARY KEY,
    car_id     integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    source_id  smallint REFERENCES test_sources(id),
    speed_kmh  numeric(4,1) NOT NULL CHECK (speed_kmh BETWEEN 30 AND 120),
    passed     boolean,
    label      text NOT NULL,                 -- «km77, Kia Sportage 2022 (1.6 T-GDI)»
    url        text,
    tested_on  date
);
CREATE INDEX moose_results_car_idx ON moose_results (car_id);

CREATE TABLE crash_ratings (
    id         serial PRIMARY KEY,
    car_id     integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    source_id  smallint REFERENCES test_sources(id),
    stars      smallint CHECK (stars BETWEEN 0 AND 5),
    score_idx  numeric(4,3) NOT NULL CHECK (score_idx BETWEEN 0 AND 1),  -- нормированный индекс
    label      text NOT NULL,
    url        text
);
CREATE INDEX crash_ratings_car_idx ON crash_ratings (car_id);

-- ───────────── 3D и медиа ─────────────
CREATE TABLE licences (
    code              text PRIMARY KEY,       -- CC-BY-4.0, SKETCHFAB-STD, ...
    name              text NOT NULL,
    url               text,
    needs_credit      boolean NOT NULL DEFAULT true,
    allows_commercial boolean NOT NULL DEFAULT false
);

CREATE TABLE car_assets (
    id           serial PRIMARY KEY,
    car_id       integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    kind         asset_kind NOT NULL,
    storage_key  text NOT NULL,               -- ключ в S3/MinIO или путь в static/media
    licence_code text REFERENCES licences(code),
    credit       text,                        -- «Lada Vesta — tiunov.se (Sketchfab)»
    size_bytes   bigint CHECK (size_bytes >= 0),
    sha256       char(64),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (car_id, kind, storage_key),
    CHECK (licence_code IS NULL OR credit IS NOT NULL)   -- есть лицензия — обязан быть автор
);

-- Справочник деталей, на которые ставятся подсказки в 3D (id из parts.json)
CREATE TABLE car_parts (
    part_id   text PRIMARY KEY,               -- hood, grille, wheel_front_left ...
    label_ru  text NOT NULL,
    grp       text NOT NULL CHECK (grp IN ('exterior', 'interior', 'chassis', 'lights', 'wheels', 'other'))
);
CREATE TABLE car_hotspots (
    car_id    integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    part_id   text    NOT NULL REFERENCES car_parts(part_id),
    fact_ru   text    NOT NULL,
    PRIMARY KEY (car_id, part_id)
);

-- ───────────── версионирование каталога ─────────────
CREATE TABLE catalog_versions (
    version    text PRIMARY KEY,              -- sha256 сида (первые 12 символов)
    cars_count integer NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);

-- ───────────── анонимные сессии ─────────────
CREATE TABLE sessions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mode             user_mode NOT NULL DEFAULT 'novice',
    profile          jsonb NOT NULL DEFAULT '{"hard":{},"nice":{},"color":null,"budget_unknown":false,"skipped":[]}'::jsonb,
    profile_version  integer NOT NULL DEFAULT 1 CHECK (profile_version >= 1),
    asked_slots      text[] NOT NULL DEFAULT '{}',          -- какие вопросы уже задавали
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL DEFAULT now() + interval '30 days',
    CHECK (jsonb_typeof(profile) = 'object' AND profile ? 'hard' AND profile ? 'nice')
);
CREATE INDEX sessions_expires_idx ON sessions (expires_at);

-- Машины, которые пользователь отверг («Не то») — в выдачу не возвращаются
CREATE TABLE session_excluded_cars (
    session_id  uuid    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    car_id      integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    excluded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, car_id)
);

-- Снимок профиля при каждом изменении: можно воспроизвести любую выдачу
CREATE TABLE profile_versions (
    session_id uuid    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    version    integer NOT NULL,
    profile    jsonb   NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, version)
);

-- Журнал диалога: что человек написал / ответил, и как мы это поняли
CREATE TABLE dialog_events (
    id              bigserial PRIMARY KEY,
    session_id      uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind            dialog_kind NOT NULL,
    slot            text,                      -- для answer/skip
    user_text       text CHECK (char_length(user_text) <= 1000),
    value           jsonb,                     -- выбранный вариант
    parsed          jsonb,                     -- ProfileDelta, который вернул NLU
    nlu_engine      text,                      -- 'local-model' | 'rules' — для сравнения качества
    profile_version integer,                   -- версия профиля ПОСЛЕ события
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dialog_events_session_idx ON dialog_events (session_id, id);

-- ───────────── выдача и обратная связь ─────────────
CREATE TABLE recommendations (
    id               bigserial PRIMARY KEY,
    session_id       uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    profile_version  integer NOT NULL,
    catalog_version  text REFERENCES catalog_versions(version),
    is_empty         boolean NOT NULL DEFAULT false,
    counterfactuals  jsonb,                    -- «что ослабить, чтобы нашлось», если выдача пуста
    created_at       timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (session_id, profile_version) REFERENCES profile_versions(session_id, version)
);
CREATE INDEX recommendations_session_idx ON recommendations (session_id, id);

CREATE TABLE recommendation_items (
    recommendation_id bigint   NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
    rank              smallint NOT NULL CHECK (rank >= 1),
    car_id            integer  NOT NULL REFERENCES cars(id),
    score             numeric(5,4) NOT NULL CHECK (score BETWEEN 0 AND 1),
    explanation       jsonb NOT NULL DEFAULT '[]'::jsonb,     -- шаблонные «почему подходит»
    PRIMARY KEY (recommendation_id, rank),
    UNIQUE (recommendation_id, car_id)
);

CREATE TABLE feedback (
    id                bigserial PRIMARY KEY,
    session_id        uuid    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    car_id            integer NOT NULL REFERENCES cars(id),
    recommendation_id bigint  REFERENCES recommendations(id) ON DELETE SET NULL,
    liked             boolean NOT NULL,
    remark            text CHECK (char_length(remark) <= 1000),
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feedback_car_idx ON feedback (car_id, liked);

-- Запуски теста на лося (что смотрят и на какой скорости)
CREATE TABLE moose_runs (
    id          bigserial PRIMARY KEY,
    session_id  uuid REFERENCES sessions(id) ON DELETE SET NULL,
    car_id      integer NOT NULL REFERENCES cars(id),
    speed_kmh   numeric(4,1) NOT NULL CHECK (speed_kmh BETWEEN 30 AND 90),
    maneuver    maneuver_type NOT NULL,
    verdict     moose_verdict,
    result      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX moose_runs_car_idx ON moose_runs (car_id);

-- Воронка продукта (считается SQL-ем, переживает рестарт, в отличие от Prometheus-счётчика)
CREATE TABLE funnel_events (
    id          bigserial PRIMARY KEY,
    session_id  uuid REFERENCES sessions(id) ON DELETE SET NULL,
    step        text NOT NULL,                 -- mode, describe, answer_1, result, moose ...
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX funnel_events_step_idx ON funnel_events (step, created_at);

-- ───────────── эталоны для оценки NLU ─────────────
CREATE TABLE nlu_examples (
    id          serial PRIMARY KEY,
    text        text NOT NULL UNIQUE,
    expected    jsonb NOT NULL,                -- ожидаемый ProfileDelta
    source      text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'synthetic', 'production')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ───────────── триггеры ─────────────
CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER cars_touch BEFORE UPDATE ON cars FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Создание сессии → версия 1 в profile_versions
CREATE FUNCTION snapshot_profile_ins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO profile_versions (session_id, version, profile) VALUES (NEW.id, NEW.profile_version, NEW.profile);
  RETURN NEW;
END $$;
CREATE TRIGGER sessions_snapshot_ins AFTER INSERT ON sessions
  FOR EACH ROW EXECUTE FUNCTION snapshot_profile_ins();

-- Любое изменение профиля → новая версия + снимок (версию приложению вести не нужно)
CREATE FUNCTION bump_profile_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.profile IS DISTINCT FROM OLD.profile THEN
    NEW.profile_version := OLD.profile_version + 1;
    INSERT INTO profile_versions (session_id, version, profile) VALUES (NEW.id, NEW.profile_version, NEW.profile);
  END IF;
  NEW.last_seen_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER sessions_bump BEFORE UPDATE ON sessions
  FOR EACH ROW EXECUTE FUNCTION bump_profile_version();

-- ───────────── представления ─────────────
-- Плоская карточка машины — то, что сейчас отдаёт Car.to_dict()
CREATE VIEW v_cars AS
SELECT c.id, b.name AS brand, m.name AS model, b.name || ' ' || m.name AS title, c.trim, c.year, c.body,
       c.seats, c.trunk_l, c.power_hp, c.consumption_l100, c.drive, c.transmission, c.fuel,
       c.price_rub AS price, c.listing_url,
       COALESCE((SELECT jsonb_agg(cc.color_code ORDER BY cc.color_code) FROM car_colors cc WHERE cc.car_id = c.id), '[]') AS colors,
       COALESCE((SELECT jsonb_agg(ct.tag_code ORDER BY ct.tag_code) FROM car_tags ct WHERE ct.car_id = c.id), '[]') AS tags,
       (SELECT jsonb_build_object('speed', mr.speed_kmh, 'label', mr.label, 'url', mr.url)
          FROM moose_results mr WHERE mr.car_id = c.id ORDER BY mr.speed_kmh DESC LIMIT 1) AS moose_real,
       (SELECT jsonb_build_object('index', cr.score_idx, 'label', cr.label, 'url', cr.url)
          FROM crash_ratings cr WHERE cr.car_id = c.id ORDER BY cr.score_idx DESC LIMIT 1) AS crash_real,
       (SELECT a.storage_key FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'model_glb' LIMIT 1) AS model_key,
       (SELECT a.storage_key FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'parts_json' LIMIT 1) AS parts_key,
       (SELECT a.credit FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'model_glb' LIMIT 1) AS model_credit,
       (SELECT a.storage_key FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'image' LIMIT 1) AS image_key
  FROM cars c
  JOIN car_models m ON m.id = c.model_id
  JOIN brands b ON b.id = m.brand_id
 WHERE c.is_active;

-- Воронка по шагам: сколько уникальных сессий дошло
CREATE VIEW v_funnel AS
SELECT step, count(DISTINCT session_id) AS sessions, count(*) AS events
  FROM funnel_events GROUP BY step;

-- Доля «нравится» по машинам — сигнал качества выдачи
CREATE VIEW v_car_feedback AS
SELECT car_id, count(*) AS votes, count(*) FILTER (WHERE liked) AS likes,
       round(avg(liked::int)::numeric, 3) AS like_rate
  FROM feedback GROUP BY car_id;

-- Подсказки по деталям одной машины одним объектом (формат hotspots для 3D)
CREATE VIEW v_car_hotspots AS
SELECT h.car_id, jsonb_object_agg(h.part_id, h.fact_ru) AS hotspots
  FROM car_hotspots h GROUP BY h.car_id;

-- ───────────── комментарии ─────────────
COMMENT ON TABLE cars IS 'Комплектация каталога — единица выдачи';
COMMENT ON TABLE sessions IS 'Анонимная сессия подбора; персональных данных нет';
COMMENT ON TABLE profile_versions IS 'Снимки профиля — чтобы воспроизвести любую выдачу';
COMMENT ON TABLE dialog_events IS 'Журнал диалога и результат разбора текста (NLU)';
COMMENT ON COLUMN car_specs.track_mm IS 'Колея, мм — вход модели лосиного теста';
COMMENT ON COLUMN car_assets.credit IS 'Автор модели; обязателен при наличии лицензии (CC BY)';

COMMIT;
