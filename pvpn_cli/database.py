import sqlite3
import os
import json
import platform
from typing import Dict, Any, List, Optional

class Database:
    def __init__(self):
        from pvpn_cli.routing import get_config_dir
        config_dir = get_config_dir()
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
            columns_to_add = [
                "wg_private_key TEXT",
                "wg_certificate TEXT",
                "cert_expires_at INTEGER",
                "cert_refresh_at INTEGER"
            ]
            for col in columns_to_add:
                try:
                    cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col}")
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
                    load INTEGER DEFAULT 0,
                    raw_json TEXT
                )
            """)

            # Check if load column exists (for migration)
            try:
                cursor.execute("ALTER TABLE servers ADD COLUMN load INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

            # Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            # AWG Configs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS awg_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    params TEXT,
                    junk_level INTEGER DEFAULT 0
                )
            """)

            # Recent connections table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recent_connections (
                    id TEXT PRIMARY KEY,
                    last_connected TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Traffic statistics table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS traffic_stats (
                    date TEXT PRIMARY KEY,
                    rx_bytes INTEGER DEFAULT 0,
                    tx_bytes INTEGER DEFAULT 0
                )
            """)

            # Add junk_level column if it doesn't exist (for migration)
            try:
                cursor.execute("ALTER TABLE awg_configs ADD COLUMN junk_level INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

            # Insert default undeletable AWG configs
            defaults = [
                ('vpn-next-default', 'vpn-next-default', 0),
                ('preset-off', 'preset-off', 4),
                ('preset-low', 'preset-low', 0),
                ('preset-medium', 'preset-medium', 1),
                ('preset-high', 'preset-high', 2)
            ]
            for name, params, junk in defaults:
                cursor.execute("""
                    INSERT OR IGNORE INTO awg_configs (name, params, junk_level)
                    VALUES (?, ?, ?)
                """, (name, params, junk))

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

    def add_awg_config(self, name: str, params: str, junk_level: int = 3):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO awg_configs (name, params, junk_level)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    params = excluded.params,
                    junk_level = excluded.junk_level
            """, (name, params, junk_level))
            conn.commit()

    def delete_awg_config(self, identifier: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if identifier.isdigit():
                cursor.execute("DELETE FROM awg_configs WHERE id = ?", (int(identifier),))
            else:
                cursor.execute("DELETE FROM awg_configs WHERE name = ?", (identifier,))
            conn.commit()
            return cursor.rowcount > 0

    def get_awg_configs(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM awg_configs ORDER BY id ASC")
            return [dict(row) for row in cursor.fetchall()]

    def get_awg_config(self, identifier: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if identifier.isdigit():
                cursor.execute("SELECT * FROM awg_configs WHERE id = ?", (int(identifier),))
            else:
                cursor.execute("SELECT * FROM awg_configs WHERE name = ?", (identifier,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def save_session(self, access_token: str, refresh_token: str, uid: str, user_id: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (id, access_token, refresh_token, uid, user_id, updated_at)
                VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    access_token=excluded.access_token,
                    refresh_token=excluded.refresh_token,
                    uid=excluded.uid,
                    user_id=excluded.user_id,
                    updated_at=CURRENT_TIMESTAMP
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

    def update_certificate(self, wg_private_key: str, wg_certificate: str, expires_at: int, refresh_at: int = 0):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (id, wg_private_key, wg_certificate, cert_expires_at, cert_refresh_at, updated_at)
                VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    wg_private_key=excluded.wg_private_key,
                    wg_certificate=excluded.wg_certificate,
                    cert_expires_at=excluded.cert_expires_at,
                    cert_refresh_at=excluded.cert_refresh_at,
                    updated_at=CURRENT_TIMESTAMP
            """, (wg_private_key, wg_certificate, expires_at, refresh_at))
            conn.commit()

    def save_servers(self, servers_list: List[Dict[str, Any]]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for srv in servers_list:
                cursor.execute("""
                    INSERT INTO servers (id, name, country, city, tier, load, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        tier=excluded.tier,
                        load=excluded.load,
                        raw_json=excluded.raw_json
                """, (
                    str(srv.get("ID", srv.get("Name", "unknown"))),
                    srv.get("Name"),
                    srv.get("EntryCountry"),
                    srv.get("City"),
                    srv.get("Tier", 0),
                    srv.get("Load", 0),
                    json.dumps(srv)
                ))
            # Delete stale servers
            existing_ids = [str(s.get("ID", s.get("Name", "unknown"))) for s in servers_list]
            if existing_ids:
                placeholders = ','.join('?' * len(existing_ids))
                cursor.execute(f"DELETE FROM servers WHERE id NOT IN ({placeholders})", existing_ids)
            conn.commit()

    def update_server_loads(self, loads_list: List[Dict[str, Any]]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            updated_count = 0
            for item in loads_list:
                server_id = str(item.get("ID") or item.get("id"))
                load = item.get("Load") or item.get("load") or 0

                cursor.execute("UPDATE servers SET load = ? WHERE id = ?", (load, server_id))
                if cursor.rowcount > 0:
                    updated_count += 1
            conn.commit()
            print(f"[Database] Updated load for {updated_count} servers.", flush=True)

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

    def add_recent_connection(self, server_id: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO recent_connections (id, last_connected)
                VALUES (?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET last_connected=CURRENT_TIMESTAMP
            """, (server_id,))
            conn.commit()

    def get_recent_connections(self, limit: int = 5) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.* FROM servers s
                JOIN recent_connections r ON s.id = r.id
                ORDER BY r.last_connected DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def update_traffic_stats(self, rx_delta: int, tx_delta: int):
        from datetime import date
        today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO traffic_stats (date, rx_bytes, tx_bytes)
                VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    rx_bytes = rx_bytes + excluded.rx_bytes,
                    tx_bytes = tx_bytes + excluded.tx_bytes
            """, (today, rx_delta, tx_delta))
            conn.commit()

    def get_traffic_stats(self, date_str: str) -> Dict[str, int]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT rx_bytes, tx_bytes FROM traffic_stats WHERE date = ?", (date_str,))
            row = cursor.fetchone()
            if row:
                return {"rx": row[0], "tx": row[1]}
            return {"rx": 0, "tx": 0}

    def get_historical_stats(self) -> Dict[str, Dict[str, int]]:
        from datetime import date, timedelta
        import calendar

        today = date.today()
        first_of_month = today.replace(day=1)
        first_of_year = today.replace(month=1, day=1)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Today
            cursor.execute("SELECT rx_bytes, tx_bytes FROM traffic_stats WHERE date = ?", (today.isoformat(),))
            t_row = cursor.fetchone()
            today_stats = {"rx": t_row[0] if t_row else 0, "tx": t_row[1] if t_row else 0}

            # Month
            cursor.execute("SELECT SUM(rx_bytes), SUM(tx_bytes) FROM traffic_stats WHERE date >= ?", (first_of_month.isoformat(),))
            m_row = cursor.fetchone()
            month_stats = {"rx": m_row[0] if m_row[0] else 0, "tx": m_row[1] if m_row[1] else 0}

            # Year
            cursor.execute("SELECT SUM(rx_bytes), SUM(tx_bytes) FROM traffic_stats WHERE date >= ?", (first_of_year.isoformat(),))
            y_row = cursor.fetchone()
            year_stats = {"rx": y_row[0] if y_row[0] else 0, "tx": y_row[1] if y_row[1] else 0}

            return {
                "today": today_stats,
                "month": month_stats,
                "year": year_stats
            }
