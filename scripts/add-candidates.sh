#!/bin/bash
# Agora daily candidate automation
# Reads next 20 unchecked names from the queue, researches them,
# adds to index.html, validates, then pushes to GitHub.

QUEUE="/Users/taylormcnabb/Documents/HatchOS/01-Projects/Agora/candidate-queue.md"
AGORA="/Users/taylormcnabb/Documents/Agora"
LOG="/Users/taylormcnabb/Documents/HatchOS/01-Projects/Agora/automation.log"
VALIDATE="$AGORA/scripts/validate-candidates.py"
CLAUDE=$(which claude)
TODAY=$(date +%Y-%m-%d)

echo "========================================" >> "$LOG"
echo "Run: $(date)" >> "$LOG"

# Prerequisites
if [ -z "$CLAUDE" ]; then
    echo "ERROR: claude not found in PATH" >> "$LOG"
    exit 1
fi

# Count remaining
REMAINING=$(grep -c '^\- \[ \]' "$QUEUE" 2>/dev/null || echo 0)
echo "Remaining in queue: $REMAINING" >> "$LOG"

if [ "$REMAINING" -eq 0 ]; then
    echo "Queue empty — nothing to add." >> "$LOG"
    exit 0
fi

# Snapshot line count before run (to detect if claude actually added anything)
LINES_BEFORE=$(wc -l < "$AGORA/index.html")

# Extract next 20 unchecked names
NAMES=$(grep '^\- \[ \]' "$QUEUE" | head -20 | sed 's/^- \[ \] //')
COUNT=$(echo "$NAMES" | grep -c '.')
echo "Processing $COUNT candidates:" >> "$LOG"
echo "$NAMES" >> "$LOG"

# Build the prompt
PROMPT="You are adding politician profiles to the Agora civic engagement app at /Users/taylormcnabb/Documents/Agora/index.html.

TODAY'S DATE: $TODAY

TASK: Research and add the following $COUNT politicians to the app, then commit + push to GitHub, then mark them done in the queue file.

POLITICIANS TO ADD:
$NAMES

=== STEP 1: UNDERSTAND THE FORMAT ===

Read /Users/taylormcnabb/Documents/Agora/index.html. Find the 'const candidates = [' array. Study 3-4 recent entries (near the end of the array) carefully to understand the exact format before writing anything.

=== STEP 2: RESEARCH EACH POLITICIAN ===

For each politician, research their actual record using WebSearch or WebFetch. Do NOT invent facts. If you cannot confirm something, write [unverified] in the source field rather than guessing.

Key things to get right:
- Current title and role as of $TODAY (verify if they still hold office)
- Actual votes they cast — use Congress.gov or credible news sources
- Finance figures — use FEC data or OpenSecrets approximations
- Controversies — only include documented incidents with a real source
- Party: always \"D\", \"R\", or \"I\" — never \"Democrat\" or \"Republican\"

=== STEP 3: BUILD EACH PROFILE ===

