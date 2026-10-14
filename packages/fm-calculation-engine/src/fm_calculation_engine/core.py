"""Shared calculation core.

Every number the engine reports about a district comes from ONE schedule built here,
so the three outputs cannot disagree:

    district total  =  federal share + province share + local share      (exact)
    local share     =  sum of the shares given to the district's local levels (exact)

Splitting uses the largest remainder method on whole rupees: each part gets the
whole rupees it is owed, and the few rupees left over go to the parts with the
largest fractions. The parts therefore always add up to the total, with no
rounding drift and no dependence on Python's round().
"""

from dataclasses import dataclass
from decimal import Decimal

from fm_plan import ImplementationPlan, norm

from fm_calculation_engine.common import BeneficiaryCount, EngineInputError


def split_by_weights(total: int, weights) -> list[int]:
    """Split a whole-rupee `total` in proportion to `weights`. The parts add up to `total`.

    Ties between equal fractions go to the earlier item, so the result is always the
    same for the same input.
    """
    weight_list = [Decimal(str(w)) for w in weights]
    if any(w < 0 for w in weight_list):
        raise ValueError("weights cannot be negative")
    weight_sum = sum(weight_list)
    if total == 0:
        return [0] * len(weight_list)
    if weight_sum == 0:
        raise ValueError(
            "cannot split a non-zero amount across weights that add up to zero")

    quotas = [Decimal(total) * w / weight_sum for w in weight_list]
    # quotas are never negative, so int() is the floor
    parts = [int(q) for q in quotas]
    leftover = total - sum(parts)
    by_largest_fraction = sorted(
        range(len(parts)), key=lambda i: (-(quotas[i] - parts[i]), i)
    )
    for i in by_largest_fraction[:leftover]:
        parts[i] += 1
    return parts


def split_liability(plan: ImplementationPlan, amount: int) -> tuple[int, int, int]:
    """(federal, province, local) shares of `amount`, adding up to `amount`."""
    g = plan.government_liability
    federal, province, local = split_by_weights(
        amount, [g.federal, g.province, g.local])
    return federal, province, local


def max_tranche_count(plan: ImplementationPlan) -> int:
    """Number of tranche rounds: the longest tranche list in the grant matrix."""
    return max((len(r.tranches) for r in plan.grant_matrix), default=0)


@dataclass
class TrancheRound:
    """Everything one district needs for one tranche round."""

    tranche: int  # 1, 2, 3, ...
    stages: list[str]
    # local level -> money it must receive and pay out
    required: dict[str, int]
    total: int  # money the district needs this round
    federal: int  # federal share of `total`
    province: int  # province share of `total`
    local: int  # local share of `total`
    local_shares: dict[str, int]  # `local` split over the local levels


@dataclass
class DistrictSchedule:
    name: str
    code: str | None
    province: str
    province_code: str | None
    region: str
    local_levels: list[str]
    local_level_codes: dict[str, str | None]
    rounds: list[TrancheRound]


def build_district_schedule(
    plan: ImplementationPlan,
    district_name: str,
    district_counts: list[BeneficiaryCount],
) -> DistrictSchedule:
    """Work out every tranche round for one district.

    `district_counts` must already be validated (see validate_beneficiary_counts)
    and belong to this district. `district_name` can be a name or code.
    """
    district = plan.government.find_district(district_name)
    if district is None:
        raise EngineInputError(
            f"District '{district_name}' was not found in this plan's districts."
        )

    prov_obj = plan.government.find_province(district.province)
    province_name = prov_obj.name if prov_obj else district.province
    province_code = prov_obj.code if prov_obj else None

    matching_levels = [
        ll for ll in plan.government.local_levels
        if norm(ll.district) == norm(district.code)
    ]
    local_levels = [ll.name for ll in matching_levels]
    local_level_codes = {ll.name: ll.code for ll in matching_levels}

    if district_counts and not local_levels:
        raise EngineInputError(
            f"District '{district.name}' has beneficiaries but no local levels in this plan's "
            "government section, so its money cannot be forwarded. Add its local levels."
        )

    matrix = {
        (norm(r.region), norm(r.disaster_type), norm(r.solution_type)): r
        for r in plan.grant_matrix
    }

    rounds: list[TrancheRound] = []
    for tranche in range(1, max_tranche_count(plan) + 1):
        required = {ll: 0 for ll in local_levels}
        unassigned = 0  # beneficiaries who did not name a local level
        stages: set[str] = set()

        for b in district_counts:
            row = matrix[(norm(b.region), norm(
                b.disaster_type), norm(b.solution_type))]
            if tranche > len(row.tranches):
                # this grant has no payment in this round (its amount is Rs. 0)
                continue
            payment = row.tranches[tranche - 1]
            stages.add(payment.stage)
            amount = b.count * payment.amount
            if b.local_level:
                required[b.local_level] += amount
            else:
                unassigned += amount

        # Money for beneficiaries with no local level is shared equally between the
        # district's local levels. Any odd rupees go to the first ones in the plan.
        if unassigned:
            for ll, share in zip(local_levels, split_by_weights(unassigned, [1] * len(local_levels))):
                required[ll] += share

        total = sum(required.values())
        federal, province, local = split_liability(plan, total)
        local_shares = dict(
            zip(local_levels, split_by_weights(
                local, [required[ll] for ll in local_levels]))
        )
        rounds.append(
            TrancheRound(
                tranche=tranche,
                stages=sorted(stages),
                required=required,
                total=total,
                federal=federal,
                province=province,
                local=local,
                local_shares=local_shares,
            )
        )

    return DistrictSchedule(
        name=district.name,
        code=district.code,
        province=province_name,
        province_code=province_code,
        region=district.region,
        local_levels=local_levels,
        local_level_codes=local_level_codes,
        rounds=rounds,
    )
