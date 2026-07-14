#!/usr/bin/env python3
"""Build districts/districts-data.json for redistricting.html.

Sources (all free, no auth):
  - 2014/2016/2018 House results: MEDSL constituency-returns GitHub mirror
    (CC0; the fuller 1976-2024 Dataverse file is guestbook-gated — pass it
    via --house-csv after a one-time manual download to upgrade all cycles
    to a single canonical source).
  - 2020/2022 House results: FEC official "Federal Elections" xlsx
    publications (parsed with stdlib zipfile+ElementTree).
  - 2024: winner name/party per district from unitedstates/congress-legislators
    (certified outcome; vote margins marked provisional until the MEDSL
    1976-2024 file is supplied via --house-csv).
  - Demographics: Census ACS5 B03002/B03003, vintage-matched to boundaries:
    acs5 2015 -> cd114, 2016 -> cd115, 2019 -> cd116, 2022 -> cd118.
  - Current members: unitedstates/congress-legislators.

Computes per-cycle margins, efficiency gaps (Stephanopoulos-McGhee wasted
votes; uncontested races imputed 75/25 on the state's median contested
two-party total; states with >= 7 seats only), and validates every result
GEOID against the cycle's committed topology file. Aborts loudly on any
inconsistency.

Usage:
  python3 scripts/build-redistricting-data.py                # fetch live
  python3 scripts/build-redistricting-data.py --house-csv 1976-2024-house.csv
  python3 scripts/build-redistricting-data.py --validate-only
"""

import argparse
import csv
import io
import json
import re
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_PATH = REPO / "districts" / "districts-data.json"

MEDSL_GITHUB_CSV = "https://raw.githubusercontent.com/MEDSL/constituency-returns/master/1976-2018-house.csv"
FEC_XLSX = {
    2020: "https://www.fec.gov/resources/cms-content/documents/federalelections2020.xlsx",
    2022: "https://www.fec.gov/resources/cms-content/documents/federalelections2022.xlsx",
}
LEGISLATORS_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"

# cycle year -> (boundary file, congress, ACS demographics vintage key)
CYCLES = {
    2014: ("districts/2010.json", 114, "cd114"),
    2016: ("districts/cd115.json", 115, "cd115"),
    2018: ("districts/cd116.json", 116, "cd116"),
    2020: ("districts/cd116.json", 117, "cd116"),
    2022: ("districts/2020.json", 118, "cd118"),
    2024: ("districts/cd119.json", 119, "cd119"),
}
# ACS5 vintage year -> demographics key (survey geography matches that congress)
ACS_VINTAGES = {2015: "cd114", 2016: "cd115", 2019: "cd116", 2022: "cd118"}
# cd119 currently reuses cd118 ACS values (no cd119 ACS geography yet);
# flagged in meta so the page can cite the vintage honestly.

GAP_MIN_SEATS = 7
IMPUTE_SHARE = 0.75

STATE_PO_TO_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "FL": "12", "GA": "13", "HI": "15", "ID": "16",
    "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22",
    "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28",
    "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34",
    "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40",
    "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47",
    "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56",
}


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    if Path("/etc/ssl/cert.pem").exists():
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ssl.create_default_context()


SSL_CTX = _ssl_context()


def log(msg):
    print(msg, file=sys.stderr)


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "agora-redistricting/1.0"})
    with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as resp:
        data = resp.read()
    return data if binary else data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- MEDSL CSV

