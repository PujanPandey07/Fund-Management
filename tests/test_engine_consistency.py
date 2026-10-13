"""Tests for the engine fixes: validation against the plan, and the three outputs agreeing.

The most important idea here: the money must add up.

    district total = federal + province + local          (every tranche)
    local share    = sum of the local governments' shares
    and what the governments owe together = what the districts need
"""

import json
import warnings
from pathlib import Path

import pytest

from fm_plan import DefaultLiabilityWarning, ImplementationPlan, load_plan
from fm_calculation_engine import (
    EngineInputError,
    calculate_district_fund,
    calculate_government_liability,
)
from fm_calculation_engine.core import split_by_weights

SAMPLE_PATH = Path(__file__).parent.parent / "example_plan.json"


def make_plan(mutate=None):
    """The sample plan, optionally changed by `mutate(data)` before it is checked."""
    data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    if mutate:
        mutate(data)
    return ImplementationPlan.model_validate(data)


def count(district, local_level, solution, n, region=None):
    item = {
        "district": district,
        "disaster_type": "Earthquake",
        "solution_type": solution,
        "count": n,
    }
    if local_level:
        item["local_level"] = local_level
    if region:
        item["region"] = region
    return item


def gov(plan, name, counts, **extra):
    return calculate_government_liability(
        plan, {"plan_id": plan.plan_id,
               "gov_entity_name": name, **extra}, counts
    )


def district(plan, name, counts):
    return calculate_district_fund(plan, {"plan_id": plan.plan_id, "district_name": name}, counts)


# ------------------------------------------------------------------------------
# split_by_weights
# ------------------------------------------------------------------------------


def test_split_adds_up_and_is_deterministic():
    assert split_by_weights(100, [1, 1, 1]) == [34, 33, 33]
    assert split_by_weights(10, [33.33, 33.33, 33.34]) == [3, 3, 4]
    assert sum(split_by_weights(1_000_001, [50, 30, 20])) == 1_000_001


def test_split_zero_total_and_bad_weights():
    assert split_by_weights(0, [1, 2]) == [0, 0]
    assert split_by_weights(0, []) == []
    with pytest.raises(ValueError):
        split_by_weights(5, [0, 0])
    with pytest.raises(ValueError):
        split_by_weights(5, [1, -1])


# ------------------------------------------------------------------------------
# Issue 1: counts are checked against the plan
# ------------------------------------------------------------------------------


def test_region_that_does_not_match_the_district_is_rejected():
    plan = make_plan()
    with pytest.raises(EngineInputError, match="does not match district 'Gorkha'"):
        district(plan, "Gorkha", [
                 count("Gorkha", "Gorkha Municipality", "Reconstruction", 1, region="Terai")])


def test_region_is_taken_from_the_plan_when_left_out():
    plan = make_plan()
    without = [count("Gorkha", "Gorkha Municipality", "Reconstruction", 10)]
    with_region = [count("Gorkha", "Gorkha Municipality",
                         "Reconstruction", 10, region="Pahad")]
    a = district(plan, "Gorkha", without)
    b = district(plan, "Gorkha", with_region)
    assert a == b
    # Pahad amounts, not Himal or Terai
    assert a.tranches[0].total_amount == 150_000


def test_beneficiary_summary_region_comes_from_the_plan():
    plan = make_plan()
    out = gov(plan, "Government of Nepal", [
              count("Gorkha", None, "Reconstruction", 4)])
    assert out.beneficiary_summary[0].region == "Pahad"


def test_district_outside_the_affected_areas_is_rejected():
    def add_dhading(data):
        data["government"]["districts"].append(
            {"name": "Dhading", "province": "Bagmati Province", "region": "Pahad"}
        )
        data["government"]["local_levels"].append(
            {"name": "Nilkantha Municipality", "district": "Dhading"})

    plan = make_plan(add_dhading)
    with pytest.raises(EngineInputError, match="district 'Dhading' is not in this plan's affected areas"):
        district(plan, "Dhading", [
                 count("Dhading", "Nilkantha Municipality", "Reconstruction", 3)])


