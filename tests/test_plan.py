import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fm_plan import ImplementationPlan, PlanLoadError, load_plan

SAMPLE = Path(__file__).parent.parent / "example_plan.json"


@pytest.fixture
def data():
    """A fresh copy of the valid sample file, as a dictionary we can change."""
    return json.loads(SAMPLE.read_text(encoding="utf-8-sig"))


def build(data):
    return ImplementationPlan.model_validate(data)


# --- the valid sample -------------------------------------------------------


def test_valid_sample_builds_and_makes_plan_id(data):
    plan = build(data)
    assert plan.plan_id == "gorkha-earthquake-2015-housing-reconstruction-v1"
    assert len(plan.grant_matrix) == 6


def test_grant_row_total_and_tranche_count(data):
    plan = build(data)
    row = next(
        r for r in plan.grant_matrix
        if r.region == "Pahad" and r.solution_type == "Reconstruction"
    )
    assert len(row.tranches) == 5
    assert row.total == 300000


# --- bad input must be rejected, with a message that says why ----------------

BAD_CASES = [
    ("missing combination",
     lambda d: d["grant_matrix"].pop(), "combination(s) missing"),
    ("unknown region in a row", lambda d: d["grant_matrix"][0].update(
        region="Mountain"), "unknown region"),
    ("repeated matrix row", lambda d: d["grant_matrix"].append(
        copy.deepcopy(d["grant_matrix"][0])), "is repeated"),
    ("negative amount", lambda d: d["grant_matrix"][0]["tranches"][0].update(
        amount=-5), "greater than 0"),
    ("unknown stage", lambda d: d["grant_matrix"][0]["tranches"][0].update(
        stage="rent"), "stage must be one of"),
    ("shelter after main", lambda d: d["grant_matrix"][0]["tranches"].append(
        {"stage": "shelter", "amount": 1000}), "must come first"),
    ("no tranches", lambda d: d["grant_matrix"]
     [0].update(tranches=[]), "at least 1 item"),
    ("province typo", lambda d: d["government"]["districts"][0].update(
        province="P99"), "is not a valid province code in this plan"),
    ("local level in unknown district", lambda d: d["government"]["local_levels"][0].update(
        district="999"), "is not a valid district code in this plan"),
    ("duplicate district", lambda d: d["government"]["districts"].append(
        copy.deepcopy(d["government"]["districts"][0])), "duplicate district"),
    ("district region not in regions", lambda d: d["government"]["districts"][0].update(
        region="Hill"), "not in regions"),
    ("duplicate in a list", lambda d: d["regions"].append(
        "himal"), "has duplicates"),
    ("affected district unknown", lambda d: d["plan"]["affected_areas"].append(
        {"district": "999"}), "is not a known district code in government.districts"),
    ("affected local level under wrong district", lambda d: d["plan"]["affected_areas"][0].update(
        local_levels=[{"code": "23001"}]), "is not in district"),
    ("same district listed twice", lambda d: d["plan"]["affected_areas"].append(
        {"district": "36"}), "more than once"),
    ("no affected areas", lambda d: d["plan"].update(
        affected_areas=[]), "at least 1 item"),
    ("plan version zero", lambda d: d["plan"].update(
        plan_version=0), "greater than 0"),
    ("disaster end missing", lambda d: d["plan"]["disaster_date_range"].pop(
        "end"), "Field required"),
    ("disaster end before start", lambda d: d["plan"]["disaster_date_range"].update(
        end="2015-04-01"), "end date is before start date"),
    ("effective starts before disaster ends", lambda d: d["plan"]["effective_date_range"].update(
        start="2015-05-01"), "must start after the disaster dates"),
    ("effective starts on disaster end day", lambda d: d["plan"]["effective_date_range"].update(
        start="2015-05-12"), "must start after the disaster dates"),
    ("misspelled optional key", lambda d: d["plan"]["affected_areas"][1].update(
        local_level=[]), "Extra inputs are not permitted"),
    ("government liability does not sum to 100", lambda d: d.update(
        government_liability={"federal": 50, "province": 30, "local": 10}), "must sum to 100%"),
    ("negative liability percentage", lambda d: d.update(
        government_liability={"federal": 110, "province": 0, "local": -10}), "greater than or equal to 0"),
]


