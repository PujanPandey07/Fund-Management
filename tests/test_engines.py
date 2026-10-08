import json
from pathlib import Path

import pytest

from fm_plan import load_plan
from fm_calculation_engine import (
    BeneficiaryProfile,
    BeneficiaryCount,
    Donation,
    DistrictProfile,
    GovEntityProfile,
    EngineInputError,
    receivable_amounts,
    calculate_government_liability,
    calculate_district_fund,
)

SAMPLE_PATH = Path(__file__).parent.parent / "example_plan.json"


@pytest.fixture
def plan():
    return load_plan(SAMPLE_PATH)


@pytest.fixture
def sample_counts():
    return [
        {
            "district": "Gorkha",
            "local_level": "Gorkha Municipality",
            "region": "Pahad",
            "disaster_type": "Earthquake",
            "solution_type": "Reconstruction",
            "count": 10,
        },
        {
            "district": "Sindhupalchok",
            "local_level": "Melamchi Municipality",
            "region": "Pahad",
            "disaster_type": "Earthquake",
            "solution_type": "Reconstruction",
            "count": 20,
        },
        {
            "district": "Sindhupalchok",
            "local_level": "Helambu Rural Municipality",
            "region": "Pahad",
            "disaster_type": "Earthquake",
            "solution_type": "Retrofitting",
            "count": 5,
        },
    ]


# ==============================================================================
# Beneficiary Engine Tests (Output 1)
# ==============================================================================


def test_beneficiary_receivable_amounts_success(plan):
    profile = {
        "plan_id": plan.plan_id,
        "region": "Pahad",
        "disaster_type": "Earthquake",
        "solution_type": "Reconstruction",
    }
    res = receivable_amounts(plan, profile)
    assert res.plan_id == plan.plan_id
    assert res.total == 300000
    assert len(res.tranches) == 5
    assert res.tranches[0].stage == "shelter"
    assert res.tranches[0].amount == 15000
    assert res.tranches[1].stage == "shelter"
    assert res.tranches[1].amount == 15000
    assert res.tranches[2].stage == "main"
    assert res.tranches[2].amount == 100000
    assert res.tranches[3].stage == "main"
    assert res.tranches[3].amount == 100000
    assert res.tranches[4].stage == "main"
    assert res.tranches[4].amount == 70000


def test_beneficiary_case_insensitive(plan):
    profile = {
        "plan_id": plan.plan_id.upper(),
        "region": "pahad",
        "disaster_type": "EARTHQUAKE",
        "solution_type": "reconstruction",
    }
    res = receivable_amounts(plan, profile)
    assert res.total == 300000


def test_beneficiary_invalid_plan_id(plan):
    with pytest.raises(EngineInputError, match="does not match this plan"):
        receivable_amounts(
            plan,
            {
                "plan_id": "wrong-plan-id",
                "region": "Pahad",
                "disaster_type": "Earthquake",
                "solution_type": "Reconstruction",
            },
        )


def test_beneficiary_unknown_region(plan):
    with pytest.raises(EngineInputError, match="not in this plan's regions"):
        receivable_amounts(
            plan,
            {
                "plan_id": plan.plan_id,
                "region": "Mountain",
                "disaster_type": "Earthquake",
                "solution_type": "Reconstruction",
            },
        )


# ==============================================================================
# Government Engine Tests (Output 2)
# ==============================================================================


def test_federal_government_liability_calculation(plan, sample_counts):
    # Federal liability = 50%
    # Gorkha: 10 * Reconstruction
    # Sindhupalchok: 20 * Reconstruction + 5 * Retrofitting
    # In Pahad:
    # Reconstruction tranches: [15000, 15000, 100000, 100000, 70000]
    # Retrofitting tranches: [50000, 50000]
    #
    # Tranche 1:
    #   Gorkha: 10 * 15000 = 150,000. Fed 50% = 75,000
    #   Sindhupalchok: 20 * 15000 + 5 * 50000 = 300,000 + 250,000 = 550,000. Fed 50% = 275,000
    #   Total Tranche 1 Gross = 75,000 + 275,000 = 350,000
    #
    # With donation of 100,000:
    #   Tranche 1: Donation applied = 100,000. Net payable = 250,000. Remaining donation = 0.
    donations = [
        {"donor_name": "Red Cross", "amount": 100000, "received_date": "2015-10-01"}
    ]
    gov_profile = {
        "plan_id": plan.plan_id,
        "gov_entity_name": "Government of Nepal",
        "donations": donations,
    }

    out = calculate_government_liability(plan, gov_profile, sample_counts)

    assert out.gov_type == "federal"
    assert out.liability_percentage == 50.0
    assert out.total_donations_received == 100000
    assert out.total_beneficiaries == 35

    t1 = out.tranches[0]
    assert t1.tranche == 1
    assert t1.gross_liability == 350000
    assert t1.donation_applied == 100000
    assert t1.net_payable == 250000
    assert t1.remaining_donation == 0

    # District breakdown in tranche 1
    d_gorkha = next(d for d in t1.districts if d.district == "Gorkha")
    d_sindhu = next(d for d in t1.districts if d.district == "Sindhupalchok")
    assert d_gorkha.gross_liability == 75000
    assert d_sindhu.gross_liability == 275000
    assert d_gorkha.donation_offset + d_sindhu.donation_offset == 100000
    assert d_gorkha.net_payable + d_sindhu.net_payable == 250000

    # Tranche 2 has 0 donation remaining
    t2 = out.tranches[1]
    assert t2.tranche == 2
    assert t2.gross_liability == 350000
    assert t2.donation_applied == 0
    assert t2.net_payable == 350000


