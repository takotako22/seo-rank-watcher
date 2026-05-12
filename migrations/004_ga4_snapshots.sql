-- GA4 週次スナップショット
CREATE TABLE IF NOT EXISTS ga4_snapshots (
    id                      SERIAL PRIMARY KEY,
    site_id                 INTEGER NOT NULL REFERENCES sites(id),
    page_url                TEXT NOT NULL,
    snapshot_date           DATE NOT NULL,
    sessions                INTEGER DEFAULT 0,
    pageviews               INTEGER DEFAULT 0,
    engagement_rate         NUMERIC(5,4) DEFAULT 0,   -- 0.00〜1.00
    avg_engagement_time_sec NUMERIC(8,2) DEFAULT 0,   -- 秒
    created_at              TIMESTAMP DEFAULT NOW(),
    UNIQUE (site_id, page_url, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_ga4_site_date ON ga4_snapshots (site_id, snapshot_date);
