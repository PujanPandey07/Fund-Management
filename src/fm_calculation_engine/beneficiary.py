from fm_plan import ImplementationPlan
from fm_plan.models import NonEmptyStr, StrictModel, norm

from fm_calculation_engine.common import EngineInputError, match_name, parse


class BeneficiaryProfile(StrictModel):
    """Input: which plan, and which cell of the grant matrix this beneficiary falls in."""

    plan_id: NonEmptyStr
    region: NonEmptyStr
    disaster_type: NonEmptyStr
    solution_type: NonEmptyStr


class TrancheAmount(StrictModel):
    tranche: int  # 1, 2, 3, ...
    stage: str
    amount: int


class ReceivableAmounts(StrictModel):
    """Output: what the beneficiary receives in each tranche."""

    plan_id: str
    region: str
    disaster_type: str
    solution_type: str
    tranches: list[TrancheAmount]
    total: int


def receivable_amounts(plan: ImplementationPlan, profile) -> ReceivableAmounts:
    """Receivable amount for each tranche for one beneficiary."""
    p = parse(BeneficiaryProfile, profile)

    problems = []
    if norm(p.plan_id) != norm(plan.plan_id):
        problems.append(
            f"plan_id '{p.plan_id}' does not match this plan ('{plan.plan_id}')")
    region = match_name(p.region, plan.regions)
    if region is None:
        problems.append(
            f"region '{p.region}' is not in this plan's regions {plan.regions}")
    disaster = match_name(p.disaster_type, plan.disaster_types)
    if disaster is None:
        problems.append(
            f"disaster_type '{p.disaster_type}' is not in this plan's disaster types {plan.disaster_types}"
        )
    solution = match_name(p.solution_type, plan.solution_types)
    if solution is None:
        problems.append(
            f"solution_type '{p.solution_type}' is not in this plan's solution types {plan.solution_types}"
        )
    if problems:
        raise EngineInputError(
            f"{len(problems)} problem(s) in the input:\n" +
            "\n".join(f"  - {x}" for x in problems)
        )

    row = next(
        r for r in plan.grant_matrix
        if (norm(r.region), norm(r.disaster_type), norm(r.solution_type))
        == (norm(region), norm(disaster), norm(solution))
    )  # a row always exists: the plan guarantees every combination has one

    return ReceivableAmounts(
        plan_id=plan.plan_id,
        region=region,
        disaster_type=disaster,
        solution_type=solution,
        tranches=[
            TrancheAmount(tranche=number, stage=t.stage, amount=t.amount)
            for number, t in enumerate(row.tranches, start=1)
        ],
        total=row.total,
    )
