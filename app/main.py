"""FastAPI backend, Multi-Partner Auth, Daily Scheduler, and Notification API for Classic Car Hunter."""

from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.database import (
    create_session_token,
    get_conn,
    get_partner_by_token,
    get_system_settings,
    hash_password,
    init_db,
    update_system_settings,
    upsert_listing,
    utc_now_iso,
)
from app.geo import CITY_COORDS, DEERFIELD_BEACH_LAT, DEERFIELD_BEACH_LON
from app.notifications import calculate_import_breakdown, dispatch_watchlist_notifications
from app.scrapers import run_watchlist_scan, score_and_classify_listing

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_scan_lock = asyncio.Lock()


def refresh_existing_scores() -> None:
    """Ensure all existing listings in SQLite match the strict target filter and have up-to-date scores."""
    conn = get_conn()
    try:
        wls = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM watchlists").fetchall()}
        rows = conn.execute("SELECT * FROM listings").fetchall()
        with conn:
            for r in rows:
                item = dict(r)
                wl = wls.get(item.get("watchlist_id"), next(iter(wls.values()), None))
                if not wl:
                    continue
                scored = score_and_classify_listing(item, wl)
                y_max = int(wl.get("year_max") or 1960)
                max_classic_year = max(1972, y_max + 5)
                yr = scored.get("year")
                tier = scored.get("match_tier")

                # Remove generic sedans/convertibles that aren't the target model/variant, and remove modern 1980s-2020s cars
                if tier not in ("exact_target", "coachbuilt") or (yr is not None and int(yr) > max_classic_year):
                    conn.execute("DELETE FROM listings WHERE id = ?", (item["id"],))
                    continue

                conn.execute(
                    """
                    UPDATE listings SET
                        match_tier = ?, match_tier_label = ?, match_score = ?,
                        match_reasons = ?, price_status = ?, distance_miles = ?, proximity_badge = ?
                    WHERE id = ?
                    """,
                    (
                        scored["match_tier"],
                        scored["match_tier_label"],
                        scored["match_score"],
                        json.dumps(scored.get("match_reasons", []), ensure_ascii=False),
                        scored.get("price_status", "unknown"),
                        scored.get("distance_miles"),
                        scored.get("proximity_badge", ""),
                        item["id"],
                    ),
                )
    except Exception as exc:
        print(f"[Startup] Score refresh warning: {exc}")
    finally:
        conn.close()


