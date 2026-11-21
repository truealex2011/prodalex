-- Режим «новичок/эксперт» можно не выбирать при регистрации: тогда его спросят при первом поиске.
ALTER TABLE users ALTER COLUMN mode DROP NOT NULL;
ALTER TABLE users ALTER COLUMN mode DROP DEFAULT;
