#!/usr/bin/env python3
"""Refresh datacenters/datacenters.json from Epoch AI + FracTracker sources.

Sources:
  - Epoch AI "AI Data Centers" (CC-BY 4.0): deep data on ~72 major AI facilities.
    Attribution on the site is a license requirement.
  - FracTracker Alliance national data centers tracker (public Google Sheet):
    broad coverage incl. proposed facilities. Reuse permission pending — see
    the tracker page before removing the attribution.

Usage:
  python3 scripts/refresh-datacenters.py                 # fetch live sources
  python3 scripts/refresh-datacenters.py --epoch-csv f --epoch-timelines f --fractracker-csv f

Merges the two sources, dedupes (haversine < 5 km AND operator/name token
overlap), preserves ids + dateAdded across refreshes, validates, and writes
datacenters/datacenters.json. Merge decisions and 5-15 km near-misses are
logged to stderr for manual review; force decisions go in
scripts/datacenter-overrides.json.

Geocoding (Epoch addresses only) uses the Census geocoder at 1 req/sec,
cached in scripts/geocode-cache.json (committed). Never use Nominatim here —
bulk geocoding violates their usage policy.
"""

import argparse
import csv
import io
import json
import math
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    # macOS system Python often lacks bundled certs; fall back to the OS bundle
    if Path("/etc/ssl/cert.pem").exists():
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ssl.create_default_context()

SSL_CTX = _ssl_context()

REPO = Path(__file__).resolve().parent.parent
OUT_PATH = REPO / "datacenters" / "datacenters.json"
CACHE_PATH = REPO / "scripts" / "geocode-cache.json"
OVERRIDES_PATH = REPO / "scripts" / "datacenter-overrides.json"

EPOCH_CSV_URL = "https://epoch.ai/data/data_centers/data_centers.csv"
EPOCH_TIMELINES_URL = "https://epoch.ai/data/data_centers/data_center_timelines.csv"
FRACTRACKER_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1JJ6kcVo-NjlAYtznwHOki2DVl4WWV6lhy-eXhFCdKKU/export?format=csv"
)

# Columns we depend on; if the FracTracker sheet drops/renames any, abort
# loudly rather than emit partial data.
FT_REQUIRED_COLS = {
    "facility_name", "address", "city", "state", "zip", "county", "lat",
    "long", "status", "location_confidence", "operator_name", "mw",
    "sizerank", "cooling_source", "cooling_type", "project_cost",
    "expected_date_online", "info_source_1", "date_created", "date_updated",
}
EPOCH_REQUIRED_COLS = {
    "Name", "Current power (MW)", "Owner", "Selected Sources", "Country", "Address",
}

FT_STATUS_MAP = {
    "proposed": "proposed",
    "pre-proposal": "proposed",
    "approved/permitted/under construction": "construction",
    "operating": "operating",
    "expanding": "expanding",
    "suspended": "suspended",
    "cancelled": "cancelled",
}

STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR",
}

STOPWORDS = {
    "data", "center", "centre", "campus", "project", "the", "of", "at",
    "llc", "inc", "corp", "site", "facility", "phase",
}


def log(msg):
    print(msg, file=sys.stderr)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "agora-datacenters/1.0"})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="replace")


def read_csv(path_or_url, is_url):
    text = fetch(path_or_url) if is_url else Path(path_or_url).read_text()
    return list(csv.DictReader(io.StringIO(text)))


def check_columns(rows, required, source_name):
    have = set(rows[0].keys())
    missing = required - have
    if missing:
        sys.exit(f"FATAL: {source_name} is missing expected columns {sorted(missing)}. "
                 "The source schema changed — update the script before re-running.")


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def tokens(s):
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t and t not in STOPWORDS}


