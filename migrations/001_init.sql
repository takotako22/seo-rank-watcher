CREATE TABLE IF NOT EXISTS rank_snapshots (
    id              SERIAL PRIMARY KEY,
    page_url        TEXT NOT NULL,
    snapshot_date   DATE NOT NULL,
    week_of_year    INT NOT NULL,   -- ISO week number
    month           INT NOT NULL,
    clicks          INT NOT NULL DEFAULT 0,
    impressions     INT NOT NULL DEFAULT 0,
    ctr             NUMERIC(5,4),
    avg_position    NUMERIC(6,2),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (page_url, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_rank_snapshots_url        ON rank_snapshots (page_url);
CREATE INDEX IF NOT EXISTS idx_rank_snapshots_week       ON rank_snapshots (week_of_year, month);
CREATE INDEX IF NOT EXISTS idx_rank_snapshots_date       ON rank_snapshots (snapshot_date);

-- 記事ごとの需要シーズン（GSCクリック推移から自動推定した結果を格納）
CREATE TABLE IF NOT EXISTS article_seasons (
    id              SERIAL PRIMARY KEY,
    page_url        TEXT NOT NULL UNIQUE,
    peak_months     INT[] NOT NULL DEFAULT '{}',  -- e.g. {5,6,7}
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
