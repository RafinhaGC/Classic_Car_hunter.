"""Multi-portal scraper engine for eBay Motors, Autotrader Classics, ClassicCars.com, and Hemmings."""

from __future__ import annotations
import json
import re
import time
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup


def _get_chrome_session():
    from curl_cffi import requests as crequests
    return crequests.Session(impersonate="chrome")

from app.geo import (
    DEERFIELD_BEACH_LAT,
    DEERFIELD_BEACH_LON,
    STATE_NAME_TO_CODE,
    estimate_distance_from_deerfield,
    parse_location_parts,
)

# Parts / memorabilia exclusion keywords for eBay Motors
PART_KEYWORDS = [
    "gasket", "thermostat", "fan clutch", "print ad", "brochure", "manual",
    "model kit", "diecast", "1:18", "1:24", "1:43", "1/18", "emblem", "script",
    "hubcap", "wheel cover", "tail light", "taillight", "headlight", "bezel",
    "fender skirt", "bumper", "grille", "carburetor", "distributor", "water pump",
    "fuel pump", "brake shoe", "brake drum", "master cylinder", "Ignition",
    "wiring harness", "weatherstrip", "floor mat", "carpet", "seat cover",
    "steering wheel", "horn ring", "radio", "speedometer", "clock", "ash tray",
    "door handle", "window crank", "mirror", "molding", "trim piece", "key blank",
    "owners guide", "shop manual", "sales catalog", "magazine", "postcard", "toy",
]

COACHBUILT_KEYWORDS = [
    "eureka", "hearse", "ambulance", "commercial chassis", "commercial",
    "flower car", "miller meteor", "miller-meteor", "superior", "funeral",
    "coach", "3-way", "combination", "ecto", "s&s", "hess & eisenhardt",
]

EBAY_BRL_TO_USD_RATE = 5.0628


def parse_price_to_usd(raw_price: Any) -> tuple[Optional[int], str]:
    """Convert raw price string or number into (price_usd_int, formatted_display)."""
    if raw_price is None:
        return None, "Sob consulta"

    if isinstance(raw_price, (int, float)):
        val = int(round(raw_price))
        if val <= 0:
            return None, "Sob consulta"
        return val, f"${val:,}"

    text = str(raw_price).strip()
    if not text:
        return None, "Sob consulta"

    # Handle range like "$20,000 to $30,000" -> take first number
    is_brl = "R$" in text or "BRL" in text.upper()
    obo = " (OBO)" if "obo" in text.lower() or "best offer" in text.lower() else ""

    m = re.search(r"(\d[\d,\.]*)", text)
    if not m:
        return None, text

    num_str = m.group(1)
    # Standard US format: 37,995.00 or 37,995
    if "," in num_str and "." in num_str:
        if num_str.rfind(".") > num_str.rfind(","):
            num_str = num_str.replace(",", "")
        else:
            num_str = num_str.replace(".", "").replace(",", ".")
    elif "," in num_str:
        # Check if comma is thousands separator (e.g. 37,995)
        parts = num_str.split(",")
        if len(parts[-1]) == 3:
            num_str = num_str.replace(",", "")
        else:
            num_str = num_str.replace(",", ".")

    try:
        val_float = float(num_str)
    except ValueError:
        return None, text

    if is_brl:
        usd_val = int(round(val_float / EBAY_BRL_TO_USD_RATE / 50.0) * 50)
        return usd_val, f"~${usd_val:,} USD{obo}"

    usd_val = int(round(val_float))
    if usd_val <= 0:
        return None, "Sob consulta"
    return usd_val, f"${usd_val:,}{obo}"


def extract_year_from_text(text: str, default: Optional[int] = None) -> Optional[int]:
    m = re.search(r"\b(19[2-9]\d|20[0-2]\d)\b", text or "")
    return int(m.group(1)) if m else default


