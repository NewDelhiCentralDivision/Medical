"""
CGHS rate matching engine (all-tier version).
Given a list of claimed line items (code and/or description + claimed amount),
matches each against the CGHS master rate table and computes the admissible
amount for a chosen city tier + accreditation, flagging overcharges,
no-matches, and likely duplicate billings.
"""
import json
import re
from rapidfuzz import fuzz, process

TIER_KEYS = {
    "Tier I (X City)": "tier1_x_city",
    "Tier II (Y City)": "tier2_y_city",
    "Tier III (Z City)": "tier3_z_city",
}

# Every CGHS code follows this exact shape: 2-4 uppercase letters then 3 digits
# (e.g. LB127, RI022, OP121, NS018, NU144, CN001). Real-world bills often print
# a second, unrelated internal lab code right alongside it (e.g. "R047 - LB127"),
# so rather than treating the whole billed-code string as the CGHS code, we pull
# out just the substring that matches this shape.
CGHS_CODE_PATTERN = re.compile(r'[A-Z]{2,4}\d{3}')


def extract_cghs_code(text, valid_codes=None):
    """Find a CGHS-shaped code (AA123 style) inside a string that may also
    contain an unrelated internal lab/hospital code of the same rough shape
    (e.g. 'WZ786 - LB012', where WZ786 is the lab's own code and LB012 is the
    real CGHS code - both happen to fit the AA123 pattern).

    If valid_codes (a set/dict of real CGHS codes) is given, every candidate
    found is checked against it and the first one that's an actual CGHS code
    wins - this is what avoids picking the wrong one. Without valid_codes,
    falls back to the last pattern match in the string, since on real bills
    the CGHS code is conventionally printed second (internal code first).
    """
    if not text:
        return None
    candidates = CGHS_CODE_PATTERN.findall(text.upper())
    if not candidates:
        return None
    if valid_codes is not None:
        for c in candidates:
            if c in valid_codes:
                return c
        return None
    return candidates[-1]


def load_master(json_path):
    """Returns (raw_list, by_code, descriptions dict code->description)."""
    with open(json_path) as f:
        data = json.load(f)
    by_code = {r['code']: r for r in data}
    descriptions = {r['code']: r['description'] for r in data}
    return data, by_code, descriptions


def get_rate(rate_row, tier_label, accreditation):
    """
    rate_row: one record from the master list (has a 'tiers' dict)
    tier_label: one of TIER_KEYS' keys, e.g. "Tier I (X City)"
    accreditation: "NABH" or "Non-NABH"
    """
    tier_key = TIER_KEYS.get(tier_label, "tier1_x_city")
    tier_rates = rate_row.get('tiers', {}).get(tier_key)
    if not tier_rates:
        return None
    return tier_rates['rate_nabh'] if accreditation == 'NABH' else tier_rates['rate_non_nabh']


def best_fuzzy_match(query, descriptions, score_cutoff=72):
    if not query or not query.strip():
        return None, 0
    result = process.extractOne(query, descriptions, scorer=fuzz.token_set_ratio, score_cutoff=score_cutoff)
    if result is None:
        return None, 0
    matched_text, score, code = result
    return code, score


def match_line_item(item, by_code, descriptions, tier_label="Tier I (X City)", accreditation="NABH"):
    """
    item: dict with optional 'code' and required 'description', 'claimed_amount'
    returns enriched dict with match info
    """
    code_raw = item.get('code', '')
    code = extract_cghs_code(code_raw, valid_codes=by_code) or extract_cghs_code(item.get('description', ''), valid_codes=by_code)
    matched_code = None
    match_method = None
    score = None

    if code and code in by_code:
        matched_code = code
        match_method = 'exact_code'
        score = 100
    else:
        fm_code, fm_score = best_fuzzy_match(item.get('description', ''), descriptions)
        if fm_code:
            matched_code = fm_code
            match_method = 'fuzzy_description'
            score = fm_score

    result = dict(item)
    if matched_code:
        rate_row = by_code[matched_code]
        cghs_rate = get_rate(rate_row, tier_label, accreditation)
        result.update({
            'matched_code': matched_code,
            'matched_description': rate_row['description'],
            'cghs_rate': cghs_rate,
            'match_method': match_method,
            'match_score': score,
            'admissible_amount': (min(item.get('claimed_amount', 0), cghs_rate)
                                   if (cghs_rate is not None and item.get('claimed_amount') is not None) else cghs_rate),
        })
        claimed = item.get('claimed_amount', 0) or 0
        if cghs_rate is not None and claimed > cghs_rate:
            result['flag'] = 'OVERCHARGED'
        elif score is not None and score < 90:
            result['flag'] = 'REVIEW MATCH'
        else:
            result['flag'] = 'OK'
    else:
        result.update({
            'matched_code': None, 'matched_description': None,
            'cghs_rate': None,
            'match_method': 'none', 'match_score': 0,
            'admissible_amount': None,
            'flag': 'NO MATCH - MANUAL REVIEW REQUIRED'
        })
    return result


def flag_duplicates(matched_items):
    """Mark items sharing the same matched_code as potential duplicate billing."""
    seen = {}
    for it in matched_items:
        c = it.get('matched_code')
        if c:
            seen.setdefault(c, []).append(it)
    for c, items in seen.items():
        if len(items) > 1:
            for it in items:
                if it['flag'] == 'OK':
                    it['flag'] = 'POSSIBLE DUPLICATE'
                else:
                    it['flag'] += ' + POSSIBLE DUPLICATE'
    return matched_items


def process_claim(line_items, master_json_path, tier_label="Tier I (X City)", accreditation="NABH"):
    _, by_code, descriptions = load_master(master_json_path)
    matched = [match_line_item(it, by_code, descriptions, tier_label, accreditation) for it in line_items]
    matched = flag_duplicates(matched)
    total_claimed = sum(it.get('claimed_amount') or 0 for it in matched)
    total_admissible = sum(it.get('admissible_amount') or 0 for it in matched if it.get('admissible_amount') is not None)
    return {
        'items': matched,
        'total_claimed': total_claimed,
        'total_admissible': total_admissible,
    }
