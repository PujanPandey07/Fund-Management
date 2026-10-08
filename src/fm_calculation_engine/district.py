from fm_plan import ImplementationPlan
from fm_plan.models import NonEmptyStr, StrictModel, norm

from fm_calculation_engine.common import (
    BeneficiaryCount,
    EngineInputError,
    match_name,
    parse,
    validate_beneficiary_counts,
)


class DistrictProfile(StrictModel):
    """Input profile for District fund calculation."""

    plan_id: NonEmptyStr
    district_name: NonEmptyStr


class DistrictCollectible(StrictModel):
    """Breakdown of funds collected from Federal, Province, and Local government levels."""

    federal: int
    province: int
    local: int
    total: int


class LocalGovDisbursement(StrictModel):
    """Total fund to be forwarded to a specific local government for a tranche."""

    local_gov: str
    amount: int


class DistrictTrancheOutput(StrictModel):
    """Collectible and disbursement amounts for a district in a single tranche round."""

    tranche: int
    stages: list[str]
    collectible: DistrictCollectible
    disbursements: list[LocalGovDisbursement]
    total_amount: int


class DistrictOutput(StrictModel):
    """Fund collection and disbursement responsibility for a district."""

    plan_id: str
    district_name: str
    province_name: str
    region: str
    total_beneficiaries: int
    tranches: list[DistrictTrancheOutput]
    grand_total: int


def calculate_district_fund(
    plan: ImplementationPlan,
    profile: DistrictProfile | dict,
    beneficiary_counts: list[BeneficiaryCount | dict],
) -> DistrictOutput:
    """Calculate collectible amounts from Fed/Prov/Local and forwarding amounts to local governments."""
    prof = parse(DistrictProfile, profile)

    if norm(prof.plan_id) != norm(plan.plan_id):
        raise EngineInputError(
            f"plan_id '{prof.plan_id}' does not match this plan ('{plan.plan_id}')"
        )

    district_names = [d.name for d in plan.government.districts]
    matched_district = match_name(prof.district_name, district_names)
    if not matched_district:
        raise EngineInputError(
            f"District '{prof.district_name}' was not found in the plan's districts {district_names}."
        )

    district_obj = next(d for d in plan.government.districts if norm(d.name) == norm(matched_district))
    province_name = district_obj.province
    region_name = district_obj.region

    # Local levels belonging to this district
    district_local_levels = [
        l.name for l in plan.government.local_levels if norm(l.district) == norm(matched_district)
    ]

    valid_counts = validate_beneficiary_counts(plan, beneficiary_counts)

    # Filter to beneficiaries in this district
    district_counts = [b for b in valid_counts if norm(b.district) == norm(matched_district)]
    total_beneficiaries = sum(b.count for b in district_counts)

    # Matrix lookup map: (region_norm, disaster_norm, solution_norm) -> GrantRow
    matrix_map = {
        (norm(r.region), norm(r.disaster_type), norm(r.solution_type)): r
        for r in plan.grant_matrix
    }

    max_tranches = max((len(r.tranches) for r in plan.grant_matrix), default=0)

    fed_pct = plan.government_liability.federal
    prov_pct = plan.government_liability.province
    local_pct = plan.government_liability.local

    tranche_outputs: list[DistrictTrancheOutput] = []
    grand_total = 0

    for t_idx in range(1, max_tranches + 1):
        tranche_total = 0
        stages_seen = set()

        # Breakdown per local level
        local_disbursements: dict[str, int] = {ll: 0 for ll in district_local_levels}
        unassigned_total = 0

        for b in district_counts:
            key = (norm(b.region), norm(b.disaster_type), norm(b.solution_type))
            grant_row = matrix_map[key]
            if t_idx <= len(grant_row.tranches):
                tranche_info = grant_row.tranches[t_idx - 1]
                stages_seen.add(tranche_info.stage)
                amount = b.count * tranche_info.amount
                tranche_total += amount

                if b.local_level:
                    matched_ll = next(
                        (ll for ll in district_local_levels if norm(ll) == norm(b.local_level)),
                        None,
                    )
                    if matched_ll:
                        local_disbursements[matched_ll] += amount
                    else:
                        unassigned_total += amount
                else:
                    unassigned_total += amount

        # If some or all counts did not specify local_level:
        if unassigned_total > 0:
            if len(district_local_levels) == 1:
                # Exactly one local level in district: allocate 100%
                local_disbursements[district_local_levels[0]] += unassigned_total
            elif len(district_local_levels) > 1:
                # Divide evenly among local levels in this district
                split_share = unassigned_total // len(district_local_levels)
                remainder = unassigned_total % len(district_local_levels)
                for i, ll in enumerate(district_local_levels):
                    local_disbursements[ll] += split_share + (1 if i < remainder else 0)

        # Collectible amounts from Fed, Prov, Local
        fed_amount = round(tranche_total * (fed_pct / 100.0))
        prov_amount = round(tranche_total * (prov_pct / 100.0))
        local_amount = tranche_total - fed_amount - prov_amount

        collectible = DistrictCollectible(
            federal=fed_amount,
            province=prov_amount,
            local=local_amount,
            total=tranche_total,
        )

        disbursements_list = [
            LocalGovDisbursement(local_gov=ll, amount=local_disbursements[ll])
            for ll in district_local_levels
        ]

        tranche_outputs.append(
            DistrictTrancheOutput(
                tranche=t_idx,
                stages=sorted(stages_seen),
                collectible=collectible,
                disbursements=disbursements_list,
                total_amount=tranche_total,
            )
        )
        grand_total += tranche_total

    return DistrictOutput(
        plan_id=plan.plan_id,
        district_name=matched_district,
        province_name=province_name,
        region=region_name,
        total_beneficiaries=total_beneficiaries,
        tranches=tranche_outputs,
        grand_total=grand_total,
    )
