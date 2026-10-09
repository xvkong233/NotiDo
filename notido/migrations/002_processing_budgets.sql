CREATE TABLE processing_budgets (group_id TEXT PRIMARY KEY REFERENCES material_groups(id), started_at REAL NOT NULL, deadline_at REAL NOT NULL, seconds INTEGER NOT NULL CHECK(seconds>0));