def test_local_level_outside_the_affected_areas_is_rejected():
    def add_local(data):
        data["government"]["local_levels"].append(
            {"name": "Sunkoshi Rural Municipality", "district": "Sindhupalchok"}
        )

    plan = make_plan(add_local)
    with pytest.raises(EngineInputError, match="Sunkoshi Rural Municipality' in district 'Sindhupalchok' is not in"):
        district(plan, "Sindhupalchok", [
                 count("Sindhupalchok", "Sunkoshi Rural Municipality", "Reconstruction", 3)])


def test_ward_limited_local_level_is_accepted():
    # Melamchi is listed with wards 1-3 only. Counts have no ward, so it counts as affected.
    plan = make_plan()
    out = district(plan, "Sindhupalchok", [count(
        "Sindhupalchok", "Melamchi Municipality", "Reconstruction", 2)])
    assert out.total_beneficiaries == 2


def test_all_problems_are_reported_together():
    plan = make_plan()
    with pytest.raises(EngineInputError) as error:
        district(
            plan,
            "Gorkha",
            [
                count("Nowhere", None, "Reconstruction", 1),
                count("Gorkha", "Melamchi Municipality", "Reconstruction", 1),
            ],
        )
    assert "2 problem(s)" in str(error.value)


# ------------------------------------------------------------------------------
# Issues 2 and 3: the outputs agree, and the money adds up
# ------------------------------------------------------------------------------


def two_local_levels(data):
    data["government"]["local_levels"] = [
        {"name": "Gorkha Municipality", "district": "Gorkha"},
        {"name": "Melamchi Municipality", "district": "Sindhupalchok"},
        {"name": "Helambu Rural Municipality", "district": "Sindhupalchok"},
    ]


def test_counts_without_a_local_level_are_not_counted_twice():
    plan = make_plan()
    # no local level named
    counts = [count("Sindhupalchok", None, "Reconstruction", 20)]

    d = district(plan, "Sindhupalchok", counts)
    melamchi = gov(plan, "Melamchi Municipality", counts)
    helambu = gov(plan, "Helambu Rural Municipality", counts)

    for t in range(len(d.tranches)):
        # the district shares the money equally between its two local levels
        amounts = {x.local_gov: x.amount for x in d.tranches[t].disbursements}
        assert amounts["Melamchi Municipality"] == amounts["Helambu Rural Municipality"]
        # and the two local governments together owe exactly the district's local share
        owed = melamchi.tranches[t].gross_liability + \
            helambu.tranches[t].gross_liability
        assert owed == d.tranches[t].collectible.local

    assert melamchi.total_beneficiaries == 0
    assert melamchi.unassigned_beneficiaries == 20


def test_federal_province_and_local_add_up_to_the_district_totals():
    # 33.33 / 33.33 / 33.34 and odd counts: the case where separate rounding drifts
    def odd_split(data):
        data["government_liability"] = {
            "federal": 33.33, "province": 33.33, "local": 33.34}

    plan = make_plan(odd_split)
    counts = [
        count("Gorkha", "Gorkha Municipality", "Reconstruction", 7),
        count("Sindhupalchok", "Melamchi Municipality", "Reconstruction", 13),
        count("Sindhupalchok", "Helambu Rural Municipality", "Retrofitting", 3),
        count("Sindhupalchok", None, "Reconstruction", 5),
    ]
    districts = {n: district(plan, n, counts)
                 for n in ("Gorkha", "Sindhupalchok")}
    federal = gov(plan, "Government of Nepal", counts)
    provinces = [gov(plan, n, counts)
                 for n in ("Gandaki Province", "Bagmati Province")]
    locals_ = [gov(plan, n, counts) for n in (
        "Gorkha Municipality", "Melamchi Municipality", "Helambu Rural Municipality")]

    for t in range(len(federal.tranches)):
        district_total = sum(
            d.tranches[t].total_amount for d in districts.values())
        owed = (
            federal.tranches[t].gross_liability
            + sum(p.tranches[t].gross_liability for p in provinces)
            + sum(l.tranches[t].gross_liability for l in locals_)
        )
        assert owed == district_total

        # each level matches what the districts say they will collect from it
        assert federal.tranches[t].gross_liability == sum(
            d.tranches[t].collectible.federal for d in districts.values())
        assert sum(p.tranches[t].gross_liability for p in provinces) == sum(
            d.tranches[t].collectible.province for d in districts.values())
        assert sum(l.tranches[t].gross_liability for l in locals_) == sum(
            d.tranches[t].collectible.local for d in districts.values())

        for d in districts.values():
            c = d.tranches[t].collectible
            assert c.federal + c.province + c.local == c.total
            assert sum(
                x.amount for x in d.tranches[t].disbursements) == c.total