def execute_scan_sync(watchlist_id: int, triggered_by_partner_id: Optional[int] = None) -> dict[str, Any]:
    """Execute a synchronous scan for a specific watchlist, persist results, and notify responsible partner if new interesting cars appear."""
    conn = get_conn()
    wl_row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (watchlist_id,)).fetchone()
    if not wl_row:
        conn.close()
        raise ValueError(f"Watchlist {watchlist_id} not found")

    watchlist = dict(wl_row)
    started_at = utc_now_iso()
    cur = conn.execute(
        "INSERT INTO scan_logs (watchlist_id, triggered_by_partner_id, started_at, status) VALUES (?, ?, ?, 'running')",
        (watchlist_id, triggered_by_partner_id, started_at),
    )
    log_id = cur.lastrowid
    conn.commit()

    try:
        scan_res = run_watchlist_scan(watchlist)
        items = scan_res["items"]
        source_stats = scan_res["source_stats"]

        new_count = 0
        interesting_new_count = 0
        price_changes = 0
        alertworthy_items: list[dict[str, Any]] = []
        min_alert_score = int(watchlist.get("min_alert_score") or 55)
        only_interesting = bool(watchlist.get("notify_only_new_interesting", 1))

        with conn:
            for item in items:
                is_new, price_changed, old_price = upsert_listing(conn, item)
                if is_new:
                    new_count += 1
                    is_interesting = (
                        int(item.get("match_score") or 0) >= min_alert_score
                        or item.get("match_tier") in ("exact_target", "coachbuilt", "year_make_budget")
                    )
                    if is_interesting:
                        interesting_new_count += 1
                    if is_interesting or not only_interesting:
                        item_copy = dict(item)
                        item_copy["_event"] = "new"
                        alertworthy_items.append(item_copy)
                elif price_changed:
                    price_changes += 1
                    item_copy = dict(item)
                    item_copy["_event"] = "price_drop" if (old_price and item.get("price_usd") and item["price_usd"] < old_price) else "price_change"
                    item_copy["_old_price_usd"] = old_price
                    alertworthy_items.append(item_copy)

            finished_at = utc_now_iso()
            conn.execute(
                "UPDATE watchlists SET last_run_at = ? WHERE id = ?",
                (finished_at, watchlist_id),
            )

        # Sort alertworthy items by score descending and dispatch notifications if any
        alertworthy_items.sort(key=lambda x: int(x.get("match_score") or 0), reverse=True)
        notif_result = {"sent": 0, "results": []}
        if alertworthy_items and (watchlist.get("auto_notify_whatsapp", 1) or watchlist.get("auto_notify_email", 1)):
            notif_result = dispatch_watchlist_notifications(
                watchlist_id=watchlist_id,
                listings=alertworthy_items,
                is_test=False,
            )

        with conn:
            conn.execute(
                """
                UPDATE scan_logs SET
                    finished_at = ?,
                    status = 'completed',
                    total_scraped = ?,
                    new_listings = ?,
                    interesting_new_listings = ?,
                    price_changes = ?,
                    notifications_sent = ?,
                    source_stats = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    len(items),
                    new_count,
                    interesting_new_count,
                    price_changes,
                    int(notif_result.get("sent") or 0),
                    json.dumps(source_stats),
                    log_id,
                ),
            )

        return {
            "log_id": log_id,
            "watchlist_id": watchlist_id,
            "status": "completed",
            "total_scraped": len(items),
            "new_listings": new_count,
            "interesting_new_listings": interesting_new_count,
            "price_changes": price_changes,
            "notifications": notif_result,
            "source_stats": source_stats,
            "finished_at": finished_at,
        }
    except Exception as exc:
        err_msg = str(exc)
        with conn:
            conn.execute(
                "UPDATE scan_logs SET finished_at = ?, status = 'error', error_message = ? WHERE id = ?",
                (utc_now_iso(), err_msg, log_id),
            )
        raise
    finally:
        conn.close()


def _is_watchlist_due(wl: dict[str, Any]) -> bool:
    """Determine if an active watchlist is due for a scheduled scan based on times_per_day or fixed_hours."""
    if not wl.get("is_active", 1):
        return False
    mode = (wl.get("schedule_mode") or "times_per_day").lower()
    if mode == "manual_only":
        return False

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()
    last_run = wl.get("last_run_at")
    elapsed_min = 999999.0
    if last_run:
        try:
            dt_last = datetime.strptime(last_run, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            elapsed_min = (now_utc - dt_last).total_seconds() / 60.0
        except Exception:
            pass

    if mode == "fixed_hours":
        # Check if current local HH:MM is within 15 minutes after one of the configured fixed_hours
        # and hasn't run in the last 45 minutes
        if elapsed_min < 45:
            return False
        raw_hours = [h.strip() for h in (wl.get("fixed_hours") or "08:00,14:00,20:00").split(",") if h.strip()]
        cur_minutes = now_local.hour * 60 + now_local.minute
        for hhmm in raw_hours:
            try:
                pts = hhmm.split(":")
                target_min = int(pts[0]) * 60 + int(pts[1])
                if 0 <= (cur_minutes - target_min) <= 15:
                    return True
            except Exception:
                continue
        return False

    if mode == "times_per_week":
        runs_per_week = max(1, min(14, int(wl.get("runs_per_day") or 2)))
        interval_min = max(120.0, (7.0 * 1440.0) / runs_per_week)
        return elapsed_min >= interval_min

    # Default: times_per_day (e.g. 2x, 3x, 4x per day)
    runs_per_day = max(1, min(24, int(wl.get("runs_per_day") or 3)))
    interval_min = max(30.0, 1440.0 / runs_per_day)
    return elapsed_min >= interval_min


def _keepalive_ping() -> None:
    """Ping external URL on Render so the free tier instance stays awake for scheduled scans."""
    ext_url = (os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_APP_URL") or "").strip().rstrip("/")
    if not ext_url:
        return
    try:
        import urllib.request
        req = urllib.request.Request(f"{ext_url}/api/health", headers={"User-Agent": "ClassicCarHunter-KeepAlive/2.0"})
        with urllib.request.urlopen(req, timeout=10) as _:
            pass
    except Exception:
        pass


async def periodic_scheduler_loop() -> None:
    """Background loop that checks every 60 seconds if any active watchlist is due for a scheduled scan."""
    tick = 0
    while True:
        await asyncio.sleep(60)
        tick += 1
        if tick % 10 == 0:
            await asyncio.to_thread(_keepalive_ping)
        if _scan_lock.locked():
            continue
        try:
            conn = get_conn()
            rows = conn.execute("SELECT * FROM watchlists WHERE is_active = 1").fetchall()
            conn.close()
            for r in rows:
                wl = dict(r)
                if _is_watchlist_due(wl):
                    async with _scan_lock:
                        await asyncio.to_thread(execute_scan_sync, wl["id"], None)
        except Exception as exc:
            print(f"[Scheduler] Error in background loop: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    refresh_existing_scores()
    task = asyncio.create_task(periodic_scheduler_loop())
    yield
    task.cancel()


app = FastAPI(
    title="Classic Car Hunter • Pesquisa & Importação de Veículos",
    description="Plataforma de Monitoramento de Portais de Venda de Autos dos EUA",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_api_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


# =====================================================================
# PYDANTIC MODELS
# =====================================================================
class LoginModel(BaseModel):
    username: str
    password: str


class PartnerUpdateModel(BaseModel):
    name: str
    role: str = "Usuário"
    email: str = ""
    whatsapp_phone: str = ""
    notify_email: int = 1
    notify_whatsapp: int = 0
    avatar_color: str = "#f59e0b"
    new_password: Optional[str] = None


class PartnerCreateModel(BaseModel):
    username: str
    name: str
    role: str = "Usuário"
    password: str
    email: str = ""
    whatsapp_phone: str = ""
    avatar_color: str = "#10b981"


class WatchlistUpdateModel(BaseModel):
    name: str
    make: str = ""
    model_primary: str = ""
    related_keywords: str = ""
    year_min: int = 1960
    year_max: int = 1970
    price_min: int = 15000
    price_max: int = 60000
    target_city: str = "Deerfield Beach"
    target_state: str = "FL"
    target_zip: str = "33441"
    sources: str = "ebay,autotrader,classiccars,hemmings,bringatrailer,classiccom,carscom,cargurus,streetside,carsandbids"
    interval_minutes: int = 240
    is_active: int = 1
    owner_partner_id: int = 1
    notify_target: str = "owner"
    client_name: str = ""
    client_notes: str = ""
    schedule_mode: str = "times_per_day"
    runs_per_day: int = 3
    fixed_hours: str = "08:00,14:00,20:00"
    notify_only_new_interesting: int = 1
    min_alert_score: int = 55
    auto_notify_email: int = 1
    auto_notify_whatsapp: int = 0


class ListingPatchModel(BaseModel):
    is_favorite: Optional[bool] = None
    is_dismissed: Optional[bool] = None
    is_new: Optional[bool] = None
    notes: Optional[str] = None


class ManualNotifyModel(BaseModel):
    watchlist_id: int
    partner_id: Optional[int] = None
    channels: list[str] = ["email"]
    only_new_interesting: bool = False
    is_test: bool = False


class ImportCalcRequest(BaseModel):
    price_usd: int
    distance_miles: Optional[float] = None
    usd_brl_rate: Optional[float] = None
    inland_tow_usd: Optional[int] = None
    ocean_freight_usd: Optional[int] = None
    br_classic_tax_pct: Optional[float] = None


def _extract_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


# =====================================================================
# AUTH & PARTNER ENDPOINTS
# =====================================================================
@app.post("/api/auth/login")
def login(payload: LoginModel) -> dict[str, Any]:
    conn = get_conn()
    try:
        uname = payload.username.strip().lower()
        alias_map = {"socio1": "rafael", "rafael campos": "rafael", "socio2": "marcal", "marçal": "marcal", "marçal borborema": "marcal", "marcal borborema": "marcal"}
        uname = alias_map.get(uname, uname)
        row = conn.execute(
            "SELECT * FROM partners WHERE LOWER(username) = ? OR LOWER(name) = ? OR LOWER(email) = ?",
            (uname, uname, uname),
        ).fetchone()
        if not row or row["password_hash"] != hash_password(payload.password):
            raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")

        token = create_session_token(conn, row["id"])
        partner = dict(row)
        partner.pop("password_hash", None)
        return {"token": token, "partner": partner}
    finally:
        conn.close()


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    token = _extract_token(authorization)
    if token:
        conn = get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
        finally:
            conn.close()
    return {"ok": True}


@app.get("/api/auth/me")
def get_me(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    partner = get_partner_by_token(_extract_token(authorization))
    if not partner:
        raise HTTPException(status_code=401, detail="Sessão não autenticada")
    return {"partner": partner}


@app.get("/api/partners")
def list_partners() -> dict[str, Any]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, username, name, role, email, whatsapp_phone, notify_email, notify_whatsapp, avatar_color, created_at FROM partners ORDER BY id ASC"
        ).fetchall()
        return {"partners": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.put("/api/partners/{partner_id}")
def update_partner(partner_id: int, payload: PartnerUpdateModel) -> dict[str, Any]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Sócio não encontrado")
        with conn:
            conn.execute(
                """
                UPDATE partners SET
                    name = ?, role = ?, email = ?, whatsapp_phone = ?,
                    notify_email = ?, notify_whatsapp = ?, avatar_color = ?
                WHERE id = ?
                """,
                (
                    payload.name.strip(),
                    payload.role.strip(),
                    payload.email.strip(),
                    payload.whatsapp_phone.strip(),
                    payload.notify_email,
                    payload.notify_whatsapp,
                    payload.avatar_color,
                    partner_id,
                ),
            )
            if payload.new_password and payload.new_password.strip():
                conn.execute(
                    "UPDATE partners SET password_hash = ? WHERE id = ?",
                    (hash_password(payload.new_password.strip()), partner_id),
                )
        updated = conn.execute(
            "SELECT id, username, name, role, email, whatsapp_phone, notify_email, notify_whatsapp, avatar_color, created_at FROM partners WHERE id = ?",
            (partner_id,),
        ).fetchone()
        return {"partner": dict(updated)}
    finally:
        conn.close()


@app.post("/api/partners")
def create_partner(payload: PartnerCreateModel) -> dict[str, Any]:
    conn = get_conn()
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO partners (username, name, role, password_hash, email, whatsapp_phone, notify_email, notify_whatsapp, avatar_color, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    payload.username.strip().lower(),
                    payload.name.strip(),
                    payload.role.strip(),
                    hash_password(payload.password.strip()),
                    payload.email.strip(),
                    payload.whatsapp_phone.strip(),
                    payload.avatar_color,
                    utc_now_iso(),
                ),
            )
            pid = cur.lastrowid
        created = conn.execute(
            "SELECT id, username, name, role, email, whatsapp_phone, notify_email, notify_whatsapp, avatar_color, created_at FROM partners WHERE id = ?",
            (pid,),
        ).fetchone()
        return {"partner": dict(created)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Erro ao criar sócio: {exc}")
    finally:
        conn.close()


# =====================================================================
# SETTINGS, NOTIFICATIONS & IMPORT CALCULATOR ENDPOINTS
# =====================================================================
@app.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    return {"settings": get_system_settings()}


@app.put("/api/settings")
def api_put_settings(payload: dict[str, Any]) -> dict[str, Any]:
    updated = update_system_settings(payload)
    return {"settings": updated}


@app.get("/api/notifications")
def api_get_notifications(watchlist_id: Optional[int] = None, limit: int = 30) -> dict[str, Any]:
    conn = get_conn()
    try:
        if watchlist_id is not None:
            rows = conn.execute(
                "SELECT * FROM notification_logs WHERE watchlist_id = ? ORDER BY id DESC LIMIT ?",
                (watchlist_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notification_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        scan_rows = conn.execute(
            "SELECT * FROM scan_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {
            "notifications": [dict(r) for r in rows],
            "scan_logs": [dict(s) for s in scan_rows],
        }
    finally:
        conn.close()


@app.post("/api/notify/send")
def api_send_notification(payload: ManualNotifyModel) -> dict[str, Any]:
    conn = get_conn()
    try:
        wl_row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (payload.watchlist_id,)).fetchone()
        if not wl_row:
            raise HTTPException(status_code=404, detail="Busca não encontrada")
        wl = dict(wl_row)
        min_score = int(wl.get("min_alert_score") or 55)

        if payload.only_new_interesting:
            rows = conn.execute(
                """
                SELECT * FROM listings
                WHERE watchlist_id = ? AND is_dismissed = 0 AND is_new = 1
                  AND (match_score >= ? OR match_tier IN ('exact_target', 'coachbuilt', 'year_make_budget'))
                ORDER BY match_score DESC LIMIT 10
                """,
                (payload.watchlist_id, min_score),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM listings WHERE watchlist_id = ? AND is_dismissed = 0 ORDER BY match_score DESC LIMIT 8",
                (payload.watchlist_id,),
            ).fetchall()

        listings = [dict(r) for r in rows]
    finally:
        conn.close()

    res = dispatch_watchlist_notifications(
        watchlist_id=payload.watchlist_id,
        listings=listings,
        target_partner_id=payload.partner_id,
        channels=payload.channels,
        is_test=payload.is_test,
    )
    return res


@app.post("/api/import-calc")
def api_import_calc(payload: ImportCalcRequest) -> dict[str, Any]:
    settings = get_system_settings()
    if payload.usd_brl_rate is not None:
        settings["usd_brl_rate"] = payload.usd_brl_rate
    if payload.ocean_freight_usd is not None:
        settings["ocean_freight_usd"] = payload.ocean_freight_usd
    if payload.br_classic_tax_pct is not None:
        settings["br_classic_tax_pct"] = payload.br_classic_tax_pct

    res = calculate_import_breakdown(payload.price_usd, payload.distance_miles, settings)
    if res and payload.inland_tow_usd is not None:
        rate = float(settings["usd_brl_rate"])
        tax_pct = float(settings["br_classic_tax_pct"])
        despachante = int(settings.get("br_port_despachante_brl") or 12500)
        res["inland_tow_usd"] = payload.inland_tow_usd
        res["cif_usd"] = payload.price_usd + payload.inland_tow_usd + int(settings["ocean_freight_usd"])
        res["total_us_usd"] = res["cif_usd"] + int(settings.get("us_export_broker_usd") or 950)
        cif_brl = int(round(res["cif_usd"] * rate))
        res["taxes_brl"] = int(round(cif_brl * (tax_pct / 100.0)))
        logistics_brl = int(round((payload.inland_tow_usd + int(settings["ocean_freight_usd"]) + int(settings.get("us_export_broker_usd") or 950)) * rate)) + despachante
        res["total_landed_brl"] = res["fob_brl"] + logistics_brl + res["taxes_brl"]
        res["total_landed_display"] = f"R$ {res['total_landed_brl']:,.0f}".replace(",", ".")
    return {"estimate": res}


# =====================================================================
# DASHBOARD, WATCHLISTS & LISTINGS ENDPOINTS
# =====================================================================
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "index.html"),
        os.path.join(os.path.dirname(__file__), "index.html"),
        os.path.join(STATIC_DIR, "index.html"),
    ]
    best_path = None
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                if "10 Portais EUA" in content:
                    return HTMLResponse(content=content, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})
                best_path = p
            except Exception:
                best_path = p
    if not best_path:
        return HTMLResponse("<h1>Classic Car Hunter</h1><p>Static UI not found.</p>")
    return FileResponse(best_path, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.get("/api/dashboard")
def get_dashboard(watchlist_id: Optional[int] = None) -> dict[str, Any]:
    conn = get_conn()
    try:
        partners = {
            r["id"]: dict(r)
            for r in conn.execute(
                "SELECT id, username, name, role, email, whatsapp_phone, notify_email, notify_whatsapp, avatar_color FROM partners ORDER BY id ASC"
            ).fetchall()
        }
        watchlists = []
        for r in conn.execute("SELECT * FROM watchlists ORDER BY id ASC").fetchall():
            wd = dict(r)
            owner = partners.get(wd.get("owner_partner_id") or 1)
            wd["owner_name"] = owner["name"] if owner else "Rafael Campos"
            wd["owner_avatar_color"] = owner["avatar_color"] if owner else "#f59e0b"
            watchlists.append(wd)

        active_wl_id = watchlist_id if watchlist_id is not None else None
        active_wl = next((w for w in watchlists if w["id"] == active_wl_id), None)
        if not active_wl:
            return {
                "partners": list(partners.values()),
                "watchlists": watchlists,
                "active_watchlist_id": None,
                "settings": get_system_settings(conn),
                "stats": {
                    "total_listings": 0,
                    "exact_and_coachbuilt": 0,
                    "in_budget_count": 0,
                    "florida_southeast_count": 0,
                    "favorites_count": 0,
                    "new_count": 0,
                    "interesting_new_count": 0,
                    "by_source": {},
                },
                "recent_scans": [],
                "recent_notifications": [],
                "is_scanning": _scan_lock.locked(),
            }
        min_alert_score = int(active_wl.get("min_alert_score") or 55)

        where_wl = "WHERE watchlist_id = ? AND is_dismissed = 0"
        params = (active_wl_id,)

        total_listings = conn.execute(f"SELECT COUNT(*) AS c FROM listings {where_wl}", params).fetchone()["c"]
        exact_and_coach = conn.execute(
            f"SELECT COUNT(*) AS c FROM listings {where_wl} AND match_tier IN ('exact_target', 'coachbuilt')",
            params,
        ).fetchone()["c"]
        in_budget = conn.execute(
            f"SELECT COUNT(*) AS c FROM listings {where_wl} AND price_status IN ('in_budget', 'near_budget')",
            params,
        ).fetchone()["c"]
        near_fl = conn.execute(
            f"SELECT COUNT(*) AS c FROM listings {where_wl} AND (state = 'FL' OR (distance_miles IS NOT NULL AND distance_miles <= 750))",
            params,
        ).fetchone()["c"]
        favorites = conn.execute(
            f"SELECT COUNT(*) AS c FROM listings {where_wl} AND is_favorite = 1",
            params,
        ).fetchone()["c"]
        new_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM listings {where_wl} AND is_new = 1",
            params,
        ).fetchone()["c"]
        interesting_new_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM listings {where_wl} AND is_new = 1 AND (match_score >= ? OR match_tier IN ('exact_target', 'coachbuilt', 'year_make_budget'))",
            (active_wl_id, min_alert_score),
        ).fetchone()["c"]

        by_source_rows = conn.execute(
            f"SELECT source, COUNT(*) AS c FROM listings {where_wl} GROUP BY source",
            params,
        ).fetchall()
        by_source = {r["source"]: r["c"] for r in by_source_rows}

        recent_scans = []
        for s in conn.execute(
            "SELECT * FROM scan_logs WHERE watchlist_id = ? ORDER BY id DESC LIMIT 8",
            (active_wl_id,),
        ).fetchall():
            sd = dict(s)
            try:
                sd["source_stats"] = json.loads(sd.get("source_stats") or "{}")
            except Exception:
                sd["source_stats"] = {}
            recent_scans.append(sd)

        recent_notifications = [
            dict(n)
            for n in conn.execute(
                "SELECT * FROM notification_logs WHERE watchlist_id = ? ORDER BY id DESC LIMIT 6",
                (active_wl_id,),
            ).fetchall()
        ]

        return {
            "partners": list(partners.values()),
            "watchlists": watchlists,
            "active_watchlist_id": active_wl_id,
            "settings": get_system_settings(conn),
            "stats": {
                "total_listings": total_listings,
                "exact_and_coachbuilt": exact_and_coach,
                "in_budget_count": in_budget,
                "florida_southeast_count": near_fl,
                "favorites_count": favorites,
                "new_count": new_count,
                "interesting_new_count": interesting_new_count,
                "by_source": by_source,
            },
            "recent_scans": recent_scans,
            "recent_notifications": recent_notifications,
            "is_scanning": _scan_lock.locked(),
        }
    finally:
        conn.close()


@app.get("/api/listings")
def get_listings(
    watchlist_id: Optional[int] = None,
    source: str = "all",
    tier: str = "all",
    price_filter: str = "all",
    max_distance: Optional[float] = None,
    only_favorites: bool = False,
    only_new: bool = False,
    only_interesting_new: bool = False,
    include_dismissed: bool = False,
    search: str = "",
    sort_by: str = "score",
) -> dict[str, Any]:
    conn = get_conn()
    try:
        settings = get_system_settings(conn)
        clauses = ["1=1"]
        params: list[Any] = []

        min_alert_score = 55
        if watchlist_id is None:
            return {"count": 0, "items": []}
        clauses.append("l.watchlist_id = ?")
        params.append(watchlist_id)
        wl_row = conn.execute("SELECT min_alert_score FROM watchlists WHERE id = ?", (watchlist_id,)).fetchone()
        if wl_row and wl_row["min_alert_score"]:
            min_alert_score = int(wl_row["min_alert_score"])

        if not include_dismissed:
            clauses.append("l.is_dismissed = 0")

        if source and source != "all":
            clauses.append("l.source = ?")
            params.append(source.lower())

        if tier == "exact_target":
            clauses.append("l.match_tier = 'exact_target'")
        elif tier == "coachbuilt":
            clauses.append("l.match_tier IN ('exact_target', 'coachbuilt')")
        elif tier == "year_make_budget":
            clauses.append("l.match_tier = 'year_make_budget'")
        elif tier == "priority":
            clauses.append("l.match_tier IN ('exact_target', 'coachbuilt', 'year_make_budget')")
        elif tier == "year_make_other":
            clauses.append("l.match_tier = 'year_make_other'")

        if price_filter == "in_budget":
            clauses.append("l.price_status = 'in_budget'")
        elif price_filter == "near_budget":
            clauses.append("l.price_status IN ('in_budget', 'near_budget')")
        elif price_filter == "under_45k":
            clauses.append("(l.price_usd IS NOT NULL AND l.price_usd <= 45000)")

        if max_distance is not None and max_distance > 0:
            clauses.append("(l.distance_miles IS NOT NULL AND l.distance_miles <= ?)")
            params.append(max_distance)

        if only_favorites:
            clauses.append("l.is_favorite = 1")

        if only_new:
            clauses.append("l.is_new = 1")

        if only_interesting_new:
            clauses.append("l.is_new = 1 AND (l.match_score >= ? OR l.match_tier IN ('exact_target', 'coachbuilt', 'year_make_budget'))")
            params.append(min_alert_score)

        if search.strip():
            q = f"%{search.strip()}%"
            clauses.append("(l.title LIKE ? OR l.location LIKE ? OR l.description LIKE ? OR l.subtitle LIKE ?)")
            params.extend([q, q, q, q])

        order_map = {
            "score": "l.match_score DESC, COALESCE(l.distance_miles, 9999) ASC, COALESCE(l.price_usd, 999999) ASC",
            "distance_asc": "COALESCE(l.distance_miles, 9999) ASC, l.match_score DESC",
            "price_asc": "COALESCE(l.price_usd, 999999) ASC, l.match_score DESC",
            "price_desc": "COALESCE(l.price_usd, 0) DESC, l.match_score DESC",
            "newest": "l.first_seen_at DESC, l.match_score DESC",
        }
        order_sql = order_map.get(sort_by, order_map["score"])

        where_sql = " AND ".join(clauses)
        rows = conn.execute(
            f"SELECT l.* FROM listings l WHERE {where_sql} ORDER BY {order_sql}",
            tuple(params),
        ).fetchall()

        ph_rows = conn.execute("SELECT * FROM price_history ORDER BY recorded_at DESC").fetchall()
        ph_by_listing: dict[str, list[dict[str, Any]]] = {}
        for ph in ph_rows:
            ph_by_listing.setdefault(ph["listing_id"], []).append(dict(ph))

        items = []
        for r in rows:
            d = dict(r)
            try:
                d["match_reasons"] = json.loads(d.get("match_reasons") or "[]")
            except Exception:
                d["match_reasons"] = []
            d["price_history"] = ph_by_listing.get(d["id"], [])
            d["import_estimate"] = calculate_import_breakdown(d.get("price_usd"), d.get("distance_miles"), settings)
            items.append(d)

        return {"count": len(items), "items": items}
    finally:
        conn.close()


@app.post("/api/scan/{watchlist_id}")
async def trigger_scan(
    watchlist_id: int,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    if _scan_lock.locked():
        raise HTTPException(status_code=409, detail="Uma varredura já está em andamento. Aguarde alguns segundos.")
    partner = get_partner_by_token(_extract_token(authorization))
    partner_id = partner["id"] if partner else None
    async with _scan_lock:
        result = await asyncio.to_thread(execute_scan_sync, watchlist_id, partner_id)
        return result


@app.post("/api/watchlists")
def create_watchlist(
    payload: WatchlistUpdateModel,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    partner = get_partner_by_token(_extract_token(authorization))
    owner_id = partner["id"] if partner else payload.owner_partner_id

    conn = get_conn()
    try:
        lat, lon = CITY_COORDS.get(
            (payload.target_city.lower().strip(), payload.target_state.upper().strip()),
            (DEERFIELD_BEACH_LAT, DEERFIELD_BEACH_LON),
        )
        interval_min = max(30, int(1440 / max(1, payload.runs_per_day)))
        with conn:
            cur = conn.execute(
                """
                INSERT INTO watchlists (
                    name, make, model_primary, related_keywords,
                    year_min, year_max, price_min, price_max,
                    target_city, target_state, target_zip, target_lat, target_lon,
                    sources, interval_minutes, is_active, created_at,
                    owner_partner_id, notify_target, client_name, client_notes,
                    schedule_mode, runs_per_day, fixed_hours,
                    notify_only_new_interesting, min_alert_score,
                    auto_notify_email, auto_notify_whatsapp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.name,
                    payload.make,
                    payload.model_primary,
                    payload.related_keywords,
                    payload.year_min,
                    payload.year_max,
                    payload.price_min,
                    payload.price_max,
                    payload.target_city,
                    payload.target_state.upper(),
                    payload.target_zip,
                    lat,
                    lon,
                    payload.sources,
                    interval_min,
                    payload.is_active,
                    utc_now_iso(),
                    owner_id,
                    payload.notify_target,
                    payload.client_name,
                    payload.client_notes,
                    payload.schedule_mode,
                    payload.runs_per_day,
                    payload.fixed_hours,
                    payload.notify_only_new_interesting,
                    payload.min_alert_score,
                    payload.auto_notify_email,
                    payload.auto_notify_whatsapp,
                ),
            )
            wl_id = cur.lastrowid
        row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (wl_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.put("/api/watchlists/{watchlist_id}")
def update_watchlist(watchlist_id: int, payload: WatchlistUpdateModel) -> dict[str, Any]:
    conn = get_conn()
    try:
        lat, lon = CITY_COORDS.get(
            (payload.target_city.lower().strip(), payload.target_state.upper().strip()),
            (DEERFIELD_BEACH_LAT, DEERFIELD_BEACH_LON),
        )
        interval_min = max(30, int(1440 / max(1, payload.runs_per_day)))
        with conn:
            conn.execute(
                """
                UPDATE watchlists SET
                    name = ?, make = ?, model_primary = ?, related_keywords = ?,
                    year_min = ?, year_max = ?, price_min = ?, price_max = ?,
                    target_city = ?, target_state = ?, target_zip = ?,
                    target_lat = ?, target_lon = ?,
                    sources = ?, interval_minutes = ?, is_active = ?,
                    owner_partner_id = ?, notify_target = ?, client_name = ?, client_notes = ?,
                    schedule_mode = ?, runs_per_day = ?, fixed_hours = ?,
                    notify_only_new_interesting = ?, min_alert_score = ?,
                    auto_notify_email = ?, auto_notify_whatsapp = ?
                WHERE id = ?
                """,
                (
                    payload.name,
                    payload.make,
                    payload.model_primary,
                    payload.related_keywords,
                    payload.year_min,
                    payload.year_max,
                    payload.price_min,
                    payload.price_max,
                    payload.target_city,
                    payload.target_state.upper(),
                    payload.target_zip,
                    lat,
                    lon,
                    payload.sources,
                    interval_min,
                    payload.is_active,
                    payload.owner_partner_id,
                    payload.notify_target,
                    payload.client_name,
                    payload.client_notes,
                    payload.schedule_mode,
                    payload.runs_per_day,
                    payload.fixed_hours,
                    payload.notify_only_new_interesting,
                    payload.min_alert_score,
                    payload.auto_notify_email,
                    payload.auto_notify_whatsapp,
                    watchlist_id,
                ),
            )
        row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (watchlist_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        return dict(row)
    finally:
        conn.close()


@app.delete("/api/watchlists/{watchlist_id}")
def delete_watchlist(watchlist_id: int) -> dict[str, Any]:
    conn = get_conn()
    try:
        with conn:
            conn.execute("DELETE FROM listings WHERE watchlist_id = ?", (watchlist_id,))
            conn.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))
            conn.execute(
                "INSERT INTO system_settings (key, value_json, updated_at) VALUES ('initial_watchlist_seeded', 'true', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = 'true', updated_at = excluded.updated_at",
                (utc_now_iso(),),
            )
        return {"ok": True}
    finally:
        conn.close()


