CREATE TABLE native_group_outcomes (group_id TEXT PRIMARY KEY REFERENCES material_groups(id), account_ref TEXT NOT NULL REFERENCES account_scopes(id), credential_generation INTEGER NOT NULL, instance_id TEXT NOT NULL, actor_key TEXT NOT NULL, session_key TEXT NOT NULL REFERENCES sessions(id), declaration TEXT CHECK(declaration IS NULL OR json_valid(declaration)), updated_at REAL NOT NULL);
DROP INDEX one_collecting_group;
CREATE UNIQUE INDEX one_collecting_group ON material_groups(session_id) WHERE state='collecting' AND mode IN ('automatic','explicit');