def parse_medsl(text, wanted_years):
    """MEDSL house returns CSV -> {year: {geoid: {candidate: [votes, party]}}}.

    Handles both the GitHub 1976-2018 file and the Dataverse 1976-2024 file
    (column names are compatible). Fusion tickets (NY): candidate rows are
    summed across party lines; candidate's major party wins the label.
    """
    out = {y: {} for y in wanted_years}
    rdr = csv.DictReader(io.StringIO(text))
    for r in rdr:
        try:
            year = int(r["year"])
        except (KeyError, ValueError):
            continue
        if year not in wanted_years:
            continue
        if (r.get("stage") or "").lower() not in ("gen", "general"):
            continue
        if (r.get("special") or "").upper() in ("TRUE", "T", "1"):
            continue
        po = (r.get("state_po") or "").upper()
        fips = STATE_PO_TO_FIPS.get(po)
        if not fips:
            continue
        dist = re.sub(r"\D", "", r.get("district") or "")
        geoid = fips + (dist.zfill(2) if dist else "00")
        if geoid.endswith("00") and dist not in ("", "0", "00"):
            pass
        cand = (r.get("candidate") or "").strip() or "(unnamed)"
        try:
            votes = int(float(r.get("candidatevotes") or 0))
        except ValueError:
            votes = 0
        party = (r.get("party") or "").strip().lower()
        d = out[year].setdefault(geoid, {})
        entry = d.setdefault(cand, [0, ""])
        entry[0] += votes
        if party in ("democrat", "democratic-farmer-labor", "democratic"):
            entry[1] = "D"
        elif party == "republican":
            entry[1] = "R"
        elif not entry[1]:
            entry[1] = "O"
    return out


# ---------------------------------------------------------------- FEC xlsx

XLSX_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_xlsx_sheet(blob, name_suffix):
    z = zipfile.ZipFile(io.BytesIO(blob))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target")
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    target = None
    for s in wb.iter(XLSX_M + "sheet"):
        if s.get("name", "").endswith(name_suffix):
            target = "xl/" + rels[s.get(XLSX_R + "id")].lstrip("/")
            break
    if not target:
        sys.exit(f"FATAL: xlsx sheet ending {name_suffix!r} not found — FEC format changed")
    sst = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(XLSX_M + "si"):
            sst.append("".join(t.text or "" for t in si.iter(XLSX_M + "t")))
    rows = []
    for row in ET.fromstring(z.read(target)).iter(XLSX_M + "row"):
        cells = {}
        for c in row:
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            v = c.find(XLSX_M + "v")
            if v is None:
                continue
            cells[col] = sst[int(v.text)] if c.get("t") == "s" else v.text
        rows.append(cells)
    return rows


def parse_fec(blob, year):
    """FEC 'US House Results by State' sheet -> {geoid: {candidate: [votes, party]}}.

    Column layout (2020/2022 publications): B=state abbr, D=district,
    H=surname, I=full name, K=party, P=general votes, T=combined GE party
    totals (fusion, NY), R=GE runoff votes (GA/LA), W=GE winner indicator.
    """
    header_seen = False
    out = {}
    for r in read_xlsx_sheet(blob, "US House Results by State"):
        if not header_seen:
            header_seen = True  # first row is the header
            continue
        po = (r.get("B") or "").strip().upper()
        fips = STATE_PO_TO_FIPS.get(po)
        dist_raw = (r.get("D") or "").strip()
        if not fips or not dist_raw:
            continue
        if (r.get("J") or "").startswith("District Votes"):
            continue
        # skip special-election / unexpired-term subtables
        if "UNEXPIRED" in (r.get("I") or "").upper():
            continue
        dist = re.sub(r"\D", "", dist_raw)
        geoid = fips + (dist.zfill(2) if dist else "00")
        cand = (r.get("I") or r.get("H") or "").strip()
        if not cand or cand.upper().startswith("DISTRICT"):
            continue
        party = (r.get("K") or "").strip().upper()
        # combined fusion total supersedes the per-line total; runoff total
        # (GA/LA) supersedes the general when present
        votes_s = r.get("R") or r.get("T") or r.get("P") or "0"
        try:
            votes = int(float(str(votes_s).replace(",", "")))
        except ValueError:
            continue
        won = (r.get("W") or "").strip().upper().startswith("W")
        has_runoff = bool((r.get("R") or "").strip())
        # Ranked-choice states (AK, ME): the last tabulated round is decisive.
        # Columns: Y=1st round votes, AA=2nd round, AC=3rd round.
        for col in ("AC", "AA"):
            rcv = (r.get(col) or "").strip()
            if rcv:
                try:
                    votes = int(float(rcv.replace(",", "")))
                    has_runoff = True
                except ValueError:
                    pass
                break
        d = out.setdefault(geoid, {})
        entry = d.setdefault(cand, [0, "", False, False])
        entry[0] = max(entry[0], votes)  # fusion lines repeat the combined total
        if party in ("D", "DFL", "D/R", "DEM"):
            entry[1] = "D"
        elif party in ("R", "REP"):
            entry[1] = "R"
        elif not entry[1]:
            entry[1] = "O"
        entry[2] = entry[2] or won
        entry[3] = entry[3] or has_runoff

    # Jungle-primary states (LA; GA runoffs): when a district went to a GE
    # runoff, the runoff is the decisive election — drop general-only
    # candidates so vote totals aren't mixed across two different elections.
    for geoid, cands in out.items():
        if any(e[3] for e in cands.values()):
            runoff_only = {n: e for n, e in cands.items() if e[3]}
            if runoff_only:
                out[geoid] = runoff_only
    return out


