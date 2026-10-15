"""Run with:  python -m pytest tests/ -q   (or just: python tests/test_bundles.py)"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bundles as b  # noqa: E402

DATA = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "cghs_all_tiers.json")))
BY_CODE = {r["code"]: r for r in DATA}


def rate(code):
    return BY_CODE[code]["tiers"]["tier1_x_city"]["rate_nabh"]


def row(code, claimed=None, override=None):
    r = rate(code)
    claimed = r if claimed is None else claimed
    d = {"code": code, "claimed_amount": claimed, "admissible_amount": min(claimed, r)}
    if override:
        d["override"] = override
    return d


def run(*rows):
    return b.apply_billing_rules(list(rows), BY_CODE)


def test_every_rule_code_exists_in_rate_list():
    assert b.validate_rules(BY_CODE) == []


def test_cbc_components_are_included():
    out = run(row("LB012"), row("LB007"), row("LB008"), row("LB013"))
    assert out[0]["rule_status"] == b.COUNTED
    for r in out[1:]:
        assert r["rule_status"] == b.INCLUDED and r["counted_amount"] == 0
    assert sum(r["counted_amount"] for r in out) == rate("LB012")


def test_order_of_rows_does_not_matter():
    out = run(row("LB007"), row("LB013"), row("LB012"))
    assert [r["rule_status"] for r in out] == [b.INCLUDED, b.INCLUDED, b.COUNTED]


def test_kft_and_lipid_profile():
    out = run(row("LB123"), row("LB058"), row("LB057"), row("LB001"))
    assert [r["counted_amount"] for r in out] == [rate("LB123"), 0, 0, 0]
    out = run(row("LB125"), row("LB074"), row("LB100"), row("LB135"))
    assert [r["counted_amount"] for r in out] == [rate("LB125"), 0, 0, 0]


def test_package_inside_package_is_resolved_to_the_biggest_one():
    # Anaemia profile covers CBC, which covers Hb - Hb and CBC are both held back
    out = run(row("LB097"), row("LB012"), row("LB007"))
    assert out[0]["rule_status"] == b.COUNTED
    assert out[1]["rule_status"] == b.INCLUDED and out[2]["rule_status"] == b.INCLUDED
    assert "LB097" in out[2]["rule_note"]
    # extended lipid covers the plain lipid profile and its cholesterol
    out = run(row("LB129"), row("LB125"), row("LB074"))
    assert [r["counted_amount"] for r in out] == [rate("LB129"), 0, 0]


def test_customary_overlaps_are_flagged_not_dropped():
    out = run(row("LB124"), row("LB086"))  # LFT + SGPT
    assert out[1]["rule_status"] == b.REVIEW_INCLUDED
    assert out[1]["counted_amount"] == rate("LB086")
    out = run(row("RI070"), row("RI069"))  # CT whole abdomen with + without contrast
    assert out[1]["rule_status"] == b.REVIEW_INCLUDED and out[1]["counted_amount"] == rate("RI069")


def test_unrelated_tests_are_untouched():
    out = run(row("LB012"), row("LB122"), row("LB161"))  # CBC, HbA1c, TSH
    assert all(r["rule_status"] == b.COUNTED for r in out)


def test_tft_includes_tsh_but_not_free_t3_t4():
    out = run(row("LB160"), row("LB161"), row("LB266"), row("LB267"))
    assert out[1]["rule_status"] == b.INCLUDED
    assert out[2]["rule_status"] == b.COUNTED and out[3]["rule_status"] == b.COUNTED


def test_package_that_was_not_charged_does_not_cover_anything():
    out = run(row("LB012", claimed=0), row("LB007"))
    assert out[1]["rule_status"] == b.COUNTED


def test_repeated_test_counted_once_but_repeated_visit_only_flagged():
    out = run(row("LB122"), row("LB122"))
    assert out[0]["rule_status"] == b.COUNTED
    assert out[1]["rule_status"] == b.REPEAT and out[1]["counted_amount"] == 0
    out = run(row("CN001"), row("CN001"))
    assert out[1]["rule_status"] == b.REVIEW_REPEAT and out[1]["counted_amount"] == rate("CN001")


def test_same_test_by_two_methods_is_flagged():
    out = run(row("LB069"), row("LB070"))
    assert all(r["rule_status"] == b.REVIEW_ALT for r in out)
    assert all(r["counted_amount"] == r["admissible_amount"] for r in out)


def test_officer_overrides_win():
    out = run(row("LB012"), row("LB007", override=b.OVERRIDE_COUNT))
    assert out[1]["rule_status"] == b.FORCED_COUNT and out[1]["counted_amount"] == rate("LB007")
    out = run(row("LB124"), row("LB086", override=b.OVERRIDE_EXCLUDE))
    assert out[1]["counted_amount"] == 0
    # an excluded package no longer covers its components
    out = run(row("LB012", override=b.OVERRIDE_EXCLUDE), row("LB007"))
    assert out[0]["counted_amount"] == 0 and out[1]["rule_status"] == b.COUNTED


def test_unmatched_rows_stay_unresolved_until_the_officer_excludes_them():
    out = run({"code": None, "claimed_amount": 500, "admissible_amount": None})
    assert out[0]["rule_status"] == b.UNMATCHED and out[0]["counted_amount"] is None
    out = run({"code": None, "claimed_amount": 500, "admissible_amount": None, "override": b.OVERRIDE_EXCLUDE})
    assert out[0]["rule_status"] == b.FORCED_EXCLUDE and out[0]["counted_amount"] == 0


def test_covered_by_reports_the_package():
    out = run(row("LB012"), row("LB007"))
    assert out[1]["covered_by"] == "LB012" and out[0]["covered_by"] is None


def test_overcharged_package_component_still_held_back_at_zero():
    out = run(row("LB012", claimed=900), row("LB007", claimed=200))
    assert out[0]["counted_amount"] == rate("LB012")  # capped at CGHS rate
    assert out[1]["counted_amount"] == 0


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", name, "-", repr(e))
    sys.exit(1 if fails else 0)
