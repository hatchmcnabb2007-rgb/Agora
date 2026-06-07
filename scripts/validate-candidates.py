#!/usr/bin/env python3
"""
Validates all candidate objects in index.html.
Exits 0 if clean, 1 if errors found (which aborts the automation push).

Checks:
  - No duplicate IDs or names
  - Required fields present
  - issues[] has exactly 11 standard topics (only candidates with the new format)
  - controversies all have a non-empty source
  - keyVotes has at least 3 entries
"""

import re
import sys

REQUIRED_TOPICS = [
    "Healthcare",
    "Climate & Energy",
    "Immigration & Border",
    "Abortion & Reproductive Rights",
    "Gun Control",
    "Tax Policy & Economy",
    "Foreign Policy & Military",
    "Education",
    "Criminal Justice & Policing",
    "Israel & Gaza",
    "Trade & China",
]

REQUIRED_FIELDS = [
    "id", "name", "party", "title", "state",
    "currentRole", "tags", "highlights", "keyVotes", "finance", "issues", "controversies"
]

def extract_candidates_text(html):
    lines = html.split("\n")
    start = next((i for i, l in enumerate(lines) if "const candidates = [" in l), None)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == "];"), None)
    if start is None or end is None:
        print("ERROR: Could not locate candidates array")
        sys.exit(1)
    return "\n".join(lines[start:end+1])

def count_and_extract_blocks(arr_text):
    open_bracket = arr_text.index("[")
    content = arr_text[open_bracket+1:]
    depth = 0
    in_string = False
    escape_next = False
    string_char = None
    blocks = []
    block_start = None
    for i, c in enumerate(content):
        if escape_next:
            escape_next = False
            continue
        if in_string:
            if c == "\\":
                escape_next = True
            elif c == string_char:
                in_string = False
            continue
        if c in ('"', "'", "`"):
            in_string = True
            string_char = c
        elif c == "{":
            if depth == 0:
                block_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and block_start is not None:
                blocks.append(content[block_start:i+1])
                block_start = None
        elif c == "]" and depth == 0:
            break
    return blocks

def extract_array_field(block_text, field_name):
    """Extract the text content of a named array field from a JS object literal."""
    pattern = rf'\b{re.escape(field_name)}\s*:\s*\['
    match = re.search(pattern, block_text)
    if not match:
        return None
    start = match.end() - 1  # position of [
    depth = 0
    in_string = False
    escape_next = False
    string_char = None
    for i in range(start, len(block_text)):
        c = block_text[i]
        if escape_next:
            escape_next = False
            continue
        if in_string:
            if c == "\\":
                escape_next = True
            elif c == string_char:
                in_string = False
            continue
        if c in ('"', "'", "`"):
            in_string = True
            string_char = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return block_text[start:i+1]
    return None

def check_issues_topics(block_text, candidate_name):
    """Only validates issues[] arrays using the new 11-topic format (stanceLabel field present)."""
    errors = []
    issues_text = extract_array_field(block_text, "issues")
    if not issues_text:
        errors.append("  Missing issues array")
        return errors

    # Skip old-format candidates (they have a 'position' or 'stance' field but no 'stanceLabel')
    # Old format uses 'stance' as a string description, new format has 'stanceLabel'
    if "stanceLabel" not in issues_text:
        return []  # old-format candidate, skip topic check

    found_topics = re.findall(r'topic\s*:\s*["\']([^"\']+)["\']', issues_text)

    for topic in REQUIRED_TOPICS:
        if topic not in found_topics:
            errors.append(f"  Missing topic: '{topic}'")
    extra = [t for t in found_topics if t not in REQUIRED_TOPICS]
    for t in extra:
        errors.append(f"  Non-standard topic: '{t}'")
    if len(found_topics) != 11:
        errors.append(f"  Has {len(found_topics)} topics, expected exactly 11")
    return errors

def check_controversies_sourced(block_text, candidate_name):
    """Check that every controversy has a non-empty source."""
    errors = []
    controversies_text = extract_array_field(block_text, "controversies")
    if not controversies_text:
        return []
    # Only flag literally empty strings: source: "" or source: ''
    empty_sources = re.findall(r'\bsource\s*:\s*(?:""|\'\')' , controversies_text)
    if empty_sources:
        errors.append(f"  {len(empty_sources)} controversy entry/entries missing a source citation")
    return errors

def check_required_fields(block_text):
    errors = []
    for field in REQUIRED_FIELDS:
        pattern = rf'\b{re.escape(field)}\s*:'
        if not re.search(pattern, block_text):
            errors.append(f"  Missing required field: '{field}'")
    return errors

def check_keyvotes_count(block_text, candidate_name):
    errors = []
    kv_text = extract_array_field(block_text, "keyVotes")
    if kv_text:
        # Count objects at depth 1
        count = 0
        depth = 0
        in_string = False
        escape_next = False
        string_char = None
        for c in kv_text:
            if escape_next:
                escape_next = False
                continue
            if in_string:
                if c == "\\":
                    escape_next = True
                elif c == string_char:
                    in_string = False
                continue
            if c in ('"', "'", "`"):
                in_string = True
                string_char = c
            elif c == "{":
                if depth == 1:
                    count += 1
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
        if count < 3:
            errors.append(f"  Only {count} keyVotes entries (minimum 3 required)")
    return errors

def check_duplicates(blocks):
    errors = []
    ids = {}
    names = {}
    for i, block in enumerate(blocks):
        id_match = re.search(r'\bid\s*:\s*["\']([^"\']+)["\']', block)
        name_match = re.search(r'\bname\s*:\s*["\']([^"\']+)["\']', block)
        if id_match:
            cid = id_match.group(1)
            if cid in ids:
                errors.append(f"Duplicate id: '{cid}' (blocks #{ids[cid]+1} and #{i+1})")
            ids[cid] = i
        if name_match:
            cname = name_match.group(1)
            if cname in names:
                errors.append(f"Duplicate name: '{cname}' (blocks #{names[cname]+1} and #{i+1})")
            names[cname] = i
    return errors

def main():
    with open("/Users/taylormcnabb/Documents/Agora/index.html", "r") as f:
        html = f.read()

    arr_text = extract_candidates_text(html)
    blocks = count_and_extract_blocks(arr_text)
    count = len(blocks)
    print(f"Validating {count} candidates...")

    all_errors = {}

    dup_errors = check_duplicates(blocks)
    if dup_errors:
        all_errors["[GLOBAL — DUPLICATES]"] = dup_errors

    for block in blocks:
        name_match = re.search(r'\bname\s*:\s*["\']([^"\']+)["\']', block)
        candidate_name = name_match.group(1) if name_match else "(unknown)"

        errors = []
        errors += check_required_fields(block)
        errors += check_issues_topics(block, candidate_name)
        errors += check_controversies_sourced(block, candidate_name)
        errors += check_keyvotes_count(block, candidate_name)

        if errors:
            all_errors[candidate_name] = errors

    if all_errors:
        total_issues = sum(len(v) for v in all_errors.values())
        print(f"\n❌ VALIDATION FAILED — {len(all_errors)} candidate(s) with {total_issues} issue(s):\n")
        for name, errs in all_errors.items():
            print(f"• {name}")
            for e in errs:
                print(e)
        print(f"\nTotal candidates in file: {count}")
        sys.exit(1)
    else:
        print(f"✅ All {count} candidates passed validation.")
        sys.exit(0)

if __name__ == "__main__":
    main()