# ------------------------------------------------------------ aggregation

def to_result(cands):
    """{candidate: [votes, party(, won)]} -> [demV, repV, totalV, winner, party]."""
    dem = rep = total = 0
    winner_name, winner_party, best = "", "", -1
    flagged_winner = None
    for name, entry in cands.items():
        votes, party = entry[0], entry[1]
        won = entry[2] if len(entry) > 2 else False
        total += votes
        if party == "D":
            dem += votes
        elif party == "R":
            rep += votes
        if won:
            flagged_winner = (name, party)
        if votes > best:
            best, winner_name, winner_party = votes, name, party
    if flagged_winner:
        winner_name, winner_party = flagged_winner
    surname = winner_name.split(",")[0].strip() if "," in winner_name else winner_name.split()[-1] if winner_name else ""
    return [dem, rep, total, surname, winner_party]


def margin(res):
    two = res[0] + res[1]
    return None if two == 0 else round((res[0] - res[1]) / two * 100, 1)


# ------------------------------------------------------- efficiency gap

def efficiency_gaps(results_by_geoid):
    """Per-state Stephanopoulos-McGhee gap from two-party votes.

    Uncontested districts get an imputed 75/25 two-party split on the
    state's median contested two-party total. States with < GAP_MIN_SEATS
    districts are skipped. Returns {fips: [seatsD, seatsR, voteShareD, gap]}.
    """
    by_state = {}
    for geoid, res in results_by_geoid.items():
        by_state.setdefault(geoid[:2], []).append(res)
    gaps = {}
    for fips, districts in by_state.items():
        if len(districts) < GAP_MIN_SEATS:
            continue
        contested = [r[0] + r[1] for r in districts if r[0] > 0 and r[1] > 0]
        if not contested:
            continue
        med = sorted(contested)[len(contested) // 2]
        wasted_d = wasted_r = total_two = 0
        seats_d = seats_r = vote_d = vote_all = 0
        for r in districts:
            d, rp = r[0], r[1]
            if d == 0 or rp == 0:
                winner_is_d = r[4] == "D" if (d == 0 and rp == 0) else d > rp
                d = int(med * (IMPUTE_SHARE if winner_is_d else 1 - IMPUTE_SHARE))
                rp = med - d
            two = d + rp
            total_two += two
            vote_d += d
            vote_all += two
            need = two // 2 + 1
            if d > rp:
                seats_d += 1
                wasted_d += d - need
                wasted_r += rp
            else:
                seats_r += 1
                wasted_r += rp - need
                wasted_d += d
        gaps[fips] = [seats_d, seats_r, round(vote_d / vote_all, 3),
                      round((wasted_d - wasted_r) / total_two, 3)]
    return gaps


# ------------------------------------------------------------ demographics
#
# The Census API now requires a key (X-DataWebAPI-KeyError). The ACS
# "table-based" 5-year Summary File on www2.census.gov is keyless and carries
# the same B03002 table for congressional districts (GEO_ID summary level
# 500 + congress number), but only exists for vintage 2021+. So:
#   cd118  <- acs5 2022 summary file (5001800US rows)
#   cd116  <- acs5 2021 summary file (5001600US rows; 2017-21 survey window,
#             tabulated on the pre-2022 lines used 2018-2020)
#   cd119  <- cd118 copy (no cd119 ACS geography published yet)
#   cd114/cd115 <- cd116 carryover (flagged in meta) unless --census-key is
#             given, in which case the exact 2015/2016 API vintages are used.

ACS_SUMMARY = {
    "cd118": ("2022", "5001800US"),
    "cd116": ("2021", "5001600US"),
}
ACS_VARS = "B03002_001E,B03002_003E,B03002_004E,B03002_006E,B03002_012E"


def _demo_row(t, w, b, a, h):
    if t == 0:
        return None
    w, b, a, h = (round(x / t * 100) for x in (w, b, a, h))
    other = max(0, 100 - w - b - a - h)
    return [w, b, h, a, other, 100 - w]


def fetch_acs_summary(vintage_year, geo_prefix, local_file=None):
    """Parse B03002 rows for congressional districts from the table-based
    ACS summary file. Fields: GEO_ID|E001|M001|E002|M002|... => E00N at 2N-1.
    """
    if local_file:
        text = Path(local_file).read_text()
    else:
        url = (f"https://www2.census.gov/programs-surveys/acs/summary_file/"
               f"{vintage_year}/table-based-SF/data/5YRData/acsdt5y{vintage_year}-b03002.dat")
        log(f"  downloading {url} (~80 MB)…")
        text = fetch(url)
    out = {}
    for line in text.splitlines():
        if not line.startswith(geo_prefix):
            continue
        f = line.split("|")
        geoid = f[0][-4:]
        cd = geoid[2:]
        if cd in ("98", "ZZ"):
            cd = "00"
        try:
            row = _demo_row(int(f[1]), int(f[5]), int(f[7]), int(f[11]), int(f[23]))
        except (ValueError, IndexError):
            continue
        if row:
            out[geoid[:2] + cd] = row
    return out


def fetch_acs_api(vintage_year, key):
    """Exact-vintage ACS via the (now key-required) Census API."""
    path = f"{vintage_year}/acs/acs5" if vintage_year >= 2016 else f"{vintage_year}/acs5"
    url = (f"https://api.census.gov/data/{path}"
           f"?get={ACS_VARS}&for=congressional%20district:*&in=state:*&key={key}")
    data = json.loads(fetch(url))
    out = {}
    for row in data[1:]:
        total, white, black, asian, hisp, state, cd = row
        try:
            r = _demo_row(int(total), int(white), int(black), int(asian), int(hisp))
        except (ValueError, TypeError):
            continue
        cd = "00" if cd in ("98", "ZZ") else cd.zfill(2)
        if r:
            out[state + cd] = r
    return out


# --------------------------------------------------------------- members

def fetch_members(src=None):
    """Returns (current members by geoid, term index by (start_year, geoid)).

    The term index lets us backfill districts whose general election the FEC
    publication omits (states that cancel/omit unopposed races, e.g. FL, LA).
    """
    data = json.loads(Path(src).read_text() if src else fetch(LEGISLATORS_URL))
    members, term_index = {}, {}
    for leg in data:
        name = (leg["name"].get("official_full")
                or f'{leg["name"]["first"]} {leg["name"]["last"]}')
        for term in leg["terms"]:
            if term["type"] != "rep":
                continue
            fips = STATE_PO_TO_FIPS.get(term["state"])
            if not fips:
                continue
            dist = term.get("district", 0)
            geoid = fips + (str(dist).zfill(2) if dist and dist > 0 else "00")
            party = {"Democrat": "D", "Republican": "R"}.get(term.get("party"), "I")
            term_index[(int(term["start"][:4]), geoid)] = (name, party)
            if term is leg["terms"][-1]:
                members[geoid] = {"n": name, "p": party}
    return members, term_index


# -------------------------------------------------------------- validation

def topology_geoids(boundary_file):
    t = json.loads((REPO / boundary_file).read_text())
    obj = next(iter(t["objects"].values()))
    return {g["properties"]["GEOID"] for g in obj["geometries"]}


def validate(cycles_out, demographics):
    errors = []
    for year, cyc in cycles_out.items():
        topo = topology_geoids(cyc["boundary"])
        results = cyc["results"]
        voting = [g for g in results if g[:2] in STATE_PO_TO_FIPS.values()]
        if cyc.get("provisional"):
            # current-membership snapshot: real vacancies are expected
            if not 428 <= len(voting) <= 435:
                errors.append(f"{year}: {len(voting)} voting seats (provisional; expected 428-435)")
            elif len(voting) < 435:
                log(f"  note: {year} provisional cycle has {435 - len(voting)} vacant seats")
        elif len(voting) != 435:
            errors.append(f"{year}: {len(voting)} voting seats (expected 435)")
        missing = [g for g in results if g not in topo]
        if missing:
            errors.append(f"{year}: {len(missing)} result GEOIDs missing from {cyc['boundary']}: {missing[:6]}")
        for g, res in results.items():
            if isinstance(res, list) and res[2]:
                m = margin(res)
                if m is not None and res[4] in ("D", "R"):
                    if (m > 0) != (res[4] == "D") and abs(m) > 1:
                        errors.append(f"{year} {g}: winner {res[4]} inconsistent with margin {m}")
    for vintage, demo in demographics.items():
        file = {"cd114": "districts/2010.json", "cd115": "districts/cd115.json",
                "cd116": "districts/cd116.json", "cd118": "districts/2020.json",
                "cd119": "districts/cd119.json"}[vintage]
        topo = topology_geoids(file)
        missing = [g for g in topo if g not in demo and not g.endswith(("98", "99")) and g[:2] in STATE_PO_TO_FIPS.values()]
        if len(missing) > 2:
            errors.append(f"demographics {vintage}: {len(missing)} topology GEOIDs missing (e.g. {missing[:5]})")
    if errors:
        sys.exit("FATAL validation errors:\n  " + "\n  ".join(errors[:25]))


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--house-csv", help="MEDSL house returns CSV (1976-2018 or 1976-2024)")
    ap.add_argument("--fec-2020", help="local federalelections2020.xlsx")
    ap.add_argument("--fec-2022", help="local federalelections2022.xlsx")
    ap.add_argument("--legislators", help="local legislators-current.json")
    ap.add_argument("--acs-2022", dest="acs_2022", help="local acsdt5y2022-b03002.dat (or CD-filtered subset)")
    ap.add_argument("--acs-2021", dest="acs_2021", help="local acsdt5y2021-b03002.dat (or CD-filtered subset)")
    ap.add_argument("--census-key", help="Census API key — enables exact cd114/cd115 ACS vintages")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()

    if args.validate_only and OUT_PATH.exists():
        data = json.loads(OUT_PATH.read_text())
        validate(data["cycles"], data["demographics"])
        for y, c in sorted(data["cycles"].items()):
            log(f"{y}: {len(c['results'])} districts, provisional={c.get('provisional', False)}")
        log("Validation OK")
        return

    log("Fetching MEDSL house returns…")
    medsl_text = Path(args.house_csv).read_text() if args.house_csv else fetch(MEDSL_GITHUB_CSV)
    medsl_years = set()
    medsl = parse_medsl(medsl_text, {2014, 2016, 2018, 2020, 2022, 2024})
    medsl = {y: v for y, v in medsl.items() if v}
    medsl_years = set(medsl)
    log(f"  MEDSL cycles present: {sorted(medsl_years)}")

    results = {}
    for year in (2014, 2016, 2018, 2020, 2022, 2024):
        if year in medsl_years:
            results[year] = {g: to_result(c) for g, c in medsl[year].items()}

    for year in (2020, 2022):
        if year in results:
            continue
        log(f"Fetching FEC {year} results…")
        local = getattr(args, f"fec_{year}")
        blob = Path(local).read_bytes() if local else fetch(FEC_XLSX[year], binary=True)
        results[year] = {g: to_result(c) for g, c in parse_fec(blob, year).items()}

    log("Fetching current members…")
    members, term_index = fetch_members(args.legislators)

    # Backfill races the FEC publication omits (cancelled/unopposed generals):
    # the seat's holder in the following congress is the certified winner.
    for year in (2014, 2016, 2018, 2020, 2022):
        if year not in results:
            continue
        topo = topology_geoids(CYCLES[year][0])
        voting = {g for g in topo if g[:2] in STATE_PO_TO_FIPS.values()}
        for geoid in sorted(voting - set(results[year])):
            hit = term_index.get((year + 1, geoid))
            if hit:
                log(f"  {year} {geoid}: no FEC general (unopposed/cancelled) — winner {hit[0]} ({hit[1]}) from term records")
                results[year][geoid] = [0, 0, 0, hit[0].split()[-1], hit[1]]

    provisional_2024 = 2024 not in results
    if provisional_2024:
        log("2024: no vote data source — using legislators file for winner/party (provisional)")
        results[2024] = {g: [0, 0, 0, m["n"].split()[-1], m["p"]] for g, m in members.items()}

    log("Fetching ACS demographics (table-based summary files)…")
    demographics = {}
    for vintage, (year, prefix) in ACS_SUMMARY.items():
        local = getattr(args, f"acs_{year}", None)
        log(f"  {vintage} ← acs5 {year}" + (" (local file)" if local else ""))
        demographics[vintage] = fetch_acs_summary(year, prefix, local)
    demographics["cd119"] = dict(demographics["cd118"])  # no cd119 ACS geography yet
    demo_carryover = False
    if args.census_key:
        log("  cd114 ← acs5 2015 (API), cd115 ← acs5 2016 (API)")
        demographics["cd114"] = fetch_acs_api(2015, args.census_key)
        demographics["cd115"] = fetch_acs_api(2016, args.census_key)
    else:
        log("  cd114/cd115: no --census-key — carrying cd116 values (flagged in meta)")
        demographics["cd114"] = dict(demographics["cd116"])
        demographics["cd115"] = dict(demographics["cd116"])
        demo_carryover = True

    cycles_out = {}
    for year, (boundary, congress, vintage) in CYCLES.items():
        cyc = {"boundary": boundary, "congress": congress, "vintage": vintage,
               "results": results[year]}
        if year == 2024 and provisional_2024:
            cyc["provisional"] = True
        cycles_out[str(year)] = cyc

    gaps = {str(y): efficiency_gaps(results[y]) for y in (2014, 2016, 2018, 2020, 2022)
            if y in results}
    if not provisional_2024:
        gaps["2024"] = efficiency_gaps(results[2024])

    validate(cycles_out, demographics)

    out = {
        "meta": {
            "generated": date.today().isoformat(),
            "sources": {
                "elections": ("MEDSL U.S. House returns (CC0) 2014-2018"
                              + ("" if provisional_2024 else "/2024")
                              + "; FEC Federal Elections publications 2020/2022"),
                "demographics": "U.S. Census Bureau ACS 5-year, B03002/B03003, vintage-matched to district boundaries",
                "members": "unitedstates/congress-legislators",
            },
            "provisional2024": provisional_2024,
            "demographicsCarryover": demo_carryover,
            "cd119DemographicsNote": "cd119 uses 2022 ACS (118th-district geography); no cd119 ACS release yet",
            "cd114cd115DemographicsNote": ("cd114/cd115 use 2021 ACS values tabulated on cd116 lines (same district numbering; shapes differ in NC/PA/VA/FL court-remap areas)" if demo_carryover else "cd114/cd115 from exact 2015/2016 ACS vintages"),
            "uncontestedImputation": f"{int(IMPUTE_SHARE*100)}/{int((1-IMPUTE_SHARE)*100)} two-party split of the state's median contested-district two-party total",
            "gapMinSeats": GAP_MIN_SEATS,
        },
        "cycles": cycles_out,
        "demographics": demographics,
        "members": members,
        "efficiencyGap": gaps,
    }
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    log(f"Wrote {OUT_PATH} — {OUT_PATH.stat().st_size // 1024} KB")
    for y in sorted(cycles_out):
        log(f"  {y}: {len(cycles_out[y]['results'])} districts"
            + (" (provisional winners only)" if cycles_out[y].get("provisional") else ""))


if __name__ == "__main__":
    main()
