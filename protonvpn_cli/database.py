import sqlite3
import os
import json
from typing import Dict, Any, List, Optional

class Database:
    def __init__(self):
        config_dir = os.path.expanduser("~/.config/protonvpn-next")
        os.makedirs(config_dir, exist_ok=True)
        self.db_path = os.path.join(config_dir, "protonvpn.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Sessions table: we only store the active session
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    access_token TEXT,
                    refresh_token TEXT,
                    uid TEXT,
                    user_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Servers table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS servers (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    country TEXT,
                    city TEXT,
                    tier INTEGER,
                    raw_json TEXT
                )
            """)
            conn.commit()

    def save_session(self, access_token: str, refresh_token: str, uid: str, user_id: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sessions (id, access_token, refresh_token, uid, user_id, updated_at)
                VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (access_token, refresh_token, uid, user_id))
            conn.commit()

    def get_session(self) -> Optional[Dict[str, str]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions WHERE id = 1")
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def save_servers(self, servers_list: List[Dict[str, Any]]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM servers") # Clear old servers
            
            for s in servers_list:
                sid = str(s.get("ID", s.get("Name", "unknown")))
                name = s.get("Name", "")
                country = s.get("EntryCountry", "")
                city = s.get("City", "")
                tier = s.get("Tier", 0)
                raw_json = json.dumps(s)
                cursor.execute("""
                    INSERT INTO servers (id, name, country, city, tier, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (sid, name, country, city, tier, raw_json))
            conn.commit()

    def get_server_count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM servers")
            return cursor.fetchone()[0]
