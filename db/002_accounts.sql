-- Аккаунты: пользователи, сохранённые машины, привязка поисков (сессий) к пользователю.
-- Применяется автоматически (app/database.py), если таблицы users ещё нет.
BEGIN;

CREATE TABLE users (
    id            bigserial PRIMARY KEY,
    email         text NOT NULL,
    password_hash text NOT NULL,
    display_name  text CHECK (display_name IS NULL OR char_length(display_name) BETWEEN 2 AND 40),
    mode          user_mode NOT NULL DEFAULT 'novice',      -- новичок/эксперт: единый для всех поисков
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_email_chk CHECK (email = lower(email) AND char_length(email) <= 254
                                      AND email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
);
CREATE UNIQUE INDEX users_email_key ON users (email);

CREATE TABLE saved_cars (
    user_id  bigint  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    car_id   integer NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    saved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, car_id)
);

-- История поиска = сессии пользователя (профиль, диалог и выдачи уже хранятся по session_id)
ALTER TABLE sessions ADD COLUMN user_id bigint REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX sessions_user_idx ON sessions (user_id, created_at DESC) WHERE user_id IS NOT NULL;

COMMENT ON TABLE users IS 'Аккаунты. Пароль — только хеш (werkzeug/scrypt)';
COMMIT;
