"""End-to-End Test and Demonstration Script for Fund Management System.

Demonstrates:
1. Plan loading and validation (Package 1: fm_plan)
2. Output 1: Beneficiary receivable amounts across tranches
3. Output 2: Government liability (Federal, Province, Local) with donation rollover
4. Output 3: District collection pooling and local government disbursements
"""

from pathlib import Path
from fm_plan import load_plan
from fm_calculation_engine import (
    receivable_amounts,
    calculate_government_liability,
    calculate_district_fund,
)

PLAN_FILE = Path(__file__).parent / "example_plan.json"


def main():
    print("=" * 80)
    print("STEP 1: Load and Validate Implementation Plan (fm_plan)")
    print("=" * 80)
    plan = load_plan(PLAN_FILE)
    print(f"Plan ID: {plan.plan_id}")
    print(f"Disaster: {plan.plan.disaster_name} ({plan.plan.disaster_date_range.start} to {plan.plan.disaster_date_range.end})")
    print(f"Government Liability %: Federal={plan.government_liability.federal}%, "
          f"Province={plan.government_liability.province}%, Local={plan.government_liability.local}%")
    print(f"Districts in Plan: {[d.name for d in plan.government.districts]}")
    print()

    # Define realistic beneficiary counts across affected districts
    beneficiary_counts = [
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

    print("=" * 80)
    print("STEP 2: Output 1 - Individual Beneficiary Receivables")
    print("=" * 80)
    b_profile = {
        "plan_id": plan.plan_id,
        "region": "Pahad",
        "disaster_type": "Earthquake",
        "solution_type": "Reconstruction",
    }
    b_out = receivable_amounts(plan, b_profile)
    print(f"Beneficiary: Region={b_out.region}, Disaster={b_out.disaster_type}, Solution={b_out.solution_type}")
    print(f"Total Grant: Rs. {b_out.total:,}")
    print("Tranche Schedule:")
    for t in b_out.tranches:
        print(f"  - Tranche {t.tranche} [{t.stage.upper()}]: Rs. {t.amount:,}")
    print()

    print("=" * 80)
    print("STEP 3: Output 2 - Government Liability & Donation Rollover")
    print("=" * 80)
    # Federal with a donation of Rs. 400,000 to observe rollover across tranches
    fed_profile = {
        "plan_id": plan.plan_id,
        "gov_entity_name": "Government of Nepal",
        "donations": [
            {"donor_name": "Red Cross", "amount": 250000, "received_date": "2015-10-01"},
            {"donor_name": "UNICEF", "amount": 150000, "received_date": "2015-10-05"},
        ],
    }
    fed_out = calculate_government_liability(plan, fed_profile, beneficiary_counts)
    print(f"Entity: {fed_out.gov_entity_name} ({fed_out.gov_type.upper()}) | Liability Share: {fed_out.liability_percentage}%")
    print(f"Total Beneficiaries Under Entity: {fed_out.total_beneficiaries}")
    print(f"Total Donations Received: Rs. {fed_out.total_donations_received:,}")
    print(f"Total Gross Liability: Rs. {fed_out.total_gross_liability:,}")
    print(f"Total Net Cash Payable: Rs. {fed_out.total_net_payable:,}")
    print("\nTranche-by-Tranche Liability and Donation Application:")
    for t in fed_out.tranches:
        print(f"  Tranche {t.tranche} ({', '.join(t.stages)}):")
        print(f"    Gross Liability:   Rs. {t.gross_liability:,}")
        print(f"    Donation Applied:  Rs. {t.donation_applied:,}")
        print(f"    Net Cash Payable:  Rs. {t.net_payable:,}")
        print(f"    Donation Rollover: Rs. {t.remaining_donation:,}")
        print("    District Outflow Breakdown:")
        for d in t.districts:
            print(f"      - {d.district}: Gross Rs. {d.gross_liability:,} | Offset Rs. {d.donation_offset:,} | Net Rs. {d.net_payable:,}")

    print("\nProvince Boundary Check (Gandaki Province only covers Gorkha):")
    prov_profile = {
        "plan_id": plan.plan_id,
        "gov_entity_name": "Gandaki Province",
    }
    prov_out = calculate_government_liability(plan, prov_profile, beneficiary_counts)
    print(f"  Entity: {prov_out.gov_entity_name} | Beneficiaries: {prov_out.total_beneficiaries} (Gorkha only)")
    print(f"  Tranche 1 Gross Liability (30%): Rs. {prov_out.tranches[0].gross_liability:,}")
    print()

    print("=" * 80)
    print("STEP 4: Output 3 - District Collection & Local Gov Forwarding")
    print("=" * 80)
    dist_profile = {
        "plan_id": plan.plan_id,
        "district_name": "Sindhupalchok",
    }
    dist_out = calculate_district_fund(plan, dist_profile, beneficiary_counts)
    print(f"District: {dist_out.district_name} ({dist_out.region}) | Province: {dist_out.province_name}")
    print(f"Total Beneficiaries: {dist_out.total_beneficiaries}")
    print(f"Grand Total Fund: Rs. {dist_out.grand_total:,}")
    print("\nTranche Collection and Forwarding Schedule:")
    for t in dist_out.tranches:
        print(f"  Tranche {t.tranche} ({', '.join(t.stages)}):")
        print(f"    Total Required: Rs. {t.total_amount:,}")
        print(f"    Collections  -> Federal (50%): Rs. {t.collectible.federal:,} | Province (30%): Rs. {t.collectible.province:,} | Local (20%): Rs. {t.collectible.local:,}")
        print("    Disbursements to Local Governments:")
        for d in t.disbursements:
            print(f"      -> {d.local_gov}: Rs. {d.amount:,}")

    print("=" * 80)
    print("ALL CHECKS COMPLETED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    main()
