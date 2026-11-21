-- 3D-модели и фото, найденные автоматически (Sketchfab / Wikimedia Commons).
-- origin отделяет найденное от данных каталога: перезагрузка cars.json не стирает найденные файлы.
ALTER TABLE car_assets
    ADD COLUMN origin      text NOT NULL DEFAULT 'catalog' CHECK (origin IN ('catalog', 'sketchfab', 'wikimedia')),
    ADD COLUMN source_url  text,                 -- страница модели / файла у источника
    ADD COLUMN title       text,
    ADD COLUMN license_url text;

-- Результат поиска по каждой машине: не ходим в сеть при каждом открытии
CREATE TABLE car_media_checks (
    car_id       integer PRIMARY KEY REFERENCES cars(id) ON DELETE CASCADE,
    checked_at   timestamptz NOT NULL DEFAULT now(),
    model_found  boolean NOT NULL DEFAULT false,
    photos_found integer NOT NULL DEFAULT 0,
    error        text                            -- не пусто, если источник был недоступен: повторим скоро
);
COMMENT ON TABLE car_media_checks IS 'Когда и с каким итогом искали 3D-модель и фото машины';
