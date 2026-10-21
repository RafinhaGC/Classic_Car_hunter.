"""Run an immediate multi-portal scan and print top results for 1960 Cadillac Eureka."""

import sys
from app.database import get_conn, init_db
from app.main import execute_scan_sync

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    init_db()
    conn = get_conn()
    with conn:
        conn.execute(
            "DELETE FROM listings WHERE LOWER(title) NOT LIKE '%cadillac%' "
            "OR (year != 1960 AND match_tier NOT IN ('exact_target', 'coachbuilt'))"
        )
    conn.close()
    print("Iniciando varredura nos 4 portais (eBay Motors, Autotrader Classics, ClassicCars.com, Hemmings)...")
    summary = execute_scan_sync(1)
    print("\n=== RESUMO DA VARREDURA ===")
    print(f"Status: {summary['status']}")
    print(f"Total de veículos encontrados: {summary['total_scraped']}")
    print(f"Novos anúncios adicionados: {summary['new_listings']}")
    print(f"Por portal: {summary['source_stats']}")

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT source, title, price_display, location, proximity_badge, match_tier_label, match_score, url
        FROM listings
        WHERE watchlist_id = 1 AND is_dismissed = 0
        ORDER BY match_score DESC, COALESCE(distance_miles, 9999) ASC
        LIMIT 15
        """
    ).fetchall()
    conn.close()

    print("\n=== TOP 15 VEÍCULOS POR RELEVÂNCIA E PROXIMIDADE DE DEERFIELD BEACH, FL ===")
    for idx, r in enumerate(rows, start=1):
        print(
            f"{idx:2d}. [{r['match_score']} pts] [{r['source'].upper()}] {r['title']} | "
            f"{r['price_display']} | {r['location']} ({r['proximity_badge']}) | {r['match_tier_label']}\n"
            f"    URL: {r['url']}"
        )


if __name__ == "__main__":
    main()
