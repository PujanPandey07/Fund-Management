import re
import warnings
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


class DefaultLiabilityWarning(UserWarning):
    """The plan file has no government_liability, so the 50/30/20 default is used."""


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
    code: NonEmptyStr  # official code of the local level (e.g. '23001')
    wards: list[PositiveInt] = []  # empty list means the whole local level


class AffectedArea(StrictModel):
    district: NonEmptyStr  # official code of the district (e.g. '23')
    # empty means the whole district
    local_levels: list[AffectedLocalLevel] = []

    @model_validator(mode="after")
    def no_repeated_local_level(self):
        codes = repeated(norm(ll.code) for ll in self.local_levels)
        if codes:
            raise ValueError(
                f"district '{self.district}' lists a local level more than once: {codes}"
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
    code: NonEmptyStr


class Province(StrictModel):
    name: NonEmptyStr
    code: NonEmptyStr


class District(StrictModel):
    name: NonEmptyStr
    province: NonEmptyStr  # "belongs to" link: code of one of the provinces
    # Himal, Pahad or Terai (checked against the regions list later)
    region: NonEmptyStr
    code: NonEmptyStr


class LocalLevel(StrictModel):
    name: NonEmptyStr
    district: NonEmptyStr  # "belongs to" link: code of one of the districts
    code: NonEmptyStr


class Government(StrictModel):
    federal: Federal
    provinces: Annotated[list[Province], Field(min_length=1)]
    districts: Annotated[list[District], Field(min_length=1)]
    local_levels: Annotated[list[LocalLevel], Field(min_length=1)]

    def find_province(self, identifier: str) -> Province | None:
        target = norm(identifier)
        for p in self.provinces:
            if norm(p.name) == target or norm(p.code) == target:
                return p
        return None

    def find_district(self, identifier: str) -> District | None:
        target = norm(identifier)
        for d in self.districts:
            if norm(d.name) == target or norm(d.code) == target:
                return d
        return None

    def find_local_level(
        self, identifier: str, district_identifier: str | None = None
    ) -> list[LocalLevel]:
        target = norm(identifier)
        results = []
        target_dist = self.find_district(district_identifier) if district_identifier else None
        for l in self.local_levels:
            matches_name_or_code = norm(l.name) == target or norm(l.code) == target
            if not matches_name_or_code:
                continue
            if target_dist:
                # l.district is a district code; target_dist can be matched by code or name
                if norm(l.district) == norm(target_dist.code):
                    results.append(l)
            else:
                results.append(l)
        return results

    @model_validator(mode="after")
    def check_names_and_links(self):
        province_names = [norm(p.name) for p in self.provinces]
        district_names = [norm(d.name) for d in self.districts]
        province_codes = [norm(p.code) for p in self.provinces]
        district_codes = [norm(d.code) for d in self.districts]
        local_codes = [norm(l.code) for l in self.local_levels]

        # Check duplicate names and duplicate codes
        for kind, items in (
            ("province", province_names),
            ("district", district_names),
            ("province code", province_codes),
            ("district code", district_codes),
            ("local level code", local_codes),
        ):
            dupes = repeated(items)
            if dupes:
                raise ValueError(f"duplicate {kind}: {dupes}")

        # A code must identify exactly one unit across ALL levels, otherwise a lookup by
        # code (for example "P3") could silently pick the wrong government.
        owners: dict[str, set[str]] = {}
        spelling: dict[str, str] = {}
        coded = [("federal", self.federal.code)]
        coded += [("province", p.code) for p in self.provinces]
        coded += [("district", d.code) for d in self.districts]
        coded += [("local level", l.code) for l in self.local_levels]
        for level, code in coded:
            owners.setdefault(norm(code), set()).add(level)
            spelling.setdefault(norm(code), code)
        shared = sorted(code for code, levels in owners.items() if len(levels) > 1)
        if shared:
            raise ValueError(
                "code used at more than one administrative level: "
                + ", ".join(f"'{spelling[c]}' ({' and '.join(sorted(owners[c]))})" for c in shared)
            )

        # Within provinces, and within districts, a name must not equal another unit's code
        for kind, units in (("province", self.provinces), ("district", self.districts)):
            names = {norm(u.name): u.name for u in units}
            for u in units:
                if norm(u.code) in names and norm(u.code) != norm(u.name):
                    raise ValueError(
                        f"{kind} code '{u.code}' is also the name of {kind} "
                        f"'{names[norm(u.code)]}', so lookups would be ambiguous"
                    )

        # Check local level name uniqueness per district
        local_pairs = [(norm(l.district), norm(l.name)) for l in self.local_levels]
        dupes_local = repeated(local_pairs)
        if dupes_local:
            raise ValueError(f"duplicate local level (district, name): {dupes_local}")

        # "Belongs to" links must use codes
        province_codes_set = set(province_codes)
        district_codes_set = set(district_codes)

        for d in self.districts:
            if norm(d.province) not in province_codes_set:
                raise ValueError(
                    f"district '{d.name}' specifies province '{d.province}', "
                    "which is not a valid province code in this plan"
                )
        for l in self.local_levels:
            if norm(l.district) not in district_codes_set:
                raise ValueError(
                    f"local level '{l.name}' specifies district '{l.district}', "
                    "which is not a valid district code in this plan"
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
        default_factory=lambda: GovernmentLiability(
            federal=50, province=30, local=20)
    )

    @model_validator(mode="before")
    @classmethod
    def ignore_saved_plan_id(cls, data):
        """A saved plan contains its plan_id. The ID is always generated again, so drop it on input."""
        if isinstance(data, dict) and "plan_id" in data:
            data = {key: value for key, value in data.items() if key !=
                    "plan_id"}
        return data

    @model_validator(mode="before")
    @classmethod
    def warn_if_liability_defaulted(cls, data):
        """The liability split changes every money result, so a silent default is not safe.

        The default (50/30/20) is still used, but the person loading the plan is told.
        A plan saved with model_dump_json() always contains the field, so it never warns.
        """
        if isinstance(data, dict) and "government_liability" not in data:
            warnings.warn(
                "government_liability is missing from the plan, so the default split "
                "federal 50% / province 30% / local 20% is being used. "
                "Add government_liability to the plan file to set it explicitly.",
                DefaultLiabilityWarning,
                stacklevel=2,
            )
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

        # 5. Affected areas must use district codes and local level codes
        district_codes_set = {norm(d.code) for d in self.government.districts}
        for area in self.plan.affected_areas:
            if norm(area.district) not in district_codes_set:
                problems.append(
                    f"affected district '{area.district}' is not a known district code in government.districts")
                continue
            dist_obj = self.government.find_district(area.district)
            for ll in area.local_levels:
                matching_lls = self.government.find_local_level(ll.code, dist_obj.name)
                if not matching_lls:
                    problems.append(
                        f"affected local level '{ll.code}' is not in district '{area.district}'"
                    )

        if problems:
            raise ValueError("; ".join(problems))
        return self

    def is_affected(self, district: str, local_level: str | None = None) -> bool:
        """Is any part of this district (or local level) inside the plan's affected areas?

        Unlike covers(), wards are ignored: a local level listed with only some wards
        still counts as affected. Use it when the address has no ward (for example
        beneficiary counts grouped by local level). Accepts names or codes.
        """
        d_obj = self.government.find_district(district)
        dist_keys = {norm(district)}
        if d_obj:
            dist_keys.add(norm(d_obj.name))
            if d_obj.code:
                dist_keys.add(norm(d_obj.code))

        for area in self.plan.affected_areas:
            area_d = self.government.find_district(area.district)
            area_keys = {norm(area.district)}
            if area_d:
                area_keys.add(norm(area_d.name))
                if area_d.code:
                    area_keys.add(norm(area_d.code))

            if not (dist_keys & area_keys):
                continue
            if local_level is None or not area.local_levels:
                return True

            ll_keys = {norm(local_level)}
            if d_obj:
                for match in self.government.find_local_level(local_level, d_obj.name):
                    ll_keys.add(norm(match.name))
                    if match.code:
                        ll_keys.add(norm(match.code))

            for area_ll in area.local_levels:
                area_ll_keys = {norm(area_ll.code)}
                if area_d:
                    for match in self.government.find_local_level(area_ll.code, area_d.name):
                        area_ll_keys.add(norm(match.name))
                        if match.code:
                            area_ll_keys.add(norm(match.code))
                if ll_keys & area_ll_keys:
                    return True
        return False

    def covers(self, district: str, local_level: str | None = None, ward: int | None = None) -> bool:
        """Is this address inside the plan's affected areas? Accepts names or codes."""
        d_obj = self.government.find_district(district)
        dist_keys = {norm(district)}
        if d_obj:
            dist_keys.add(norm(d_obj.name))
            if d_obj.code:
                dist_keys.add(norm(d_obj.code))

        for area in self.plan.affected_areas:
            area_d = self.government.find_district(area.district)
            area_keys = {norm(area.district)}
            if area_d:
                area_keys.add(norm(area_d.name))
                if area_d.code:
                    area_keys.add(norm(area_d.code))

            if not (dist_keys & area_keys):
                continue
            if not area.local_levels:
                return True  # the whole district is listed
            if local_level is None:
                return True  # asked only about the district, and part of it is covered

            ll_keys = {norm(local_level)}
            if d_obj:
                for match in self.government.find_local_level(local_level, d_obj.name):
                    ll_keys.add(norm(match.name))
                    if match.code:
                        ll_keys.add(norm(match.code))

            for ll in area.local_levels:
                area_ll_keys = {norm(ll.code)}
                if area_d:
                    for match in self.government.find_local_level(ll.code, area_d.name):
                        area_ll_keys.add(norm(match.name))
                        if match.code:
                            area_ll_keys.add(norm(match.code))

                if not (ll_keys & area_ll_keys):
                    continue
                if not ll.wards or ward in ll.wards:
                    return True
        return False