def test_donation_offsets_add_up_exactly():
    plan = make_plan()
    counts = [
        count("Gorkha", "Gorkha Municipality", "Reconstruction", 7),
        count("Sindhupalchok", "Melamchi Municipality", "Reconstruction", 13),
    ]
    out = gov(plan, "Government of Nepal", counts, donations=[
              {"donor_name": "X", "amount": 100_001}])
    for t in out.tranches:
        assert sum(d.donation_offset for d in t.districts) == t.donation_applied
        assert sum(d.net_payable for d in t.districts) == t.net_payable


def test_same_named_local_level_in_two_districts():
    def same_name(data):
        # "Central Municipality" exists in Gorkha and also in Sindhupalchok
        for ll in data["government"]["local_levels"]:
            if ll["name"] == "Gorkha Municipality":
                ll["name"] = "Central Municipality"
        data["government"]["local_levels"].append(
            {"name": "Central Municipality", "district": "Sindhupalchok"})
        data["plan"]["affected_areas"][1]["local_levels"].append(
            {"name": "Central Municipality"})

    plan = make_plan(same_name)
    counts = [
        count("Gorkha", "Central Municipality", "Reconstruction", 10),
        count("Sindhupalchok", "Central Municipality", "Reconstruction", 20),
    ]
    with pytest.raises(EngineInputError, match="more than one district"):
        gov(plan, "Central Municipality", counts)

    a = gov(plan, "Central Municipality", counts, district="Gorkha")
    b = gov(plan, "Central Municipality", counts, district="Sindhupalchok")
    assert a.total_beneficiaries == 10 and [
        d.district for d in a.tranches[0].districts] == ["Gorkha"]
    assert b.total_beneficiaries == 20 and [
        d.district for d in b.tranches[0].districts] == ["Sindhupalchok"]


def test_district_only_allowed_for_local_governments():
    plan = make_plan()
    with pytest.raises(EngineInputError, match="only be given for a local government"):
        gov(plan, "Gandaki Province", [], district="Gorkha")


def test_beneficiaries_in_a_district_with_no_local_levels_are_rejected():
    def no_locals(data):
        data["government"]["districts"].append(
            {"name": "Dhading", "province": "Bagmati Province", "region": "Pahad"})
        data["plan"]["affected_areas"].append({"district": "Dhading"})

    plan = make_plan(no_locals)
    with pytest.raises(EngineInputError, match="no local levels"):
        district(plan, "Dhading", [
                 count("Dhading", None, "Reconstruction", 2)])


# ------------------------------------------------------------------------------
# Issue 4: donation rule is explicit
# ------------------------------------------------------------------------------


def test_donations_are_listed_oldest_first_and_pooled_from_tranche_1():
    plan = make_plan()
    counts = [count("Gorkha", "Gorkha Municipality", "Reconstruction", 10)]
    out = gov(
        plan,
        "Government of Nepal",
        counts,
        donations=[
            {"donor_name": "Undated", "amount": 10_000},
            {"donor_name": "Late", "amount": 20_000, "received_date": "2016-01-01"},
            {"donor_name": "Early", "amount": 30_000,
                "received_date": "2015-10-01"},
        ],
    )
    assert [d.donor_name for d in out.donations] == [
        "Early", "Late", "Undated"]
    # pooled: the date does not hold any back
    assert out.tranches[0].donation_applied == 60_000


# ------------------------------------------------------------------------------
# Issue 5: the liability default is no longer silent
# ------------------------------------------------------------------------------


