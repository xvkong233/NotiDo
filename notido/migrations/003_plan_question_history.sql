CREATE TABLE operation_plan_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL REFERENCES operations(id), plan_id TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)), created_at REAL NOT NULL);
CREATE INDEX operation_plan_history ON operation_plan_versions(operation_id,id);
INSERT INTO operation_plan_versions(operation_id,plan_id,payload,created_at) SELECT id,coalesce(json_extract(plan,'$.plan_id'),id),plan,created_at FROM operations;
CREATE TRIGGER operation_plan_insert AFTER INSERT ON operations BEGIN INSERT INTO operation_plan_versions(operation_id,plan_id,payload,created_at) VALUES (NEW.id,coalesce(json_extract(NEW.plan,'$.plan_id'),NEW.id),NEW.plan,unixepoch('subsec')); END;
CREATE TRIGGER operation_plan_replace AFTER UPDATE OF plan ON operations WHEN OLD.plan != NEW.plan BEGIN INSERT INTO operation_plan_versions(operation_id,plan_id,payload,created_at) VALUES (NEW.id,coalesce(json_extract(NEW.plan,'$.plan_id'),NEW.id),NEW.plan,unixepoch('subsec')); END;
CREATE TABLE question_history (question_ref TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES material_groups(id), session_id TEXT NOT NULL REFERENCES sessions(id), payload TEXT NOT NULL CHECK(json_valid(payload)), expires_at REAL NOT NULL, created_at REAL NOT NULL);
CREATE INDEX question_group_history ON question_history(group_id,created_at);
INSERT INTO question_history SELECT json_extract(question,'$.question_ref'),json_extract(question,'$.group_id'),id,question,question_expires,question_expires-1800 FROM sessions WHERE question IS NOT NULL;
