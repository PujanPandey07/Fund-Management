import re
from datetime import date
from itertools import product
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

# ASSUMPTIONS: kept together here so they are easy to find and change
STAGES = ("shelter", "main")  # what a tranche can be for
# Names are compared without caring about capital letters (see norm below).
# The effective dates must start after the disaster's end date (see PlanDetails).

# A text value that cannot be empty (spaces at the start and end are removed)
NonEmptyStr = Annotated[str, StringConstraints(
    strip_whitespace=True, min_length=1)]


class StrictModel(BaseModel):
    """Base for every template: a key that is not part of the template is an error."""

    model_config = ConfigDict(extra="forbid")


def norm(text: str) -> str:
    """Name used for comparing: 'Gorkha', 'GORKHA' and 'gorkha' all become 'gorkha'."""
    return text.casefold()


def repeated(items) -> list:
    """The values that appear more than once in a list."""
    items = list(items)
    return sorted({i for i in items if items.count(i) > 1})


class DateRange(StrictModel):
    start: date
    end: date | None = None  # None means there is no closing date

    @model_validator(mode="after")
    def end_not_before_start(self):
        if self.end is not None and self.end < self.start:
            raise ValueError("end date is before start date")
        return self


class ClosedDateRange(DateRange):
    end: date  # required: both the start and the end must be given


class AffectedLocalLevel(StrictModel):
    name: NonEmptyStr
    wards: list[PositiveInt] = []  # empty list means the whole local level


class AffectedArea(StrictModel):
    district: NonEmptyStr
    # empty means the whole district
    local_levels: list[AffectedLocalLevel] = []

    @model_validator(mode="after")
    def no_repeated_local_level(self):
        names = repeated(norm(ll.name) for ll in self.local_levels)
        if names:
            raise ValueError(
                f"district '{self.district}' lists a local level more than once: {names}"
            )
        return self


class PlanDetails(StrictModel):
    plan_name: NonEmptyStr
    plan_version: PositiveInt
    disaster_name: NonEmptyStr
    disaster_date_range: ClosedDateRange
    effective_date_range: DateRange
    affected_areas: Annotated[list[AffectedArea], Field(min_length=1)]

    @model_validator(mode="after")
    def effective_starts_after_disaster(self):
        disaster_end = self.disaster_date_range.end
        if self.effective_date_range.start <= disaster_end:
            raise ValueError(
                "effective_date_range must start after the disaster dates "
                f"(disaster ends {disaster_end}, plan starts {self.effective_date_range.start})"
            )
        return self

    @model_validator(mode="after")
    def no_repeated_district(self):
        keys = [norm(a.district) for a in self.affected_areas]
        names = sorted(
            {a.district for a in self.affected_areas if keys.count(norm(a.district)) > 1})
        if names:
            raise ValueError(
                f"affected_areas lists a district more than once: {names}")
        return self


class Federal(StrictModel):
    name: NonEmptyStr


class Province(StrictModel):
    name: NonEmptyStr


class District(StrictModel):
    name: NonEmptyStr
    province: NonEmptyStr  # "belongs to" link: must be one of the provinces
    # Himal, Pahad or Terai (checked against the regions list later)
    region: NonEmptyStr


class LocalLevel(StrictModel):
    name: NonEmptyStr
    district: NonEmptyStr  # "belongs to" link: must be one of the districts


class Government(StrictModel):
    federal: Federal
    provinces: Annotated[list[Province], Field(min_length=1)]
    districts: Annotated[list[District], Field(min_length=1)]
    local_levels: Annotated[list[LocalLevel], Field(min_length=1)]

    @model_validator(mode="after")
    def check_names_and_links(self):
        province_keys = [norm(p.name) for p in self.provinces]
        district_keys = [norm(d.name) for d in self.districts]

        # No duplicates. A local level name may repeat in different districts,
        # so it is unique only together with its district.
        pairs = [(norm(l.district), norm(l.name)) for l in self.local_levels]
        for kind, items in (
            ("province", province_keys),
            ("district", district_keys),
            ("local level (district, name)", pairs),
        ):
            dupes = repeated(items)
            if dupes:
                raise ValueError(f"duplicate {kind}: {dupes}")

        # "Belongs to" links must point at something that exists
        for d in self.districts:
            if norm(d.province) not in province_keys:
                raise ValueError(
                    f"district '{d.name}' is in province '{d.province}', "
                    "which is not in the provinces list"
                )
        for l in self.local_levels:
            if norm(l.district) not in district_keys:
                raise ValueError(
                    f"local level '{l.name}' is in district '{l.district}', "
                    "which is not in the districts list"
                )
        return self


class Tranche(StrictModel):
    stage: NonEmptyStr
    amount: PositiveInt  # whole rupees, must be above zero

    @field_validator("stage")
    @classmethod
    def stage_is_known(cls, value):
        if norm(value) not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}, got '{value}'")
        return norm(value)  # stored in lowercase


class GrantRow(StrictModel):
    region: NonEmptyStr
    disaster_type: NonEmptyStr
    solution_type: NonEmptyStr
    tranches: Annotated[list[Tranche], Field(min_length=1)]

    @property
    def total(self) -> int:
        return sum(t.amount for t in self.tranches)

    @model_validator(mode="after")
    def shelter_comes_first(self):
        seen_main = False
        for t in self.tranches:
            if t.stage == "main":
                seen_main = True
            elif seen_main:
                raise ValueError(
                    "a shelter tranche comes after a main tranche; "
                    "shelter tranches must come first"
                )
        return self