def test_missing_government_liability_warns_and_uses_the_default():
    data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    del data["government_liability"]
    with pytest.warns(DefaultLiabilityWarning, match="50% / province 30% / local 20%"):
        plan = ImplementationPlan.model_validate(data)
    assert (plan.government_liability.federal, plan.government_liability.province,
            plan.government_liability.local) == (50, 30, 20)


def test_explicit_government_liability_does_not_warn(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        plan = load_plan(SAMPLE_PATH)
        # a plan saved and loaded again always carries the field, so it stays quiet
        saved = tmp_path / "saved.json"
        saved.write_text(plan.model_dump_json(), encoding="utf-8")
        load_plan(saved)


def test_is_affected_ignores_wards():
    plan = make_plan()
    assert plan.is_affected("Gorkha")
    assert plan.is_affected("Gorkha", "Gorkha Municipality")
    # wards 1-3 only, still affected
    assert plan.is_affected("Sindhupalchok", "Melamchi Municipality")
    # covers() needs the ward
    assert not plan.covers("Sindhupalchok", "Melamchi Municipality")
    assert not plan.is_affected("Nowhere")
    assert not plan.is_affected("Sindhupalchok", "Gorkha Municipality")


# ------------------------------------------------------------------------------
# Issue 6: Administrative codes for Government bodies
# ------------------------------------------------------------------------------


def test_administrative_codes_linking_and_lookups():
    # Plan where districts link to provinces via province code, and local levels link via district code
    def use_code_links(data):
        data["government"]["districts"][0]["province"] = "P4"  # Gandaki
        data["government"]["districts"][1]["province"] = "P3"  # Bagmati
        data["government"]["local_levels"][0]["district"] = "36"  # Gorkha
        data["government"]["local_levels"][1]["district"] = "23"  # Sindhupalchok
        data["government"]["local_levels"][2]["district"] = "23"
        data["plan"]["affected_areas"][0]["district"] = "36"
        data["plan"]["affected_areas"][1]["district"] = "23"
        data["plan"]["affected_areas"][1]["local_levels"][0]["name"] = "23001"

    plan = make_plan(use_code_links)
    assert plan.is_affected("36")
    assert plan.is_affected("Gorkha")
    assert plan.is_affected("23", "23001")
    assert plan.is_affected("Sindhupalchok", "Melamchi Municipality")

    # Counts supplied with codes
    counts = [
        count("36", "36001", "Reconstruction", 10),
        count("23", "23001", "Reconstruction", 20),
    ]

    # District query using code "23"
    d_out = district(plan, "23", counts)
    assert d_out.district_name == "Sindhupalchok"
    assert d_out.district_code == "23"
    assert d_out.province_name == "Bagmati Province"
    assert d_out.province_code == "P3"
    assert d_out.tranches[0].disbursements[0].local_gov == "Melamchi Municipality"
    assert d_out.tranches[0].disbursements[0].local_gov_code == "23001"

    # Government query using province code "P3"
    p_out = gov(plan, "P3", counts)
    assert p_out.gov_entity_name == "Bagmati Province"
    assert p_out.gov_entity_code == "P3"
    assert p_out.gov_type == "province"
    assert p_out.total_beneficiaries == 20
    assert p_out.tranches[0].districts[0].district == "Sindhupalchok"
    assert p_out.tranches[0].districts[0].district_code == "23"

    # Government query using local level code "36001"
    l_out = gov(plan, "36001", counts)
    assert l_out.gov_entity_name == "Gorkha Municipality"
    assert l_out.gov_entity_code == "36001"
    assert l_out.gov_type == "local"
    assert l_out.total_beneficiaries == 10


def test_duplicate_administrative_codes_rejected():
    def dup_district_code(data):
        data["government"]["districts"][1]["code"] = "36"  # same as Gorkha

    with pytest.raises(Exception, match="duplicate district code"):
        make_plan(dup_district_code)

    def dup_prov_code(data):
        data["government"]["provinces"][1]["code"] = "P3"  # same as Bagmati

    with pytest.raises(Exception, match="duplicate province code"):
        make_plan(dup_prov_code)

    def dup_local_code(data):
        data["government"]["local_levels"][1]["code"] = "36001"  # same as Gorkha Mun

    with pytest.raises(Exception, match="duplicate local level code"):
        make_plan(dup_local_code)
