-- サイト管理テーブル
CREATE TABLE IF NOT EXISTS sites (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    gsc_site_url TEXT NOT NULL,
    url_prefix  TEXT NOT NULL,
    ga4_property_id TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 既存テーブルに site_id を追加
ALTER TABLE rank_snapshots   ADD COLUMN IF NOT EXISTS site_id INT NOT NULL DEFAULT 1;
ALTER TABLE article_seasons  ADD COLUMN IF NOT EXISTS site_id INT NOT NULL DEFAULT 1;
ALTER TABLE page_titles      ADD COLUMN IF NOT EXISTS site_id INT NOT NULL DEFAULT 1;

-- インデックス
CREATE INDEX IF NOT EXISTS idx_rank_snapshots_site ON rank_snapshots  (site_id);
CREATE INDEX IF NOT EXISTS idx_article_seasons_site ON article_seasons (site_id);
CREATE INDEX IF NOT EXISTS idx_page_titles_site     ON page_titles     (site_id);

-- UNIQUE制約を site_id 込みに変更
ALTER TABLE rank_snapshots   DROP CONSTRAINT IF EXISTS rank_snapshots_page_url_snapshot_date_key;
ALTER TABLE rank_snapshots   ADD  CONSTRAINT rank_snapshots_site_url_date_key UNIQUE (site_id, page_url, snapshot_date);

ALTER TABLE article_seasons  DROP CONSTRAINT IF EXISTS article_seasons_page_url_key;
ALTER TABLE article_seasons  ADD  CONSTRAINT article_seasons_site_url_key UNIQUE (site_id, page_url);

ALTER TABLE page_titles      DROP CONSTRAINT IF EXISTS page_titles_pkey;
ALTER TABLE page_titles      ADD  CONSTRAINT page_titles_site_url_key UNIQUE (site_id, page_url);
