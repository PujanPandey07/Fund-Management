from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field, PositiveInt, field_validator, model_validator

# ASSUMPTIONS: kept together here so they are easy to find and change
STAGES = ("shelter", "main")  # what a tranche can be for

# A text value that cannot be empty
NonEmptyStr = Annotated[str, Field(min_length=1)]


class DateRange(BaseModel):
    start: date
    end: date | None = None  # None means there is no closing date

    @model_validator(mode="after")
    def end_not_before_start(self):
        if self.end is not None and self.end < self.start:
            raise ValueError("end date is before start date")
        return self


class AffectedLocalLevel(BaseModel):
    name: NonEmptyStr
    wards: list[PositiveInt] = []  # empty list means the whole local level


class AffectedArea(BaseModel):
    district: NonEmptyStr
    # empty means the whole district
    local_levels: list[AffectedLocalLevel] = []


class PlanDetails(BaseModel):
    plan_name: NonEmptyStr
    plan_version: PositiveInt
    disaster_name: NonEmptyStr
    disaster_date_range: DateRange
    effective_date_range: DateRange
    affected_areas: Annotated[list[AffectedArea], Field(min_length=1)]

###################################################################################################################


class Federal(BaseModel):
    name: NonEmptyStr


class Province(BaseModel):
    name: NonEmptyStr


class District(BaseModel):
    name: NonEmptyStr
    province: NonEmptyStr  # "belongs to" link: must be one of the provinces
    # Himal, Pahad or Terai (checked against the regions list later)
    region: NonEmptyStr


class LocalLevel(BaseModel):
    name: NonEmptyStr
    district: NonEmptyStr  # "belongs to" link: must be one of the districts


class Government(BaseModel):
    federal: Federal
    provinces: Annotated[list[Province], Field(min_length=1)]
    districts: Annotated[list[District], Field(min_length=1)]
    local_levels: Annotated[list[LocalLevel], Field(min_length=1)]

    @model_validator(mode="after")
    def check_names_and_links(self):
        province_names = [p.name for p in self.provinces]
        district_names = [d.name for d in self.districts]

        # No duplicates. A local level name may repeat in different districts,
        # so it is unique only together with its district.
        pairs = [(l.district, l.name) for l in self.local_levels]
        for kind, items in (
            ("province", province_names),
            ("district", district_names),
            ("local level (district, name)", pairs),
        ):
            repeated = sorted({i for i in items if items.count(i) > 1})
            if repeated:
                raise ValueError(f"duplicate {kind}: {repeated}")

        # "Belongs to" links must point at something that exists
        for d in self.districts:
            if d.province not in province_names:
                raise ValueError(
                    f"district '{d.name}' is in province '{d.province}', "
                    "which is not in the provinces list"
                )
        for l in self.local_levels:
            if l.district not in district_names:
                raise ValueError(
                    f"local level '{l.name}' is in district '{l.district}', "
                    "which is not in the districts list"
                )
        return self


class Tranche(BaseModel):
    stage: NonEmptyStr
    amount: PositiveInt  # whole rupees, must be above zero

    @field_validator("stage")
    @classmethod
    def stage_is_known(cls, value):
        if value not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}, got '{value}'")
        return value


class GrantRow(BaseModel):
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
