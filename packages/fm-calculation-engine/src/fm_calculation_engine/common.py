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
    """A donation received by a government entity from INGO, NGO, or private/public source.

    Donations are pooled: the whole amount is available from tranche 1 and any surplus
    rolls over to the next tranche. `received_date` is recorded for transparency (the
    output lists donations oldest first) but does not decide which tranche a donation
    can pay for, because the plan does not give tranches due dates.
    """

    donor_name: NonEmptyStr
    amount: Annotated[int, Field(
        gt=0, description="Exact donation amount in whole rupees")]
    received_date: date | None = None


class BeneficiaryCount(StrictModel):
    """Beneficiary count for a specific category within a district (and optional local level).

    `district` and `local_level` accept either official names or codes.
    `region` is optional. A district belongs to exactly one region in the plan, so it
    is taken from the plan. If you do give it, it must match the plan's region for
    that district.
    """

    district: NonEmptyStr
    disaster_type: NonEmptyStr
    solution_type: NonEmptyStr
    count: Annotated[int, Field(
        ge=0, description="Number of beneficiary households")]
    region: NonEmptyStr | None = None
    local_level: NonEmptyStr | None = None
    district_code: str | None = None
    local_level_code: str | None = None


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
    """Check every count against the plan and return them with the plan's own spelling and codes.

    Accepts district and local level by name or code.
    A count is rejected when:
      - its district, disaster type or solution type is not in the plan;
      - its region does not match the region the plan gives that district;
      - its local level does not belong to its district;
      - its district (or local level) is not inside the plan's affected areas.

    In the returned counts `region` is always filled in, taken from the plan.
    """
    parsed_counts = [parse(BeneficiaryCount, c) for c in counts]
    problems: list[str] = []
    checked: list[BeneficiaryCount] = []

    for idx, b in enumerate(parsed_counts):
        district = plan.government.find_district(b.district)
        if district is None:
            problems.append(
                f"item {idx}: district '{b.district}' is not in plan's districts "
                f"{[d.name for d in plan.government.districts]}"
            )

        if b.region is not None:
            matched_region = match_name(b.region, plan.regions)
            if matched_region is None:
                problems.append(
                    f"item {idx}: region '{b.region}' is not in plan's regions {plan.regions}"
                )
            elif district is not None and norm(matched_region) != norm(district.region):
                problems.append(
                    f"item {idx}: region '{b.region}' does not match district '{district.name}', "
                    f"which is in region '{district.region}' in this plan"
                )

        disaster = match_name(b.disaster_type, plan.disaster_types)
        if disaster is None:
            problems.append(
                f"item {idx}: disaster_type '{b.disaster_type}' is not in plan's disaster types "
                f"{plan.disaster_types}"
            )

        solution = match_name(b.solution_type, plan.solution_types)
        if solution is None:
            problems.append(
                f"item {idx}: solution_type '{b.solution_type}' is not in plan's solution types "
                f"{plan.solution_types}"
            )

        local_level_obj = None
        if b.local_level and district is not None:
            matching_lls = plan.government.find_local_level(b.local_level, district.name)
            if not matching_lls:
                problems.append(
                    f"item {idx}: local level '{b.local_level}' does not belong to district "
                    f"'{district.name}' in this plan"
                )
            else:
                local_level_obj = matching_lls[0]

        local_level_name = local_level_obj.name if local_level_obj else None
        if district is not None and (local_level_name or not b.local_level):
            if not plan.is_affected(district.name, local_level_name):
                where = f"local level '{b.local_level}' in district '{district.name}'" if b.local_level else f"district '{district.name}'"
                problems.append(
                    f"item {idx}: {where} is not in this plan's affected areas")

        if district is not None and disaster and solution and (local_level_name or not b.local_level):
            checked.append(
                BeneficiaryCount(
                    district=district.name,
                    district_code=district.code,
                    region=district.region,
                    disaster_type=disaster,
                    solution_type=solution,
                    count=b.count,
                    local_level=local_level_name,
                    local_level_code=local_level_obj.code if local_level_obj else None,
                )
            )

    if problems:
        raise EngineInputError(
            f"{len(problems)} problem(s) in beneficiary counts:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    return checked
