CREATE TABLE retention_checks (group_id TEXT PRIMARY KEY REFERENCES material_groups(id), checked_at REAL NOT NULL, body_purged_at REAL, summary_purged_at REAL);
CREATE TABLE task_completion_observations (account_ref TEXT NOT NULL REFERENCES account_scopes(id), project_id TEXT NOT NULL, task_id TEXT NOT NULL, observed_completed_at REAL NOT NULL, PRIMARY KEY(account_ref,project_id,task_id));
CREATE TABLE blob_tombstones (hash TEXT PRIMARY KEY REFERENCES blobs(hash), removed_at REAL NOT NULL, unlinked_at REAL);
ALTER TABLE operation_plan_versions ADD COLUMN payload_hash TEXT;
ALTER TABLE operation_plan_versions ADD COLUMN expired_at REAL;
