CREATE TABLE IF NOT EXISTS page_titles (
    page_url   TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