def token_overlap(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")


def parse_float(s):
    s = (s or "").strip().replace(",", "").replace("$", "")
    if not s:
        return None
    m = re.match(r"^-?\d+(\.\d+)?", s)
    return float(m.group(0)) if m else None


def iso_date(mdY):
    """MM/DD/YYYY -> YYYY-MM-DD, else None."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", (mdY or "").strip())
    if not m:
        return None
    mo, d, y = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


# ---------------------------------------------------------------- geocoding

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


_last_census_call = [0.0]

def census_get(url):
    wait = 1.0 - (time.time() - _last_census_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_census_call[0] = time.time()
    return json.loads(fetch(url))


def geocode_address(address, cache):
    """Address -> {lat, lng, county, state} via Census geocoder, cached."""
    key = "addr:" + address
    if key in cache:
        return cache[key]
    result = None
    q = urllib.parse.quote(address)
    try:
        data = census_get(
            "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
            f"?address={q}&benchmark=Public_AR_Current&vintage=Current_Current"
            "&layers=Counties&format=json"
        )
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            m = matches[0]
            coords = m["coordinates"]
            counties = m.get("geographies", {}).get("Counties", [])
            county = counties[0]["BASENAME"] if counties else None
            state = None
            sm = re.search(r",\s*([A-Z]{2})\s+\d{5}", address)
            if sm and sm.group(1) in STATE_ABBRS:
                state = sm.group(1)
            result = {"lat": round(coords["y"], 5), "lng": round(coords["x"], 5),
                      "county": county, "state": state}
    except Exception as e:
        log(f"  geocode error for {address!r}: {e}")
        return None  # transient failure — don't poison the cache
    cache[key] = result
    return result


# ------------------------------------------------------------------ parsing

def parse_fractracker(rows):
    facilities = []
    skipped = 0
    for r in rows:
        r = {k: (v or "").strip() for k, v in r.items()}
        lat, lng = parse_float(r["lat"]), parse_float(r["long"])
        status = FT_STATUS_MAP.get(r["status"].lower())
        if lat is None or lng is None or status is None:
            skipped += 1
            continue
        state = r["state"].upper() if r["state"].upper() in STATE_ABBRS else None
        urls = [r[f"info_source_{i}"] for i in range(1, 9)
                if r.get(f"info_source_{i}", "").startswith("http")][:4]
        cooling = " / ".join(x for x in (r["cooling_source"], r["cooling_type"]) if x) or None
        facilities.append({
            "name": r["facility_name"] or "Unnamed data center",
            "operator": r["operator_name"] or None,
            "status": status,
            "lat": lat, "lng": lng,
            "city": r["city"] or None,
            "county": (r["county"] or None) and r["county"].replace(" County", ""),
            "state": state,
            "power_mw": parse_float(r["mw"]) or None,
            "size_label": (r["sizerank"] if r["sizerank"] and r["sizerank"] != "Unknown" else None),
            "water_mgd": None,
            "cooling": cooling,
            "cost": r["project_cost"] or None,
            "timeline": {"expected_online": r["expected_date_online"] or None} if r["expected_date_online"] and r["expected_date_online"].lower() != "unknown" else None,
            "sources": ["fractracker"],
            "source_urls": urls,
            "dateAdded": iso_date(r["date_created"]) or date.today().isoformat(),
            "dateUpdated": iso_date(r["date_updated"]),
        })
    if skipped:
        log(f"FracTracker: skipped {skipped} rows (missing coords or unknown status)")
    return facilities


def parse_epoch(rows, timeline_rows, cache):
    # Latest timeline row per facility -> water use + buildings operational.
    latest = {}
    for tr in timeline_rows:
        name = tr["Data center"]
        if not name:
            continue
        cur = latest.setdefault(name, {"water": None, "buildings": None, "first": None, "last": None})
        d = tr.get("Date", "")
        if d:
            cur["first"] = min(cur["first"], d) if cur["first"] else d
            if not cur["last"] or d >= cur["last"]:
                cur["last"] = d
                w = parse_float(tr.get("Water use (MGD)", ""))
                if w is not None:
                    cur["water"] = w
                b = parse_float(tr.get("Buildings operational", ""))
                if b is not None:
                    cur["buildings"] = b
        else:
            w = parse_float(tr.get("Water use (MGD)", ""))
            if w is not None and cur["water"] is None:
                cur["water"] = w

    facilities = []
    for r in rows:
        if "United States" not in (r["Country"] or ""):
            continue
        address = (r["Address"] or "").strip()
        geo = geocode_address(address, cache) if address else None
        if not geo:
            # Keep the record without coords — dedupe() will try to adopt
            # coordinates from a FracTracker name match before dropping it.
            sm = re.search(r",?\s*([A-Z]{2})\s+\d{5}", address)
            geo = {"lat": None, "lng": None, "county": None,
                   "state": sm.group(1) if sm and sm.group(1) in STATE_ABBRS else None}
        owner = re.sub(r"\s*#\w+", "", r["Owner"] or "").strip() or None
        urls = re.findall(r"\((https?://[^\s)]+)\)", r["Selected Sources"] or "")[:4]
        tl = latest.get(r["Name"], {})
        status = "operating" if (tl.get("buildings") or 0) > 0 else "construction"
        city = None
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 3:
            city = parts[-2] if not re.search(r"[A-Z]{2}\s+\d{5}", parts[-2]) else parts[-3] if len(parts) >= 4 else None
        if len(parts) >= 2 and city is None:
            m = re.match(r"^(.*?)\s+[A-Z]{2}\s+\d{5}", parts[-1])
            city = parts[-2] if m else None
        facilities.append({
            "name": r["Name"],
            "operator": owner,
            "status": status,
            "lat": geo["lat"], "lng": geo["lng"],
            "city": city,
            "county": geo["county"],
            "state": geo["state"],
            "power_mw": parse_float(r["Current power (MW)"]) or None,  # 0 MW = not yet online
            "size_label": None,
            "water_mgd": tl.get("water"),
            "cooling": None,
            "cost": None,
            "timeline": {"first_observed": tl["first"][:10]} if tl.get("first") else None,
            "sources": ["epoch"],
            "source_urls": urls,
            "dateAdded": date.today().isoformat(),
            "dateUpdated": (tl.get("last") or "")[:10] or None,
        })
    return facilities


# ------------------------------------------------------------------- merge

def merge_key(f):
    return f"{f['name'].lower()}|{round(f['lat'], 3)}|{round(f['lng'], 3)}"


def merge_records(epoch_f, ft_f):
    """Epoch wins for curated numbers; FracTracker wins for status/locale."""
    merged = dict(ft_f)
    merged["name"] = epoch_f["name"]
    merged["operator"] = epoch_f["operator"] or ft_f["operator"]
    merged["power_mw"] = epoch_f["power_mw"] if epoch_f["power_mw"] is not None else ft_f["power_mw"]
    merged["water_mgd"] = epoch_f["water_mgd"] if epoch_f["water_mgd"] is not None else ft_f["water_mgd"]
    merged["county"] = ft_f["county"] or epoch_f["county"]
    merged["state"] = ft_f["state"] or epoch_f["state"]
    merged["city"] = ft_f["city"] or epoch_f["city"]
    tl = dict(ft_f["timeline"] or {})
    tl.update(epoch_f["timeline"] or {})
    merged["timeline"] = tl or None
    merged["sources"] = ["epoch", "fractracker"]
    merged["source_urls"] = (epoch_f["source_urls"] + [u for u in ft_f["source_urls"] if u not in epoch_f["source_urls"]])[:6]
    merged["dateUpdated"] = max(filter(None, [epoch_f["dateUpdated"], ft_f["dateUpdated"]]), default=None)
    return merged


def dedupe(epoch_fs, ft_fs, overrides):
    force_merge = {tuple(p) for p in overrides.get("force_merge", [])}
    force_split = {tuple(p) for p in overrides.get("force_split", [])}
    merged, used_ft = [], set()

    # Epoch records the Census geocoder couldn't place: adopt coordinates from
    # a confident FracTracker name match (same state, >=0.75 token overlap)
    # rather than losing Epoch's power/water data for major facilities.
    coordless = [ef for ef in epoch_fs if ef["lat"] is None]
    epoch_fs = [ef for ef in epoch_fs if ef["lat"] is not None]
    for ef in coordless:
        best, best_score = None, 0.0
        for i, ff in enumerate(ft_fs):
            if i in used_ft or (ef["name"], ff["name"]) in force_split:
                continue
            if ef["state"] and ff["state"] and ef["state"] != ff["state"]:
                continue
            score = max(token_overlap(ef["name"], ff["name"]),
                        min(token_overlap(ef["operator"], ff["operator"]),
                            token_overlap(ef["name"], ff["name"]) or 0))
            if (ef["name"], ff["name"]) in force_merge:
                score = 1.0
            if score >= 0.75 and score > best_score:
                best, best_score = i, score
        if best is not None:
            used_ft.add(best)
            log(f"MERGE (name match, no epoch coords): epoch {ef['name']!r} + fractracker {ft_fs[best]['name']!r}")
            merged.append(merge_records(ef, ft_fs[best]))
        else:
            log(f"DROP: epoch {ef['name']!r} — no coords and no FracTracker match "
                "(add manual_coords to datacenter-overrides.json)")

    for ef in epoch_fs:
        best, best_dist = None, None
        for i, ff in enumerate(ft_fs):
            if i in used_ft:
                continue
            pair = (ef["name"], ff["name"])
            d = haversine_km(ef["lat"], ef["lng"], ff["lat"], ff["lng"])
            if pair in force_split:
                continue
            is_match = pair in force_merge or (
                d < 5.0 and (token_overlap(ef["operator"], ff["operator"]) >= 0.5
                             or token_overlap(ef["name"], ff["name"]) >= 0.5)
            )
            if is_match and (best is None or d < best_dist):
                best, best_dist = i, d
            elif 5.0 <= d <= 15.0 and token_overlap(ef["name"], ff["name"]) >= 0.5:
                log(f"NEAR-MISS ({d:.1f} km): epoch {ef['name']!r} vs fractracker {ff['name']!r} "
                    f"— add to force_merge/force_split in datacenter-overrides.json if needed")
        if best is not None:
            used_ft.add(best)
            log(f"MERGE ({best_dist:.1f} km): epoch {ef['name']!r} + fractracker {ft_fs[best]['name']!r}")
            merged.append(merge_records(ef, ft_fs[best]))
        else:
            merged.append(ef)
    merged.extend(ff for i, ff in enumerate(ft_fs) if i not in used_ft)
    return merged


def assign_ids(facilities, old_facilities):
    old_by_name_state = {}
    for f in old_facilities:
        old_by_name_state.setdefault((f["name"].lower(), f.get("state")), f)
    seen_ids, pending = set(), []
    for f in facilities:
        old = old_by_name_state.get((f["name"].lower(), f.get("state")))
        if old and old["id"] not in seen_ids:
            f["id"] = old["id"]
            f["dateAdded"] = old.get("dateAdded") or f["dateAdded"]
            seen_ids.add(f["id"])
        else:
            pending.append(f)
    counters = {}
    for f in pending:
        base = "-".join(x for x in (
            slugify(f.get("state") or "us"),
            slugify(f.get("county") or f.get("city") or ""),
            slugify((f.get("operator") or f["name"]).split("/")[0])[:30],
        ) if x)
        n = counters.get(base, 0) + 1
        while f"{base}-{n}" in seen_ids:
            n += 1
        counters[base] = n
        f["id"] = f"{base}-{n}"
        seen_ids.add(f["id"])
    return facilities


def validate(facilities):
    errors = []
    ids = set()
    for f in facilities:
        if f["id"] in ids:
            errors.append(f"duplicate id {f['id']}")
        ids.add(f["id"])
        if not (17.0 <= f["lat"] <= 72.0 and -180.0 <= f["lng"] <= -65.0):
            errors.append(f"{f['id']}: coords outside US bounds ({f['lat']}, {f['lng']})")
        if f["status"] not in {"operating", "expanding", "construction", "proposed", "suspended", "cancelled"}:
            errors.append(f"{f['id']}: bad status {f['status']!r}")
    if errors:
        sys.exit("FATAL validation errors:\n  " + "\n  ".join(errors[:20]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epoch-csv")
    ap.add_argument("--epoch-timelines")
    ap.add_argument("--fractracker-csv")
    args = ap.parse_args()

    log("Fetching FracTracker sheet…")
    ft_rows = read_csv(args.fractracker_csv or FRACTRACKER_CSV_URL, not args.fractracker_csv)
    check_columns(ft_rows, FT_REQUIRED_COLS, "FracTracker sheet")
    log("Fetching Epoch CSVs…")
    ep_rows = read_csv(args.epoch_csv or EPOCH_CSV_URL, not args.epoch_csv)
    check_columns(ep_rows, EPOCH_REQUIRED_COLS, "Epoch data_centers.csv")
    ep_tl_rows = read_csv(args.epoch_timelines or EPOCH_TIMELINES_URL, not args.epoch_timelines)

    cache = load_json(CACHE_PATH, {})
    overrides = load_json(OVERRIDES_PATH, {})

    ft_fs = parse_fractracker(ft_rows)
    ep_fs = parse_epoch(ep_rows, ep_tl_rows, cache)
    CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True))
    log(f"Parsed: {len(ep_fs)} Epoch (US), {len(ft_fs)} FracTracker")

    for name, coords in overrides.get("manual_coords", {}).items():
        for f in ep_fs + ft_fs:
            if f["name"] == name:
                f["lat"], f["lng"] = coords["lat"], coords["lng"]

    facilities = dedupe(ep_fs, ft_fs, overrides)
    old = load_json(OUT_PATH, {}).get("facilities", [])
    facilities = assign_ids(facilities, old)
    facilities.sort(key=lambda f: (f.get("state") or "ZZ", f["name"]))
    validate(facilities)

    counts = {}
    for f in facilities:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    out = {
        "meta": {
            "generated": date.today().isoformat(),
            "sources": [
                {"id": "epoch", "name": "Epoch AI — AI Data Centers",
                 "url": "https://epoch.ai/data/ai-data-centers", "license": "CC-BY 4.0",
                 "retrieved": date.today().isoformat()},
                {"id": "fractracker", "name": "FracTracker Alliance — U.S. Data Centers Tracker",
                 "url": "https://www.fractracker.org/2025/07/national-data-centers-tracker/",
                 "license": "used with attribution; permission requested",
                 "retrieved": date.today().isoformat()},
            ],
            "counts": {"total": len(facilities), **counts},
        },
        "facilities": facilities,
    }
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    size_kb = OUT_PATH.stat().st_size // 1024
    log(f"Wrote {OUT_PATH} — {len(facilities)} facilities, {size_kb} KB")
    log(f"Counts: {counts}")


if __name__ == "__main__":
    main()