def test_donation_rollover_across_multiple_tranches(plan, sample_counts):
    # If donation is 400,000:
    # Tranche 1 gross is 350,000 -> 350,000 applied, net = 0, remaining donation = 50,000
    # Tranche 2 gross is 350,000 -> 50,000 applied, net = 300,000, remaining donation = 0
    donations = [
        {"donor_name": "UNICEF", "amount": 400000}
    ]
    gov_profile = {
        "plan_id": plan.plan_id,
        "gov_entity_name": "Government of Nepal",
        "donations": donations,
    }
    out = calculate_government_liability(plan, gov_profile, sample_counts)

    t1 = out.tranches[0]
    assert t1.gross_liability == 350000
    assert t1.donation_applied == 350000
    assert t1.net_payable == 0
    assert t1.remaining_donation == 50000

    t2 = out.tranches[1]
    assert t2.gross_liability == 350000
    assert t2.donation_applied == 50000
    assert t2.net_payable == 300000
    assert t2.remaining_donation == 0


def test_province_government_liability_boundary(plan, sample_counts):
    # Gandaki Province only covers Gorkha district (not Sindhupalchok which is Bagmati Province)
    # Liability % = 30%
    # Gorkha: 10 * Reconstruction
    # Tranche 1 gross: 10 * 15,000 = 150,000 * 30% = 45,000
    gov_profile = {
        "plan_id": plan.plan_id,
        "gov_entity_name": "Gandaki Province",
    }
    out = calculate_government_liability(plan, gov_profile, sample_counts)

    assert out.gov_type == "province"
    assert out.liability_percentage == 30.0
    assert out.total_beneficiaries == 10  # Only Gorkha beneficiaries count
    assert len(out.tranches[0].districts) == 1
    assert out.tranches[0].districts[0].district == "Gorkha"
    assert out.tranches[0].gross_liability == 45000


def test_local_government_liability(plan, sample_counts):
    # Gorkha Municipality is in Gorkha district.
    # Liability % = 20%
    # Gorkha has 10 Reconstruction beneficiaries.
    # Tranche 1 gross: 150,000 * 20% = 30,000
    gov_profile = {
        "plan_id": plan.plan_id,
        "gov_entity_name": "Gorkha Municipality",
    }
    out = calculate_government_liability(plan, gov_profile, sample_counts)

    assert out.gov_type == "local"
    assert out.liability_percentage == 20.0
    assert out.total_beneficiaries == 10
    assert out.tranches[0].gross_liability == 30000


def test_government_liability_unknown_entity(plan, sample_counts):
    with pytest.raises(EngineInputError, match="was not found in the plan's government structure"):
        calculate_government_liability(
            plan,
            {"plan_id": plan.plan_id, "gov_entity_name": "Unknown Council"},
            sample_counts,
        )


# ==============================================================================
# District Engine Tests (Output 3)
# ==============================================================================


def test_district_fund_calculation(plan, sample_counts):
    # Gorkha district: 10 Reconstruction beneficiaries
    # Tranche 1 total: 10 * 15000 = 150,000
    # Collectible split:
    #   Federal (50%): 75,000
    #   Province (30%): 45,000
    #   Local (20%): 30,000
    #   Total: 150,000
    # Disbursement:
    #   Gorkha Municipality: 150,000
    dist_profile = {
        "plan_id": plan.plan_id,
        "district_name": "Gorkha",
    }
    out = calculate_district_fund(plan, dist_profile, sample_counts)

    assert out.district_name == "Gorkha"
    assert out.province_name == "Gandaki Province"
    assert out.region == "Pahad"
    assert out.total_beneficiaries == 10

    t1 = out.tranches[0]
    assert t1.tranche == 1
    assert t1.total_amount == 150000
    assert t1.collectible.federal == 75000
    assert t1.collectible.province == 45000
    assert t1.collectible.local == 30000
    assert t1.collectible.total == 150000
    assert len(t1.disbursements) == 1
    assert t1.disbursements[0].local_gov == "Gorkha Municipality"
    assert t1.disbursements[0].amount == 150000


def test_district_multiple_local_gov_disbursement(plan, sample_counts):
    # Sindhupalchok district:
    # Melamchi Municipality: 20 Reconstruction * 15000 = 300,000 in Tranche 1
    # Helambu Rural Municipality: 5 Retrofitting * 50000 = 250,000 in Tranche 1
    # Total Tranche 1 = 550,000
    # Fed 50% = 275,000, Prov 30% = 165,000, Local 20% = 110,000
    dist_profile = {
        "plan_id": plan.plan_id,
        "district_name": "Sindhupalchok",
    }
    out = calculate_district_fund(plan, dist_profile, sample_counts)

    assert out.total_beneficiaries == 25
    t1 = out.tranches[0]
    assert t1.total_amount == 550000
    assert t1.collectible.federal == 275000
    assert t1.collectible.province == 165000
    assert t1.collectible.local == 110000

    melamchi = next(d for d in t1.disbursements if d.local_gov == "Melamchi Municipality")
    helambu = next(d for d in t1.disbursements if d.local_gov == "Helambu Rural Municipality")
    assert melamchi.amount == 300000
    assert helambu.amount == 250000
    assert melamchi.amount + helambu.amount == 550000


def test_district_invalid_district_name(plan, sample_counts):
    with pytest.raises(EngineInputError, match="was not found in the plan's districts"):
        calculate_district_fund(
            plan,
            {"plan_id": plan.plan_id, "district_name": "NonExistentDistrict"},
            sample_counts,
        )