def is_likely_part_or_memorabilia(title: str, price_usd: Optional[int]) -> bool:
    """Filter out non-vehicle parts/literature on eBay."""
    t_low = (title or "").lower()
    if price_usd is not None and price_usd < 2200:
        return True
    for kw in PART_KEYWORDS:
        if kw.lower() in t_low:
            return True
    # Range of years like "1953-1962" or "1960-1992" in title is almost always a replacement part
    if re.search(r"\b(19\d\d)\s*[-/]\s*(19\d\d|20\d\d|\d\d)\b", t_low):
        return True
    return False


def score_and_classify_listing(
    item: dict[str, Any],
    watchlist: dict[str, Any],
) -> dict[str, Any]:
    """Enrich listing with geolocation distance, price status, match tier, and 0-100 score."""
    title = item.get("title", "")
    subtitle = item.get("subtitle", "")
    desc = item.get("description", "")
    model = item.get("model", "")
    full_text = f"{title} {subtitle} {model} {desc}".lower()

    year = item.get("year") or extract_year_from_text(title)
    item["year"] = year

    price_usd = item.get("price_usd")
    p_min = int(watchlist.get("price_min", 20000))
    p_max = int(watchlist.get("price_max", 40000))
    y_min = int(watchlist.get("year_min", 1960))
    y_max = int(watchlist.get("year_max", 1960))
    primary_kw = (watchlist.get("model_primary") or "Eureka").strip().lower()
    related_kws = [
        k.strip().lower()
        for k in (watchlist.get("related_keywords") or "").split(",")
        if k.strip()
    ]

    # 1. Geolocation & Distance to Deerfield Beach, FL
    ref_lat = float(watchlist.get("target_lat") or DEERFIELD_BEACH_LAT)
    ref_lon = float(watchlist.get("target_lon") or DEERFIELD_BEACH_LON)
    dist_miles, prox_badge = estimate_distance_from_deerfield(
        raw_location=item.get("location", ""),
        city=item.get("city"),
        state=item.get("state"),
        zip_code=item.get("zip_code"),
        ref_lat=ref_lat,
        ref_lon=ref_lon,
    )
    if dist_miles is None and item.get("distance_miles") is not None:
        dist_miles = float(item["distance_miles"])
        prox_badge = f"EUA ({int(dist_miles)} mi)"

    item["distance_miles"] = dist_miles
    item["proximity_badge"] = prox_badge

    # 2. Price Status
    reasons: list[str] = []
    price_points = 0
    if price_usd is None:
        price_status = "unknown"
        reasons.append("Preço sob consulta / leilão")
        price_points = 10
    elif p_min <= price_usd <= p_max:
        price_status = "in_budget"
        reasons.append(f"Na faixa ideal (${p_min // 1000}k–${p_max // 1000}k)")
        price_points = 25
    elif (p_min * 0.7) <= price_usd < p_min:
        price_status = "below_budget"
        reasons.append(f"Abaixo do teto (${price_usd:,})")
        price_points = 18
    elif p_max < price_usd <= (p_max * 1.25):
        price_status = "near_budget"
        reasons.append(f"Próximo ao teto (${price_usd:,} negociável)")
        price_points = 15
    elif price_usd < (p_min * 0.7):
        price_status = "below_budget"
        reasons.append(f"Projeto / Abaixo de ${p_min // 1000}k (${price_usd:,})")
        price_points = 10
    else:
        price_status = "above_budget"
        reasons.append(f"Acima de ${p_max // 1000}k (${price_usd:,})")
        price_points = 0

    item["price_status"] = price_status

    # 3. Vehicle Match Tier
    has_primary = bool(primary_kw and primary_kw in full_text)
    is_hearse_watchlist = any(k in (primary_kw + " " + " ".join(related_kws)) for k in ("eureka", "hearse", "ambulance", "funeral", "miller"))
    kw_pool = related_kws if related_kws else (COACHBUILT_KEYWORDS if is_hearse_watchlist else [])
    matched_related = [kw for kw in kw_pool if kw in full_text]
    is_target_year = year is not None and (y_min <= year <= y_max)
    is_near_year = year is not None and ((y_min - 3) <= year <= (y_max + 3))

    if has_primary:
        match_tier = "exact_target"
        match_tier_label = f"🎯 Alvo Exato ({watchlist.get('model_primary', 'Alvo')})"
        vehicle_points = 55 if is_target_year else (49 if is_near_year else 42)
        reasons.insert(0, f"Modelo '{watchlist.get('model_primary', '')}' identificado")
    elif matched_related:
        match_tier = "coachbuilt"
        kw_display = matched_related[0].title()
        if is_hearse_watchlist and year and year >= 1985:
            match_tier_label = f"⚱️ Funeral Coach Moderno ({year})"
            vehicle_points = 18
            reasons.insert(0, f"Coach moderno ({year})")
        elif is_target_year or (is_hearse_watchlist and year and 1955 <= year <= 1966):
            label_prefix = "🚑 Coachbuilt Clássica" if is_hearse_watchlist else "✨ Variante Alvo"
            match_tier_label = f"{label_prefix} ({kw_display})"
            vehicle_points = 50 if is_target_year else 46
            reasons.insert(0, f"Variante relevante ({year or ''} - {', '.join(set(k.title() for k in matched_related[:2]))})")
        else:
            label_prefix = "🚑 Coachbuilt Vintage" if is_hearse_watchlist else "✨ Variante Relacionada"
            match_tier_label = f"{label_prefix} ({kw_display})"
            vehicle_points = 38
            reasons.insert(0, f"Variante relacionada ({', '.join(set(k.title() for k in matched_related[:2]))})")
    elif is_target_year and price_status in ("in_budget", "near_budget", "below_budget"):
        match_tier = "year_make_budget"
        yr_str = str(y_min) if y_min == y_max else f"{y_min}-{y_max}"
        match_tier_label = f"💰 {watchlist.get('make', 'Clássico')} {yr_str} no Orçamento"
        vehicle_points = 32
        reasons.insert(0, f"{watchlist.get('make', '')} {year} na faixa de busca")
    else:
        match_tier = "year_make_other"
        match_tier_label = f"🌴 {watchlist.get('make', 'Clássico')} {year or y_min}"
        vehicle_points = 18

    # 4. Proximity Points (Target US Port / City priority)
    target_city_label = watchlist.get("target_city") or "Deerfield Beach"
    prox_points = 5
    if dist_miles is not None:
        if dist_miles <= 100:
            prox_points = 20
            reasons.append(f"Muito próximo de {target_city_label} ({int(dist_miles)} mi)")
        elif dist_miles <= 350 or item.get("state") == (watchlist.get("target_state") or "FL"):
            prox_points = 16
            reasons.append(f"No estado-base / Flórida ({int(dist_miles)} mi de {target_city_label})")
        elif dist_miles <= 750:
            prox_points = 11
            reasons.append(f"Sudeste dos EUA ({int(dist_miles)} mi)")
        elif dist_miles <= 1300:
            prox_points = 6

    total_score = min(100, max(5, vehicle_points + price_points + prox_points))
    item["match_tier"] = match_tier
    item["match_tier_label"] = match_tier_label
    item["match_score"] = total_score
    item["match_reasons"] = reasons
    return item


