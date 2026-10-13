from datetime import date

from fm_plan import ImplementationPlan
from fm_plan.models import NonEmptyStr, StrictModel, norm

from fm_calculation_engine.common import (
    BeneficiaryCount,
    Donation,
    EngineInputError,
    parse,
    validate_beneficiary_counts,
)
from fm_calculation_engine.core import (
    build_district_schedule,
    max_tranche_count,
    split_by_weights,
)


class GovEntityProfile(StrictModel):
    """Input profile for Federal, Provincial, or Local government entity."""

    plan_id: NonEmptyStr
    gov_entity_name: NonEmptyStr
    # "federal", "province", or "local" (optional: auto-detected if omitted)
    gov_type: NonEmptyStr | None = None
    # Only for a local government whose name is used in more than one district.
    district: NonEmptyStr | None = None
    donations: list[Donation] = []


class GovDistrictPayment(StrictModel):
    """Payment responsibility towards a specific district for a single tranche."""

    district: str
    gross_liability: int
    donation_offset: int
    net_payable: int
    district_code: str | None = None


class GovTrancheLiability(StrictModel):
    """Fund liability breakdown for a government entity for a specific tranche round."""

    tranche: int
    stages: list[str]
    gross_liability: int
    donation_applied: int
    net_payable: int
    remaining_donation: int
    districts: list[GovDistrictPayment]


class BeneficiaryGroupSummary(StrictModel):
    """Summary count of beneficiaries by disaster, region, and solution type under this entity."""

    disaster_type: str
    region: str
    solution_type: str
    count: int


class GovernmentLiabilityOutput(StrictModel):
    """Complete liability and disbursement responsibility for a government entity."""

    plan_id: str
    gov_entity_name: str
    gov_type: str  # "federal", "province", or "local"
    liability_percentage: float
    total_donations_received: int
    donations: list[Donation]  # oldest first; donations with no date come last
    total_beneficiaries: int
    # Local governments only: households in the same district that did not name a local
    # level. They are not counted in total_beneficiaries, but their money is shared
    # equally between the district's local levels, so it is part of the liability.
    unassigned_beneficiaries: int = 0
    beneficiary_summary: list[BeneficiaryGroupSummary]
    tranches: list[GovTrancheLiability]
    total_gross_liability: int
    total_donation_applied: int
    total_net_payable: int
    final_remaining_donation: int
    gov_entity_code: str | None = None


def _resolve_entity(
    plan: ImplementationPlan,
    entity_name: str,
    requested_type: str | None = None,
    district: str | None = None,
):
    """Find which government this is (by name or code). Returns (type, canonical name, code, its district names)."""
    federal = plan.government.federal
    canonical_code = None

    if norm(entity_name) == norm(federal.name) or (federal.code and norm(entity_name) == norm(federal.code)):
        entity_type = "federal"
        canonical_name = federal.name
        canonical_code = federal.code
        managed_districts = [d.name for d in plan.government.districts]
    elif (prov_obj := plan.government.find_province(entity_name)) is not None:
        entity_type = "province"
        canonical_name = prov_obj.name
        canonical_code = prov_obj.code
        managed_districts = [
            d.name
            for d in plan.government.districts
            if norm(d.province) == norm(prov_obj.name) or (prov_obj.code and norm(d.province) == norm(prov_obj.code))
        ]
    else:
        # Check local levels
        dist_filter_name = None
        if district is not None:
            dist_filter = plan.government.find_district(district)
            if dist_filter is None:
                raise EngineInputError(f"District '{district}' is not in this plan's districts.")
            dist_filter_name = dist_filter.name

        locals_found = plan.government.find_local_level(entity_name, district_identifier=dist_filter_name)
        if not locals_found:
            # Check if entity exists in another district when district wasn't given
            all_matching = plan.government.find_local_level(entity_name)
            if all_matching:
                raise EngineInputError(
                    f"Local government '{all_matching[0].name}' is not in district '{district}' in this plan."
                )
            raise EngineInputError(
                f"Government entity '{entity_name}' was not found in the plan's government structure."
            )

        entity_type = "local"
        canonical_name = locals_found[0].name
        canonical_code = locals_found[0].code
        managed_districts = [
            (plan.government.find_district(ll.district).name if plan.government.find_district(ll.district) else ll.district)
            for ll in locals_found
        ]
        if len(set(managed_districts)) > 1:
            raise EngineInputError(
                f"'{canonical_name}' is a local government in more than one district "
                f"{managed_districts}. Say which one with the 'district' field."
            )

    if district is not None and entity_type != "local":
        raise EngineInputError(
            "'district' can only be given for a local government "
            f"('{canonical_name}' is a {entity_type} government)."
        )

    if requested_type is not None and norm(requested_type) != norm(entity_type):
        raise EngineInputError(
            f"Specified gov_type '{requested_type}' does not match entity '{entity_name}' (expected '{entity_type}')."
        )

    return entity_type, canonical_name, canonical_code, managed_districts


