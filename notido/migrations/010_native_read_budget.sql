CREATE TABLE native_read_budgets (
    group_id TEXT PRIMARY KEY REFERENCES material_groups(id),
    limit_seconds REAL NOT NULL CHECK(limit_seconds > 0),
    charged_seconds REAL NOT NULL DEFAULT 0 CHECK(charged_seconds >= 0 AND charged_seconds <= limit_seconds)
);
CREATE TABLE native_asset_reads (
    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL REFERENCES native_read_budgets(group_id),
    hash TEXT NOT NULL,
    reservation TEXT,
    reserved_seconds REAL NOT NULL DEFAULT 0 CHECK(reserved_seconds >= 0),
    payload TEXT CHECK(payload IS NULL OR json_valid(payload))
);
CREATE INDEX native_asset_reads_group ON native_asset_reads(group_id);