def _make_listing_id(watchlist_id: int, source: str, ext_id: str) -> str:
    if int(watchlist_id or 1) == 1:
        return f"{source}:{ext_id}"
    return f"w{watchlist_id}:{source}:{ext_id}"


# =====================================================================
# 1. CLASSICCARS.COM SCRAPER
# =====================================================================
def scrape_classiccars(watchlist: dict[str, Any]) -> list[dict[str, Any]]:
    """Scrape ClassicCars.com for target year+make and primary model/keywords."""
    make_slug = (watchlist.get("make") or "cadillac").strip().lower().replace(" ", "-")
    y_min = int(watchlist.get("year_min", 1960))
    y_max = int(watchlist.get("year_max", 1960))
    primary_slug = (watchlist.get("model_primary") or "").strip().lower().replace(" ", "-")
    related_raw = (watchlist.get("related_keywords") or "").lower()

    year_segment = str(y_min) if y_min == y_max else f"{y_min}-{y_max}"
    urls = []
    if primary_slug and primary_slug != "eureka":
        urls.append(f"https://classiccars.com/listings/find/{year_segment}/{make_slug}/{primary_slug}")
    urls.append(f"https://classiccars.com/listings/find/{year_segment}/{make_slug}")
    if "hearse" in related_raw or primary_slug == "eureka":
        urls.append(f"https://classiccars.com/listings/find/all-years/{make_slug}/hearse")

    results: dict[str, dict[str, Any]] = {}
    session = _get_chrome_session()

    for url in urls:
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("div.search-result-item")
            for card in cards:
                ld_script = card.find("script", type="application/ld+json")
                ld_data = {}
                if ld_script and ld_script.string:
                    try:
                        ld_data = json.loads(ld_script.string.strip())
                    except Exception:
                        ld_data = {}

                sku = ld_data.get("sku") or ""
                link_el = card.select_one("a[href*='/listings/view/']")
                href = (
                    ld_data.get("offers", {}).get("url")
                    or (link_el["href"] if link_el and link_el.has_attr("href") else "")
                )
                if not href:
                    continue
                full_url = f"https://classiccars.com{href}" if href.startswith("/") else href

                if not sku:
                    m_id = re.search(r"/listings/view/(\d+)", full_url)
                    sku = f"CC-{m_id.group(1)}" if m_id else full_url

                if sku in results:
                    continue

                title_el = card.select_one(".h-sri-car-title")
                title = (
                    ld_data.get("name")
                    or (title_el.get_text(" ", strip=True) if title_el else "")
                ).strip()
                if not title:
                    continue
                if make_slug not in title.lower() and make_slug not in str(ld_data.get("manufacturer", "")).lower():
                    continue

                # Extract location from aria-label ("... for sale in Venice, Florida") and ZIP from URL slug
                aria = link_el.get("aria-label", "") if link_el else ""
                loc_str = ""
                m_loc = re.search(r"for sale in\s+(.+)$", aria, flags=re.I)
                if m_loc:
                    loc_str = m_loc.group(1).strip()

                m_zip = re.search(r"-(\d{5})$", full_url)
                zip_code = m_zip.group(1) if m_zip else None
                city, state_code, parsed_zip = parse_location_parts(loc_str)
                zip_code = zip_code or parsed_zip
                if city and state_code:
                    loc_display = f"{city.title()}, {state_code}" + (f" {zip_code}" if zip_code else "")
                else:
                    loc_display = loc_str

                price_el = card.select_one(".mrg-b-sri-price")
                raw_price = (
                    price_el.get_text(" ", strip=True)
                    if price_el
                    else ld_data.get("offers", {}).get("price")
                )
                price_usd, price_display = parse_price_to_usd(raw_price)

                img_el = card.select_one("img")
                image_url = (
                    ld_data.get("image", {}).get("url")
                    or (img_el.get("data-src") or img_el.get("src") if img_el else "")
                )

                desc_el = card.select_one(".h-sri-desc-text")
                description = (
                    ld_data.get("description")
                    or (desc_el.get_text(" ", strip=True) if desc_el else "")
                )

                seller_el = card.select_one(".rib-standard")
                seller_info = seller_el.get_text(" ", strip=True) if seller_el else "ClassicCars.com"

                year = extract_year_from_text(str(ld_data.get("modelDate") or title))

                item = {
                    "id": _make_listing_id(watchlist["id"], "classiccars", str(sku)),
                    "watchlist_id": watchlist["id"],
                    "source": "classiccars",
                    "external_id": str(sku),
                    "title": title,
                    "subtitle": ld_data.get("model", ""),
                    "year": year,
                    "make": ld_data.get("manufacturer", watchlist.get("make", "Cadillac")),
                    "model": ld_data.get("model", ""),
                    "price_usd": price_usd,
                    "price_display": price_display,
                    "location": loc_display,
                    "city": city.title() if city else "",
                    "state": state_code or "",
                    "zip_code": zip_code or "",
                    "url": full_url,
                    "image_url": image_url,
                    "description": description,
                    "seller_info": seller_info,
                }
                results[sku] = score_and_classify_listing(item, watchlist)
        except Exception as exc:
            print(f"[ClassicCars] Error scraping {url}: {exc}")

    return list(results.values())


