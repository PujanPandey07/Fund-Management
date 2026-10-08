from typing import Annotated

from pydantic import Field

from fm_plan import ImplementationPlan
from fm_plan.models import NonEmptyStr, StrictModel, norm

from fm_calculation_engine.common import (
    BeneficiaryCount,
    Donation,
    EngineInputError,
    match_name,
    parse,
    validate_beneficiary_counts,
)


class GovEntityProfile(StrictModel):
    """Input profile for Federal, Provincial, or Local government entity."""

    plan_id: NonEmptyStr
    gov_entity_name: NonEmptyStr
    gov_type: NonEmptyStr | None = None  # "federal", "province", or "local" (optional: auto-detected if omitted)
    donations: list[Donation] = []


class GovDistrictPayment(StrictModel):
    """Payment responsibility towards a specific district for a single tranche."""

    district: str
    gross_liability: int
    donation_offset: int
    net_payable: int


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
    donations: list[Donation]
    total_beneficiaries: int
    beneficiary_summary: list[BeneficiaryGroupSummary]
    tranches: list[GovTrancheLiability]
    total_gross_liability: int
    total_donation_applied: int
    total_net_payable: int
    final_remaining_donation: int


def _resolve_entity(plan: ImplementationPlan, entity_name: str, requested_type: str | None = None):
    """Determine entity type and validate against the plan's government structure."""
    federal_name = plan.government.federal.name
    province_names = [p.name for p in plan.government.provinces]
    district_names = [d.name for d in plan.government.districts]
    local_names = [l.name for l in plan.government.local_levels]

    entity_type = None
    canonical_name = None
    managed_districts = None

    if norm(entity_name) == norm(federal_name):
        entity_type = "federal"
        canonical_name = federal_name
        managed_districts = district_names
    else:
        p_match = match_name(entity_name, province_names)
        if p_match:
            entity_type = "province"
            canonical_name = p_match
            managed_districts = [
                d.name for d in plan.government.districts if norm(d.province) == norm(p_match)
            ]
        else:
            l_match = match_name(entity_name, local_names)
            if l_match:
                entity_type = "local"
                canonical_name = l_match
                # Find districts where this local level exists
                matching_locals = [
                    l for l in plan.government.local_levels if norm(l.name) == norm(l_match)
                ]
                managed_districts = [l.district for l in matching_locals]

    if entity_type is None:
        raise EngineInputError(
            f"Government entity '{entity_name}' was not found in the plan's government structure."
        )

    if requested_type is not None and norm(requested_type) != norm(entity_type):
        raise EngineInputError(
            f"Specified gov_type '{requested_type}' does not match entity '{entity_name}' (expected '{entity_type}')."
        )

    return entity_type, canonical_name, managed_districts


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

    entity_type, canonical_name, managed_districts = _resolve_entity(
        plan, prof.gov_entity_name, prof.gov_type
    )

    valid_counts = validate_beneficiary_counts(plan, beneficiary_counts)

    # Filter beneficiary counts to this entity's jurisdiction
    relevant_counts: list[BeneficiaryCount] = []
    managed_district_norms = {norm(d) for d in managed_districts}

    for b in valid_counts:
        if norm(b.district) not in managed_district_norms:
            continue
        if entity_type == "local" and b.local_level:
            if norm(b.local_level) != norm(canonical_name):
                continue
        relevant_counts.append(b)

    # Determine liability percentage
    if entity_type == "federal":
        liability_pct = plan.government_liability.federal
    elif entity_type == "province":
        liability_pct = plan.government_liability.province
    else:
        liability_pct = plan.government_liability.local

    # Beneficiary summary
    grouped: dict[tuple[str, str, str], int] = {}
    for b in relevant_counts:
        key = (norm(b.disaster_type), norm(b.region), norm(b.solution_type))
        grouped[key] = grouped.get(key, 0) + b.count

    summary_list: list[BeneficiaryGroupSummary] = []
    # Preserve proper casing from plan
    for (dis_norm, reg_norm, sol_norm), total_count in grouped.items():
        matched_dis = next(d for d in plan.disaster_types if norm(d) == dis_norm)
        matched_reg = next(r for r in plan.regions if norm(r) == reg_norm)
        matched_sol = next(s for s in plan.solution_types if norm(s) == sol_norm)
        summary_list.append(
            BeneficiaryGroupSummary(
                disaster_type=matched_dis,
                region=matched_reg,
                solution_type=matched_sol,
                count=total_count,
            )
        )
    total_beneficiaries = sum(b.count for b in relevant_counts)

    # Matrix lookup map: (region_norm, disaster_norm, solution_norm) -> GrantRow
    matrix_map = {
        (norm(r.region), norm(r.disaster_type), norm(r.solution_type)): r
        for r in plan.grant_matrix
    }

    max_tranches = max((len(r.tranches) for r in plan.grant_matrix), default=0)

    # Donations tracking
    total_donations_received = sum(d.amount for d in prof.donations)
    available_donation = total_donations_received

    tranche_outputs: list[GovTrancheLiability] = []
    total_gross = 0
    total_donations_used = 0

    # Get canonical district names that have relevant counts
    active_districts_order: list[str] = []
    for d in plan.government.districts:
        if any(norm(b.district) == norm(d.name) for b in relevant_counts):
            if d.name not in active_districts_order:
                active_districts_order.append(d.name)

    for t_idx in range(1, max_tranches + 1):
        # District gross required for this tranche
        district_gross: dict[str, int] = {d: 0 for d in active_districts_order}
        stages_seen = set()

        for b in relevant_counts:
            key = (norm(b.region), norm(b.disaster_type), norm(b.solution_type))
            grant_row = matrix_map[key]
            if t_idx <= len(grant_row.tranches):
                tranche_info = grant_row.tranches[t_idx - 1]
                stages_seen.add(tranche_info.stage)
                item_required = b.count * tranche_info.amount
                canonical_dist = next(d for d in active_districts_order if norm(d) == norm(b.district))
                # District gross liability for this entity
                item_gross = round(item_required * (liability_pct / 100.0))
                district_gross[canonical_dist] += item_gross

        tranche_gross = sum(district_gross.values())
        donation_applied = min(available_donation, tranche_gross)
        net_payable = tranche_gross - donation_applied
        available_donation -= donation_applied

        total_gross += tranche_gross
        total_donations_used += donation_applied

        # Distribute donation offset across districts proportionally
        district_payments: list[GovDistrictPayment] = []
        if tranche_gross > 0 and donation_applied > 0:
            remaining_don_to_dist = donation_applied
            dist_offsets: dict[str, int] = {}
            for d in active_districts_order:
                d_gross = district_gross[d]
                if d_gross > 0:
                    share = int(round(donation_applied * (d_gross / tranche_gross)))
                    dist_offsets[d] = share
                else:
                    dist_offsets[d] = 0

            # Adjust rounding difference on largest district gross
            diff = donation_applied - sum(dist_offsets.values())
            if diff != 0:
                largest_d = max(active_districts_order, key=lambda d: district_gross[d])
                dist_offsets[largest_d] += diff

            for d in active_districts_order:
                d_gross = district_gross[d]
                d_offset = dist_offsets[d]
                district_payments.append(
                    GovDistrictPayment(
                        district=d,
                        gross_liability=d_gross,
                        donation_offset=d_offset,
                        net_payable=d_gross - d_offset,
                    )
                )
        else:
            for d in active_districts_order:
                d_gross = district_gross[d]
                district_payments.append(
                    GovDistrictPayment(
                        district=d,
                        gross_liability=d_gross,
                        donation_offset=0,
                        net_payable=d_gross,
                    )
                )

        tranche_outputs.append(
            GovTrancheLiability(
                tranche=t_idx,
                stages=sorted(stages_seen),
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
        gov_type=entity_type,
        liability_percentage=liability_pct,
        total_donations_received=total_donations_received,
        donations=prof.donations,
        total_beneficiaries=total_beneficiaries,
        beneficiary_summary=summary_list,
        tranches=tranche_outputs,
        total_gross_liability=total_gross,
        total_donation_applied=total_donations_used,
        total_net_payable=total_gross - total_donations_used,
        final_remaining_donation=available_donation,
    )
