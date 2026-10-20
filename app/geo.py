"""Geolocation and distance calculation relative to Deerfield Beach, FL (33441)."""

from __future__ import annotations
import math
import re
from typing import Optional, Tuple

# Default reference: Deerfield Beach, Florida (ZIP 33441)
DEERFIELD_BEACH_LAT = 26.3184
DEERFIELD_BEACH_LON = -80.0998

# US State name to 2-letter code mapping
STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KT", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
STATE_NAME_TO_CODE["kentucky"] = "KY"

# Approximate coordinates for major Florida & US cities frequently seen in classic car listings
CITY_COORDS: dict[str, Tuple[float, float]] = {
    ("deerfield beach", "FL"): (26.3184, -80.0998),
    ("boca raton", "FL"): (26.3683, -80.1289),
    ("pompano beach", "FL"): (26.2379, -80.1248),
    ("fort lauderdale", "FL"): (26.1224, -80.1373),
    ("west palm beach", "FL"): (26.7153, -80.0534),
    ("miami", "FL"): (25.7617, -80.1918),
    ("hollywood", "FL"): (26.0112, -80.1495),
    ("coral springs", "FL"): (26.2712, -80.2706),
    ("delray beach", "FL"): (26.4615, -80.0728),
    ("stuart", "FL"): (27.1975, -80.2528),
    ("orlando", "FL"): (28.5383, -81.3792),
    ("kissimmee", "FL"): (28.2920, -81.4076),
    ("tampa", "FL"): (27.9506, -82.4572),
    ("lutz", "FL"): (28.1511, -82.4615),
    ("st. petersburg", "FL"): (27.7676, -82.6403),
    ("clearwater", "FL"): (27.9659, -82.8001),
    ("sarasota", "FL"): (27.3364, -82.5307),
    ("venice", "FL"): (27.0998, -82.4543),
    ("naples", "FL"): (26.1420, -81.7948),
    ("fort myers", "FL"): (26.6406, -81.8723),
    ("punta gorda", "FL"): (26.9298, -82.0454),
    ("lakeland", "FL"): (28.0395, -81.9498),
    ("ocala", "FL"): (29.1872, -82.1401),
    ("daytona beach", "FL"): (29.2108, -81.0228),
    ("jacksonville", "FL"): (30.3322, -81.6557),
    ("st. augustine", "FL"): (29.8947, -81.3145),
    ("tallahassee", "FL"): (30.4383, -84.2807),
    ("pensacola", "FL"): (30.4213, -87.2169),
    ("atlanta", "GA"): (33.7490, -84.3880),
    ("savannah", "GA"): (32.0809, -81.0912),
    ("charleston", "SC"): (32.7765, -79.9311),
    ("greer", "SC"): (34.9387, -82.2271),
    ("greenville", "SC"): (34.8526, -82.3940),
    ("charlotte", "NC"): (35.2271, -80.8431),
    ("concord", "NC"): (35.4088, -80.5795),
    ("raleigh", "NC"): (35.7796, -78.6382),
    ("nashville", "TN"): (36.1627, -86.7816),
    ("birmingham", "AL"): (33.5186, -86.8104),
    ("new orleans", "LA"): (29.9511, -90.0715),
    ("dallas", "TX"): (32.7767, -96.7970),
    ("houston", "TX"): (29.7604, -95.3698),
    ("austin", "TX"): (30.2672, -97.7431),
    ("san antonio", "TX"): (29.4241, -98.4936),
    ("tulsa", "OK"): (36.1540, -95.9928),
    ("blanchard", "OK"): (35.1381, -97.6584),
    ("oklahoma city", "OK"): (35.4676, -97.5164),
    ("branson", "MO"): (36.6437, -93.2185),
    ("st. louis", "MO"): (38.6270, -90.1994),
    ("kansas city", "MO"): (39.0997, -94.5786),
    ("des moines", "IA"): (41.5868, -93.6250),
    ("chicago", "IL"): (41.8781, -87.6298),
    ("volo", "IL"): (42.3236, -88.1665),
    ("detroit", "MI"): (42.3314, -83.0458),
    ("cadillac", "MI"): (44.2519, -85.4012),
    ("grand rapids", "MI"): (42.9634, -85.6681),
    ("oshkosh", "WI"): (44.0247, -88.5426),
    ("milwaukee", "WI"): (43.0389, -87.9065),
    ("columbus", "OH"): (39.9612, -82.9988),
    ("cincinnati", "OH"): (39.1031, -84.5120),
    ("cleveland", "OH"): (41.4993, -81.6944),
    ("indianapolis", "IN"): (39.7684, -86.1581),
    ("pittsburgh", "PA"): (40.4406, -79.9959),
    ("philadelphia", "PA"): (39.9526, -75.1652),
    ("atlantic city", "NJ"): (39.3643, -74.4229),
    ("new york", "NY"): (40.7128, -74.0060),
    ("calverton", "NY"): (40.9168, -72.7429),
    ("boston", "MA"): (42.3601, -71.0589),
    ("phoenix", "AZ"): (33.4484, -112.0740),
    ("scottsdale", "AZ"): (33.4942, -111.9261),
    ("las vegas", "NV"): (36.1699, -115.1398),
    ("los angeles", "CA"): (34.0522, -118.2437),
    ("san diego", "CA"): (32.7157, -117.1611),
    ("san francisco", "CA"): (37.7749, -122.4194),
    ("sacramento", "CA"): (38.5816, -121.4944),
    ("portland", "OR"): (45.5152, -122.6784),
    ("seattle", "WA"): (47.6062, -122.3321),
    ("denver", "CO"): (39.7392, -104.9903),
}

