# disaster-fund-plan

**Describe a disaster reconstruction plan in one JSON file, and get it checked before any money is calculated.**

`disaster-fund-plan` reads a plan file, validates it, and gives you one trusted `ImplementationPlan` object. It does **no money calculation**: it only answers *"what is the plan?"* The companion package [`disaster-fund-management-engine`](https://pypi.org/project/disaster-fund-management-engine/) takes this plan and works out the money.

- Python 3.10 or newer
- Only dependency: `pydantic>=2`
- Import name: `fm_plan` (the PyPI name is `disaster-fund-plan`)

```bash
pip install disaster-fund-plan
```

---

## What is a "plan"?

A plan is a single JSON file that says:

| Section | What it says | Example |
| --- | --- | --- |
| `plan` | Which disaster, which plan, when it applies, and which areas are affected | Gorkha Earthquake, Housing Reconstruction v1, districts Gorkha and Sindhupalchok |
| `government` | The administrative tree: Federal → Provinces → Districts → Local levels, each optionally with an official code | Sindhupalchok (`23`) is in Bagmati Province (`P3`) and is a `Pahad` district |
| `regions`, `disaster_types`, `solution_types` | The allowed categories | `Himal / Pahad / Terai`, `Earthquake`, `Reconstruction / Retrofitting` |
| `grant_matrix` | How much a household receives, tranche by tranche, for every region + disaster + solution combination | Pahad + Earthquake + Reconstruction: 15,000 + 15,000 (shelter), 100,000 + 100,000 + 70,000 (main) |
| `government_liability` | Who pays what share: Federal %, Province %, Local % (must total 100) | 50 / 30 / 20 |

A complete working example (`example_plan.json`) ships in the project repository; a minimal one is shown at the end of this README.

---

## Use cases

### 1. Check a plan file before you trust it (command line)

You have just received or edited a plan file and want to know whether it is consistent.

```bash
python -m fm_plan example_plan.json
```

```text
OK: plan_id = gorkha-earthquake-2015-housing-reconstruction-v1
  2 affected area(s), 2 district(s), 6 matrix row(s)
```

Exit code is `0` when the file is valid and `1` when it is not, so it also works as a check in a script or CI job. A bad file prints every problem it can find, with the path to the field:

```text
bad_plan.json has 1 problem(s):
  - government_liability: government liability percentages must sum to 100%, got 110.0%
```

The same command is installed as `fm-plan example_plan.json`.

### 2. Load a plan inside your application

Your backend needs a validated plan at start-up, and a clear error if the file is wrong.

```python
from fm_plan import load_plan, PlanLoadError

try:
    plan = load_plan("example_plan.json")
except PlanLoadError as error:
    print(error)          # human-readable list of problems
    raise SystemExit(1)

print(plan.plan_id)                      # gorkha-earthquake-2015-housing-reconstruction-v1
print(plan.government_liability.federal) # 50.0
```

`load_plan` accepts a path, understands files saved by Windows editors with a BOM, and turns JSON syntax errors into messages that include the line and column.

### 3. Find out whether an address is covered by the plan

A caseworker enters a household address and you must decide whether it falls inside the disaster-affected area. Districts and local levels can be given by **name or official code**, in any capitalisation.

```python
plan.covers("Sindhupalchok")                              # True  (district is listed)
plan.covers("23")                                         # True  (same district, by code)
plan.covers("Sindhupalchok", "Melamchi Municipality", 2)  # True  (ward 2 is listed)
plan.covers("Sindhupalchok", "Melamchi Municipality", 9)  # False (only wards 1, 2, 3 are affected)
plan.covers("Kathmandu")                                  # False (not in the plan)
```

- `covers(district, local_level=None, ward=None)` is exact, **including wards**.
- `is_affected(district, local_level=None)` ignores wards: use it when your data has no ward (for example, counts grouped by municipality).

In the plan file, leaving out `local_levels` means "the whole district"; leaving out `wards` means "the whole local level".

### 4. Look up what a household is entitled to

Read the grant for one combination straight from the matrix.

```python
row = next(
    r for r in plan.grant_matrix
    if (r.region, r.disaster_type, r.solution_type) == ("Pahad", "Earthquake", "Reconstruction")
)
print(row.total)                                  # 300000
print([(t.stage, t.amount) for t in row.tranches])
# [('shelter', 15000), ('shelter', 15000), ('main', 100000), ('main', 100000), ('main', 70000)]
```

The package guarantees the matrix has **no gaps** (every region × disaster × solution has exactly one row) and that **shelter tranches always come before main tranches**, so this lookup always succeeds for a valid combination. For a ready-made per-household schedule, use the engine package (its `receivable_amounts` function).

### 5. Resolve administrative units by name or code

Master data often arrives with codes in one system and names in another.

```python
gov = plan.government
gov.find_district("23").name       # 'Sindhupalchok'
gov.find_district("gorkha").code   # '36'
gov.find_province("P3").name       # 'Bagmati Province'
gov.find_local_level("23001")      # [LocalLevel(name='Melamchi Municipality', ...)]
```

`find_local_level` returns a list because two districts can legitimately have a local level with the same name; pass the district as the second argument to narrow it down.

### 6. Build a plan in code, save it, and load it again

You are generating plans from a form or database instead of writing JSON by hand.

```python
import json
from fm_plan import ImplementationPlan

plan = ImplementationPlan.model_validate(my_dict)   # raises pydantic.ValidationError if wrong
open("saved_plan.json", "w").write(plan.model_dump_json(indent=2))

same_plan = ImplementationPlan.model_validate_json(open("saved_plan.json").read())
assert same_plan.plan_id == plan.plan_id
```

A saved plan contains its `plan_id`, and it is **always re-derived** on load, so a hand-edited ID can never disagree with the plan's contents.

### 7. Set (and protect) the government liability split

The Federal/Province/Local percentages change every rupee the engine reports, so they are checked strictly: each between 0 and 100, together exactly 100.

If `government_liability` is **missing** from the file, the package uses 50 / 30 / 20 but emits a `DefaultLiabilityWarning` so the default is never silent:

```python
import warnings
from fm_plan import DefaultLiabilityWarning

warnings.simplefilter("error", DefaultLiabilityWarning)   # make a missing split a hard failure
```

---

## What gets validated

| Area | Rule |
| --- | --- |
| Dates | Disaster has a start **and** end date; end is not before start. The plan's effective date must start **after** the disaster ends. |
| Hierarchy | Every district names an existing province; every local level names an existing district. No duplicate province/district names or codes. |
| Codes | A code identifies exactly one unit across **all** levels (the Federal code `FED`, a province `P3`, a district `23` and a local level `23001` can never overlap). A province or district name may not equal another unit's code. Codes are compared as text, so `036` and `36` are different. |
| Affected areas | Every listed district and local level exists in the `government` section; no district or local level is listed twice. |
| Regions | Every district's region is in `regions` (`Himal`, `Pahad`, `Terai`). |
| Grant matrix | Every row uses known region / disaster / solution names; no repeated row; **no missing combination**. |
| Tranches | Amounts are whole rupees above zero; stage is `shelter` or `main`; shelter tranches come first. |
| Liability | Federal + Province + Local = 100%. |
| Whole file | Unknown keys are rejected (a typo such as `"distrcit"` is an error, not silently ignored). Names are compared ignoring capital letters. |

## Plan ID

The ID is computed, never typed:

```
<disaster name>-<disaster start year>-<plan name>-v<plan version>
Gorkha Earthquake, 2015, Housing Reconstruction, version 1  →  gorkha-earthquake-2015-housing-reconstruction-v1
```

Changing the plan version creates a new ID, so results from different plan versions can never be mixed up.

## Minimal plan file

```json
{
  "plan": {
    "plan_name": "Housing Reconstruction",
    "plan_version": 1,
    "disaster_name": "Gorkha Earthquake",
    "disaster_date_range": { "start": "2015-04-25", "end": "2015-05-12" },
    "effective_date_range": { "start": "2015-09-01", "end": null },
    "affected_areas": [ { "district": "Gorkha" } ]
  },
  "government": {
    "federal": { "name": "Government of Nepal", "code": "FED" },
    "provinces": [ { "name": "Gandaki Province", "code": "P4" } ],
    "districts": [ { "name": "Gorkha", "code": "36", "province": "Gandaki Province", "region": "Pahad" } ],
    "local_levels": [ { "name": "Gorkha Municipality", "code": "36001", "district": "Gorkha" } ]
  },
  "government_liability": { "federal": 50, "province": 30, "local": 20 },
  "regions": ["Pahad"],
  "disaster_types": ["Earthquake"],
  "solution_types": ["Reconstruction"],
  "grant_matrix": [
    {
      "region": "Pahad", "disaster_type": "Earthquake", "solution_type": "Reconstruction",
      "tranches": [
        { "stage": "shelter", "amount": 15000 },
        { "stage": "main", "amount": 100000 }
      ]
    }
  ]
}
```

## API summary

| Name | Purpose |
| --- | --- |
| `load_plan(path)` | Read, validate and return an `ImplementationPlan`. Raises `PlanLoadError`. |
| `PlanLoadError` | The file is missing, not valid JSON, or fails validation. The message lists each problem. |
| `ImplementationPlan` | The validated plan. Has `.plan_id`, `.plan`, `.government`, `.regions`, `.disaster_types`, `.solution_types`, `.grant_matrix`, `.government_liability`, `.covers()`, `.is_affected()`. |
| `DefaultLiabilityWarning` | Emitted when `government_liability` is missing and the 50/30/20 default is used. |

## Limits

- Amounts are whole rupees (no paisa).
- The plan describes *rates and structure*. Counts of actual beneficiaries, donations and all money calculations belong to `disaster-fund-management-engine`.

## License

MIT
