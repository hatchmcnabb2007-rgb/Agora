#!/usr/bin/env bash
# Build districts/cd120.json — the 120th-Congress (2026 election) district map.
#
# Base: Census GENZ cd119 (500k) with seven mid-decade-redraw states replaced by
# their enacted 2026 plans (verified sources, July 2026):
#   TX  PLANC2333 (HB 4, Aug 2025)         — Texas Legislative Council
#   CA  AB 604 / Prop 50 (Nov 2025)        — UC Berkeley Statewide Database
#   MO  HB 1 (Sept 2025)                   — MSDIS state GIS clearinghouse
#   NC  SL 2025-95 / C2025E (Oct 2025)     — ncleg.gov
#   OH  Commission map (Oct 31, 2025)      — PlanScore mirror (state posts no GIS file;
#                                            GeoJSON features are ordered districts 1-15)
#   UT  Court-adopted Map 1 (Nov 10, 2025) — Utah UGRC SGID ArcGIS service
#   TN  HB 7003 (May 7, 2026)              — TN Comptroller (NewCongressional26.zip)
#
# IMPORTANT: no -proj albersusa — keep lon/lat; the page's d3.geoAlbersUsa projects.

set -euo pipefail
cd "$(dirname "$0")/.."

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
UA="agora-redistricting/1.0"

fetch() { curl -sfL --compressed -A "$UA" "$1" -o "$2"; }

echo "── base: cd119 minus redrawn states"
fetch "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip" "$WORK/cd119.zip"
unzip -oq "$WORK/cd119.zip" -d "$WORK/cd119"
npx -y mapshaper "$WORK/cd119/cb_2024_us_cd119_500k.shp" \
  -filter '"48,06,29,37,39,49,47".indexOf(STATEFP) === -1' \
  -filter-fields STATEFP,GEOID \
  -simplify visvalingam 12% keep-shapes \
  -o "$WORK/base.json" format=geojson

shape_state() {  # url zip_name fips id_field
  local url=$1 name=$2 fips=$3 field=$4
  echo "── $name (FIPS $fips)"
  fetch "$url" "$WORK/$name.zip"
  unzip -oq "$WORK/$name.zip" -d "$WORK/$name"
  local shp
  shp=$(find "$WORK/$name" -name "*.shp" | head -1)
  # -proj wgs84 is REQUIRED here: TX/MO/NC publish in projected CRS (state
  # plane / Albers) and d3.geoAlbersUsa needs lon/lat. (This converts the
  # coordinate system; it is not the forbidden albersusa pre-projection.)
  npx -y mapshaper "$shp" \
    -proj wgs84 \
    -each "GEOID = \"$fips\" + String(+${field}).padStart(2, \"0\"), STATEFP = \"$fips\"" \
    -filter-fields STATEFP,GEOID \
    -simplify visvalingam 2% keep-shapes \
    -o "$WORK/st_$fips.json" format=geojson
}

shape_state "https://data.capitol.texas.gov/dataset/748c952b-e926-4f44-8d01-a738884b3ec8/resource/5712ebe1-d777-4d4a-b836-0534e17bca01/download/planc2333.zip" tx 48 District
shape_state "https://statewidedatabase.org/pub/data/d25/AB604%202025-08-16.zip" ca 06 DISTRICT
shape_state "https://www.arcgis.com/sharing/rest/content/items/ee1971b86cce43d4b92b5ce614866a18/data" mo 29 District
shape_state "https://webservices.ncleg.gov/ViewBillDocument/2025/7667/0/SL%202025-95%20-%20Shapefile" nc 37 DISTRICT
shape_state "https://comptroller.tn.gov/content/dam/cot/pa/documents/district-maps/congress-districts/NewCongressional26.zip" tn 47 DISTRICT

echo "── Utah (UGRC ArcGIS service)"
fetch "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/political_us_congress_districts_2026_to_2032/FeatureServer/0/query?where=1%3D1&outFields=DISTRICT&f=geojson" "$WORK/ut_raw.json"
npx -y mapshaper "$WORK/ut_raw.json" \
  -each 'GEOID = "49" + String(+DISTRICT).padStart(2, "0"), STATEFP = "49"' \
  -filter-fields STATEFP,GEOID \
  -simplify visvalingam 2% keep-shapes \
  -o "$WORK/st_49.json" format=geojson

echo "── Ohio (PlanScore mirror; features ordered districts 1-15)"
fetch "https://planscore.s3.amazonaws.com/uploads/20260311T232128.698847864Z/geometry.json" "$WORK/oh_raw.json"
python3 - "$WORK/oh_raw.json" "$WORK/oh_tagged.json" <<'PY'
import json, sys
gj = json.load(open(sys.argv[1]))
for i, f in enumerate(gj["features"]):
    f["properties"] = {"STATEFP": "39", "GEOID": "39" + str(i + 1).zfill(2)}
json.dump(gj, open(sys.argv[2], "w"))
print(f"  tagged {len(gj['features'])} Ohio districts")
PY
npx -y mapshaper "$WORK/oh_tagged.json" \
  -simplify visvalingam 2% keep-shapes \
  -o "$WORK/st_39.json" format=geojson

echo "── combine"
npx -y mapshaper -i "$WORK/base.json" "$WORK"/st_*.json combine-files \
  -merge-layers force \
  -o districts/cd120.json format=topojson quantization=1e5
ls -l districts/cd120.json | awk '{printf "cd120.json: %s KB\n", int($5/1024)}'
python3 - <<'PY'
import json
t = json.load(open("districts/cd120.json"))
obj = next(iter(t["objects"].values()))
geoids = [g["properties"]["GEOID"] for g in obj["geometries"]]
by_state = {}
for g in geoids: by_state[g[:2]] = by_state.get(g[:2], 0) + 1
assert len(geoids) == len(set(geoids)), "duplicate GEOIDs"
for f, n in (("48", 38), ("06", 52), ("29", 8), ("37", 14), ("39", 15), ("49", 4), ("47", 9)):
    assert by_state.get(f) == n, f"state {f}: {by_state.get(f)} districts (expected {n})"
print(f"OK — {len(geoids)} districts, redrawn-state counts verified")
PY
