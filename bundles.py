"""
Billing-overlap rules for the CGHS claim calculation sheet.

Some CGHS codes are *packages* that already include tests which also have their
own separate code - e.g. Complete Haemogram/CBC (LB012) already covers Haemoglobin
(LB007), TLC (LB008), DLC (LB009), Platelet count (LB013), ESR (LB010) and so on.
If a lab prints both the package and its component tests on the same bill, paying
every line would pay for the same test twice.

This module knows which codes cover which, and works out - for the whole claim at
once - which rows should really count towards the admissible total.

Two confidence levels, because they deserve different treatment:

  LISTED    The CGHS rate list itself names the component in the package's
            description (e.g. "Kidney Function Test (Sr.Creatinine, Blood Urea ...)").
            The component row is excluded from the admissible total by default.

  CUSTOMARY The CGHS description does not spell the components out (e.g. "Liver
            Function Test (LFT)"), or the overlap is a general billing convention
            (a contrast scan normally covers its own plain scan). These rows stay in
            the total but are flagged for the officer to decide.

Nothing here is final: every row can be overridden to Count / Exclude by the
processing officer in the app. The rules only set the starting point.
"""

LISTED = "listed"
CUSTOMARY = "customary"

# package code -> {component code: confidence}
# Only codes that exist in the bundled CGHS rate list are used (checked by
# `validate_rules`, which is run by the tests in tests/test_bundles.py).
_PACKAGES = {
    # --- Haematology -------------------------------------------------------
    # CBC: Hb, RBC count & indices, TLC, DLC, Platelet, ESR, Peripheral smear
    "LB012": {"LB007": LISTED, "LB011": LISTED, "LB008": LISTED, "LB009": LISTED,
              "LB013": LISTED, "LB010": LISTED, "LB017": LISTED},
    # Anaemia profile: Hb, Serum Iron, TIBC, Ferritin, Stool Occult Blood, CBC, Reticulocyte
    "LB097": {"LB007": LISTED, "LB068": LISTED, "LB075": LISTED, "LB126": LISTED,
              "LB004": LISTED, "LB012": LISTED, "LB014": LISTED},
    # Bleeding disorder panel: PT, APTT, Thrombin Time, Fibrinogen, D-Dimer/FDP
    "LB027": {"LB105": LISTED, "LB025": LISTED, "LB138": LISTED, "LB200": LISTED},
    # Coagulation profile - components not spelled out by CGHS
    "LB199": {"LB105": CUSTOMARY, "LB025": CUSTOMARY},
    # Thalassemia studies (Red Cell Indices and Hb HPLC)
    "LB034": {"LB011": LISTED},
    # Bone marrow smear with iron stain / with cytochemistry include the plain smear
    "LB023": {"LB022": LISTED},
    "LB024": {"LB022": LISTED},

    # --- Biochemistry ------------------------------------------------------
    # KFT: Creatinine, Urea/BUN, Uric acid, Sodium, Potassium, Urine R/E
    "LB123": {"LB058": LISTED, "LB057": LISTED, "LB066": LISTED, "LB092": LISTED,
              "LB093": LISTED, "LB001": LISTED},
    # LFT - CGHS does not list the components; these are the usual ones
    "LB124": {"LB067": CUSTOMARY, "LB086": CUSTOMARY, "LB087": CUSTOMARY,
              "LB107": CUSTOMARY, "LB080": CUSTOMARY, "LB072": CUSTOMARY},
    # Lipid profile: Total cholesterol, Triglycerides, LDL, HDL, VLDL
    "LB125": {"LB074": LISTED, "LB100": LISTED, "LB135": LISTED},
    # Extended lipid profile: lipid profile + Apo A1, Apo B, Lp(a)
    "LB129": {"LB125": LISTED, "LB074": LISTED, "LB100": LISTED, "LB135": LISTED,
              "LB130": LISTED, "LB131": LISTED, "LB132": LISTED},
    # Glucose (Fasting & PP) covers the single-glucose code; GTT usually does too
    "LB076": {"LB055": LISTED},
    "LB101": {"LB055": CUSTOMARY, "LB076": CUSTOMARY},
    # 24 hr urine for Proteins, Sodium, Creatinine
    "LB056": {"LB063": LISTED, "LB227": LISTED},
    # CSF / body fluid basic studies include cell count, protein, sugar
    "LB201": {"LB071": LISTED},
    # Blood gas with electrolytes includes the plain ABG (and usually Na/K/Cl)
    "LB120": {"LB119": LISTED, "LB092": CUSTOMARY, "LB093": CUSTOMARY, "LB094": CUSTOMARY},
    # Serum protein electrophoresis with immunofixation includes plain electrophoresis
    "LB155": {"LB137": LISTED},

    # --- Endocrine / maternal screening ------------------------------------
    # T3, T4, TSH - Thyroid Function Test. (T3/T4 have no separate code; free
    # T3/T4 are different tests and are NOT treated as included.)
    "LB160": {"LB161": LISTED},
    # Triple / Quadruple / Double marker
    "LB102": {"LB144": LISTED, "LB145": LISTED},
    "LB296": {"LB144": LISTED, "LB145": LISTED, "LB304": LISTED},
    "LB295": {"LB145": LISTED},
    # ANA profile normally includes the basic ANA
    "LB283": {"LB084": CUSTOMARY},

    # --- Radiology ----------------------------------------------------------
    # USG whole abdomen includes pelvis and post-void urine
    "RI020": {"RI021": LISTED},
    # Growth scan (including BPP, AFI, Doppler)
    "RI015": {"RI018": LISTED, "RI017": LISTED},
    # CT head / chest with contrast covers the plain study
    "RI063": {"RI062": CUSTOMARY},
    "RI066": {"RI064": CUSTOMARY},
}

