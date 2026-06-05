-- ============================================================
-- init.sql – Datenbankinitialisierung für MyFeed
-- Wird vom PostgreSQL-Container beim ersten Start ausgeführt.
-- ============================================================

-- Erweiterungen aktivieren
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Tabelle: context_queue
-- Speichert rohe Kontext-Ereignisse von allen Clients.
-- Die Spalte "processed" trennt Ingest (Option 2) von der
-- asynchronen KI-Verarbeitung (Vektorisierung durch Worker).
-- ============================================================
CREATE TABLE IF NOT EXISTS context_queue (
    -- Eindeutige ID jedes Eintrags (UUID v4)
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Quelle des Ereignisses, z.B. 'browser_chrome', 'vscode', 'github_cron'
    source       VARCHAR(64) NOT NULL,

    -- Seitentitel oder Beschreibung des Kontexts
    title        TEXT NOT NULL,

    -- Optionale URL der Ressource
    url          TEXT,

    -- Extrahierter Seiteninhalt (Meta-Description + Text-Snippet, max. 2000 Zeichen)
    content      TEXT,

    -- Zeitstempel der Erfassung (mit Zeitzone)
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Option-2-Flag: false = noch nicht vom KI-Worker verarbeitet
    processed    BOOLEAN NOT NULL DEFAULT false,

    -- Embedding-Vektor (384 Dimensionen, kompatibel mit all-MiniLM-L6-v2).
    -- NULL, bis der asynchrone Worker das Embedding berechnet hat.
    embedding    VECTOR(384)
);

-- ============================================================
-- Indizes
-- ============================================================

-- Schneller Zugriff für den KI-Worker, der unverarbeitete Einträge
-- in der Reihenfolge ihres Eingangs abruft
CREATE INDEX IF NOT EXISTS idx_context_queue_processed
    ON context_queue (processed, created_at ASC);

-- Allgemeiner Index auf created_at für zeitbasierte Abfragen
CREATE INDEX IF NOT EXISTS idx_context_queue_created_at
    ON context_queue (created_at DESC);

-- HNSW-Index für Nearest-Neighbour-Suche auf dem Embedding-Vektor.
-- Wird vom späteren Such-/Empfehlungsservice genutzt.
-- ef_construction=128 ist ein guter Ausgangswert für ~1M Einträge.
CREATE INDEX IF NOT EXISTS idx_context_queue_embedding_hnsw
    ON context_queue
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

