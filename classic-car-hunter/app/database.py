"""SQLite database management for Classic Car Hunter (Multi-Partner On-Demand Import Platform)."""

from __future__ import annotations
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from app.taxonomy import CANONICAL_MAKES, PORTAL_RULES, resolve_portal_slugs

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hunter.db")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_password(password: str) -> str:
    return hashlib.sha256(f"cch_salt_2026:{password}".encode("utf-8")).hexdigest()


def _clear_wal_header_flag(path: str) -> None:
    if os.path.exists(path) and os.path.getsize(path) >= 100:
        with open(path, "r+b") as f:
            f.seek(18)
            b = f.read(2)
            if b == b"\x02\x02":
                f.seek(18)
                f.write(b"\x01\x01")


def get_conn() -> sqlite3.Connection:
    _clear_wal_header_flag(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


DEFAULT_SETTINGS: dict[str, Any] = {
    # Email SMTP configuration
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from_name": "Classic Car Hunter • Importação",
    "smtp_use_tls": True,
    # Brazil Import Calculator parameters
    "usd_brl_rate": 5.75,
    "inland_tow_per_mile_usd": 1.65,
    "inland_tow_min_usd": 350,
    "ocean_freight_usd": 2800,
    "us_export_broker_usd": 950,
    "br_classic_tax_pct": 95.0,
    "br_port_despachante_brl": 12500,
}

SENSITIVE_SETTING_KEYS = {
    "smtp_password",
    "whatsapp_api_key",
    "twilio_auth_token",
    "twilio_account_sid",
}

INTERNAL_MIGRATION_KEYS = {
    "v3_blank_start_done",
    "v4_taxonomy_seeded",
    "v5_taxonomy_10portals_seeded",
    "initial_watchlist_seeded",
    "whatsapp_provider",
    "whatsapp_api_url",
    "whatsapp_api_key",
    "whatsapp_instance_id",
    "twilio_account_sid",
    "twilio_auth_token",
    "twilio_from_number",
}


def init_db() -> None:
    conn = get_conn()
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'Sócio',
                password_hash TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                whatsapp_phone TEXT NOT NULL DEFAULT '',
                notify_email INTEGER NOT NULL DEFAULT 1,
                notify_whatsapp INTEGER NOT NULL DEFAULT 1,
                avatar_color TEXT NOT NULL DEFAULT '#f59e0b',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token TEXT PRIMARY KEY,
                partner_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(partner_id) REFERENCES partners(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watchlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                make TEXT NOT NULL DEFAULT 'Cadillac',
                model_primary TEXT NOT NULL DEFAULT 'Eureka',
                related_keywords TEXT NOT NULL DEFAULT 'hearse, ambulance, commercial, coach, flower car, miller meteor, superior, funeral',
                year_min INTEGER NOT NULL DEFAULT 1960,
                year_max INTEGER NOT NULL DEFAULT 1960,
                coachbuilt_year_min INTEGER NOT NULL DEFAULT 1938,
                coachbuilt_year_max INTEGER NOT NULL DEFAULT 1970,
                price_min INTEGER NOT NULL DEFAULT 20000,
                price_max INTEGER NOT NULL DEFAULT 40000,
                target_city TEXT NOT NULL DEFAULT 'Deerfield Beach',
                target_state TEXT NOT NULL DEFAULT 'FL',
                target_zip TEXT NOT NULL DEFAULT '33441',
                target_lat REAL NOT NULL DEFAULT 26.3184,
                target_lon REAL NOT NULL DEFAULT -80.0998,
                sources TEXT NOT NULL DEFAULT 'ebay,autotrader,classiccars,hemmings',
                interval_minutes INTEGER NOT NULL DEFAULT 240,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                owner_partner_id INTEGER NOT NULL DEFAULT 1,
                notify_target TEXT NOT NULL DEFAULT 'owner',
                client_name TEXT NOT NULL DEFAULT '',
                client_notes TEXT NOT NULL DEFAULT '',
                schedule_mode TEXT NOT NULL DEFAULT 'times_per_day',
                runs_per_day INTEGER NOT NULL DEFAULT 3,
                fixed_hours TEXT NOT NULL DEFAULT '08:00,14:00,20:00',
                notify_only_new_interesting INTEGER NOT NULL DEFAULT 1,
                min_alert_score INTEGER NOT NULL DEFAULT 55,
                auto_notify_email INTEGER NOT NULL DEFAULT 1,
                auto_notify_whatsapp INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                watchlist_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT DEFAULT '',
                year INTEGER,
                make TEXT DEFAULT '',
                model TEXT DEFAULT '',
                price_usd INTEGER,
                price_display TEXT DEFAULT 'Sob consulta',
                price_status TEXT DEFAULT 'unknown',
                location TEXT DEFAULT '',
                city TEXT DEFAULT '',
                state TEXT DEFAULT '',
                zip_code TEXT DEFAULT '',
                distance_miles REAL,
                proximity_badge TEXT DEFAULT '',
                url TEXT NOT NULL,
                image_url TEXT DEFAULT '',
                description TEXT DEFAULT '',
                seller_info TEXT DEFAULT '',
                match_tier TEXT NOT NULL DEFAULT 'year_make_other',
                match_tier_label TEXT NOT NULL DEFAULT 'Cadillac 1960',
                match_score INTEGER NOT NULL DEFAULT 50,
                match_reasons TEXT NOT NULL DEFAULT '[]',
                is_favorite INTEGER NOT NULL DEFAULT 0,
                is_dismissed INTEGER NOT NULL DEFAULT 0,
                is_new INTEGER NOT NULL DEFAULT 1,
                notes TEXT DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY(watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT NOT NULL,
                old_price_usd INTEGER,
                new_price_usd INTEGER,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watchlist_id INTEGER NOT NULL,
                triggered_by_partner_id INTEGER,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                total_scraped INTEGER NOT NULL DEFAULT 0,
                new_listings INTEGER NOT NULL DEFAULT 0,
                interesting_new_listings INTEGER NOT NULL DEFAULT 0,
                price_changes INTEGER NOT NULL DEFAULT 0,
                notifications_sent INTEGER NOT NULL DEFAULT 0,
                source_stats TEXT NOT NULL DEFAULT '{}',
                error_message TEXT DEFAULT '',
                FOREIGN KEY(watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watchlist_id INTEGER,
                partner_id INTEGER,
                partner_name TEXT DEFAULT '',
                channel TEXT NOT NULL,
                recipient TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                message_preview TEXT NOT NULL DEFAULT '',
                whatsapp_link TEXT NOT NULL DEFAULT '',
                items_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'sent',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS taxonomy_makes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_name TEXT NOT NULL UNIQUE,
                slug TEXT NOT NULL UNIQUE,
                country TEXT NOT NULL DEFAULT 'EUA',
                portal_slugs_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS taxonomy_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                make_id INTEGER NOT NULL,
                canonical_name TEXT NOT NULL,
                slug TEXT NOT NULL,
                year_min_typical INTEGER NOT NULL DEFAULT 1950,
                year_max_typical INTEGER NOT NULL DEFAULT 1975,
                common_variants_json TEXT NOT NULL DEFAULT '[]',
                portal_slugs_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(make_id) REFERENCES taxonomy_makes(id) ON DELETE CASCADE,
                UNIQUE(make_id, slug)
            );

            CREATE TABLE IF NOT EXISTS portal_filter_rules (
                portal_id TEXT PRIMARY KEY,
                portal_name TEXT NOT NULL,
                category_scope TEXT NOT NULL,
                filter_hierarchy TEXT NOT NULL,
                endpoint_type TEXT NOT NULL,
                url_template TEXT NOT NULL,
                param_rules_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        # Safe migrations for existing watchlists and scan_logs tables
        _ensure_column(conn, "watchlists", "owner_partner_id", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "watchlists", "notify_target", "TEXT NOT NULL DEFAULT 'owner'")
        _ensure_column(conn, "watchlists", "client_name", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "watchlists", "client_notes", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "watchlists", "schedule_mode", "TEXT NOT NULL DEFAULT 'times_per_day'")
        _ensure_column(conn, "watchlists", "runs_per_day", "INTEGER NOT NULL DEFAULT 3")
        _ensure_column(conn, "watchlists", "fixed_hours", "TEXT NOT NULL DEFAULT '08:00,14:00,20:00'")
        _ensure_column(conn, "watchlists", "notify_only_new_interesting", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "watchlists", "min_alert_score", "INTEGER NOT NULL DEFAULT 55")
        _ensure_column(conn, "watchlists", "auto_notify_email", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "watchlists", "auto_notify_whatsapp", "INTEGER NOT NULL DEFAULT 1")

        _ensure_column(conn, "scan_logs", "triggered_by_partner_id", "INTEGER")
        _ensure_column(conn, "scan_logs", "interesting_new_listings", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "scan_logs", "notifications_sent", "INTEGER NOT NULL DEFAULT 0")

        # Seed default users (Rafael Campos & Marçal Borborema)
        rafael_email = os.environ.get("RAFAEL_EMAIL", "").strip() or "rafael-reseach@urbanabrasil.com"
        marcal_email = os.environ.get("MARCAL_EMAIL", "").strip()

        p_count = conn.execute("SELECT COUNT(*) AS cnt FROM partners").fetchone()["cnt"]
        if p_count == 0:
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO partners (username, name, role, password_hash, email, whatsapp_phone, notify_email, notify_whatsapp, avatar_color, created_at)
                VALUES (?, ?, ?, ?, ?, '', 1, 0, ?, ?)
                """,
                ("rafael", "Rafael Campos", "Desenvolvedor & Pesquisa", hash_password("RafA2305"), rafael_email, "#f59e0b", now),
            )
            conn.execute(
                """
                INSERT INTO partners (username, name, role, password_hash, email, whatsapp_phone, notify_email, notify_whatsapp, avatar_color, created_at)
                VALUES (?, ?, ?, ?, ?, '', 1, 0, ?, ?)
                """,
                ("marcal", "Marçal Borborema", "Diretor / Gestor", hash_password("123456"), marcal_email, "#38bdf8", now),
            )
        else:
            conn.execute(
                "UPDATE partners SET username = 'rafael', name = 'Rafael Campos', role = 'Desenvolvedor & Pesquisa', password_hash = ?, email = CASE WHEN email IS NULL OR email = '' THEN ? ELSE email END, whatsapp_phone = '', notify_whatsapp = 0 WHERE id = 1",
                (hash_password("RafA2305"), rafael_email),
            )
            conn.execute(
                "UPDATE partners SET username = 'marcal', name = 'Marçal Borborema', role = 'Diretor / Gestor', whatsapp_phone = '', notify_whatsapp = 0 WHERE id = 2"
            )
            if marcal_email:
                conn.execute("UPDATE partners SET email = ? WHERE id = 2 AND (email IS NULL OR email = '')", (marcal_email,))

        # Seed default system settings
        now = utc_now_iso()
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO system_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                (k, json.dumps(v), now),
            )

        # Remove legacy pre-seeded Cadillac Eureka watchlist so the app starts blank until a car is searched
        v3_mig = conn.execute("SELECT value_json FROM system_settings WHERE key = 'v3_blank_start_done'").fetchone()
        if not v3_mig:
            conn.execute("DELETE FROM watchlists WHERE client_name = 'Encomenda Especial #01' OR name LIKE 'Cadillac Eureka 1960%'")
            conn.execute("DELETE FROM listings WHERE watchlist_id NOT IN (SELECT id FROM watchlists)")
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value_json, updated_at) VALUES ('v3_blank_start_done', 'true', ?)",
                (now,),
            )

        # Clean up legacy WhatsApp/Twilio settings from DB
        conn.execute(
            "DELETE FROM system_settings WHERE key IN ('whatsapp_provider', 'whatsapp_api_url', 'whatsapp_api_key', 'whatsapp_instance_id', 'twilio_account_sid', 'twilio_auth_token', 'twilio_from_number')"
        )

        # Seed/refresh standardized taxonomy and 10-portal filter rules
        v5_tax = conn.execute("SELECT value_json FROM system_settings WHERE key = 'v5_taxonomy_10portals_seeded'").fetchone()
        portal_cnt = conn.execute("SELECT COUNT(*) AS cnt FROM portal_filter_rules").fetchone()["cnt"]
        if not v5_tax or conn.execute("SELECT COUNT(*) AS cnt FROM taxonomy_makes").fetchone()["cnt"] == 0 or portal_cnt < len(PORTAL_RULES):
            for m in CANONICAL_MAKES:
                conn.execute(
                    """
                    INSERT INTO taxonomy_makes (canonical_name, slug, country, portal_slugs_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(canonical_name) DO UPDATE SET
                        slug = excluded.slug,
                        country = excluded.country,
                        portal_slugs_json = excluded.portal_slugs_json
                    """,
                    (m["canonical_name"], m["slug"], m.get("country", "EUA"), json.dumps(m.get("portal_slugs", {})), now),
                )
                make_id = conn.execute("SELECT id FROM taxonomy_makes WHERE slug = ?", (m["slug"],)).fetchone()["id"]
                for mod in m.get("models", []):
                    conn.execute(
                        """
                        INSERT INTO taxonomy_models (make_id, canonical_name, slug, year_min_typical, year_max_typical, common_variants_json, portal_slugs_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(make_id, slug) DO UPDATE SET
                            canonical_name = excluded.canonical_name,
                            year_min_typical = excluded.year_min_typical,
                            year_max_typical = excluded.year_max_typical,
                            common_variants_json = excluded.common_variants_json,
                            portal_slugs_json = excluded.portal_slugs_json
                        """,
                        (
                            make_id,
                            mod["canonical_name"],
                            mod["slug"],
                            mod.get("year_min_typical", 1950),
                            mod.get("year_max_typical", 1975),
                            json.dumps(mod.get("common_variants", []), ensure_ascii=False),
                            json.dumps(mod.get("portal_slugs", {})),
                            now,
                        ),
                    )

            for rule in PORTAL_RULES:
                param_rules = {
                    "make": rule.get("param_make", ""),
                    "model": rule.get("param_model", ""),
                    "trim": rule.get("param_trim", ""),
                    "year": rule.get("param_year", ""),
                    "price_min": rule.get("param_price_min", ""),
                    "price_max": rule.get("param_price_max", ""),
                    "zip": rule.get("param_zip", ""),
                }
                conn.execute(
                    """
                    INSERT OR REPLACE INTO portal_filter_rules (
                        portal_id, portal_name, category_scope, filter_hierarchy,
                        endpoint_type, url_template, param_rules_json, notes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rule["portal_id"],
                        rule["portal_name"],
                        rule["category_scope"],
                        rule["filter_hierarchy"],
                        rule["endpoint_type"],
                        rule["url_template"],
                        json.dumps(param_rules, ensure_ascii=False),
                        rule["notes"],
                        now,
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value_json, updated_at) VALUES ('v5_taxonomy_10portals_seeded', 'true', ?)",
                (now,),
            )
    conn.close()


def get_system_settings(conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    """Internal settings loader (includes raw smtp_password for backend email dispatch ONLY)."""
    close_after = False
    if conn is None:
        conn = get_conn()
        close_after = True
    try:
        rows = conn.execute("SELECT key, value_json FROM system_settings").fetchall()
        result = dict(DEFAULT_SETTINGS)
        for r in rows:
            k = r["key"]
            if k in INTERNAL_MIGRATION_KEYS:
                continue
            try:
                result[k] = json.loads(r["value_json"])
            except Exception:
                result[k] = r["value_json"]
        return result
    finally:
        if close_after:
            conn.close()


def get_masked_settings(conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    """Public/API-safe settings loader: NEVER exposes smtp_password or API keys in cleartext."""
    raw = get_system_settings(conn)
    has_secret = bool(str(raw.get("smtp_password") or "").strip())
    masked: dict[str, Any] = {}
    for k, v in raw.items():
        if k in SENSITIVE_SETTING_KEYS or k in INTERNAL_MIGRATION_KEYS:
            continue
        masked[k] = v
    masked["smtp_password"] = ""
    masked["smtp_password_configured"] = has_secret
    masked["smtp_password_masked"] = "••••••••" if has_secret else ""
    return masked


def _is_masked_or_empty_secret(val: Any) -> bool:
    s = str(val or "").strip()
    if not s:
        return True
    # Ignore placeholder strings composed of bullets or asterisks
    if set(s).issubset({"•", "*", "●"}):
        return True
    return False


def update_system_settings(updates: dict[str, Any]) -> dict[str, Any]:
    conn = get_conn()
    now = utc_now_iso()
    try:
        with conn:
            for k, v in updates.items():
                if k not in DEFAULT_SETTINGS:
                    continue
                if k in SENSITIVE_SETTING_KEYS and _is_masked_or_empty_secret(v):
                    # Preserve existing secret in DB when blank or masked placeholder is submitted
                    continue
                conn.execute(
                    "INSERT INTO system_settings (key, value_json, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                    (k, json.dumps(v), now),
                )
        return get_masked_settings(conn)
    finally:
        conn.close()


def create_session_token(conn: sqlite3.Connection, partner_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with conn:
        conn.execute(
            "INSERT INTO auth_sessions (token, partner_id, created_at) VALUES (?, ?, ?)",
            (token, partner_id, utc_now_iso()),
        )
    return token


def get_partner_by_token(token: Optional[str]) -> Optional[dict[str, Any]]:
    if not token:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT p.id, p.username, p.name, p.role, p.email, p.whatsapp_phone,
                   p.notify_email, p.notify_whatsapp, p.avatar_color, p.created_at
            FROM auth_sessions s
            JOIN partners p ON p.id = s.partner_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_listing(conn: sqlite3.Connection, item: dict[str, Any]) -> tuple[bool, bool, Optional[int]]:
    """Insert or update a listing. Returns (is_newly_inserted, price_changed, old_price_usd)."""
    now = utc_now_iso()
    existing = conn.execute(
        "SELECT id, price_usd, is_favorite, is_dismissed, is_new, notes, first_seen_at FROM listings WHERE id = ?",
        (item["id"],),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO listings (
                id, watchlist_id, source, external_id, title, subtitle,
                year, make, model, price_usd, price_display, price_status,
                location, city, state, zip_code, distance_miles, proximity_badge,
                url, image_url, description, seller_info,
                match_tier, match_tier_label, match_score, match_reasons,
                is_favorite, is_dismissed, is_new, notes, first_seen_at, last_seen_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                0, 0, 1, '', ?, ?
            )
            """,
            (
                item["id"],
                item["watchlist_id"],
                item["source"],
                item["external_id"],
                item["title"],
                item.get("subtitle", ""),
                item.get("year"),
                item.get("make", ""),
                item.get("model", ""),
                item.get("price_usd"),
                item.get("price_display", "Sob consulta"),
                item.get("price_status", "unknown"),
                item.get("location", ""),
                item.get("city", ""),
                item.get("state", ""),
                item.get("zip_code", ""),
                item.get("distance_miles"),
                item.get("proximity_badge", ""),
                item["url"],
                item.get("image_url", ""),
                item.get("description", ""),
                item.get("seller_info", ""),
                item.get("match_tier", "year_make_other"),
                item.get("match_tier_label", "Veículo Clássico"),
                item.get("match_score", 50),
                json.dumps(item.get("match_reasons", []), ensure_ascii=False),
                now,
                now,
            ),
        )
        return True, False, None

    old_price = existing["price_usd"]
    new_price = item.get("price_usd")
    price_changed = (
        old_price is not None
        and new_price is not None
        and old_price != new_price
        and abs(old_price - new_price) >= 50
    )

    if price_changed:
        conn.execute(
            "INSERT INTO price_history (listing_id, old_price_usd, new_price_usd, recorded_at) VALUES (?, ?, ?, ?)",
            (item["id"], old_price, new_price, now),
        )

    conn.execute(
        """
        UPDATE listings SET
            title = ?,
            subtitle = ?,
            year = COALESCE(?, year),
            price_usd = COALESCE(?, price_usd),
            price_display = ?,
            price_status = ?,
            location = CASE WHEN ? != '' THEN ? ELSE location END,
            city = CASE WHEN ? != '' THEN ? ELSE city END,
            state = CASE WHEN ? != '' THEN ? ELSE state END,
            zip_code = CASE WHEN ? != '' THEN ? ELSE zip_code END,
            distance_miles = COALESCE(?, distance_miles),
            proximity_badge = CASE WHEN ? != '' THEN ? ELSE proximity_badge END,
            url = ?,
            image_url = CASE WHEN ? != '' THEN ? ELSE image_url END,
            description = CASE WHEN ? != '' THEN ? ELSE description END,
            seller_info = CASE WHEN ? != '' THEN ? ELSE seller_info END,
            match_tier = ?,
            match_tier_label = ?,
            match_score = ?,
            match_reasons = ?,
            last_seen_at = ?
        WHERE id = ?
        """,
        (
            item["title"],
            item.get("subtitle", ""),
            item.get("year"),
            new_price,
            item.get("price_display", "Sob consulta"),
            item.get("price_status", "unknown"),
            item.get("location", ""), item.get("location", ""),
            item.get("city", ""), item.get("city", ""),
            item.get("state", ""), item.get("state", ""),
            item.get("zip_code", ""), item.get("zip_code", ""),
            item.get("distance_miles"),
            item.get("proximity_badge", ""), item.get("proximity_badge", ""),
            item["url"],
            item.get("image_url", ""), item.get("image_url", ""),
            item.get("description", ""), item.get("description", ""),
            item.get("seller_info", ""), item.get("seller_info", ""),
            item.get("match_tier", "year_make_other"),
            item.get("match_tier_label", "Veículo Clássico"),
            item.get("match_score", 50),
            json.dumps(item.get("match_reasons", []), ensure_ascii=False),
            now,
            item["id"],
        ),
    )
    return False, price_changed, old_price


def get_taxonomy_tree(conn: Optional[sqlite3.Connection] = None) -> list[dict[str, Any]]:
    """Return all canonical vehicle makes with their nested models, variants, and portal identifiers."""
    close_after = False
    if conn is None:
        conn = get_conn()
        close_after = True
    try:
        makes_rows = conn.execute("SELECT * FROM taxonomy_makes ORDER BY canonical_name ASC").fetchall()
        models_rows = conn.execute("SELECT * FROM taxonomy_models ORDER BY canonical_name ASC").fetchall()

        models_by_make: dict[int, list[dict[str, Any]]] = {}
        for mr in models_rows:
            m_dict = dict(mr)
            try:
                m_dict["common_variants"] = json.loads(m_dict.get("common_variants_json") or "[]")
            except Exception:
                m_dict["common_variants"] = []
            try:
                m_dict["portal_slugs"] = json.loads(m_dict.get("portal_slugs_json") or "{}")
            except Exception:
                m_dict["portal_slugs"] = {}
            models_by_make.setdefault(m_dict["make_id"], []).append(m_dict)

        result: list[dict[str, Any]] = []
        for mkr in makes_rows:
            mk_dict = dict(mkr)
            try:
                mk_dict["portal_slugs"] = json.loads(mk_dict.get("portal_slugs_json") or "{}")
            except Exception:
                mk_dict["portal_slugs"] = {}
            mk_dict["models"] = models_by_make.get(mk_dict["id"], [])
            result.append(mk_dict)
        return result
    finally:
        if close_after:
            conn.close()


def get_models_for_make(make_slug_or_id: str | int, conn: Optional[sqlite3.Connection] = None) -> list[dict[str, Any]]:
    """Return list of models for a specific make (by make ID or make slug)."""
    close_after = False
    if conn is None:
        conn = get_conn()
        close_after = True
    try:
        if isinstance(make_slug_or_id, int) or (isinstance(make_slug_or_id, str) and make_slug_or_id.isdigit()):
            make_id = int(make_slug_or_id)
        else:
            row = conn.execute("SELECT id FROM taxonomy_makes WHERE slug = ? OR lower(canonical_name) = lower(?)", (str(make_slug_or_id).lower(), str(make_slug_or_id))).fetchone()
            if not row:
                return []
            make_id = row["id"]

        rows = conn.execute("SELECT * FROM taxonomy_models WHERE make_id = ? ORDER BY canonical_name ASC", (make_id,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["common_variants"] = json.loads(d.get("common_variants_json") or "[]")
            except Exception:
                d["common_variants"] = []
            try:
                d["portal_slugs"] = json.loads(d.get("portal_slugs_json") or "{}")
            except Exception:
                d["portal_slugs"] = {}
            result.append(d)
        return result
    finally:
        if close_after:
            conn.close()


def get_portal_rules(conn: Optional[sqlite3.Connection] = None) -> list[dict[str, Any]]:
    """Return portal hierarchy and network parameter rules for top 5 search portals."""
    close_after = False
    if conn is None:
        conn = get_conn()
        close_after = True
    try:
        rows = conn.execute("SELECT * FROM portal_filter_rules ORDER BY portal_name ASC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["param_rules"] = json.loads(d.get("param_rules_json") or "{}")
            except Exception:
                d["param_rules"] = {}
            result.append(d)
        return result
    finally:
        if close_after:
            conn.close()