@app.patch("/api/listings/{listing_id:path}")
def patch_listing(listing_id: str, payload: ListingPatchModel) -> dict[str, Any]:
    lid = unquote(listing_id)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")

        updates = []
        params: list[Any] = []
        if payload.is_favorite is not None:
            updates.append("is_favorite = ?")
            params.append(1 if payload.is_favorite else 0)
        if payload.is_dismissed is not None:
            updates.append("is_dismissed = ?")
            params.append(1 if payload.is_dismissed else 0)
        if payload.is_new is not None:
            updates.append("is_new = ?")
            params.append(1 if payload.is_new else 0)
        if payload.notes is not None:
            updates.append("notes = ?")
            params.append(payload.notes)

        if updates:
            params.append(lid)
            with conn:
                conn.execute(f"UPDATE listings SET {', '.join(updates)} WHERE id = ?", tuple(params))

        updated = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
        return dict(updated)
    finally:
        conn.close()


@app.post("/api/mark-all-seen")
def mark_all_seen(watchlist_id: Optional[int] = Query(default=None)) -> dict[str, Any]:
    conn = get_conn()
    try:
        with conn:
            if watchlist_id is not None:
                conn.execute("UPDATE listings SET is_new = 0 WHERE watchlist_id = ?", (watchlist_id,))
            else:
                conn.execute("UPDATE listings SET is_new = 0")
        return {"ok": True}
    finally:
        conn.close()