# State centroids as fallback
STATE_COORDS: dict[str, Tuple[float, float]] = {
    "FL": (27.9944, -81.7603),
    "GA": (32.6782, -83.2229),
    "SC": (33.8361, -81.1637),
    "NC": (35.7596, -79.0193),
    "AL": (32.8067, -86.7911),
    "TN": (35.5175, -86.5804),
    "MS": (32.3547, -89.3985),
    "LA": (30.9843, -91.9623),
    "VA": (37.4316, -78.6569),
    "WV": (38.5976, -80.4549),
    "KY": (37.8393, -84.2700),
    "MD": (39.0458, -76.6413),
    "DE": (38.9108, -75.5277),
    "DC": (38.9072, -77.0369),
    "PA": (41.2033, -77.1945),
    "NJ": (40.0583, -74.4057),
    "NY": (42.6526, -75.5268),
    "CT": (41.6032, -73.0877),
    "RI": (41.5801, -71.4774),
    "MA": (42.4072, -71.3824),
    "VT": (44.5588, -72.5778),
    "NH": (43.1939, -71.5724),
    "ME": (45.2538, -69.4455),
    "OH": (40.4173, -82.9071),
    "IN": (40.2672, -86.1349),
    "MI": (43.3266, -84.5361),
    "IL": (40.6331, -89.3985),
    "WI": (43.7844, -88.7879),
    "MN": (46.7296, -94.6859),
    "IA": (41.8780, -93.0977),
    "MO": (37.9643, -91.8318),
    "AR": (35.2010, -91.8318),
    "OK": (35.4676, -97.5164),
    "TX": (31.9686, -99.9018),
    "KS": (39.0119, -98.4842),
    "NE": (41.4925, -99.9018),
    "SD": (43.9695, -99.9018),
    "ND": (47.5515, -101.0020),
    "MT": (46.8797, -110.3626),
    "WY": (43.0760, -107.2903),
    "CO": (39.5501, -105.7821),
    "NM": (34.5199, -105.8701),
    "AZ": (34.0489, -111.0937),
    "UT": (39.3210, -111.0937),
    "NV": (38.8026, -116.4194),
    "ID": (44.0682, -114.7420),
    "WA": (47.7511, -120.7401),
    "OR": (43.8041, -120.5542),
    "CA": (36.7783, -119.4179),
}

