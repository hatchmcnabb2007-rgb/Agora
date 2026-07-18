#!/usr/bin/env python3
"""Assemble batch profiles into index.html with pre-checks.

For each profile-*.js in the batch dir:
  1. Node-parse it (eval as object) and dump JSON for field checks
  2. Check required fields, 11 topics in exact order, stance values,
     finance percentages sum, keyVotes >= 3, controversies sourced
  3. Check id/name not already in index.html
Then insert all valid blocks before the closing ]; of the candidates array.
Run from the Agora repo root. Exits 1 if any profile fails (inserts nothing).
"""
import json, re, subprocess, sys
from pathlib import Path

import sys
BATCH = Path(sys.argv[sys.argv.index("--batch-dir") + 1]) if "--batch-dir" in sys.argv else Path("batch-profiles")
INDEX = Path("index.html")
TOPICS = ["Healthcare","Climate & Energy","Immigration & Border","Abortion & Reproductive Rights",
          "Gun Control","Tax Policy & Economy","Foreign Policy & Military","Education",
          "Criminal Justice & Policing","Israel & Gaza","Trade & China"]
STANCES = {"strongly-supports","supports","mixed","opposes","strongly-opposes","silent","neutral"}

html = INDEX.read_text()
existing_ids = set(re.findall(r'\bid\s*:\s*["\']([^"\']+)["\']', html))
existing_names = {n.lower() for n in re.findall(r'\bname\s*:\s*["\']([^"\']+)["\']', html)}

files = sorted(BATCH.glob("profile-*.js"))
print(f"{len(files)} profile files found")
errors, blocks, summary = [], [], []

for f in files:
    raw = f.read_text().strip()
    if raw.startswith(","):
        raw = raw.lstrip(",").strip()
    r = subprocess.run(["node", "-e",
        f"const o = eval('(' + require('fs').readFileSync({json.dumps(str(f))},'utf8') + ')'); console.log(JSON.stringify(o))"],
        capture_output=True, text=True)
    if r.returncode != 0:
        errors.append(f"{f.name}: NODE PARSE FAIL: {r.stderr.strip()[:200]}")
        continue
    o = json.loads(r.stdout)
    e = []
    for field in ["id","name","party","title","state","currentRole","tags","highlights",
                  "keyVotes","finance","issues","controversies","dateAdded","trump"]:
        if field not in o or o[field] in (None, "", []) and field != "trump":
            e.append(f"missing/empty {field}")
    topics = [i.get("topic") for i in o.get("issues", [])]
    if topics != TOPICS:
        e.append(f"topics wrong: {topics[:3]}... (len {len(topics)})")
    for i in o.get("issues", []):
        if i.get("stance") not in STANCES: e.append(f"bad stance {i.get('stance')!r}")
        if not i.get("source"): e.append(f"issue {i.get('topic')} missing source")
    if len(o.get("keyVotes", [])) < 3: e.append(f"only {len(o.get('keyVotes',[]))} keyVotes")
    for kv in o.get("keyVotes", []):
        if not kv.get("source"): e.append(f"keyVote {kv.get('bill','?')[:30]} missing source")
    for c in o.get("controversies", []):
        if not c.get("source"): e.append(f"controversy {c.get('title','?')[:30]} missing source")
    for fin in o.get("finance", []):
        pct = sum(fin.get(k, 0) for k in ["small_individual","large_individual","super_pac",
                                          "labor_pac","corporate_pac","party","other"])
        if not 97 <= pct <= 103: e.append(f"finance {fin.get('year')} pcts sum {pct}")
    if o.get("id") in existing_ids: e.append(f"duplicate id {o['id']}")
    if o.get("name","").lower() in existing_names: e.append(f"duplicate name {o['name']}")
    if not re.match(r"20\d\d-\d\d-\d\d", o.get("dateAdded","")): e.append(f"dateAdded {o.get('dateAdded')}")
    if e:
        errors.append(f"{f.name}: " + "; ".join(e))
    else:
        blocks.append(raw)
        summary.append(f"  ok {o['name']} ({o['id']}) photo={'yes' if o.get('photo') else 'NULL'}")

print("\n".join(summary))
if errors:
    print("\nERRORS:")
    print("\n".join(errors))
    sys.exit(1)

if "--check-only" in sys.argv:
    print(f"\ncheck-only: {len(blocks)} profiles would be inserted")
    sys.exit(0)

start = html.find("const candidates = [")
end = html.find("\n];", start)
insertion = ",\n" + ",\n".join(blocks)
INDEX.write_text(html[:end] + insertion + html[end:])
print(f"\ninserted {len(blocks)} profiles into index.html")
