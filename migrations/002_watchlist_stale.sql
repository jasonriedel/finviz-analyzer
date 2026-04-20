-- Migration 002: watchlist table + stale flags on article_analysis

ALTER TABLE article_analysis
    ADD COLUMN IF NOT EXISTS is_stale   BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS stale_note TEXT;

CREATE TABLE IF NOT EXISTS watchlist (
    id          SERIAL PRIMARY KEY,
    run_id      INTEGER     REFERENCES digest_runs(id) ON DELETE CASCADE,
    ticker      TEXT        NOT NULL,
    company     TEXT,
    reason      TEXT,
    direction   TEXT,   -- bullish, bearish, watch
    urgency     TEXT,   -- today, this_week
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_watchlist_run_id ON watchlist(run_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_ticker ON watchlist(ticker);
