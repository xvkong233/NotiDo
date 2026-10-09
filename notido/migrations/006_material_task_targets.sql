CREATE TABLE material_task_targets (
    group_id TEXT PRIMARY KEY REFERENCES material_groups(id),
    account_ref TEXT NOT NULL REFERENCES account_scopes(id),
    credential_generation INTEGER NOT NULL,
    config_revision INTEGER NOT NULL,
    task_id TEXT NOT NULL
);
CREATE INDEX operation_remote_history ON operations(account_ref,remote_id,state,kind,created_at);
