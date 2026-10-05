-- Этап 1 миграции: сырой захват посуточных снапшотов карпулов.
--
-- Почему «сырой», а не финальная модель: задача первого этапа — перестать
-- терять данные, а не спроектировать всё сразу. Нормализация (person / sites /
-- presence) приедет следующим этапом и будет строиться ИЗ этой таблицы.
--
-- Что эта таблица чинит прямо сейчас:
--   1. Ротация week1..week4 даёт глубину ровно 28 дней, дальше week4
--      затирается безвозвратно. Здесь история живёт вечно.
--   2. В week2 намерено 366 дублей из 854 пар (водитель, дата): GAS-функция
--      hasSnapshotKey_ написана ровно для защиты от этого, но нигде не
--      вызывается. Первичный ключ делает дубль физически невозможным.
--   3. Пассажиры лежали в четырёх колонках Passenger1..4 — лимит был
--      артефактом листа. Здесь это массив с явным CHECK.

CREATE TABLE IF NOT EXISTS carpool_snapshot (
    snapshot_date date        NOT NULL,
    telegram_id   bigint      NOT NULL,
    driver_name   text        NOT NULL,
    phone         text        NOT NULL DEFAULT '',
    shift_raw     text        NOT NULL DEFAULT '',
    site_raw      text        NOT NULL DEFAULT '',
    passengers    text[]      NOT NULL DEFAULT '{}',

    -- Откуда строка: 'live' (ежедневный прогон) или 'backfill:week2' и т.п.
    source        text        NOT NULL,
    captured_at   timestamptz NOT NULL DEFAULT now(),
    -- Сколько раз эту пару (водитель, дата) записывали. >1 означает повторный
    -- прогон за день — как 21:54 и 23:19, породившие дубли в старой схеме.
    -- Здесь это не дубль, а видимый факт.
    capture_count integer     NOT NULL DEFAULT 1,

    CONSTRAINT carpool_snapshot_pk PRIMARY KEY (telegram_id, snapshot_date),
    CONSTRAINT carpool_snapshot_max_4 CHECK (cardinality(passengers) <= 4)
);

CREATE INDEX IF NOT EXISTS carpool_snapshot_date_idx
    ON carpool_snapshot (snapshot_date);

-- Журнал прогонов: с первого дня видно, что захват отработал и что именно он
-- увидел. Без этого «снапшот не записался» опять обнаружится через месяц.
CREATE TABLE IF NOT EXISTS capture_run (
    id           bigserial   PRIMARY KEY,
    source       text        NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    rows_read    integer     NOT NULL DEFAULT 0,
    rows_written integer     NOT NULL DEFAULT 0,
    rows_updated integer     NOT NULL DEFAULT 0,
    rows_skipped integer     NOT NULL DEFAULT 0,
    error        text
);
