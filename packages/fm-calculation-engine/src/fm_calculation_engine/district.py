from fm_plan import ImplementationPlan, NonEmptyStr, StrictModel, norm

from fm_calculation_engine.common import (
    BeneficiaryCount,
    EngineInputError,
    parse,
    validate_beneficiary_counts,
)
from fm_calculation_engine.core import build_district_schedule


class DistrictProfile(StrictModel):
    """Input profile for District fund calculation (accepts district name or code)."""

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
    local_gov_code: str | None = None


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
    district_code: str | None = None
    province_code: str | None = None


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

    district_obj = plan.government.find_district(prof.district_name)
    if district_obj is None:
        district_names = [d.name for d in plan.government.districts]
        raise EngineInputError(
            f"District '{prof.district_name}' was not found in the plan's districts {district_names}."
        )

    valid_counts = validate_beneficiary_counts(plan, beneficiary_counts)
    district_counts = [b for b in valid_counts if norm(
        b.district) == norm(district_obj.name)]

    schedule = build_district_schedule(plan, district_obj.name, district_counts)

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
                LocalGovDisbursement(
                    local_gov=ll,
                    local_gov_code=schedule.local_level_codes.get(ll),
                    amount=r.required[ll],
                )
                for ll in schedule.local_levels
            ],
            total_amount=r.total,
        )
        for r in schedule.rounds
    ]

    return DistrictOutput(
        plan_id=plan.plan_id,
        district_name=schedule.name,
        district_code=schedule.code,
        province_name=schedule.province,
        province_code=schedule.province_code,
        region=schedule.region,
        total_beneficiaries=sum(b.count for b in district_counts),
        tranches=tranche_outputs,
        grand_total=sum(r.total for r in schedule.rounds),
    )
