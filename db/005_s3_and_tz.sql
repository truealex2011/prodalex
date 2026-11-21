-- 005: вторая БД проекта — S3 (3D-модели) — и таблицы под требования ТЗ.
-- PostgreSQL хранит только метаданные и ссылки (ключи объектов); сами файлы GLB лежат в S3.
BEGIN;

-- ───────────── S3: где физически лежит файл ─────────────
ALTER TABLE car_assets
    ADD COLUMN storage      text NOT NULL DEFAULT 'local' CHECK (storage IN ('local', 's3')),
    ADD COLUMN bucket       text,
    ADD COLUMN etag         text,
    ADD COLUMN content_type text;
COMMENT ON COLUMN car_assets.storage_key IS 'Ключ объекта в S3 (storage=s3) или путь внутри static/media (storage=local)';

CREATE OR REPLACE VIEW v_cars AS
SELECT c.id, b.name AS brand, m.name AS model, b.name || ' ' || m.name AS title, c.trim, c.year, c.body,
       c.seats, c.trunk_l, c.power_hp, c.consumption_l100, c.drive, c.transmission, c.fuel,
       c.price_rub AS price, c.listing_url,
       COALESCE((SELECT jsonb_agg(cc.color_code ORDER BY cc.color_code) FROM car_colors cc WHERE cc.car_id = c.id), '[]') AS colors,
       COALESCE((SELECT jsonb_agg(ct.tag_code ORDER BY ct.tag_code) FROM car_tags ct WHERE ct.car_id = c.id), '[]') AS tags,
       (SELECT jsonb_build_object('speed', mr.speed_kmh, 'label', mr.label, 'url', mr.url)
          FROM moose_results mr WHERE mr.car_id = c.id ORDER BY mr.speed_kmh DESC LIMIT 1) AS moose_real,
       (SELECT jsonb_build_object('index', cr.score_idx, 'label', cr.label, 'url', cr.url)
          FROM crash_ratings cr WHERE cr.car_id = c.id ORDER BY cr.score_idx DESC LIMIT 1) AS crash_real,
       (SELECT a.storage_key FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'model_glb' ORDER BY a.id LIMIT 1) AS model_key,
       (SELECT a.storage_key FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'parts_json' LIMIT 1) AS parts_key,
       (SELECT a.credit FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'model_glb' ORDER BY a.id LIMIT 1) AS model_credit,
       (SELECT a.storage_key FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'image' LIMIT 1) AS image_key,
       (SELECT a.storage FROM car_assets a WHERE a.car_id = c.id AND a.kind = 'model_glb' ORDER BY a.id LIMIT 1) AS model_storage
  FROM cars c
  JOIN car_models m ON m.id = c.model_id
  JOIN brands b ON b.id = m.brand_id
 WHERE c.is_active;

-- Реестр 3D-моделей, выбранных командой на Sketchfab (в т.ч. тех, что пока не соответствуют ни одной машине каталога)
CREATE TABLE model_sources (
    id             serial PRIMARY KEY,
    sketchfab_uid  char(32) NOT NULL UNIQUE,
    short_code     text,                                  -- skfb.ly/<код>
    title          text NOT NULL,
    author         text,
    license_label  text,
    license_url    text,
    license_code   text REFERENCES licences(code),
    downloadable   boolean NOT NULL,
    faces          integer CHECK (faces >= 0),
    source_url     text NOT NULL,
    status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'stored', 'blocked')),
    blocked_reason text,
    car_id         integer REFERENCES cars(id) ON DELETE SET NULL,   -- машина каталога, для которой модель
    storage_key    text,                                              -- ключ GLB в S3
    size_bytes     bigint CHECK (size_bytes >= 0),
    sha256         char(64),
    stored_at      timestamptz,
    added_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'stored' OR storage_key IS NOT NULL),
    CHECK (status <> 'blocked' OR blocked_reason IS NOT NULL)
);
CREATE INDEX model_sources_car_idx ON model_sources (car_id) WHERE car_id IS NOT NULL;
CREATE INDEX model_sources_status_idx ON model_sources (status);
COMMENT ON TABLE model_sources IS 'Откуда взята каждая 3D-модель: автор, лицензия, ссылка; status=stored — файл лежит в S3';