# =====================================================================
# 2. AUTOTRADER CLASSICS SCRAPER
# =====================================================================
def scrape_autotrader(watchlist: dict[str, Any]) -> list[dict[str, Any]]:
    """Scrape Classics on Autotrader using embedded window.__PRELOADED_STATE__ JSON."""
    make_slug = (watchlist.get("make") or "cadillac").strip().lower().replace(" ", "_")
    y_min = int(watchlist.get("year_min", 1960))
    primary_kw = (watchlist.get("model_primary") or "").strip().lower()
    related_kws = [k.strip().lower() for k in (watchlist.get("related_keywords") or "").split(",") if k.strip()]

    urls = [
        f"https://classics.autotrader.com/classic-cars-for-sale/{y_min}-{make_slug}-for-sale",
    ]
    if primary_kw:
        urls.append(f"https://classics.autotrader.com/classic-cars-for-sale/{make_slug}-for-sale?keyword={quote_plus(primary_kw)}")
    for rkw in related_kws[:2]:
        if rkw != primary_kw:
            urls.append(f"https://classics.autotrader.com/classic-cars-for-sale/{make_slug}-for-sale?keyword={quote_plus(rkw)}")

    results: dict[str, dict[str, Any]] = {}
    session = _get_chrome_session()
    decoder = json.JSONDecoder()

    for url in urls:
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code != 200:
                continue

            # Also extract descriptions from ld+json if present
            soup = BeautifulSoup(resp.text, "lxml")
            ld_descriptions: dict[str, str] = {}
            ld_tag = soup.find("script", type="application/ld+json")
            if ld_tag and ld_tag.string:
                try:
                    ld_list = json.loads(ld_tag.string)
                    if isinstance(ld_list, list):
                        for entry in ld_list:
                            if isinstance(entry, dict) and entry.get("@type") == "Car":
                                ld_descriptions[entry.get("name", "")] = entry.get("description", "")
                except Exception:
                    pass

            marker = "window.__PRELOADED_STATE__ = "
            idx = resp.text.find(marker)
            if idx == -1:
                continue

            state, _ = decoder.raw_decode(resp.text[idx + len(marker):])
            vehicles = state.get("current_search", {}).get("results", [])
            for v in vehicles:
                ext_id = str(v.get("id") or "")
                if not ext_id or ext_id in results:
                    continue

                title = v.get("title") or f"{v.get('year', '')} {v.get('make', '')} {v.get('model', '')}".strip()
                v_year = extract_year_from_text(str(v.get("year") or title))

                desc = ld_descriptions.get(title, "")
                combined_check = f"{title} {v.get('model', '')} {v.get('trim', '')} {desc}".lower()
                check_pool = ([primary_kw] if primary_kw else []) + (related_kws or COACHBUILT_KEYWORDS)
                if "keyword=" in url and v_year != y_min:
                    if check_pool and not any(kw in combined_check for kw in check_pool):
                        continue

                price_usd, price_display = parse_price_to_usd(v.get("price"))
                seller = v.get("seller") or {}
                loc_str = seller.get("location") or ""
                city = seller.get("public_city") or ""
                state_name = seller.get("public_state") or ""
                zip_code = str(seller.get("public_zip") or "")
                state_code = STATE_NAME_TO_CODE.get(state_name.lower(), state_name[:2].upper() if state_name else "")

                vdp_url = v.get("vdp_url") or ""
                full_url = f"https://classics.autotrader.com{vdp_url}" if vdp_url.startswith("/") else vdp_url

                photos = v.get("photos") or []
                image_url = photos[0] if photos else ""

                subtitle_parts = [p for p in [v.get("engine"), v.get("exterior_color"), f"{v.get('mileage'):,} mi" if isinstance(v.get("mileage"), int) else None] if p]

                item = {
                    "id": _make_listing_id(watchlist["id"], "autotrader", ext_id),
                    "watchlist_id": watchlist["id"],
                    "source": "autotrader",
                    "external_id": ext_id,
                    "title": title,
                    "subtitle": " • ".join(subtitle_parts),
                    "year": v_year,
                    "make": v.get("make") or watchlist.get("make", "Cadillac"),
                    "model": v.get("model") or "",
                    "price_usd": price_usd,
                    "price_display": price_display,
                    "location": loc_str or f"{city}, {state_code} {zip_code}".strip(" ,"),
                    "city": city,
                    "state": state_code,
                    "zip_code": zip_code,
                    "url": full_url,
                    "image_url": image_url,
                    "description": desc[:400] if desc else " • ".join(subtitle_parts),
                    "seller_info": "Private Seller" if v.get("sale_type") == "private_seller" else "Dealer (Autotrader Classics)",
                }
                results[ext_id] = score_and_classify_listing(item, watchlist)
        except Exception as exc:
            print(f"[Autotrader] Error scraping {url}: {exc}")

    return list(results.values())


