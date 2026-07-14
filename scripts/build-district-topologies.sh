#!/usr/bin/env bash
# Build per-congress district boundary topologies for redistricting.html.
#
# Downloads Census GENZ 500k cartographic boundary shapefiles and converts
# them to TopoJSON with mapshaper (npx). Output matches the format of the
# existing districts/2010.json (cd114) and districts/2020.json (cd118):
# geographic lon/lat coordinates, GEOID + STATEFP properties.
#
# IMPORTANT: do NOT pre-project with `-proj albersusa`. The page's
# d3.geoAlbersUsa() does the projection at render time; pre-projected
# coordinates render as garbage (documented prior bug, 2026-06).
#
# cd120 (2026 mid-decade maps) is built separately — see the cd120 section
# at the bottom; it needs the six state shapefiles fetched first.

set -euo pipefail
cd "$(dirname "$0")/.."

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# vintage year | congress | output file
SPECS=(
  "2016 cd115 districts/cd115.json"
  "2018 cd116 districts/cd116.json"
  "2024 cd119 districts/cd119.json"
)

for spec in "${SPECS[@]}"; do
  read -r year cd out <<<"$spec"
  name="cb_${year}_us_${cd}_500k"
  echo "── $name → $out"
  curl -sfL "https://www2.census.gov/geo/tiger/GENZ${year}/shp/${name}.zip" -o "$WORK/$name.zip"
  unzip -oq "$WORK/$name.zip" -d "$WORK/$name"
  # keep-shapes prevents small districts from vanishing at high simplification
  npx -y mapshaper "$WORK/$name/$name.shp" \
    -filter-fields STATEFP,GEOID \
    -simplify visvalingam 12% keep-shapes \
    -o "$out" format=topojson quantization=1e5
  ls -l "$out" | awk '{printf "   %s KB\n", int($5/1024)}'
done

echo "Done. Verify each file is ≤ ~550 KB; if larger, lower the simplify %."
