from fm_plan import ImplementationPlan
from fm_plan.models import NonEmptyStr, StrictModel, norm

from fm_calculation_engine.common import (
    BeneficiaryCount,
    EngineInputError,
    match_name,
    parse,
    validate_beneficiary_counts,
)
from fm_calculation_engine.core import build_district_schedule


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

    valid_counts = validate_beneficiary_counts(plan, beneficiary_counts)
    district_counts = [b for b in valid_counts if norm(
        b.district) == norm(matched_district)]

    schedule = build_district_schedule(plan, matched_district, district_counts)

    tranche_outputs = [
        DistrictTrancheOutput(
            tranche=r.tranche,
            stages=r.stages,
            collectible=DistrictCollectible(
                federal=r.federal,
                province=r.province,
                local=r.local,
                total=r.total,
            ),
            disbursements=[
                LocalGovDisbursement(local_gov=ll, amount=r.required[ll])
                for ll in schedule.local_levels
            ],
            total_amount=r.total,
        )
        for r in schedule.rounds
    ]

    return DistrictOutput(
        plan_id=plan.plan_id,
        district_name=schedule.name,
        province_name=schedule.province,
        region=schedule.region,
        total_beneficiaries=sum(b.count for b in district_counts),
        tranches=tranche_outputs,
        grand_total=sum(r.total for r in schedule.rounds),
    )