-- ============================================================
-- Tabelle: system_settings
-- Persistente Schlüssel-Wert-Einstellungen (z.B. Google-Cookies
-- für den Android-Scraper, Ollama-Konfiguration, Zeitpläne).
-- Wird via API-Gateway geschrieben und gelesen.
-- ============================================================
CREATE TABLE IF NOT EXISTS system_settings (
    key        VARCHAR(128) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Standard-Einstellungen für Ollama / Tag-Generierung / News-Suche
INSERT INTO system_settings (key, value, updated_at) VALUES
    ('ollama_url',                'http://localhost:11434',       NOW()),
    ('ollama_model',              '',                            NOW()),
    ('tag_schedule_1',            '23:30',                      NOW()),
    ('tag_schedule_2',            '',                            NOW()),
    ('news_duckduckgo_enabled',   'false',                       NOW()),
    ('news_searxng_enabled',      'false',                       NOW()),
    ('searxng_url',               'http://searxng:8080',         NOW()),
    ('news_ollama_rerank',        'false',                       NOW()),
    ('news_max_results_per_tag',  '5',                           NOW()),
    ('news_schedule',             '',                            NOW()),
    ('news_languages',            'de,en',                       NOW()),
    ('news_timelimit',            'w',                           NOW()),
    ('long_term_tags_enabled',    'false',                       NOW()),
    ('timeline_enabled',          'true',                        NOW())
ON CONFLICT (key) DO NOTHING;

-- ============================================================
-- Tabelle: tags
-- Semantische Themen-Tags, entweder KI-generiert ('auto')
-- oder manuell angelegt ('manual').
-- Gewichtung 1 (unwichtig) – 10 (dominantes Hauptthema).
-- Persistente Tags werden bei der automatischen Generierung
-- nicht überschrieben und können nur manuell gelöscht werden.
-- ============================================================
CREATE TABLE IF NOT EXISTS tags (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Tag-Name (eindeutig, Groß-/Kleinschreibung beachten)
    name         VARCHAR(128) NOT NULL,

    -- Gewichtung 1–10 (1 = allgemein, 10 = Hauptthema des Tages)
    weight       INTEGER NOT NULL DEFAULT 5 CHECK (weight BETWEEN 1 AND 10),

    -- 'auto' = KI-generiert, 'manual' = vom Benutzer angelegt
    type         VARCHAR(16) NOT NULL DEFAULT 'manual',

    -- Datum, für das der Tag generiert wurde (NULL bei manuellen Tags)
    source_date  DATE,

    -- Persistente Tags werden von der automatischen Generierung nicht überschrieben
    persistent   BOOLEAN NOT NULL DEFAULT false,

    -- Hauptkategorie des Interesses (z.B. 'Gaming', 'IT/Security')
    category        VARCHAR(64)  NOT NULL DEFAULT '',
    -- Wie wichtig ist diese Hauptkategorie für den Nutzer (1-10)
    category_weight INTEGER NOT NULL DEFAULT 5 CHECK (category_weight BETWEEN 1 AND 10),

    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT tags_name_unique UNIQUE (name)
);

-- ============================================================
-- Tabelle: news_results
-- Gefundene Nachrichtenartikel, die zu den aktuellen Tags passen.
-- ============================================================
CREATE TABLE IF NOT EXISTS news_results (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tag_name      VARCHAR(128) NOT NULL,
    tag_weight    INTEGER NOT NULL,
    headline      TEXT NOT NULL,
    snippet       TEXT,
    url           TEXT NOT NULL,
    image_url     VARCHAR(2048) DEFAULT '',
    source_name   VARCHAR(256),
    search_method VARCHAR(16) NOT NULL DEFAULT 'duckduckgo',
    found_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    published_at  TIMESTAMP WITH TIME ZONE,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT news_unique_url_tag_date UNIQUE (url, tag_name, found_date)
);

CREATE INDEX IF NOT EXISTS idx_news_found_date ON news_results (found_date DESC);
CREATE INDEX IF NOT EXISTS idx_news_tag ON news_results (tag_name, found_date DESC);

-- ============================================================
-- Tabelle: long_term_tags
-- Akkumulierter Langzeit-Interesse-Speicher. Wird bei jeder
-- Tag-Generierung automatisch aktualisiert (laufender Mittelwert).
-- Kann optional bei der News-Suche einbezogen werden.
-- ============================================================
-- ============================================================
-- Tabelle: activity_timeline
-- Persönliche Aktivitäts-Zeitachse, abgeleitet aus context_queue.
-- Wird bei jedem Ingest-Event ergänzt und NICHT täglich gelöscht.
-- ============================================================
CREATE TABLE IF NOT EXISTS activity_timeline (
    id               BIGSERIAL PRIMARY KEY,
    activity_date    DATE NOT NULL,
    activity_ts      TIMESTAMPTZ NOT NULL,
    source           VARCHAR(64) NOT NULL,
    title            TEXT NOT NULL,
    url              TEXT,
    domain           VARCHAR(255),
    icon_type        VARCHAR(32) NOT NULL DEFAULT 'web',
    context_queue_id UUID UNIQUE REFERENCES context_queue(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activity_timeline_date
    ON activity_timeline(activity_date DESC);
CREATE INDEX IF NOT EXISTS idx_activity_timeline_ts
    ON activity_timeline(activity_ts DESC);

CREATE TABLE IF NOT EXISTS long_term_tags (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(128) NOT NULL UNIQUE,
    weight          INTEGER NOT NULL DEFAULT 5 CHECK (weight BETWEEN 1 AND 10),
    category        VARCHAR(64)  NOT NULL DEFAULT '',
    category_weight INTEGER NOT NULL DEFAULT 5 CHECK (category_weight BETWEEN 1 AND 10),
    mention_count   INTEGER NOT NULL DEFAULT 1,
    first_seen      DATE NOT NULL DEFAULT CURRENT_DATE,
    last_seen       DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
