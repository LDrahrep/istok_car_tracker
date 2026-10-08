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

-- ────────────────────────────────────────────────────────────────────────
-- Этап 2: нормализованная модель.
--
-- Решения, которые она реализует (гриль 05.10.2026):
--   • объект — отдельная сущность, а не суффикс в имени листа и не часть
--     значения смены («Meltech Day»). Новый объект = одна строка;
--   • смена — только day/night;
--   • у человека есть «текущий объект» для удобства и как тай-брейк, но
--     истина для доплат — факт присутствия за конкретный день;
--   • emp_uuid из tabeli хранится рядом, но ключом быть не может: бейджи
--     сканируют только на AMAZON, у остальных его нет.
-- ────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS site (
    id         text        PRIMARY KEY,   -- AMAZON, MELTECH, COLUMBUS, BUFFALO
    name       text        NOT NULL,
    is_active  boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Псевдонимы в именах листов: «MELTEH», «Fluidstack TX», «CBUS». Держать их
-- в коде нельзя — новый объект тогда стоит правки кода и деплоя, а объекты
-- появляются чаще. Здесь новый объект это одна строка, заводится в админке.
ALTER TABLE site ADD COLUMN IF NOT EXISTS aliases text[] NOT NULL DEFAULT '{}';

-- Засев ТОЛЬКО на пустой таблице. Дальше справочником владеет администратор:
-- объекты переименовывают и объединяют (AMAZON стал TULANE), и повторный
-- засев при каждом старте бота воскрешал бы удалённые строки.
INSERT INTO site (id, name, aliases)
SELECT * FROM (VALUES
    ('AMAZON',     'Amazon',     ARRAY['AMAZON','AMZN']),
    ('MELTECH',    'Meltech',    ARRAY['MELTECH','MELTEH','MILTECH','MLT']),
    ('COLUMBUS',   'Columbus',   ARRAY['COLUMBUS','CLMB','CBUS']),
    ('BUFFALO',    'Buffalo',    ARRAY['BUFFALO','BUFF','BUF','BFLO'])
) AS seed(id, name, aliases)
WHERE NOT EXISTS (SELECT 1 FROM site);

CREATE TABLE IF NOT EXISTS person (
    id              bigserial   PRIMARY KEY,
    -- Ключ из tabeli. Есть не у всех, поэтому UNIQUE, но не NOT NULL.
    emp_uuid        text        UNIQUE,
    full_name       text        NOT NULL,
    -- Нормализованное имя: отсортированные токены в нижнем регистре.
    -- Ловит перестановку «Иванов Иван» / «Иван Иванов», на которой
    -- сопоставление по сырому имени регулярно разъезжалось.
    name_key        text        NOT NULL UNIQUE,
    shift           text        NOT NULL DEFAULT 'unknown'
                                CHECK (shift IN ('day', 'night', 'unknown')),
    -- Только у водителей: пассажиры ботом не пользуются.
    telegram_id     bigint      UNIQUE,
    -- Удобство и тай-брейк при конфликте, НЕ источник истины для доплат.
    current_site_id text        REFERENCES site(id),
    is_active       boolean     NOT NULL DEFAULT true,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Когда человек впервые появился. Без этого на вопрос «откуда взялись
-- 52 новых» ответить нечем: импорт трогает updated_at у всех строк сразу.
-- У записей, созданных до появления колонки, дата будет датой миграции.
ALTER TABLE person ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS person_site_idx ON person (current_site_id);
CREATE INDEX IF NOT EXISTS person_shift_idx ON person (shift);

-- Факт присутствия: кто, когда, на каком объекте. Именно это решает,
-- какому объекту засчитать день, — а не current_site_id.
CREATE TABLE IF NOT EXISTS presence (
    person_id  bigint      NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    work_date  date        NOT NULL,
    site_id    text        NOT NULL REFERENCES site(id),
    source     text        NOT NULL,      -- 'timesheet:<лист>' | 'tabeli'
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, work_date, site_id)
);

CREATE INDEX IF NOT EXISTS presence_date_idx ON presence (work_date);
CREATE INDEX IF NOT EXISTS presence_site_date_idx ON presence (site_id, work_date);

-- Журнал изменений. Append-only: ответ на вопрос «кто и когда это стёр»,
-- которого нам не хватало весь сентябрь.
CREATE TABLE IF NOT EXISTS audit_log (
    id         bigserial   PRIMARY KEY,
    happened_at timestamptz NOT NULL DEFAULT now(),
    actor      text        NOT NULL,      -- 'bot:<tg_id>' | 'import' | 'admin'
    action     text        NOT NULL,
    subject    text,
    details    jsonb
);

CREATE INDEX IF NOT EXISTS audit_log_time_idx ON audit_log (happened_at DESC);

-- Связь «кто кого вёз». Отсутствие этой таблицы было дырой: в сыром слое
-- пассажир — строка в массиве, ни с чем не связанная, поэтому любой вопрос
-- «а был ли он в тот день на объекте» требовал заново сопоставлять имена
-- нечётко. Ровно на этом проект уже обжёгся: Дайс 0.824 при пороге 0.82
-- склеил двух разных людей. Здесь пассажир — ссылка на person, и
-- сопоставление делается один раз, при импорте.
--
-- Два правила карпула жили только в Python (sheets.find_driver_for_passenger
-- и handlers.MAX_PASSENGERS). Здесь они становятся ограничениями, которые
-- нельзя обойти ни ботом, ни правкой в админке, ни кривым импортом.
CREATE TABLE IF NOT EXISTS carpool (
    ride_date    date     NOT NULL,
    driver_id    bigint   NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    passenger_id bigint   NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    -- Номера мест не выдумка: в листе это колонки Passenger1..4.
    seat         smallint NOT NULL CHECK (seat BETWEEN 1 AND 4),
    source       text     NOT NULL,   -- 'snapshot' | 'bot' | 'admin'
    created_at   timestamptz NOT NULL DEFAULT now(),

    -- Ключ БЕЗ водителя: у пассажира на дату может быть только одна строка,
    -- то есть только один водитель. Это и есть правило «уже записан
    -- к другому водителю», но теперь его невозможно нарушить.
    CONSTRAINT carpool_pk PRIMARY KEY (ride_date, passenger_id),
    -- Вместимость как уникальность: пятому пассажиру некуда сесть,
    -- все четыре номера заняты. Без триггеров и счётчиков.
    CONSTRAINT carpool_seat_uniq UNIQUE (ride_date, driver_id, seat),
    CONSTRAINT carpool_not_self CHECK (driver_id <> passenger_id)
);

CREATE INDEX IF NOT EXISTS carpool_driver_date_idx ON carpool (driver_id, ride_date);
CREATE INDEX IF NOT EXISTS carpool_date_idx ON carpool (ride_date);

-- ────────────────────────────────────────────────────────────────────────
-- Вьюхи для ручного разбора в админке.
--
-- Нужны потому, что сырые таблицы читаются плохо: passengers лежит
-- массивом и в гриде выглядит как {Иванов,Петров}, а вопросы у
-- администратора совсем другие — «с кем ездил этот человек», «кто
-- записан дважды», «кого нет в ростере». CREATE OR REPLACE —
-- пересоздаются при каждом старте бота вместе со схемой.
-- ────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_carpool_day AS
SELECT s.snapshot_date            AS дата,
       s.driver_name              AS водитель,
       s.shift_raw                AS смена,
       -- Колонка Site в листе хранит ШТАТ водителя (tn, ga, ca, ny),
       -- а не объект. Объект берётся из присутствия — см. v_credit_day.
       s.site_raw                 AS штат,
       s.passengers[1]            AS пассажир_1,
       s.passengers[2]            AS пассажир_2,
       s.passengers[3]            AS пассажир_3,
       s.passengers[4]            AS пассажир_4,
       cardinality(s.passengers)  AS занято_мест,
       cardinality(s.passengers) >= 2 AS хватает_для_зачёта,
       s.telegram_id              AS водитель_tg
FROM carpool_snapshot s;

-- «С кем ездил этот человек» — вопрос, который задаётся чаще всего.
CREATE OR REPLACE VIEW v_passenger_rides AS
SELECT s.snapshot_date AS дата,
       p               AS пассажир,
       s.driver_name   AS водитель,
       s.shift_raw     AS смена,
       s.site_raw      AS штат,
       s.telegram_id   AS водитель_tg
FROM carpool_snapshot s, unnest(s.passengers) p;

-- Нормализованное имя: отсортированные слова в нижнем регистре. Тот же
-- принцип, что в person.name_key, — иначе «Akmal Shah» и «Shah Akmal»
-- выглядят как разные люди.
CREATE OR REPLACE VIEW v_name_key AS
SELECT s.snapshot_date AS дата, s.driver_name AS водитель, p AS написание,
       (SELECT string_agg(w, ' ' ORDER BY w)
        FROM unnest(string_to_array(lower(btrim(p)), ' ')) w WHERE w <> '') AS ключ
FROM carpool_snapshot s, unnest(s.passengers) p;

-- Один человек у двух водителей в один день.
CREATE OR REPLACE VIEW v_conflicts AS
SELECT дата, ключ AS имя_нормализованное,
       string_agg(DISTINCT написание, ' / ') AS написания,
       string_agg(DISTINCT водитель, ' | ')  AS водители,
       count(DISTINCT водитель)              AS сколько_водителей
FROM v_name_key GROUP BY дата, ключ HAVING count(DISTINCT водитель) > 1;

-- Пассажиры, которых нет в ростере.
CREATE OR REPLACE VIEW v_unknown_passengers AS
SELECT n.написание AS имя, count(DISTINCT n.дата) AS дней,
       min(n.дата) AS с, max(n.дата) AS по
FROM v_name_key n LEFT JOIN person pe ON pe.name_key = n.ключ
WHERE pe.id IS NULL GROUP BY n.написание;

-- Правило доплат целиком: водитель отмечен в табеле И не меньше двух
-- пассажиров. Присутствие пассажиров не учитывается — решение 06.10.
CREATE OR REPLACE VIEW v_credit_day AS
SELECT c.ride_date   AS дата,
       d.full_name   AS водитель,
       d.current_site_id AS объект,
       count(*)      AS пассажиров,
       EXISTS (SELECT 1 FROM presence pr
               WHERE pr.person_id = c.driver_id AND pr.work_date = c.ride_date)
                     AS отмечен_в_табеле,
       count(*) >= 2 AND EXISTS (SELECT 1 FROM presence pr
               WHERE pr.person_id = c.driver_id AND pr.work_date = c.ride_date)
                     AS день_засчитан
FROM carpool c JOIN person d ON d.id = c.driver_id
GROUP BY c.ride_date, c.driver_id, d.full_name, d.current_site_id;