@pytest.mark.parametrize(
    "label, mutate, expected", BAD_CASES, ids=[case[0] for case in BAD_CASES]
)
def test_bad_input_is_rejected(data, label, mutate, expected):
    mutate(data)
    with pytest.raises(ValidationError) as error:
        build(data)
    assert expected in str(error.value)


# --- things that must be accepted --------------------------------------------


def test_effective_end_date_is_optional(data):
    data["plan"]["effective_date_range"]["end"] = None
    build(data)  # must not raise


def test_names_ignore_capital_letters(data):
    data["plan"]["affected_areas"][1]["district"] = "23"
    data["government"]["districts"][0]["province"] = "p4"
    data["grant_matrix"][0]["region"] = "himal"
    data["grant_matrix"][0]["tranches"][0]["stage"] = "Shelter"
    build(data)  # must not raise


def test_same_local_level_name_allowed_in_different_districts(data):
    data["government"]["local_levels"].append(
        {"name": "Melamchi Municipality", "code": "36002", "district": "36"}
    )
    build(data)  # must not raise


def test_saved_plan_can_be_loaded_again(tmp_path):
    plan = load_plan(SAMPLE)
    path = tmp_path / "saved.json"
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    again = load_plan(path)
    assert again.plan_id == plan.plan_id
    assert again.model_dump() == plan.model_dump()


# --- the covers() helper -------------------------------------------------------


@pytest.mark.parametrize(
    "address, expected",
    [
        (("Gorkha", None, None), True),
        (("Gorkha", "Gorkha Municipality", 4), True),
        (("Sindhupalchok", "Melamchi Municipality", 2), True),
        (("Sindhupalchok", "Melamchi Municipality", 5), False),
        (("Sindhupalchok", "Melamchi Municipality", None), False),
        (("Sindhupalchok", "Helambu Rural Municipality", 7), True),
        (("Sindhupalchok", None, None), True),
        (("Sindhupalchok", "Gorkha Municipality", 1), False),
        (("Jhapa", None, None), False),
        (("sindhupalchok", "MELAMCHI MUNICIPALITY", 2), True),
    ],
)
def test_covers(data, address, expected):
    assert build(data).covers(*address) is expected


# --- the loader ------------------------------------------------------------------


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loader_reads_a_valid_file():
    assert load_plan(SAMPLE).plan_id.startswith("gorkha-earthquake-2015")


def test_loader_accepts_a_file_saved_with_bom(tmp_path, data):
    path = tmp_path / "bom.json"
    path.write_text(json.dumps(data), encoding="utf-8-sig")
    load_plan(path)  # must not raise


def test_loader_reports_missing_file(tmp_path):
    with pytest.raises(PlanLoadError, match="File not found"):
        load_plan(tmp_path / "nothing.json")


def test_loader_reports_invalid_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"plan": ', encoding="utf-8")
    with pytest.raises(PlanLoadError, match="not valid JSON"):
        load_plan(path)


def test_loader_lists_every_problem_at_once(tmp_path, data):
    data["grant_matrix"].pop()
    data["plan"]["affected_areas"].append({"district": "Jhapa"})
    path = write(tmp_path / "two_problems.json", data)
    with pytest.raises(PlanLoadError) as error:
        load_plan(path)
    message = str(error.value)
    assert "2 problem(s)" in message
    assert "combination(s) missing" in message
    assert "Jhapa" in message


# --- codes must identify exactly one unit ------------------------------------


def test_code_shared_by_federal_and_province_is_rejected(data):
    data["government"]["federal"]["code"] = "P3"
    with pytest.raises(ValidationError, match=r"'P3' \(federal and province\)"):
        build(data)


def test_code_shared_by_district_and_province_is_rejected(data):
    data["government"]["districts"][0]["code"] = "P3"
    with pytest.raises(ValidationError, match="more than one administrative level"):
        build(data)


def test_local_level_code_equal_to_district_code_is_rejected(data):
    data["government"]["local_levels"][0]["code"] = "23"
    with pytest.raises(ValidationError, match=r"'23' \(district and local level\)"):
        build(data)


def test_district_name_equal_to_another_districts_code_is_rejected(data):
    data["government"]["districts"][0]["name"] = "23"
    with pytest.raises(ValidationError):
        build(data)


def test_plan_using_only_codes_is_accepted(data):
    plan = build(data)
    assert plan.covers("23", "23001", 2)
    assert not plan.covers("23", "23001", 9)
    assert plan.is_affected("36")