# Florida 3-digit ZIP prefix coordinates for high-accuracy South/Central/North Florida distances
FL_ZIP3_COORDS: dict[str, Tuple[float, float]] = {
    "330": (25.95, -80.20),  # Hollywood / Pompano / Homestead / Key West
    "331": (25.77, -80.19),  # Miami
    "332": (25.77, -80.19),  # Miami
    "333": (26.12, -80.14),  # Fort Lauderdale
    "334": (26.46, -80.09),  # Boca Raton / Deerfield Beach / West Palm Beach
    "335": (28.05, -82.35),  # Lutz / Brandon / Tampa suburbs
    "336": (27.95, -82.46),  # Tampa
    "337": (27.77, -82.64),  # St. Petersburg / Clearwater
    "338": (28.04, -81.95),  # Lakeland / Winter Haven
    "339": (26.64, -81.87),  # Fort Myers / Cape Coral
    "341": (26.14, -81.79),  # Naples
    "342": (27.20, -82.48),  # Sarasota / Venice / Bradenton
    "344": (29.18, -82.14),  # Ocala / Gainesville area
    "346": (28.30, -82.70),  # Spring Hill / New Port Richey
    "347": (28.35, -81.45),  # Kissimmee / Clermont
    "349": (27.28, -80.35),  # Port St. Lucie / Stuart / Fort Pierce
    "320": (30.15, -81.80),  # St. Augustine / Lake City
    "321": (29.21, -81.02),  # Daytona Beach / Palm Coast
    "322": (30.33, -81.66),  # Jacksonville
    "323": (30.44, -84.28),  # Tallahassee
    "324": (30.16, -85.66),  # Panama City
    "325": (30.42, -87.22),  # Pensacola
    "326": (29.65, -82.32),  # Gainesville
    "327": (28.75, -81.30),  # Sanford / Winter Park
    "328": (28.54, -81.38),  # Orlando
    "329": (28.08, -80.60),  # Melbourne / Vero Beach
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in miles between two coordinates."""
    r_miles = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return round(2 * r_miles * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def parse_location_parts(raw_location: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract (city, state_code, zip_code) from strings like 'Venice, Florida 34293' or 'Calverton, NY 11933'."""
    if not raw_location:
        return None, None, None

    text = raw_location.strip()
    text = re.sub(r"^from\s+", "", text, flags=re.I)

    # Extract 5-digit US ZIP code if present
    zip_match = re.search(r"\b(\d{5})\b", text)
    zip_code = zip_match.group(1) if zip_match else None

    # Clean ZIP from string
    clean = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", text).strip(" ,-")

    # Try City, ST or City, State Name
    parts = [p.strip() for p in clean.split(",") if p.strip()]
    city = None
    state_code = None

    if len(parts) >= 2:
        city = parts[0]
        st_candidate = parts[1].upper()
        if len(st_candidate) == 2 and st_candidate in STATE_COORDS:
            state_code = st_candidate
        else:
            st_lower = parts[1].lower()
            state_code = STATE_NAME_TO_CODE.get(st_lower)
    elif len(parts) == 1:
        st_lower = parts[0].lower()
        if st_lower in STATE_NAME_TO_CODE:
            state_code = STATE_NAME_TO_CODE[st_lower]
        elif parts[0].upper() in STATE_COORDS:
            state_code = parts[0].upper()
        else:
            city = parts[0]

    return city, state_code, zip_code


def estimate_distance_from_deerfield(
    raw_location: str = "",
    city: Optional[str] = None,
    state: Optional[str] = None,
    zip_code: Optional[str] = None,
    ref_lat: float = DEERFIELD_BEACH_LAT,
    ref_lon: float = DEERFIELD_BEACH_LON,
) -> Tuple[Optional[float], str]:
    """Return (distance_miles, proximity_badge) from Deerfield Beach, FL."""
    p_city, p_state, p_zip = parse_location_parts(raw_location)
    city = city or p_city
    state = state or p_state
    zip_code = zip_code or p_zip

    if state and len(state) > 2:
        state = STATE_NAME_TO_CODE.get(state.lower(), state[:2].upper())
    elif state:
        state = state.upper()

    coords: Optional[Tuple[float, float]] = None

    # 1. Exact city + state lookup
    if city and state:
        coords = CITY_COORDS.get((city.lower().strip(), state))

    # 2. Florida 3-digit ZIP lookup
    if not coords and zip_code and len(zip_code) >= 3 and zip_code[:3] in FL_ZIP3_COORDS:
        if zip_code == "33441":
            coords = (DEERFIELD_BEACH_LAT, DEERFIELD_BEACH_LON)
        else:
            coords = FL_ZIP3_COORDS[zip_code[:3]]

    # 3. State centroid fallback
    if not coords and state and state in STATE_COORDS:
        coords = STATE_COORDS[state]

    if not coords:
        return None, "Localização EUA (Distância N/D)"

    dist = haversine_miles(ref_lat, ref_lon, coords[0], coords[1])

    if dist <= 60:
        badge = f"Sul da Flórida ({int(dist)} mi de Deerfield Beach)"
    elif state == "FL" or dist <= 320:
        badge = f"Flórida ({int(dist)} mi de Deerfield Beach)"
    elif dist <= 750:
        badge = f"Sudeste EUA ({int(dist)} mi)"
    elif dist <= 1300:
        badge = f"Costa Leste / Meio-Oeste ({int(dist)} mi)"
    else:
        badge = f"EUA ({int(dist)} mi)"

    return dist, badge
