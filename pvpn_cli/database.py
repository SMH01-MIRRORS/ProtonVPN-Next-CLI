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
        self.timeout = 30
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, timeout=self.timeout)

    def _init_db(self):
        with self._get_connection() as conn:
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

            # Snapshot columns so recents survive server list rotation:
            # Proton rotates free servers, save_servers() deletes stale rows
            # from `servers`, and recents must stay displayable regardless.
            for col in ["name TEXT", "country TEXT", "city TEXT"]:
                try:
                    cursor.execute(f"ALTER TABLE recent_connections ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass

            # Traffic statistics table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS traffic_stats (
                    date TEXT PRIMARY KEY,
                    rx_bytes INTEGER DEFAULT 0,
                    tx_bytes INTEGER DEFAULT 0,
                    usage_seconds INTEGER DEFAULT 0
                )
            """)

            # Granular traffic stats (hourly)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS traffic_stats_hourly (
                    hour TEXT PRIMARY KEY,
                    rx_bytes INTEGER DEFAULT 0,
                    tx_bytes INTEGER DEFAULT 0,
                    usage_seconds INTEGER DEFAULT 0
                )
            """)

            # Add usage_seconds column if it doesn't exist
            try:
                cursor.execute("ALTER TABLE traffic_stats ADD COLUMN usage_seconds INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

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
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO settings (key, value)
                VALUES (?, ?)
            """, (key, value))
            conn.commit()

    def get_setting(self, key: str, default: str = None) -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return row[0]
            return default

    def add_awg_config(self, name: str, params: str, junk_level: int = 3):
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if identifier.isdigit():
                cursor.execute("DELETE FROM awg_configs WHERE id = ?", (int(identifier),))
            else:
                cursor.execute("DELETE FROM awg_configs WHERE name = ?", (identifier,))
            conn.commit()
            return cursor.rowcount > 0

    def get_awg_configs(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM awg_configs ORDER BY id ASC")
            return [dict(row) for row in cursor.fetchall()]

    def get_awg_config(self, identifier: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if identifier.isdigit():
                cursor.execute("SELECT * FROM awg_configs WHERE id = ?", (int(identifier),))
            else:
                cursor.execute("SELECT * FROM awg_configs WHERE name = ?", (identifier,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def save_session(self, access_token: str, refresh_token: str, uid: str, user_id: str):
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions WHERE id = 1")
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def update_certificate(self, wg_private_key: str, wg_certificate: str, expires_at: int, refresh_at: int = 0):
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for srv in servers_list:
                cursor.execute("""
                    INSERT INTO servers (id, name, country, city, tier, load, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        country=excluded.country,
                        city=COALESCE((SELECT NULLIF(city, json_extract(raw_json, '$.City')) FROM servers WHERE id=excluded.id), excluded.city),
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for country, city_dict in cities_map.items():
                for eng_city, loc_city in city_dict.items():
                    if loc_city:
                        cursor.execute("UPDATE servers SET city = ? WHERE country = ? AND city = ?", 
                                       (loc_city, country, eng_city))
            conn.commit()

    def get_server_count(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM servers")
            return cursor.fetchone()[0]

    def get_all_servers(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM servers ORDER BY country, city, name")
            return [dict(row) for row in cursor.fetchall()]

    def add_recent_connection(self, server_id: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Snapshot display fields now: the server may later disappear
            # from `servers` (free server rotation), and the recent entry
            # must remain displayable on its own.
            cursor.execute("SELECT name, country, city FROM servers WHERE id = ?", (server_id,))
            snapshot = cursor.fetchone() or (None, None, None)
            cursor.execute("""
                INSERT INTO recent_connections (id, last_connected, name, country, city)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_connected=CURRENT_TIMESTAMP,
                    name=COALESCE(excluded.name, name),
                    country=COALESCE(excluded.country, country),
                    city=COALESCE(excluded.city, city)
            """, (server_id, snapshot[0], snapshot[1], snapshot[2]))
            conn.commit()

    def get_recent_connections(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # LEFT JOIN + snapshot fallback: a recent must not vanish just
            # because the server left the current list (free server rotation).
            # `available` tells consumers whether the server still exists.
            cursor.execute("""
                SELECT
                    r.id AS id,
                    COALESCE(s.name, r.name) AS name,
                    COALESCE(s.country, r.country) AS country,
                    COALESCE(s.city, r.city) AS city,
                    s.tier AS tier,
                    s."load" AS "load",
                    s.raw_json AS raw_json,
                    r.last_connected AS last_connected,
                    CASE WHEN s.id IS NULL THEN 0 ELSE 1 END AS available
                FROM recent_connections r
                LEFT JOIN servers s ON s.id = r.id
                WHERE COALESCE(s.name, r.name) IS NOT NULL
                ORDER BY r.last_connected DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def update_traffic_stats(self, rx_delta: int, tx_delta: int, usage_delta: int = 0):
        from datetime import datetime
        now = datetime.now()
        today = now.date().isoformat()
        hour = now.strftime("%Y-%m-%dT%H:00:00")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Update daily stats
            cursor.execute("""
                INSERT INTO traffic_stats (date, rx_bytes, tx_bytes, usage_seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    rx_bytes = rx_bytes + excluded.rx_bytes,
                    tx_bytes = tx_bytes + excluded.tx_bytes,
                    usage_seconds = usage_seconds + excluded.usage_seconds
            """, (today, rx_delta, tx_delta, usage_delta))

            # Update hourly stats
            cursor.execute("""
                INSERT INTO traffic_stats_hourly (hour, rx_bytes, tx_bytes, usage_seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(hour) DO UPDATE SET
                    rx_bytes = rx_bytes + excluded.rx_bytes,
                    tx_bytes = tx_bytes + excluded.tx_bytes,
                    usage_seconds = usage_seconds + excluded.usage_seconds
            """, (hour, rx_delta, tx_delta, usage_delta))

            # Cleanup old hourly stats (keep last 48 hours)
            cursor.execute("DELETE FROM traffic_stats_hourly WHERE hour < datetime('now', '-48 hours')")

            conn.commit()

    def get_traffic_stats(self, date_str: str) -> Dict[str, int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT rx_bytes, tx_bytes, usage_seconds FROM traffic_stats WHERE date = ?", (date_str,))
            row = cursor.fetchone()
            if row:
                return {"rx": row[0], "tx": row[1], "usage": row[2]}
            return {"rx": 0, "tx": 0, "usage": 0}

    def get_historical_stats(self) -> Dict[str, Any]:
        from datetime import date, datetime, timedelta
        import calendar

        today = date.today()
        first_of_month = today.replace(day=1)
        first_of_year = today.replace(month=1, day=1)

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Today
            cursor.execute("SELECT rx_bytes, tx_bytes, usage_seconds FROM traffic_stats WHERE date = ?", (today.isoformat(),))
            t_row = cursor.fetchone()
            today_stats = {"rx": t_row['rx_bytes'] if t_row else 0, "tx": t_row['tx_bytes'] if t_row else 0, "usage": t_row['usage_seconds'] if t_row else 0}

            # Month
            cursor.execute("SELECT SUM(rx_bytes), SUM(tx_bytes), SUM(usage_seconds) FROM traffic_stats WHERE date >= ?", (first_of_month.isoformat(),))
            m_row = cursor.fetchone()
            month_stats = {
                "rx": m_row[0] if m_row and m_row[0] else 0,
                "tx": m_row[1] if m_row and m_row[1] else 0,
                "usage": m_row[2] if m_row and m_row[2] else 0
            }

            # Year
            cursor.execute("SELECT SUM(rx_bytes), SUM(tx_bytes), SUM(usage_seconds) FROM traffic_stats WHERE date >= ?", (first_of_year.isoformat(),))
            y_row = cursor.fetchone()
            year_stats = {
                "rx": y_row[0] if y_row and y_row[0] else 0,
                "tx": y_row[1] if y_row and y_row[1] else 0,
                "usage": y_row[2] if y_row and y_row[2] else 0
            }

            # Daily Chart Data (Last 24 hours)
            cursor.execute("SELECT * FROM traffic_stats_hourly ORDER BY hour DESC LIMIT 24")
            daily_chart = [dict(row) for row in cursor.fetchall()]

            # Monthly Chart Data (Last 30 days)
            cursor.execute("SELECT * FROM traffic_stats WHERE date >= ? ORDER BY date DESC LIMIT 30", ((today - timedelta(days=30)).isoformat(),))
            monthly_chart = [dict(row) for row in cursor.fetchall()]

            # Yearly Chart Data (Last 12 months)
            # Grouping by month
            cursor.execute("""
                SELECT strftime('%Y-%m', date) as month, SUM(rx_bytes) as rx_bytes, SUM(tx_bytes) as tx_bytes, SUM(usage_seconds) as usage_seconds
                FROM traffic_stats
                GROUP BY month
                ORDER BY month DESC
                LIMIT 12
            """)
            yearly_chart = [dict(row) for row in cursor.fetchall()]

            return {
                "summary": {
                    "today": today_stats,
                    "month": month_stats,
                    "year": year_stats
                },
                "charts": {
                    "daily": daily_chart[::-1],
                    "monthly": monthly_chart[::-1],
                    "yearly": yearly_chart[::-1]
                }
            }