# Contrast CT/MRI -> its own plain ("without contrast") scan of the same region.
# Found by matching the CGHS descriptions and checked by hand.
_CONTRAST_COVERS_PLAIN = {
    "RI067": "RI068", "RI070": "RI069", "RI078": "RI077", "RI081": "RI080",
    "RI083": "RI082", "RI088": "RI087", "RI090": "RI089", "RI092": "RI091",
    "RI094": "RI093", "RI097": "RI096", "RI099": "RI098", "RI101": "RI100",
    "RI103": "RI102", "RI105": "RI104", "RI107": "RI106", "RI109": "RI108",
    "RI111": "RI110", "RI112": "RI113", "RI114": "RI115", "RI117": "RI116",
    "RI118": "RI119", "RI120": "RI121", "RI122": "RI123", "RI125": "RI124",
    "RI127": "RI128", "RI131": "RI130", "RI135": "RI134", "RI137": "RI136",
}
for _with_contrast, _plain in _CONTRAST_COVERS_PLAIN.items():
    _PACKAGES.setdefault(_with_contrast, {})[_plain] = CUSTOMARY

# Different codes for what is really the same test (different method / unit).
# Billing more than one of a group is flagged for review, never auto-excluded,
# because the officer has to decide which one is the payable one.
ALTERNATE_GROUPS = [
    ("LB069", "LB070"),   # CRP / CRP quantitative
    ("LB109", "LB110"),   # CK-MB / CK-MB mass
    ("LB133", "LB134"),   # CD 3,4,8 counts / percentage
    ("LB178", "LB179"),   # HBV DNA qualitative / quantitative
    ("LB180", "LB244"),   # HCV RNA qualitative / quantitative
    ("LB084", "LB316"),   # ANA / ANA-IFA
    ("LB255", "LB325"),   # Chromogranin A / Serum Chromogranin A
]

# Repeating the same code is a normal thing for visits and procedures (two
# consultations, a second dressing) but almost always double billing for a
# diagnostic test. Only these classifications are auto-excluded when repeated.
_REPEAT_EXCLUDE_KEYWORDS = ("investigation", "radiolog")


