-- Finviz Analyzer Schema

CREATE TABLE IF NOT EXISTS digest_runs (
    id          SERIAL PRIMARY KEY,
    run_date    DATE        NOT NULL,
    created_at  TIMESTAMP   DEFAULT NOW(),
    status      VARCHAR(20) DEFAULT 'pending',  -- pending, success, failed
    article_count INTEGER,
    error_message TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_digest_runs_date ON digest_runs(run_date);

CREATE TABLE IF NOT EXISTS articles (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER     REFERENCES digest_runs(id) ON DELETE CASCADE,
    url             TEXT        NOT NULL,
    headline        TEXT,
    source          TEXT,
    ticker          TEXT,
    full_text       TEXT,
    text_truncated  BOOLEAN     DEFAULT FALSE,
    paywall         BOOLEAN     DEFAULT FALSE,
    crawl_error     TEXT,
    crawled_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_articles_run_id ON articles(run_id);
CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);

CREATE TABLE IF NOT EXISTS article_analysis (
    id              SERIAL PRIMARY KEY,
    article_id      INTEGER     REFERENCES articles(id) ON DELETE CASCADE,
    run_id          INTEGER     REFERENCES digest_runs(id) ON DELETE CASCADE,
    relevance_score INTEGER,    -- 1-10
    upshot          TEXT,
    theme           TEXT,
    asset_class     TEXT,
    is_duplicate    BOOLEAN     DEFAULT FALSE,
    duplicate_of_id INTEGER     REFERENCES article_analysis(id)
);

CREATE INDEX IF NOT EXISTS idx_article_analysis_run_id ON article_analysis(run_id);
CREATE INDEX IF NOT EXISTS idx_article_analysis_theme ON article_analysis(theme);

CREATE TABLE IF NOT EXISTS themes (
    id                  SERIAL PRIMARY KEY,
    run_id              INTEGER     REFERENCES digest_runs(id) ON DELETE CASCADE,
    name                TEXT        NOT NULL,
    summary             TEXT,
    article_count       INTEGER,
    convergence_signal  TEXT,       -- strong, moderate, weak, none
    convergence_note    TEXT
);

CREATE INDEX IF NOT EXISTS idx_themes_run_id ON themes(run_id);

CREATE TABLE IF NOT EXISTS recommendations (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER     REFERENCES digest_runs(id) ON DELETE CASCADE,
    title           TEXT,
    recommendation  TEXT,
    direction       TEXT,       -- bullish, bearish, neutral
    sector          TEXT,
    confidence      TEXT,       -- high, medium, low
    rationale       TEXT,
    supporting_theme TEXT,
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendations_run_id ON recommendations(run_id);

CREATE TABLE IF NOT EXISTS digests (
    id          SERIAL PRIMARY KEY,
    run_id      INTEGER     REFERENCES digest_runs(id) ON DELETE CASCADE,
    html_body   TEXT,
    sent_at     TIMESTAMP,
    sent_to     TEXT,
    executive_summary TEXT
);
