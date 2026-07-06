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
            
            # Add certificate columns if they don't exist
            try:
                cursor.execute("ALTER TABLE sessions ADD COLUMN wg_private_key TEXT")
                cursor.execute("ALTER TABLE sessions ADD COLUMN wg_certificate TEXT")
                cursor.execute("ALTER TABLE sessions ADD COLUMN cert_expires_at INTEGER")
            except sqlite3.OperationalError:
                pass
            
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
            
            # Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()

    def set_setting(self, key: str, value: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO settings (key, value)
                VALUES (?, ?)
            """, (key, value))
            conn.commit()

    def get_setting(self, key: str, default: str = None) -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return row[0]
            return default

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

    def update_certificate(self, wg_private_key: str, wg_certificate: str, expires_at: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sessions SET 
                    wg_private_key = ?, 
                    wg_certificate = ?, 
                    cert_expires_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
            """, (wg_private_key, wg_certificate, expires_at))
            conn.commit()

    def save_servers(self, servers_list: List[Dict[str, Any]]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for srv in servers_list:
                cursor.execute("""
                    INSERT INTO servers (id, name, country, city, tier, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        tier=excluded.tier,
                        raw_json=excluded.raw_json
                """, (
                    str(srv.get("ID", srv.get("Name", "unknown"))),
                    srv.get("Name"),
                    srv.get("EntryCountry"),
                    srv.get("City"),
                    srv.get("Tier", 0),
                    json.dumps(srv)
                ))
            # Delete stale servers
            existing_ids = [str(s.get("ID", s.get("Name", "unknown"))) for s in servers_list]
            if existing_ids:
                placeholders = ','.join('?' * len(existing_ids))
                cursor.execute(f"DELETE FROM servers WHERE id NOT IN ({placeholders})", existing_ids)
            conn.commit()

    def update_localized_cities(self, cities_map: Dict[str, Dict[str, str]]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for country, city_dict in cities_map.items():
                for eng_city, loc_city in city_dict.items():
                    if loc_city:
                        cursor.execute("UPDATE servers SET city = ? WHERE country = ? AND city = ?", 
                                       (loc_city, country, eng_city))
            conn.commit()

    def get_server_count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM servers")
            return cursor.fetchone()[0]

    def get_all_servers(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM servers ORDER BY country, city, name")
            return [dict(row) for row in cursor.fetchall()]