Each entry MUST follow this exact format:

    {
        id: \"firstname-lastname\",
        name: \"Full Name\",
        initials: \"FL\",
        photo: \"https://bioguide.congress.gov/bioguide/photo/X/X000000.jpg\",
        party: \"D\",
        title: \"Current Title — State\",
        state: \"State Name\",
        office: \"Full office name\",
        currentRole: \"senator\" | \"representative\" | \"governor\" | \"president\" | \"vp\" | \"cabinet\" | \"mayor\" | \"former\" | \"politician\",
        dateAdded: \"$TODAY\",
        tags: [\"3-5 short descriptive tags\"],
        highlights: [
            \"5 specific highlight strings — real facts, dates, significance. No vague platitudes.\"
        ],
        keyVotes: [
            { bill: \"Bill Name\", vote: \"yes\" | \"no\" | \"signed\" | \"vetoed\" | \"action\", date: \"YYYY-MM-DD\", significance: \"One sentence on why this vote matters.\", source: \"Roll call vote number or named source\" }
        ],
        finance: [
            { year: YYYY, office: \"Office, State\", total: 0000000, small_individual: 0, large_individual: 0, super_pac: 0, labor_pac: 0, corporate_pac: 0, party: 0, other: 0 }
        ],
        issues: [
            { topic: \"Healthcare\", stance: \"supports\" | \"opposes\" | \"mixed\" | \"strongly-supports\" | \"strongly-opposes\", stanceLabel: \"3-5 word label\", description: \"2-3 sentence description.\", actions: [\"Specific vote or action.\"], source: \"Source\" },
            { topic: \"Climate & Energy\", ... },
            { topic: \"Immigration & Border\", ... },
            { topic: \"Abortion & Reproductive Rights\", ... },
            { topic: \"Gun Control\", ... },
            { topic: \"Tax Policy & Economy\", ... },
            { topic: \"Foreign Policy & Military\", ... },
            { topic: \"Education\", ... },
            { topic: \"Criminal Justice & Policing\", ... },
            { topic: \"Israel & Gaza\", ... },
            { topic: \"Trade & China\", ... }
        ],
        controversies: [
            { title: \"Title\", description: \"Factual, sourced description.\", date: \"YYYY-MM-DD or YYYY\", source: \"Credible outlet, date\" }
        ],
        trump: \"supports\" | \"opposes\" | \"mixed\" | \"trump-ally\" | \"trump-opponent\" | null,
        vision: null
    }

CRITICAL RULES:
- issues[] MUST have EXACTLY these 11 topics in this exact order: Healthcare, Climate & Energy, Immigration & Border, Abortion & Reproductive Rights, Gun Control, Tax Policy & Economy, Foreign Policy & Military, Education, Criminal Justice & Policing, Israel & Gaza, Trade & China
- Every controversy MUST have a non-empty source field — no exceptions
- keyVotes MUST have at least 3 entries for legislators, at least 2 for others
- party MUST be \"D\", \"R\", or \"I\" — never the full word
- dateAdded MUST be \"$TODAY\"
- If a politician is already in the app (search for their name in index.html), skip them and still mark them done in the queue
- No duplicate IDs — check index.html first

=== STEP 4: INSERT INTO THE FILE ===

Add all new candidate objects BEFORE the closing ]; of the candidates array. Each new object goes after the last existing entry, separated by a comma.

=== STEP 5: VALIDATE ===

After inserting, run: python3 /Users/taylormcnabb/Documents/Agora/scripts/validate-candidates.py

If validation fails, fix the errors before proceeding. Do not push if validation fails.

=== STEP 6: COMMIT AND PUSH ===

Only after validation passes:
cd /Users/taylormcnabb/Documents/Agora && git add index.html && git commit -m 'Add $COUNT candidates via daily queue automation ($TODAY)' && git push origin main && git push origin main:gh-pages --force

=== STEP 7: MARK QUEUE DONE ===

In $QUEUE, change '- [ ]' to '- [x]' for every politician you successfully added or confirmed already in app."

# Run claude
echo "Running claude..." >> "$LOG"
"$CLAUDE" -p "$PROMPT" \
    --allowedTools "Read,Edit,Bash,WebSearch,WebFetch" \
    >> "$LOG" 2>&1

EXIT_CODE=$?
echo "claude exited with code $EXIT_CODE" >> "$LOG"

# Post-run validation
echo "Running post-run validation..." >> "$LOG"
python3 "$VALIDATE" >> "$LOG" 2>&1
VALID_EXIT=$?

LINES_AFTER=$(wc -l < "$AGORA/index.html")
LINES_ADDED=$((LINES_AFTER - LINES_BEFORE))
echo "Lines added to index.html: $LINES_ADDED" >> "$LOG"

if [ "$VALID_EXIT" -ne 0 ]; then
    echo "WARNING: Post-run validation found issues. Check log." >> "$LOG"
fi

echo "Done: $(date)" >> "$LOG"
