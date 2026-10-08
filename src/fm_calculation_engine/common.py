from datetime import date
from typing import Annotated

from pydantic import Field, ValidationError

from fm_plan import ImplementationPlan
from fm_plan.models import NonEmptyStr, StrictModel, norm


class EngineInputError(Exception):
    """The input given to the engine is not valid."""


def parse(model_class, data):
    """Build model_class from a dictionary (or an existing object).

    A problem becomes an EngineInputError with one readable line per problem.
    """
    try:
        return model_class.model_validate(data)
    except ValidationError as error:
        lines = []
        for err in error.errors():
            where = ".".join(str(part) for part in err["loc"]) or "input"
            message = err["msg"].removeprefix("Value error, ")
            for problem in message.split("; "):
                lines.append(f"  - {where}: {problem}")
        raise EngineInputError(
            f"{len(lines)} problem(s) in the input:\n" + "\n".join(lines)
        ) from None


class Donation(StrictModel):
    """A donation received by a government entity from INGO, NGO, or private/public source."""

    donor_name: NonEmptyStr
    amount: Annotated[int, Field(gt=0, description="Exact donation amount in whole rupees")]
    received_date: date | None = None


class BeneficiaryCount(StrictModel):
    """Beneficiary count for a specific category within a district (and optional local level)."""

    district: NonEmptyStr
    region: NonEmptyStr
    disaster_type: NonEmptyStr
    solution_type: NonEmptyStr
    count: Annotated[int, Field(ge=0, description="Number of beneficiary households")]
    local_level: NonEmptyStr | None = None


def match_name(value: str, allowed: list[str]) -> str | None:
    """The name in `allowed` that equals `value`, ignoring capital letters, or None."""
    for name in allowed:
        if norm(name) == norm(value):
            return name
    return None


def validate_beneficiary_counts(
    plan: ImplementationPlan,
    counts: list[BeneficiaryCount | dict],
) -> list[BeneficiaryCount]:
    """Parse and validate that beneficiary counts match the plan's entities and categories."""
    parsed_counts = [parse(BeneficiaryCount, c) for c in counts]
    problems = []

    district_names = [d.name for d in plan.government.districts]
    local_pairs = {(norm(l.district), norm(l.name)) for l in plan.government.local_levels}

    for idx, b in enumerate(parsed_counts):
        matched_dist = match_name(b.district, district_names)
        if not matched_dist:
            problems.append(
                f"item {idx}: district '{b.district}' is not in plan's districts {district_names}"
            )

        matched_reg = match_name(b.region, plan.regions)
        if not matched_reg:
            problems.append(
                f"item {idx}: region '{b.region}' is not in plan's regions {plan.regions}"
            )

        matched_dis = match_name(b.disaster_type, plan.disaster_types)
        if not matched_dis:
            problems.append(
                f"item {idx}: disaster_type '{b.disaster_type}' is not in plan's disaster types {plan.disaster_types}"
            )

        matched_sol = match_name(b.solution_type, plan.solution_types)
        if not matched_sol:
            problems.append(
                f"item {idx}: solution_type '{b.solution_type}' is not in plan's solution types {plan.solution_types}"
            )

        if b.local_level and matched_dist:
            if (norm(matched_dist), norm(b.local_level)) not in local_pairs:
                problems.append(
                    f"item {idx}: local level '{b.local_level}' does not belong to district '{matched_dist}' in this plan"
                )

    if problems:
        raise EngineInputError(
            f"{len(problems)} problem(s) in beneficiary counts:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    return parsed_counts