def calculate_government_liability(
    plan: ImplementationPlan,
    profile: GovEntityProfile | dict,
    beneficiary_counts: list[BeneficiaryCount | dict],
) -> GovernmentLiabilityOutput:
    """Calculate government liability, donation offsets across tranches, and district breakdowns."""
    prof = parse(GovEntityProfile, profile)

    if norm(prof.plan_id) != norm(plan.plan_id):
        raise EngineInputError(
            f"plan_id '{prof.plan_id}' does not match this plan ('{plan.plan_id}')"
        )

    entity_type, canonical_name, canonical_code, managed_districts = _resolve_entity(
        plan, prof.gov_entity_name, prof.gov_type, prof.district
    )

    valid_counts = validate_beneficiary_counts(plan, beneficiary_counts)

    # Counts inside this government's districts
    managed_norms = {norm(d) for d in managed_districts}
    in_scope = [b for b in valid_counts if norm(b.district) in managed_norms]

    if entity_type == "local":
        relevant_counts = [
            b for b in in_scope if b.local_level and norm(b.local_level) == norm(canonical_name)
        ]
        unassigned_beneficiaries = sum(
            b.count for b in in_scope if not b.local_level)
    else:
        relevant_counts = in_scope
        unassigned_beneficiaries = 0

    liability = plan.government_liability
    liability_pct = {
        "federal": liability.federal,
        "province": liability.province,
        "local": liability.local,
    }[entity_type]

    # Beneficiary summary (names are already in the plan's spelling and region comes from the plan)
    grouped: dict[tuple[str, str, str], int] = {}
    for b in relevant_counts:
        key = (b.disaster_type, b.region, b.solution_type)
        grouped[key] = grouped.get(key, 0) + b.count
    summary_list = [
        BeneficiaryGroupSummary(
            disaster_type=dis, region=reg, solution_type=sol, count=n)
        for (dis, reg, sol), n in grouped.items()
    ]
    total_beneficiaries = sum(b.count for b in relevant_counts)

    # Districts with beneficiaries, in the plan's order
    active_districts = [
        d.name
        for d in plan.government.districts
        if norm(d.name) in managed_norms and any(norm(b.district) == norm(d.name) for b in in_scope)
    ]

    # One schedule per district: the same numbers the district output uses
    schedules = {
        d: build_district_schedule(
            plan, d, [b for b in in_scope if norm(b.district) == norm(d)]
        )
        for d in active_districts
    }

    def share_of_round(district: str, tranche_round) -> int:
        if entity_type == "federal":
            return tranche_round.federal
        if entity_type == "province":
            return tranche_round.province
        local_level = next(
            ll for ll in schedules[district].local_levels if norm(ll) == norm(canonical_name)
        )
        return tranche_round.local_shares[local_level]

    # Donations are pooled and spent from tranche 1 onward; the surplus rolls over.
    donations = sorted(
        prof.donations, key=lambda d: (
            d.received_date is None, d.received_date or date.min)
    )
    total_donations_received = sum(d.amount for d in donations)
    available_donation = total_donations_received

    tranche_outputs: list[GovTrancheLiability] = []
    total_gross = 0
    total_donations_used = 0

    for index in range(max_tranche_count(plan)):
        district_gross = {d: share_of_round(
            d, schedules[d].rounds[index]) for d in active_districts}
        stages = sorted(
            {s for d in active_districts for s in schedules[d].rounds[index].stages})

        tranche_gross = sum(district_gross.values())
        donation_applied = min(available_donation, tranche_gross)
        net_payable = tranche_gross - donation_applied
        available_donation -= donation_applied

        total_gross += tranche_gross
        total_donations_used += donation_applied

        # The donation relief is shared between districts in proportion to what each one needs
        offsets = split_by_weights(
            donation_applied, [district_gross[d] for d in active_districts])
        district_payments = [
            GovDistrictPayment(
                district=d,
                district_code=schedules[d].code,
                gross_liability=district_gross[d],
                donation_offset=offset,
                net_payable=district_gross[d] - offset,
            )
            for d, offset in zip(active_districts, offsets)
        ]

        tranche_outputs.append(
            GovTrancheLiability(
                tranche=index + 1,
                stages=stages,
                gross_liability=tranche_gross,
                donation_applied=donation_applied,
                net_payable=net_payable,
                remaining_donation=available_donation,
                districts=district_payments,
            )
        )

    return GovernmentLiabilityOutput(
        plan_id=plan.plan_id,
        gov_entity_name=canonical_name,
        gov_entity_code=canonical_code,
        gov_type=entity_type,
        liability_percentage=liability_pct,
        total_donations_received=total_donations_received,
        donations=donations,
        total_beneficiaries=total_beneficiaries,
        unassigned_beneficiaries=unassigned_beneficiaries,
        beneficiary_summary=summary_list,
        tranches=tranche_outputs,
        total_gross_liability=total_gross,
        total_donation_applied=total_donations_used,
        total_net_payable=total_gross - total_donations_used,
        final_remaining_donation=available_donation,
    )
