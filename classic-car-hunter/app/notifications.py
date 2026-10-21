"""Email Notification & Audit Engine + US->Brazil Classic Car Import Calculator."""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import smtplib
import socket
from typing import Any, Optional

import httpx

from app.database import get_conn, get_system_settings, utc_now_iso

# Force IPv4 DNS resolution in Docker containers (e.g. Render) where IPv6 raises Errno 101 Network is unreachable
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host: Any, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0) -> Any:
    try:
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    except Exception:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo


def calculate_import_breakdown(
    price_usd: Optional[int],
    distance_miles: Optional[float],
    settings: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Calculate estimated landed cost in Brazil for importing a classic vehicle from the US via Florida."""
    if not price_usd or price_usd <= 0:
        return None

    cfg = settings or get_system_settings()
    rate = float(cfg.get("usd_brl_rate") or 5.75)
    tow_per_mile = float(cfg.get("inland_tow_per_mile_usd") or 1.65)
    tow_min = int(cfg.get("inland_tow_min_usd") or 350)
    ocean_usd = int(cfg.get("ocean_freight_usd") or 2800)
    broker_usd = int(cfg.get("us_export_broker_usd") or 950)
    tax_pct = float(cfg.get("br_classic_tax_pct") or 95.0)
    despachante_brl = int(cfg.get("br_port_despachante_brl") or 12500)

    if distance_miles is not None and distance_miles >= 0:
        inland_tow_usd = max(tow_min, int(round(distance_miles * tow_per_mile)))
    else:
        inland_tow_usd = 850

    cif_usd = price_usd + inland_tow_usd + ocean_usd
    total_us_usd = cif_usd + broker_usd

    fob_brl = int(round(price_usd * rate))
    cif_brl = int(round(cif_usd * rate))
    taxes_brl = int(round(cif_brl * (tax_pct / 100.0)))
    logistics_brl = int(round((inland_tow_usd + ocean_usd + broker_usd) * rate)) + despachante_brl
    total_landed_brl = fob_brl + logistics_brl + taxes_brl

    return {
        "fob_usd": price_usd,
        "fob_brl": fob_brl,
        "inland_tow_usd": inland_tow_usd,
        "ocean_freight_usd": ocean_usd,
        "us_export_broker_usd": broker_usd,
        "cif_usd": cif_usd,
        "total_us_usd": total_us_usd,
        "usd_brl_rate": rate,
        "br_tax_pct": tax_pct,
        "taxes_brl": taxes_brl,
        "despachante_brl": despachante_brl,
        "total_landed_brl": total_landed_brl,
        "total_landed_display": f"R$ {total_landed_brl:,.0f}".replace(",", "."),
        "short_display": f"~R$ {round(total_landed_brl / 1000)}k no Brasil",
    }


def format_email_plain(
    watchlist: dict[str, Any],
    user: dict[str, Any],
    listings: list[dict[str, Any]],
    settings: dict[str, Any],
    is_test: bool = False,
) -> str:
    """Create a plain-text report in Portuguese for email body and preview."""
    header = "TESTE DE ALERTA POR E-MAIL • CLASSIC CAR HUNTER" if is_test else "NOVAS OPORTUNIDADES • CLASSIC CAR HUNTER"
    client_tag = f"\nReferência/Demanda: {watchlist.get('client_name')}" if watchlist.get("client_name") else ""
    lines = [
        header,
        f"Olá {user.get('name', 'Usuário')}, o sistema encontrou atualizações para a pesquisa:",
        f"Pesquisa: {watchlist.get('name')}{client_tag}",
        f"Base EUA: {watchlist.get('target_city', 'Deerfield Beach')}, {watchlist.get('target_state', 'FL')} ({watchlist.get('target_zip', '33441')})",
        "----------------------------------------",
    ]

    for idx, it in enumerate(listings[:10], start=1):
        imp = calculate_import_breakdown(it.get("price_usd"), it.get("distance_miles"), settings)
        br_est = f" ({imp['total_landed_display']} nacionalizado)" if imp else ""
        dist_str = (
            f"{int(it['distance_miles'])} mi de {watchlist.get('target_city', 'Deerfield Beach')}"
            if it.get("distance_miles") is not None
            else (it.get("location") or "EUA")
        )
        event_badge = "BAIXOU PREÇO" if it.get("_event") == "price_drop" else "NOVO"
        lines.extend([
            f"{idx}. {it.get('title')} [{event_badge}]",
            f"   Preço EUA: {it.get('price_display', 'Sob consulta')}{br_est}",
            f"   Local: {it.get('location') or 'EUA'} ({dist_str})",
            f"   Score: {it.get('match_score', 0)}/100 • Fonte: {str(it.get('source', '')).upper()}",
            f"   Link: {it.get('url')}",
            "",
        ])

    if len(listings) > 10:
        lines.append(f"...e mais {len(listings) - 10} veículo(s) disponíveis no painel web.")

    return "\n".join(lines)


def format_email_html(
    watchlist: dict[str, Any],
    user: dict[str, Any],
    listings: list[dict[str, Any]],
    settings: dict[str, Any],
    is_test: bool = False,
) -> tuple[str, str]:
    """Return (subject, html_body) for email alert."""
    prefix = "[TESTE] " if is_test else "🚨 "
    subject = f"{prefix}{len(listings)} Oportunidade(s): {watchlist.get('name')} • Classic Car Hunter"

    cards_html = []
    for it in listings[:12]:
        imp = calculate_import_breakdown(it.get("price_usd"), it.get("distance_miles"), settings)
        br_line = (
            f"<div style='font-size:12px;color:#fbbf24;margin-top:4px;'>🚢 Estimativa Brasil: <strong>{imp['total_landed_display']}</strong> (c/ frete FL + impostos)</div>"
            if imp else ""
        )
        dist_str = (
            f"{int(it['distance_miles'])} milhas de {watchlist.get('target_city', 'Deerfield Beach')}, {watchlist.get('target_state', 'FL')}"
            if it.get("distance_miles") is not None
            else (it.get("location") or "EUA")
        )
        img_tag = (
            f"<img src='{it.get('image_url')}' alt='Foto' style='width:160px;height:110px;object-fit:cover;border-radius:8px;border:1px solid #334155;' />"
            if it.get("image_url")
            else "<div style='width:160px;height:110px;background:#1e293b;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#64748b;font-size:12px;'>Sem foto</div>"
        )
        cards_html.append(
            f"""
            <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:14px;margin-bottom:12px;display:flex;gap:14px;">
              <div>{img_tag}</div>
              <div style="flex:1;">
                <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.5px;">
                  {str(it.get('source', '')).upper()} • Score {it.get('match_score', 0)}/100 • {it.get('match_tier_label', '')}
                </div>
                <div style="font-size:16px;font-weight:700;color:#f8fafc;margin:4px 0;">{it.get('title')}</div>
                <div style="font-size:15px;font-weight:700;color:#34d399;">{it.get('price_display', 'Sob consulta')}</div>
                {br_line}
                <div style="font-size:12px;color:#cbd5e1;margin-top:6px;">📍 {it.get('location') or 'EUA'} ({dist_str})</div>
                <div style="margin-top:10px;">
                  <a href="{it.get('url')}" style="background:#f59e0b;color:#0f172a;text-decoration:none;padding:6px 12px;border-radius:6px;font-size:12px;font-weight:700;">Abrir Anúncio Original →</a>
                </div>
              </div>
            </div>
            """
        )

    html = f"""
    <div style="font-family:Inter,Segoe UI,Arial,sans-serif;background:#020617;color:#e2e8f0;padding:24px;border-radius:16px;max-width:680px;margin:0 auto;">
      <div style="border-bottom:1px solid #1e293b;padding-bottom:16px;margin-bottom:18px;">
        <div style="color:#f59e0b;font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Classic Car Hunter • Pesquisa & Importação de Veículos</div>
        <h2 style="margin:6px 0 4px;color:#ffffff;font-size:20px;">Olá, {user.get('name', 'Usuário')}!</h2>
        <p style="margin:0;color:#94a3b8;font-size:14px;">
          O monitoramento encontrou <strong>{len(listings)} veículo(s)</strong> para a pesquisa <strong>{watchlist.get('name')}</strong>
          {f"({watchlist.get('client_name')})" if watchlist.get('client_name') else ""}.
        </p>
      </div>
      {''.join(cards_html)}
      <div style="font-size:12px;color:#64748b;text-align:center;margin-top:20px;padding-top:14px;border-top:1px solid #1e293b;">
        Relatório automático • Classic Car Hunter • Desenvolvido por Rafael Campos
      </div>
    </div>
    """
    return subject, html


def _post_ipv4(url: str, **kwargs: Any) -> httpx.Response:
    """Send HTTP POST forcing IPv4 so Render Docker containers never fail with Errno 101 on IPv6."""
    transport = httpx.HTTPTransport(local_address="0.0.0.0")
    with httpx.Client(transport=transport, timeout=12.0) as client:
        return client.post(url, **kwargs)


def send_email_message(to_email: str, subject: str, html_body: str, plain_body: str, settings: dict[str, Any]) -> tuple[str, str]:
    """Send HTML email via configured HTTPS API (Brevo/Resend) or SMTP server."""
    to_addr = (to_email or "").strip()
    if not to_addr:
        return "error", "E-mail do destinatário não cadastrado."

    smtp_host = (settings.get("smtp_host") or "brevo.com").strip()
    smtp_port = int(settings.get("smtp_port") or 587)
    smtp_user = (settings.get("smtp_user") or os.environ.get("RAFAEL_EMAIL") or "rafael-reseach@urbanabrasil.com").strip()
    smtp_password = (settings.get("smtp_password") or os.environ.get("BREVO_API_KEY") or "").strip()
    from_name = (settings.get("smtp_from_name") or "Classic Car Hunter").strip()
    use_tls = bool(settings.get("smtp_use_tls", True))

    if not smtp_password:
        return "error", "Chave de envio de e-mail (Brevo API Key / SMTP) ainda não configurada no painel de administração."

    try:
        if smtp_password.startswith("re_") or "resend" in smtp_host.lower():
            resp = _post_ipv4(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {smtp_password}", "Content-Type": "application/json"},
                json={
                    "from": f"{from_name} <{smtp_user if '@' in smtp_user else 'onboarding@resend.dev'}>",
                    "to": [to_addr],
                    "subject": subject,
                    "html": html_body,
                },
            )
            if resp.status_code in (200, 201):
                return "sent", "E-mail enviado com sucesso via Resend HTTPS API!"
            return "error", f"Resend HTTP {resp.status_code}: {resp.text[:160]}"

        if smtp_password.startswith("xkeysib-") or "brevo" in smtp_host.lower() or len(smtp_password) > 25:
            resp = _post_ipv4(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": smtp_password, "Content-Type": "application/json", "Accept": "application/json"},
                json={
                    "sender": {"name": from_name, "email": smtp_user},
                    "to": [{"email": to_addr}],
                    "subject": subject,
                    "htmlContent": html_body,
                },
            )
            if resp.status_code in (200, 201, 202):
                return "sent", "E-mail enviado com sucesso via Brevo HTTPS API!"
            return "error", f"Brevo HTTP {resp.status_code}: {resp.text[:160]}"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{smtp_user}>"
        msg["To"] = to_addr
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=6) as server:
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=6) as server:
                if use_tls:
                    server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, [to_addr], msg.as_string())
        return "sent", f"E-mail enviado via {smtp_host}"
    except Exception as exc:
        return "error", f"Falha no envio ({exc}). Verifique a chave API da Brevo (xkeysib-...)."


def dispatch_watchlist_notifications(
    watchlist_id: int,
    listings: list[dict[str, Any]],
    target_partner_id: Optional[int] = None,
    channels: Optional[list[str]] = None,
    is_test: bool = False,
) -> dict[str, Any]:
    """
    Dispatch Email notifications to the user who created the search
    (or all active users if notify_target == 'both'). Records every attempt in notification_logs for Rafael's audit.
    """
    conn = get_conn()
    try:
        wl_row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (watchlist_id,)).fetchone()
        if not wl_row:
            return {"sent": 0, "results": []}
        watchlist = dict(wl_row)
        settings = get_system_settings(conn)

        if target_partner_id is not None:
            p_rows = conn.execute("SELECT * FROM partners WHERE id = ?", (target_partner_id,)).fetchall()
        elif watchlist.get("notify_target") == "both":
            p_rows = conn.execute("SELECT * FROM partners ORDER BY id ASC").fetchall()
        else:
            owner_id = int(watchlist.get("owner_partner_id") or 1)
            p_rows = conn.execute("SELECT * FROM partners WHERE id = ?", (owner_id,)).fetchall()

        users = [dict(r) for r in p_rows]
        if not users:
            return {"sent": 0, "results": []}

        if not listings:
            rows = conn.execute(
                "SELECT * FROM listings WHERE watchlist_id = ? AND is_dismissed = 0 ORDER BY match_score DESC LIMIT 8",
                (watchlist_id,),
            ).fetchall()
            listings = [dict(r) for r in rows]

        results = []
        now = utc_now_iso()

        for user in users:
            plain_text = format_email_plain(watchlist, user, listings, settings, is_test=is_test)
            subj, html_body = format_email_html(watchlist, user, listings, settings, is_test=is_test)
            st, detail = send_email_message(user.get("email", ""), subj, html_body, plain_text, settings)
            with conn:
                conn.execute(
                    """
                    INSERT INTO notification_logs (
                        watchlist_id, partner_id, partner_name, channel, recipient,
                        subject, message_preview, whatsapp_link, items_count, status, error_message, created_at
                    ) VALUES (?, ?, ?, 'email', ?, ?, ?, '', ?, ?, ?, ?)
                    """,
                    (
                        watchlist_id,
                        user["id"],
                        user["name"],
                        user.get("email") or "Sem e-mail",
                        subj,
                        plain_text,
                        len(listings),
                        st,
                        detail,
                        now,
                    ),
                )
            results.append({
                "partner_id": user["id"],
                "partner_name": user["name"],
                "channel": "email",
                "status": st,
                "detail": detail,
                "message_preview": plain_text,
            })

        sent_count = sum(1 for r in results if r["status"] == "sent")
        return {"sent": sent_count, "results": results}
    finally:
        conn.close()