-- ───────────── ТЗ: эмоциональные предпочтения и «игровой» подбор ─────────────
-- Теги стиля: ретро/футуризм, яркость/сдержанность, уют/спортивность. Размечаются командой самостоятельно.
CREATE TABLE style_tags (
    code      text PRIMARY KEY,
    label_ru  text NOT NULL,
    axis      text NOT NULL CHECK (axis IN ('era', 'expression', 'mood'))
);
INSERT INTO style_tags (code, label_ru, axis) VALUES
    ('retro', 'Ретро', 'era'), ('futurism', 'Футуризм', 'era'),
    ('bright', 'Яркая', 'expression'), ('restrained', 'Сдержанная', 'expression'),
    ('cozy', 'Уютная', 'mood'), ('sporty', 'Спортивная', 'mood')
ON CONFLICT DO NOTHING;

CREATE TABLE car_style_tags (
    car_id   integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    tag_code text    NOT NULL REFERENCES style_tags(code),
    weight   numeric(3,2) NOT NULL DEFAULT 1 CHECK (weight BETWEEN 0 AND 1),
    PRIMARY KEY (car_id, tag_code)
);

-- Слепые визуальные дуэли: пара машин, выбор «левая / правая / оба нравятся / ни одна»
CREATE TABLE style_duels (
    id          bigserial PRIMARY KEY,
    session_id  uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    round       smallint NOT NULL CHECK (round >= 1),
    car_a       integer NOT NULL REFERENCES cars(id),
    car_b       integer NOT NULL REFERENCES cars(id),
    choice      text CHECK (choice IN ('a', 'b', 'both', 'none')),
    answered_at timestamptz,
    CHECK (car_a <> car_b),
    UNIQUE (session_id, round)
);

-- Регулятор «хочу ↔ нужно»: 0 — только рациональное, 1 — только эмоциональное; по умолчанию баланс
ALTER TABLE sessions        ADD COLUMN want_need numeric(3,2) NOT NULL DEFAULT 0.5 CHECK (want_need BETWEEN 0 AND 1);
ALTER TABLE recommendations ADD COLUMN want_need numeric(3,2) CHECK (want_need BETWEEN 0 AND 1);  -- значение на момент выдачи

-- Три акцента выдачи: «Разумный выбор», «Выбор сердцем», «Твой баланс» + причины, сильные стороны, компромиссы
CREATE TYPE rec_accent AS ENUM ('reasonable', 'heart', 'balance');
ALTER TABLE recommendation_items
    ADD COLUMN accent      rec_accent,
    ADD COLUMN need_score  numeric(5,4) CHECK (need_score BETWEEN 0 AND 1),
    ADD COLUMN want_score  numeric(5,4) CHECK (want_score BETWEEN 0 AND 1),
    ADD COLUMN strengths   jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN compromises jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Гараж (saved_cars уже есть): отправка персонального гаража по ссылке
CREATE TABLE garage_shares (
    token      text PRIMARY KEY,
    user_id    bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '30 days'
);

-- Игра «угадай машину друга»: владелец загадывает машину из гаража, друг угадывает по подсказкам
CREATE TABLE guess_games (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   bigint  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    car_id     integer NOT NULL REFERENCES cars(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE guess_attempts (
    id           bigserial PRIMARY KEY,
    game_id      uuid    NOT NULL REFERENCES guess_games(id) ON DELETE CASCADE,
    guess_car_id integer NOT NULL REFERENCES cars(id),
    correct      boolean NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX guess_attempts_game_idx ON guess_attempts (game_id, id);

COMMIT;