def slugify(text: str) -> str:
    """'Gorkha Earthquake' -> 'gorkha-earthquake'"""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class GovernmentLiability(StrictModel):
    federal: Annotated[float, Field(ge=0, le=100)]
    province: Annotated[float, Field(ge=0, le=100)]
    local: Annotated[float, Field(ge=0, le=100)]

    @model_validator(mode="after")
    def sum_to_100(self):
        total = self.federal + self.province + self.local
        if round(total, 4) != 100.0:
            raise ValueError(
                f"government liability percentages must sum to 100%, got {total}%"
            )
        return self


class ImplementationPlan(StrictModel):
    plan: PlanDetails
    government: Government
    regions: Annotated[list[NonEmptyStr], Field(min_length=1)]
    disaster_types: Annotated[list[NonEmptyStr], Field(min_length=1)]
    solution_types: Annotated[list[NonEmptyStr], Field(min_length=1)]
    grant_matrix: Annotated[list[GrantRow], Field(min_length=1)]
    government_liability: GovernmentLiability = Field(
        default_factory=lambda: GovernmentLiability(federal=50, province=30, local=20)
    )

    @model_validator(mode="before")
    @classmethod
    def ignore_saved_plan_id(cls, data):
        """A saved plan contains its plan_id. The ID is always generated again, so drop it on input."""
        if isinstance(data, dict) and "plan_id" in data:
            data = {key: value for key, value in data.items() if key !=
                    "plan_id"}
        return data

    @computed_field
    @property
    def plan_id(self) -> str:
        p = self.plan
        return (
            f"{slugify(p.disaster_name)}-{p.disaster_date_range.start.year}-"
            f"{slugify(p.plan_name)}-v{p.plan_version}"
        )

    @model_validator(mode="after")
    def check_across_sections(self):
        problems = []
        region_keys = {norm(r) for r in self.regions}
        disaster_keys = {norm(x) for x in self.disaster_types}
        solution_keys = {norm(x) for x in self.solution_types}

        # 1. The three lists have no duplicates
        for label, items in (
            ("regions", self.regions),
            ("disaster_types", self.disaster_types),
            ("solution_types", self.solution_types),
        ):
            dupes = repeated(norm(i) for i in items)
            if dupes:
                problems.append(f"{label} has duplicates: {dupes}")

        # 2. Every district has a region that is in the regions list
        for d in self.government.districts:
            if norm(d.region) not in region_keys:
                problems.append(
                    f"district '{d.name}' has region '{d.region}', which is not in regions"
                )

        # 3. Matrix rows use names from the lists, and no row is repeated
        seen = set()
        for row in self.grant_matrix:
            if norm(row.region) not in region_keys:
                problems.append(
                    f"matrix row uses unknown region '{row.region}'")
            if norm(row.disaster_type) not in disaster_keys:
                problems.append(
                    f"matrix row uses unknown disaster type '{row.disaster_type}'")
            if norm(row.solution_type) not in solution_keys:
                problems.append(
                    f"matrix row uses unknown solution type '{row.solution_type}'")
            key = (norm(row.region), norm(
                row.disaster_type), norm(row.solution_type))
            if key in seen:
                problems.append(
                    f"matrix row is repeated: {row.region} / {row.disaster_type} / {row.solution_type}"
                )
            seen.add(key)

        # 4. Every combination has a row (no gaps)
        missing = [
            combo
            for combo in product(self.regions, self.disaster_types, self.solution_types)
            if tuple(norm(c) for c in combo) not in seen
        ]
        if missing:
            shown = ", ".join(" / ".join(c) for c in missing[:10])
            more = f" (and {len(missing) - 10} more)" if len(missing) > 10 else ""
            problems.append(
                f"{len(missing)} matrix combination(s) missing: {shown}{more}")

        # 5. Affected areas exist in the government section
        district_names = {norm(d.name) for d in self.government.districts}
        local_pairs = {(norm(l.district), norm(l.name))
                       for l in self.government.local_levels}
        for area in self.plan.affected_areas:
            if norm(area.district) not in district_names:
                problems.append(
                    f"affected district '{area.district}' is not in government.districts")
                continue
            for ll in area.local_levels:
                if (norm(area.district), norm(ll.name)) not in local_pairs:
                    problems.append(
                        f"affected local level '{ll.name}' is not in district '{area.district}'"
                    )

        if problems:
            raise ValueError("; ".join(problems))
        return self

    def covers(self, district: str, local_level: str | None = None, ward: int | None = None) -> bool:
        """Is this address inside the plan's affected areas?"""
        for area in self.plan.affected_areas:
            if norm(area.district) != norm(district):
                continue
            if not area.local_levels:
                return True  # the whole district is listed
            if local_level is None:
                return True  # asked only about the district, and part of it is covered
            for ll in area.local_levels:
                if norm(ll.name) != norm(local_level):
                    continue
                if not ll.wards or ward in ll.wards:
                    return True
        return False