# =====================================================================
# 3. HEMMINGS SCRAPER
# =====================================================================
def scrape_hemmings(watchlist: dict[str, Any]) -> list[dict[str, Any]]:
    """Scrape Hemmings Classifieds using embedded listings-search-json."""
    make_slug = (watchlist.get("make") or "cadillac").strip().lower().replace(" ", "-")
    y_min = int(watchlist.get("year_min", 1960))
    primary_kw = (watchlist.get("model_primary") or "").strip().lower()
    related_kws = [k.strip().lower() for k in (watchlist.get("related_keywords") or "").split(",") if k.strip()]

    urls = [
        f"https://www.hemmings.com/classifieds/cars-for-sale/{make_slug}/all-models/{y_min}",
    ]
    if primary_kw:
        urls.append(f"https://www.hemmings.com/classifieds/cars-for-sale/{make_slug}?q={quote_plus(primary_kw)}")
    for rkw in related_kws[:3]:
        if rkw != primary_kw:
            urls.append(f"https://www.hemmings.com/classifieds/cars-for-sale/{make_slug}?q={quote_plus(rkw)}")

    results: dict[str, dict[str, Any]] = {}
    session = _get_chrome_session()

    for url in urls:
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            ls_tag = soup.find("script", id="listings-search-json")
            if not ls_tag or not ls_tag.string:
                continue

            data = json.loads(ls_tag.string)
            items = data.get("results", {}).get("results", [])
            for it in items:
                ext_id = str(it.get("id") or "")
                if not ext_id or ext_id in results:
                    continue

                title = (it.get("long_title") or it.get("title") or "").strip()
                if not title:
                    continue

                raw_price = it.get("price") or it.get("current_price") or it.get("current_bid")
                price_usd, price_display = parse_price_to_usd(raw_price)

                loc_raw = it.get("location") or ""
                city = it.get("city") or ""
                state = it.get("state") or ""
                _, parsed_state, zip_code = parse_location_parts(loc_raw)
                state_code = state if len(state) == 2 else (parsed_state or STATE_NAME_TO_CODE.get(state.lower(), ""))

                thumb = it.get("thumbnail") or {}
                image_url = (
                    thumb.get("xl", {}).get("16:9")
                    or thumb.get("lg", {}).get("16:9")
                    or thumb.get("md", {}).get("16:9")
                    or ""
                )

                full_url = it.get("url") or f"https://www.hemmings.com{it.get('relative_url', '')}"
                year = extract_year_from_text(title, default=y_min if f"/{y_min}" in url else None)

                seller = it.get("seller") or ("Dealer Certificado" if it.get("certified_dealer") else "Hemmings Classifieds")
                listing_type = it.get("type") or it.get("listing_type") or ""
                subtitle = f"Hemmings {listing_type.replace('_', ' ').title()}".strip()

                item = {
                    "id": _make_listing_id(watchlist["id"], "hemmings", ext_id),
                    "watchlist_id": watchlist["id"],
                    "source": "hemmings",
                    "external_id": ext_id,
                    "title": title,
                    "subtitle": subtitle,
                    "year": year,
                    "make": watchlist.get("make", "Cadillac"),
                    "model": it.get("title", "").replace(str(year or ""), "").replace(watchlist.get("make", "Cadillac"), "").strip(),
                    "price_usd": price_usd,
                    "price_display": price_display,
                    "location": f"{city}, {state_code}".strip(" ,") if (city or state_code) else loc_raw,
                    "city": city,
                    "state": state_code,
                    "zip_code": zip_code or "",
                    "url": full_url,
                    "image_url": image_url,
                    "description": it.get("abstract") or f"{title} listado via {seller} no Hemmings.",
                    "seller_info": str(seller),
                }
                results[ext_id] = score_and_classify_listing(item, watchlist)
        except Exception as exc:
            print(f"[Hemmings] Error scraping {url}: {exc}")

    return list(results.values())


