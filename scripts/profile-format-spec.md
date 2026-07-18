# Agora Profile Format Spec (STRICT — validation will reject deviations)

Write ONE JavaScript object literal (not JSON — unquoted keys, double-quoted strings) per assigned politician to `/private/tmp/claude-503/-Users-taylormcnabb/798f3966-e3ae-4a45-98d1-5f3741740b61/scratchpad/batch/profile-<id>.js`. The file must contain EXACTLY the object, starting with `{` and ending with `}` — no trailing comma, no `const`, no markdown fences. It must parse under Node `eval`.

A complete example profile is at `/private/tmp/claude-503/-Users-taylormcnabb/798f3966-e3ae-4a45-98d1-5f3741740b61/scratchpad/pearson-profile.js` (strip its leading comma; note it is unusually deep — yours should match its rigor for sourcing but `vision: null` is fine unless the figure has a truly distinctive articulated worldview).

## Required fields, in this order
- `id`: kebab-case, e.g. "lois-frankel"
- `name`: display name, e.g. "Lois Frankel"
- `initials`: e.g. "LF"
- `photo`: see photo rules
- `party`: "D" or "R"
- `title`: "Representative" | "Former Representative" | "Former Senator" | "EPA Administrator" etc.
- `state`: full state name, e.g. "Florida"
- `office`: e.g. "U.S. House, FL-22" for sitting members (the "XX-NN" pattern makes the "Represents You" feature work — USE it for sitting members, AVOID it for formers: write "U.S. House, Florida 20th (2022–2026)" style instead)
- `currentRole`: "representative" (sitting House), "senator", "cabinet", "former"
- `bioguideId`: for current/recent members of Congress (given in your assignment). Omit for non-members.
- `dateAdded`: "2026-07-17"
- `tags`: 3-4 short strings
- `highlights`: 5 sentences, each a substantive fact (career arc, signature work, current status incl. any 2025-26 developments)
- `keyVotes`: >= 4 entries `{ bill, vote, date, significance, source }`. For members of Congress use REAL floor votes with their actual position ("yes"/"no") — e.g. Laken Riley Act (Jan 2025), One Big Beautiful Bill Act (Jul 2025), TikTok divestiture (2024), Infrastructure Investment and Jobs Act (2021), CHIPS (2022), IRA (2022), Respect for Marriage (2022), impeachments, NDAA amendments, ICE funding fights, government shutdown CRs (2025), plus member-specific sponsored bills. VERIFY each vote position with web search — do not guess. `vote: "sponsor"` or `"action"` allowed for non-floor items.
- `finance`: array of `{ year, office, total, small_individual, large_individual, super_pac, labor_pac, corporate_pac, party, other }` — percentages sum to 100, total in dollars. For members/candidates pull REAL totals from the FEC API: `curl -s "https://api.open.fec.gov/v1/candidates/search/?q=<LASTNAME>&state=<XX>&office=H&api_key=DEMO_KEY"` then `curl -s "https://api.open.fec.gov/v1/candidate/<CAND_ID>/totals/?cycle=2024&api_key=DEMO_KEY"` (use `individual_unitemized_contributions` for small-dollar %, itemized for large, PAC contributions for pac categories). Use cycle 2024 (or 2026 if they're a 2026 candidate with meaningful totals). If the API fails after 2 tries, use OpenSecrets-style estimates from web sources and keep percentages plausible.
- `issues`: EXACTLY these 11 topics, in this exact order, exact spelling:
  1. "Healthcare"  2. "Climate & Energy"  3. "Immigration & Border"  4. "Abortion & Reproductive Rights"  5. "Gun Control"  6. "Tax Policy & Economy"  7. "Foreign Policy & Military"  8. "Education"  9. "Criminal Justice & Policing"  10. "Israel & Gaza"  11. "Trade & China"
  Each: `{ topic, stance, stanceLabel, description, actions, source }` — stance in {"strongly-supports","supports","mixed","opposes","strongly-opposes","silent"}; stanceLabel 2-4 words; description 2-3 sentences specific to THIS person (votes, bills, statements — not generic party boilerplate); actions = 2-4 concrete items; source = real outlets/records. If the person has no public record on a topic, use stance "silent" with an honest description — NEVER invent positions.
- `controversies`: >= 1 entry `{ title, description, source }`, every source non-empty. Neutral voice: state what critics say and what defenders say. Include real 2025-26 events where relevant.
- `trump`: `{ relationship, badge, summary, moments: [{ event, action, source }] }` — badge in {"trump-ally","trump-opponent","trump-mixed","trump-turned-enemy"}; 2+ moments, prefer 2025-26 events.
- `vision`: null (or the full pillar structure if truly warranted)

## Photo rules (MUST verify before writing the file)
- Sitting members of Congress: `https://unitedstates.github.io/images/congress/450x550/<bioguideId>.jpg` — verify with `curl -sI` that it returns 200 and content-type image/*.
- Everyone else (formers, cabinet): the person's Wikipedia lead portrait as a DIRECT `https://upload.wikimedia.org/wikipedia/commons/thumb/...` URL at ~330-500px — find it on their Wikipedia page, verify 200 + image content-type with curl. NEVER use a URL you have not verified.
- If no verifiable photo exists, use `photo: null` (the app falls back to initials).

## Accuracy rules
- Check the current date. Use web search to verify each person's CURRENT status — several people in this batch have had major 2025-26 changes (noted in your assignment). The 119th Congress (2025-26) is Republican-controlled with Trump as president; votes like the "One Big Beautiful Bill" (H.R. 1, signed July 2025) and 2025 rescissions are useful recent markers.
- Every controversy, key vote, and issue stance must be attributable to a real source you found. No invented bills, no invented quotes.
- Write descriptions in neutral, plain language — Agora is nonpartisan.
