"""Multi-channel Notification Engine (WhatsApp + Email) & US->Brazil Classic Car Import Calculator."""

from __future__ import annotations
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import re
import smtplib
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.database import get_conn, get_system_settings, utc_now_iso


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
        inland_tow_usd = max(tow_min, int(round(distance_miles * tow_per_mile / 25.0) * 25))
        inland_tow_usd = min(inland_tow_usd, 3800)  # Cap enclosed/open carrier coast-to-coast
    else:
        inland_tow_usd = 950  # National average to South Florida port

    cif_usd = price_usd + inland_tow_usd + ocean_usd
    total_us_usd = cif_usd + broker_usd
    cif_brl = int(round(cif_usd * rate))
    fob_brl = int(round(price_usd * rate))
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


def clean_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def format_whatsapp_digest(
    watchlist: dict[str, Any],
    partner: dict[str, Any],
    listings: list[dict[str, Any]],
    settings: dict[str, Any],
    is_test: bool = False,
) -> str:
    """Create a rich WhatsApp report in Portuguese for the partner who owns the search."""
    header = "🧪 *TESTE DE ALERTA • CLASSIC CAR HUNTER*" if is_test else "🚨 *NOVAS OPORTUNIDADES • CLASSIC CAR HUNTER*"
    client_tag = f"\n👤 *Cliente/Demanda:* {watchlist.get('client_name')}" if watchlist.get("client_name") else ""
    lines = [
        header,
        f"Olá *{partner.get('name', 'Sócio')}*, o agente encontrou novidades para a sua busca:",
        f"🔎 *Busca:* {watchlist.get('name')}{client_tag}",
        f"📍 *Base EUA:* {watchlist.get('target_city', 'Deerfield Beach')}, {watchlist.get('target_state', 'FL')} ({watchlist.get('target_zip', '33441')})",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for idx, it in enumerate(listings[:8], start=1):
        imp = calculate_import_breakdown(it.get("price_usd"), it.get("distance_miles"), settings)
        br_est = f" ({imp['total_landed_display']} nacionalizado)" if imp else ""
        dist_str = f"{int(it['distance_miles'])} mi de {watchlist.get('target_city', 'Deerfield Beach')}" if it.get("distance_miles") is not None else (it.get("location") or "EUA")
        event_badge = "📉 *BAIXOU PREÇO*" if it.get("_event") == "price_drop" else "🆕 *NOVO*"
        lines.extend([
            f"*{idx}. {it.get('title')}* [{event_badge}]",
            f"💵 *Preço EUA:* {it.get('price_display', 'Sob consulta')}{br_est}",
            f"📍 *Local:* {it.get('location') or 'EUA'} ({dist_str})",
            f"⭐ *Score:* {it.get('match_score', 0)}/100 • Portal: {str(it.get('source', '')).upper()}",
            f"🔗 {it.get('url')}",
            "",
        ])

    if len(listings) > 8:
        lines.append(f"➕ _...e mais {len(listings) - 8} veículo(s) no Painel Web._")

    lines.append("⚙️ _Enviado automaticamente pelo seu Agente de Importação Sob Demanda._")
    return "\n".join(lines)


def format_email_html(
    watchlist: dict[str, Any],
    partner: dict[str, Any],
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
        <div style="color:#f59e0b;font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Classic Car Hunter • Importação Sob Demanda</div>
        <h2 style="margin:6px 0 4px;color:#ffffff;font-size:20px;">Olá, {partner.get('name', 'Sócio')}!</h2>
        <p style="margin:0;color:#94a3b8;font-size:14px;">
          O agente identificou <strong>{len(listings)} veículo(s)</strong> para o pedido <strong>{watchlist.get('name')}</strong>
          {f"(Cliente: {watchlist.get('client_name')})" if watchlist.get('client_name') else ""}.
        </p>
      </div>
      {''.join(cards_html)}
      <div style="font-size:12px;color:#64748b;text-align:center;margin-top:20px;padding-top:14px;border-top:1px solid #1e293b;">
        Gerado automaticamente pelo painel Classic Car Hunter • Base logística: {watchlist.get('target_city', 'Deerfield Beach')}, {watchlist.get('target_state', 'FL')}
      </div>
    </div>
    """
    return subject, html


def send_whatsapp_message(phone: str, message: str, settings: dict[str, Any]) -> tuple[str, str]:
    """
    Send WhatsApp message using configured provider.
    Returns (status: 'sent' | 'ready_link' | 'error', detail_or_error).
    """
    digits = clean_phone(phone)
    provider = (settings.get("whatsapp_provider") or "callmebot").lower().strip()
    api_key = (settings.get("whatsapp_api_key") or "").strip()
    api_url = (settings.get("whatsapp_api_url") or "").strip()
    instance_id = (settings.get("whatsapp_instance_id") or "").strip()

    if not digits:
        return "ready_link", "Número de WhatsApp do sócio ainda não preenchido (link direto gerado)."

    try:
        if provider == "callmebot" and api_key:
            url = f"https://api.callmebot.com/whatsapp.php?phone={digits}&text={quote(message)}&apikey={quote(api_key)}"
            resp = httpx.get(url, timeout=15.0)
            if resp.status_code == 200:
                return "sent", "Enviado via CallMeBot API"
            return "error", f"CallMeBot HTTP {resp.status_code}: {resp.text[:120]}"

        if provider == "zapi" and api_key and instance_id:
            url = api_url or f"https://api.z-api.io/instances/{instance_id}/token/{api_key}/send-text"
            resp = httpx.post(url, json={"phone": digits, "message": message}, timeout=15.0)
            if resp.status_code in (200, 201):
                return "sent", "Enviado via Z-API"
            return "error", f"Z-API HTTP {resp.status_code}: {resp.text[:120]}"

        if provider == "evolution" and api_url and api_key and instance_id:
            url = f"{api_url.rstrip('/')}/message/sendText/{instance_id}"
            resp = httpx.post(
                url,
                headers={"apikey": api_key, "Content-Type": "application/json"},
                json={"number": digits, "text": message},
                timeout=15.0,
            )
            if resp.status_code in (200, 201):
                return "sent", "Enviado via Evolution API"
            return "error", f"Evolution HTTP {resp.status_code}: {resp.text[:120]}"

        if provider == "ultramsg" and instance_id and api_key:
            url = f"https://api.ultramsg.com/{instance_id}/messages/chat"
            resp = httpx.post(url, data={"token": api_key, "to": f"+{digits}", "body": message}, timeout=15.0)
            if resp.status_code == 200:
                return "sent", "Enviado via UltraMsg API"
            return "error", f"UltraMsg HTTP {resp.status_code}: {resp.text[:120]}"

        if provider == "twilio":
            sid = (settings.get("twilio_account_sid") or "").strip()
            token = (settings.get("twilio_auth_token") or "").strip()
            from_num = (settings.get("twilio_from_number") or "").strip()
            if sid and token and from_num:
                url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
                from_fmt = from_num if from_num.startswith("whatsapp:") else f"whatsapp:{from_num}"
                to_fmt = f"whatsapp:+{digits}"
                resp = httpx.post(url, auth=(sid, token), data={"From": from_fmt, "To": to_fmt, "Body": message}, timeout=15.0)
                if resp.status_code in (200, 201):
                    return "sent", "Enviado via Twilio WhatsApp"
                return "error", f"Twilio HTTP {resp.status_code}: {resp.text[:120]}"

        if provider == "webhook" and api_url:
            resp = httpx.post(api_url, json={"phone": f"+{digits}", "message": message}, timeout=15.0)
            if resp.status_code in (200, 201, 202, 204):
                return "sent", "Enviado via Webhook HTTP"
            return "error", f"Webhook HTTP {resp.status_code}: {resp.text[:120]}"

        return "ready_link", "Mensagem pronta (configure a chave da API WhatsApp ou clique em Abrir no WhatsApp)."
    except Exception as exc:
        return "error", str(exc)


def _post_ipv4(url: str, **kwargs: Any) -> httpx.Response:
    """Send HTTP POST forcing IPv4 so Render Docker containers never fail with Errno 101 on IPv6."""
    transport = httpx.HTTPTransport(local_address="0.0.0.0")
    with httpx.Client(transport=transport, timeout=12.0) as client:
        return client.post(url, **kwargs)


def send_email_message(to_email: str, subject: str, html_body: str, plain_body: str, settings: dict[str, Any]) -> tuple[str, str]:
    """Send HTML email via configured HTTPS API (Brevo/Resend) or SMTP server."""
    to_addr = (to_email or "").strip()
    if not to_addr:
        return "ready_link", "E-mail do sócio ainda não cadastrado."

    smtp_host = (settings.get("smtp_host") or "smtp.gmail.com").strip()
    smtp_port = int(settings.get("smtp_port") or 587)
    smtp_user = (settings.get("smtp_user") or "").strip() or to_addr
    smtp_password = (settings.get("smtp_password") or "").strip()
    from_name = (settings.get("smtp_from_name") or "Classic Car Hunter").strip()
    use_tls = bool(settings.get("smtp_use_tls", True))

    if not smtp_password:
        return "ready_link", "E-mail formatado e gravado no histórico (preencha a chave Brevo xkeysib-... no campo Senha nas Configurações para disparo automático)."

    try:
        # Support HTTPS Email APIs (Resend / Brevo) which work on Render Free Tier (port 443 over IPv4)
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

        if smtp_password.startswith("xkeysib-") or "brevo" in smtp_host.lower():
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
        return "error", f"Falha SMTP ({exc}). Dica: no plano Free do Render, portas SMTP 587/465 são bloqueadas; use uma chave gratuita da Brevo.com (xkeysib-...) ou Resend.com (re_...) no campo Senha."


def dispatch_watchlist_notifications(
    watchlist_id: int,
    listings: list[dict[str, Any]],
    target_partner_id: Optional[int] = None,
    channels: Optional[list[str]] = None,
    is_test: bool = False,
) -> dict[str, Any]:
    """
    Dispatch WhatsApp and/or Email notifications to the partner who created the search
    (or both partners if notify_target == 'both').
    """
    conn = get_conn()
    try:
        wl_row = conn.execute("SELECT * FROM watchlists WHERE id = ?", (watchlist_id,)).fetchone()
        if not wl_row:
            return {"sent": 0, "results": []}
        watchlist = dict(wl_row)
        settings = get_system_settings(conn)

        # Determine which partner(s) should receive the notification
        if target_partner_id is not None:
            p_rows = conn.execute("SELECT * FROM partners WHERE id = ?", (target_partner_id,)).fetchall()
        elif watchlist.get("notify_target") == "both":
            p_rows = conn.execute("SELECT * FROM partners ORDER BY id ASC").fetchall()
        else:
            owner_id = int(watchlist.get("owner_partner_id") or 1)
            p_rows = conn.execute("SELECT * FROM partners WHERE id = ?", (owner_id,)).fetchall()

        partners = [dict(r) for r in p_rows]
        if not partners:
            return {"sent": 0, "results": []}

        if not listings:
            # Fetch top listings from DB if none passed
            rows = conn.execute(
                "SELECT * FROM listings WHERE watchlist_id = ? AND is_dismissed = 0 ORDER BY match_score DESC LIMIT 6",
                (watchlist_id,),
            ).fetchall()
            listings = [dict(r) for r in rows]

        results = []
        now = utc_now_iso()

        for partner in partners:
            wa_text = format_whatsapp_digest(watchlist, partner, listings, settings, is_test=is_test)
            digits = clean_phone(partner.get("whatsapp_phone", ""))
            wa_link = f"https://wa.me/{digits}?text={quote(wa_text)}" if digits else f"https://wa.me/?text={quote(wa_text)}"

            active_channels = channels or []
            if not active_channels:
                if partner.get("notify_whatsapp", 1) and watchlist.get("auto_notify_whatsapp", 1):
                    active_channels.append("whatsapp")
                if partner.get("notify_email", 1) and watchlist.get("auto_notify_email", 1):
                    active_channels.append("email")

            if "whatsapp" in active_channels:
                st, detail = send_whatsapp_message(partner.get("whatsapp_phone", ""), wa_text, settings)
                with conn:
                    conn.execute(
                        """
                        INSERT INTO notification_logs (
                            watchlist_id, partner_id, partner_name, channel, recipient,
                            subject, message_preview, whatsapp_link, items_count, status, error_message, created_at
                        ) VALUES (?, ?, ?, 'whatsapp', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            watchlist_id,
                            partner["id"],
                            partner["name"],
                            partner.get("whatsapp_phone") or "WhatsApp Direto",
                            f"Alerta WhatsApp • {watchlist['name']}",
                            wa_text,
                            wa_link,
                            len(listings),
                            st,
                            detail,
                            now,
                        ),
                    )
                results.append({
                    "partner_id": partner["id"],
                    "partner_name": partner["name"],
                    "channel": "whatsapp",
                    "status": st,
                    "detail": detail,
                    "whatsapp_link": wa_link,
                    "message_preview": wa_text,
                })

            if "email" in active_channels:
                subj, html_body = format_email_html(watchlist, partner, listings, settings, is_test=is_test)
                st, detail = send_email_message(partner.get("email", ""), subj, html_body, wa_text, settings)
                with conn:
                    conn.execute(
                        """
                        INSERT INTO notification_logs (
                            watchlist_id, partner_id, partner_name, channel, recipient,
                            subject, message_preview, whatsapp_link, items_count, status, error_message, created_at
                        ) VALUES (?, ?, ?, 'email', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            watchlist_id,
                            partner["id"],
                            partner["name"],
                            partner.get("email") or "E-mail pendente",
                            subj,
                            wa_text,
                            wa_link,
                            len(listings),
                            st,
                            detail,
                            now,
                        ),
                    )
                results.append({
                    "partner_id": partner["id"],
                    "partner_name": partner["name"],
                    "channel": "email",
                    "status": st,
                    "detail": detail,
                    "subject": subj,
                })

        return {"sent": len(results), "results": results}
    finally:
        conn.close()