# =====================================================================
# 4. EBAY MOTORS SCRAPER
# =====================================================================
def scrape_ebay(watchlist: dict[str, Any]) -> list[dict[str, Any]]:
    """Scrape eBay Motors Cars & Trucks (Category 6001) for target vehicle & related keywords."""
    make = (watchlist.get("make") or "Cadillac").strip()
    y_min = int(watchlist.get("year_min", 1960))
    primary_kw = (watchlist.get("model_primary") or "").strip()
    related_kws = [k.strip().lower() for k in (watchlist.get("related_keywords") or "").split(",") if k.strip()]
    target_zip = (watchlist.get("target_zip") or "33441").strip()

    queries = [f"{y_min} {make.lower()}"]
    if primary_kw:
        queries.append(f"{y_min} {make.lower()} {primary_kw.lower()}" if primary_kw.lower() != "eureka" else f"{make.lower()} {primary_kw.lower()}")
    if related_kws:
        queries.append(f"{make.lower()} {related_kws[0]}")
    unique_queries = list(dict.fromkeys(queries))

    results: dict[str, dict[str, Any]] = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    for attempt_session in range(2):
        try:
            with httpx.Client(headers=headers, follow_redirects=True, timeout=20.0) as client:
                client.get("https://www.ebay.com/")
                client.get(f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(make.lower())}&_sacat=6001")

                for q in unique_queries:
                    url = (
                        f"https://www.ebay.com/sch/6001/i.html?_nkw={quote_plus(q)}"
                        f"&_stpos={target_zip}&_fcid=1&LH_PrefLoc=1&_udlo=5000"
                    )
                    cards = []
                    for retry in range(3):
                        resp = client.get(url)
                        if resp.status_code == 200:
                            soup = BeautifulSoup(resp.text, "lxml")
                            cards = soup.select("li.s-card, li.s-item")
                            if len(cards) > 2:
                                break
                        time.sleep(0.7)

                    for card in cards:
                        title_el = card.select_one(".s-card__title, .s-item__title")
                        if not title_el:
                            continue
                        raw_title = title_el.get_text(" ", strip=True)
                        title = re.sub(r"^New Listing\s+", "", raw_title, flags=re.I)
                        title = re.sub(r"\s*Opens in a new window or tab.*$", "", title, flags=re.I).strip()
                        if not title or "shop on ebay" in title.lower():
                            continue
                        if make.lower() not in title.lower():
                            continue

                        link_el = card.select_one("a[href*='/itm/']")
                        if not link_el or not link_el.has_attr("href"):
                            continue
                        href = link_el["href"].split("?")[0]
                        m_itm = re.search(r"/itm/(\d+)", href)
                        ext_id = m_itm.group(1) if m_itm else href
                        if ext_id in results or ext_id == "123456":
                            continue

                        price_el = card.select_one(".s-card__price, .s-item__price")
                        raw_price = price_el.get_text(" ", strip=True) if price_el else None
                        price_usd, price_display = parse_price_to_usd(raw_price)

                        if is_likely_part_or_memorabilia(title, price_usd):
                            continue

                        attrs = [
                            a.get_text(" ", strip=True)
                            for a in card.select(".s-card__attribute-row, .s-item__location, .s-item__subtitle")
                            if a.get_text(" ", strip=True)
                        ]

                        year = extract_year_from_text(title)
                        miles_str = ""
                        seller_str = "eBay Motors"
                        loc_str = "Estados Unidos (eBay Motors)"
                        sale_format = ""

                        for attr in attrs:
                            if attr.startswith("Year:"):
                                year = extract_year_from_text(attr, default=year)
                            elif attr.startswith("Miles:"):
                                miles_str = attr
                            elif "positive" in attr:
                                seller_str = attr
                            elif any(k in attr.lower() for k in ["buy it now", "best offer", "classified ad", "bids", "bid"]):
                                sale_format = attr
                            elif attr.lower().startswith("from ") or re.search(r",\s*[A-Z]{2}\b", attr):
                                loc_str = re.sub(r"^located in\s+|^from\s+", "", attr, flags=re.I).strip()

                        img_el = card.select_one("img")
                        image_url = ""
                        if img_el:
                            image_url = img_el.get("src") or img_el.get("data-src") or ""
                            image_url = re.sub(r"/s-l\d+\.(jpg|webp|png)", r"/s-l800.\1", image_url)

                        subtitle_parts = [p for p in [sale_format, miles_str] if p]

                        item = {
                            "id": _make_listing_id(watchlist["id"], "ebay", ext_id),
                            "watchlist_id": watchlist["id"],
                            "source": "ebay",
                            "external_id": ext_id,
                            "title": title,
                            "subtitle": " • ".join(subtitle_parts),
                            "year": year,
                            "make": make,
                            "model": title.replace(str(year or ""), "").replace(make, "").strip(),
                            "price_usd": price_usd,
                            "price_display": price_display,
                            "location": loc_str,
                            "city": "",
                            "state": "",
                            "zip_code": "",
                            "url": href,
                            "image_url": image_url,
                            "description": " | ".join(attrs[:6]),
                            "seller_info": seller_str,
                        }
                        results[ext_id] = score_and_classify_listing(item, watchlist)

                    time.sleep(0.5)
            if results:
                break
        except Exception as exc:
            print(f"[eBay] Session error: {exc}")

    return list(results.values())


