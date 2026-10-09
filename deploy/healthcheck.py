import json
import sqlite3
import time
from pathlib import Path

root = Path("/AstrBot/data/plugin_data/astrbot_plugin_notido")
heartbeat = root / "health.json"
if not heartbeat.exists():
    raise SystemExit(1)
state = json.loads(heartbeat.read_text())
if time.time() - state["updated_at"] > 30 or state.get("maintenance"):
    raise SystemExit(1)
with sqlite3.connect(f"file:{root / 'notido.db'}?mode=ro", uri=True) as conn:
    conn.execute("SELECT 1 FROM schema_migrations").fetchone()