# ---------------------------------------------------------------------------
# Closure: package -> everything it covers, directly or through another package
# ---------------------------------------------------------------------------
def _closure(code, _seen=None):
    """{covered code: confidence} for `code`, following packages inside packages.
    A path is only as sure as its weakest link."""
    if _seen is None:
        _seen = set()
    if code in _seen:
        return {}
    _seen = _seen | {code}
    out = {}
    for comp, conf in _PACKAGES.get(code, {}).items():
        if out.get(comp) != LISTED:
            out[comp] = conf
        for sub, sub_conf in _closure(comp, _seen).items():
            merged = LISTED if (conf == LISTED and sub_conf == LISTED) else CUSTOMARY
            if sub not in out or (out[sub] == CUSTOMARY and merged == LISTED):
                out[sub] = merged
    return out


COVERS = {pkg: _closure(pkg) for pkg in _PACKAGES}

_ALT_PARTNERS = {}
for _grp in ALTERNATE_GROUPS:
    for _c in _grp:
        _ALT_PARTNERS.setdefault(_c, set()).update(set(_grp) - {_c})


def validate_rules(by_code):
    """Return a list of problems (empty list = all good). Every code used in a rule
    must exist in the loaded CGHS list, and no package may (indirectly) cover itself."""
    problems = []
    for pkg, comps in _PACKAGES.items():
        if pkg not in by_code:
            problems.append(f"package {pkg} is not in the rate list")
        for c in comps:
            if c not in by_code:
                problems.append(f"component {c} (of {pkg}) is not in the rate list")
        if pkg in COVERS.get(pkg, {}):
            problems.append(f"{pkg} covers itself through a cycle")
    for grp in ALTERNATE_GROUPS:
        for c in grp:
            if c not in by_code:
                problems.append(f"alternate-group code {c} is not in the rate list")
    return problems


# ---------------------------------------------------------------------------
# The claim-level pass
# ---------------------------------------------------------------------------
COUNTED = "COUNTED"
INCLUDED = "INCLUDED IN PACKAGE"
REVIEW_INCLUDED = "REVIEW - USUALLY IN PACKAGE"
REPEAT = "REPEAT - COUNTED ONCE"
REVIEW_REPEAT = "REVIEW - CODE REPEATED"
REVIEW_ALT = "REVIEW - SAME TEST BILLED TWICE"
UNMATCHED = "NO MATCH"
FORCED_COUNT = "COUNTED (officer override)"
FORCED_EXCLUDE = "EXCLUDED (officer override)"

OVERRIDE_AUTO, OVERRIDE_COUNT, OVERRIDE_EXCLUDE = "Auto", "Count", "Exclude"
OVERRIDE_CHOICES = [OVERRIDE_AUTO, OVERRIDE_COUNT, OVERRIDE_EXCLUDE]


def _short(code, by_code):
    row = by_code.get(code)
    if not row:
        return code
    d = row["description"].split("(")[0].strip().rstrip("-").strip()
    return f"{code} {d[:45]}"