def run_watchlist_scan(watchlist: dict[str, Any]) -> dict[str, Any]:
    """Run all enabled portal scrapers for a watchlist and return aggregated results."""
    enabled = [
        s.strip().lower()
        for s in (watchlist.get("sources") or "ebay,autotrader,classiccars,hemmings").split(",")
        if s.strip()
    ]

    scrapers = {
        "classiccars": scrape_classiccars,
        "autotrader": scrape_autotrader,
        "hemmings": scrape_hemmings,
        "ebay": scrape_ebay,
    }

    all_items: list[dict[str, Any]] = []
    source_stats: dict[str, int] = {}

    y_min = int(watchlist.get("year_min", 1960))
    y_max = int(watchlist.get("year_max", 1960))
    max_classic_year = max(1972, y_max + 5)
    primary_kw = (watchlist.get("model_primary") or "").strip().lower()
    broad_model = primary_kw in ("", "all", "todos", "qualquer", "*")

    for src in enabled:
        fn = scrapers.get(src)
        if not fn:
            continue
        t0 = time.time()
        raw_items = fn(watchlist)
        filtered_items = []
        for it in raw_items:
            yr = it.get("year")
            tier = it.get("match_tier")
            # 1. Exact model match (e.g., "Eureka", "Mustang", "Bel Air") within target year window
            if tier == "exact_target":
                if yr is None or (y_min - 2) <= int(yr) <= (y_max + 2):
                    filtered_items.append(it)
            # 2. Classic variant / Coachbuilt match (e.g. Hearse, Ambulance, Fastback) within classic window
            elif tier == "coachbuilt":
                if yr is None or int(yr) <= max_classic_year:
                    filtered_items.append(it)
            # 3. If user searched only by Make + Year (left Model blank / 'todos'), include all models of that year
            elif broad_model and yr is not None and y_min <= int(yr) <= y_max:
                filtered_items.append(it)
        elapsed = round(time.time() - t0, 2)
        source_stats[src] = len(filtered_items)
        print(f"[Hunter] {src.upper()}: {len(filtered_items)} vehicles found in {elapsed}s")
        all_items.extend(filtered_items)

    return {
        "items": all_items,
        "source_stats": source_stats,
    }