def apply_billing_rules(items, by_code):
    """
    items: list of dicts, one per billed row, in bill order. Needed keys:
        code              matched CGHS code, or None/'' if unmatched
        claimed_amount    what was billed
        admissible_amount the rate-capped amount (min(claimed, CGHS rate)) or None
        override          optional: 'Auto' (default) / 'Count' / 'Exclude'
    Returns a new list of dicts (same order) with three extra keys:
        rule_status      one of the status constants above
        rule_note        a plain-English reason, ready to show the officer
        counted_amount   what this row contributes to the admissible total
                         (None for unmatched rows)
    """
    n = len(items)
    out = [dict(it) for it in items]
    for it in out:
        it["rule_status"], it["rule_note"], it["covered_by"] = COUNTED, "", None
        it["counted_amount"] = it.get("admissible_amount")

    def is_active(i):
        """A row can only 'cover' others if it is really being paid for."""
        it = out[i]
        if it.get("override") == OVERRIDE_EXCLUDE:
            return False
        adm = it.get("admissible_amount")
        return bool(it.get("code")) and adm is not None and adm > 0

    # 1. Package inclusion --------------------------------------------------
    for i in range(n):
        code = out[i].get("code")
        if not code or out[i].get("admissible_amount") is None:
            continue
        best = None
        for j in range(n):
            pkg = out[j].get("code")
            if j == i or not pkg or pkg == code or not is_active(j):
                continue
            covers = COVERS.get(pkg, {})
            if code in covers:
                size = len(covers)
                if best is None or size > best[0]:
                    best = (size, j, pkg, covers[code])
        if best:
            _, _, pkg, conf = best
            out[i]["covered_by"] = pkg
            where = _short(pkg, by_code)
            if conf == LISTED:
                out[i]["rule_status"] = INCLUDED
                out[i]["rule_note"] = f"Already included in {where}, which is also billed - not payable separately."
                out[i]["counted_amount"] = 0
            else:
                out[i]["rule_status"] = REVIEW_INCLUDED
                out[i]["rule_note"] = (f"Usually part of {where}, which is also billed - "
                                       f"check it was a separate test; set to Exclude if not.")

    # 2. Same code billed more than once -----------------------------------
    first_seen = {}
    for i in range(n):
        code = out[i].get("code")
        if not code:
            continue
        if code not in first_seen:
            first_seen[code] = i
            continue
        if out[i]["rule_status"] in (INCLUDED,):
            continue  # already explained
        cls = (by_code.get(code, {}).get("classification") or "").lower()
        if any(k in cls for k in _REPEAT_EXCLUDE_KEYWORDS):
            out[i]["rule_status"] = REPEAT
            out[i]["rule_note"] = (f"{code} is billed more than once - a repeated test is counted once. "
                                   f"Set to Count if it was genuinely done again (e.g. different date).")
            out[i]["counted_amount"] = 0
        else:
            if out[i]["rule_status"] == COUNTED:
                out[i]["rule_status"] = REVIEW_REPEAT
                out[i]["rule_note"] = f"{code} appears more than once - confirm these are separate visits/procedures."

    # 3. Alternate codes for the same test ---------------------------------
    present = {out[i].get("code"): i for i in range(n) if out[i].get("code")}
    for i in range(n):
        code = out[i].get("code")
        partners = _ALT_PARTNERS.get(code)
        if not partners or out[i]["rule_status"] != COUNTED:
            continue
        hit = [p for p in partners if p in present]
        if hit:
            out[i]["rule_status"] = REVIEW_ALT
            out[i]["rule_note"] = (f"{_short(hit[0], by_code)} is the same test by another method and is also billed - "
                                   f"normally only one is payable.")

    # 4. Officer overrides win over everything -----------------------------
    for it in out:
        ov = it.get("override") or OVERRIDE_AUTO
        if ov == OVERRIDE_COUNT and it.get("admissible_amount") is not None:
            it["rule_status"], it["counted_amount"] = FORCED_COUNT, it["admissible_amount"]
            it["rule_note"] = "Counted as per officer's decision."
        elif ov == OVERRIDE_EXCLUDE:
            it["rule_status"], it["counted_amount"] = FORCED_EXCLUDE, 0
            it["rule_note"] = "Excluded as per officer's decision."
        if it.get("admissible_amount") is None and ov != OVERRIDE_EXCLUDE:
            it["rule_status"], it["counted_amount"] = UNMATCHED, None
            it["rule_note"] = ("No CGHS code matched - resolve before sanctioning." if not it.get("code")
                               else "No CGHS rate is listed for this code at the chosen tier / accreditation.")
    return out


def summarise(rows):
    """Small summary for the UI: how many rows were held back and how much money."""
    excluded = [r for r in rows if r["rule_status"] in (INCLUDED, REPEAT, FORCED_EXCLUDE)]
    review = [r for r in rows if r["rule_status"].startswith("REVIEW")]
    held_back = sum((r.get("admissible_amount") or 0) for r in excluded)
    return {"excluded": len(excluded), "review": len(review), "held_back_amount": held_back}
