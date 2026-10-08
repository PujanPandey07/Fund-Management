# Fund Management: Plan Builder (`fm_plan`)

`fm_plan` is package 1 of the Fund Management system for disaster reconstruction. It reads **one JSON file** that describes a reconstruction plan, checks that everything in it is consistent, and builds one validated **Implementation Plan object**.

It does **no money calculation**. It only answers: _what is this plan?_ The calculation engine (package 2) takes the plan, plus beneficiary counts, and works out the required fund, each government's liability and what each district must collect.

## Contents

1. [What the plan answers](#1-what-the-plan-answers)
2. [Install](#2-install)
3. [How to use it](#3-how-to-use-it)
4. [Input file reference](#4-input-file-reference)
5. [What the package checks](#5-what-the-package-checks)
6. [Error messages](#6-error-messages)
7. [The plan object (output)](#7-the-plan-object-output)
8. [Using it in the overall system](#8-using-it-in-the-overall-system)
9. [Assumptions and limits](#9-assumptions-and-limits)
10. [Project layout and tests](#10-project-layout-and-tests)

---

## 1. What the plan answers

| Question                                                            | Part of the file                              |
| ------------------------------------------------------------------- | --------------------------------------------- |
| What disaster, and when is the plan in force?                       | `plan`                                        |
| Which places does the plan apply to?                                | `plan.affected_areas`                         |
| Which governments and districts take part, and who belongs to whom? | `government`                                  |
| Which regions, disaster types and solution types exist?             | `regions`, `disaster_types`, `solution_types` |
| How many tranches, and how much money in each?                      | `grant_matrix`                                |

The plan ID is **not** an input. It is generated from the plan details.

## 2. Install

Python 3.10 or newer is needed. From the repository root, with a virtual environment active:

```powershell
# Windows PowerShell
python -m venv fundenv
.\fundenv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

```bash
# macOS / Linux
python3 -m venv fundenv
source fundenv/bin/activate
pip install -e ".[dev]"
```

`-e` installs the package in "editable" mode, so changes to the code take effect immediately. `[dev]` also installs `pytest`.

## 3. How to use it

### Check a file from the command line

```powershell
python -m fm_plan example_plan.json
```

On success it prints the plan ID and a short summary:

```
OK: plan_id = gorkha-earthquake-2015-housing-reconstruction-v1
  2 affected area(s), 2 district(s), 6 matrix row(s)
```

On failure it lists every problem, and the exit code is 1.

### Use it from Python

```python
from fm_plan import load_plan, PlanLoadError

try:
    plan = load_plan("example_plan.json")
except PlanLoadError as error:
    print(error)          # a readable list of every problem
else:
    print(plan.plan_id)                      # gorkha-earthquake-2015-housing-reconstruction-v1
    print(plan.plan.plan_name)               # Housing Reconstruction
    print(plan.covers("Sindhupalchok", "Melamchi Municipality", 2))   # True
    print(plan.model_dump_json(indent=2))    # the whole plan as JSON, including plan_id
```

If you already have the data as a Python dictionary (for example from a web request), skip the file:

```python
from fm_plan import ImplementationPlan

plan = ImplementationPlan.model_validate(data)    # raises pydantic.ValidationError if invalid
```

## 4. Input file reference

All values in the sample file (`example_plan.json`) are made up. Only the shape matters.

### 4.1 Top level

| Key              | What it holds                                                            |
| ---------------- | ------------------------------------------------------------------------ |
| `plan`           | Plan details and affected areas                                          |
| `government`     | Federal, provinces, districts, local levels, and who belongs to whom     |
| `regions`        | List of region names                                                     |
| `disaster_types` | List of disaster type names                                              |
| `solution_types` | List of solution type names                                              |
| `grant_matrix`   | One row per region × disaster type × solution type, with tranche amounts |

### 4.2 `plan`

| Field                        | Type              | Required | Meaning                           | Rules                                                                 |
| ---------------------------- | ----------------- | -------- | --------------------------------- | --------------------------------------------------------------------- |
| `plan_name`                  | text              | Yes      | Name of the implementation plan   | Not empty                                                             |
| `plan_version`               | whole number      | Yes      | Revision of the plan (1, 2, ...)  | Above zero                                                            |
| `disaster_name`              | text              | Yes      | The disaster the plan responds to | Not empty                                                             |
| `disaster_date_range.start`  | date `YYYY-MM-DD` | Yes      | First day of the disaster         |                                                                       |
| `disaster_date_range.end`    | date              | Yes      | Last day of the disaster          | Not before `start`                                                    |
| `effective_date_range.start` | date              | Yes      | First day the plan is in force    | **After** `disaster_date_range.end`                                   |
| `effective_date_range.end`   | date or `null`    | No       | Last day the plan is in force     | `null` or missing means no closing date; otherwise not before `start` |
| `affected_areas`             | list              | Yes      | Where the plan applies            | At least one entry                                                    |

### 4.3 `plan.affected_areas[]`

Each entry starts with a district and can be narrowed to local levels and wards, depending on how much detail is known.

| Field                  | Type                  | Required | Meaning                                | Rules                                                                       |
| ---------------------- | --------------------- | -------- | -------------------------------------- | --------------------------------------------------------------------------- |
| `district`             | text                  | Yes      | District name                          | Must exist in `government.districts`; listed only once                      |
| `local_levels`         | list                  | No       | Narrows the area to these local levels | Empty or missing means the **whole district**                               |
| `local_levels[].name`  | text                  | Yes      | Local level name                       | Must belong to that district in `government.local_levels`; listed only once |
| `local_levels[].wards` | list of whole numbers | No       | Narrows to these wards                 | Empty or missing means the **whole local level**; each number above zero    |

### 4.4 `government`

| Field          | Type               | Required        | Meaning                                        |
| -------------- | ------------------ | --------------- | ---------------------------------------------- |
| `federal`      | object with `name` | Yes             | The federal government (one only)              |
| `provinces`    | list               | Yes, at least 1 | Province governments                           |
| `districts`    | list               | Yes, at least 1 | Districts                                      |
| `local_levels` | list               | Yes, at least 1 | Local levels. Each is also a local government. |

| Entry            | Field      | Type | Meaning                    | Rules                                                                        |
| ---------------- | ---------- | ---- | -------------------------- | ---------------------------------------------------------------------------- |
| `provinces[]`    | `name`     | text | Province name              | Unique                                                                       |
| `districts[]`    | `name`     | text | District name              | Unique                                                                       |
| `districts[]`    | `province` | text | The province it belongs to | Must be in `provinces`                                                       |
| `districts[]`    | `region`   | text | Himal, Pahad or Terai      | Must be in `regions`                                                         |
| `local_levels[]` | `name`     | text | Local level name           | Unique within its district (the same name may appear in different districts) |
| `local_levels[]` | `district` | text | The district it belongs to | Must be in `districts`                                                       |

The `province` on a district and the `district` on a local level are the **"belongs to" links**. They are how the system knows which province pays into which district, and which local levels a district hands money to.

### 4.5 Lists

| Key              | Example values                                          | Rules                       |
| ---------------- | ------------------------------------------------------- | --------------------------- |
| `regions`        | `Himal`, `Pahad`, `Terai`                               | At least one, no duplicates |
| `disaster_types` | `Earthquake`, `Fire`, `Flood`, `Multi-hazard`           | At least one, no duplicates |
| `solution_types` | `Reconstruction`, `Retrofitting`, `Relocation`, `Other` | At least one, no duplicates |

### 4.6 `grant_matrix[]`

Each row is one cell of the region × disaster × solution table.

| Field               | Type         | Required | Meaning                 | Rules                                                                       |
| ------------------- | ------------ | -------- | ----------------------- | --------------------------------------------------------------------------- |
| `region`            | text         | Yes      | Region                  | Must be in `regions`                                                        |
| `disaster_type`     | text         | Yes      | Disaster type           | Must be in `disaster_types`                                                 |
| `solution_type`     | text         | Yes      | Solution type           | Must be in `solution_types`                                                 |
| `tranches`          | list         | Yes      | The payments, in order  | At least 1. The length of the list is the number of tranches.               |
| `tranches[].stage`  | text         | Yes      | What the payment is for | `shelter` or `main`. All `shelter` tranches come before any `main` tranche. |
| `tranches[].amount` | whole number | Yes      | Amount in rupees        | Above zero                                                                  |

The whole `grant_matrix` must contain **exactly one row for every combination** of region, disaster type and solution type.

Example row: Pahad, Earthquake, Reconstruction is 15,000 + 15,000 (shelter) and 100,000 + 100,000 + 70,000 (main), which is 5 tranches totalling 300,000.

```json
{
  "region": "Pahad",
  "disaster_type": "Earthquake",
  "solution_type": "Reconstruction",
  "tranches": [
    { "stage": "shelter", "amount": 15000 },
    { "stage": "shelter", "amount": 15000 },
    { "stage": "main", "amount": 100000 },
    { "stage": "main", "amount": 100000 },
    { "stage": "main", "amount": 70000 }
  ]
}
```

### 4.7 General rules for the whole file

| Rule                            | Meaning                                                                                                       |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Unknown keys are errors         | A misspelled key, such as `local_level` instead of `local_levels`, is rejected. It is never silently ignored. |
| Names ignore capital letters    | `Gorkha`, `GORKHA` and `gorkha` count as the same name. They are stored as typed.                             |
| Spaces around names are removed | `"Gorkha "` becomes `"Gorkha"`                                                                                |
| Names cannot be empty           |                                                                                                               |

## 5. What the package checks

| Area           | Check                                                                                                                        |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Dates          | A date range's end is not before its start                                                                                   |
| Dates          | The disaster end date is given                                                                                               |
| Dates          | The plan's effective start is after the disaster end date                                                                    |
| Plan           | `plan_version` is above zero                                                                                                 |
| Affected areas | At least one area; no district listed twice; no local level listed twice within a district                                   |
| Affected areas | Every district and local level exists in `government`, with the local level under the right district                         |
| Government     | No duplicate provinces or districts; no duplicate local level within a district                                              |
| Government     | Every district names a province that exists; every local level names a district that exists                                  |
| Lists          | `regions`, `disaster_types` and `solution_types` are not empty and have no duplicates                                        |
| Districts      | Every district's `region` is in `regions`                                                                                    |
| Grant matrix   | Every row uses names that are in the three lists                                                                             |
| Grant matrix   | No row is repeated                                                                                                           |
| Grant matrix   | **Every region × disaster × solution combination has a row**                                                                 |
| Tranches       | At least one tranche per row; stage is `shelter` or `main`; shelter tranches come first; amount is a whole number above zero |

## 6. Error messages

All problems are collected and reported together, so a file can be fixed in one pass. Each line reads `where: problem`. In the location, a number is a position in a list (starting from 0).

```
broken.json has 2 problem(s):
  - plan: 1 matrix combination(s) missing: Terai / Earthquake / Retrofitting
  - plan: affected district 'Jhapa' is not in government.districts
```

```
broken.json has 2 problem(s):
  - plan.plan_version: Input should be greater than 0
  - grant_matrix.0.tranches.0.amount: Input should be greater than 0
```

```
broken.json is not valid JSON (line 37, column 13): Expecting ':' delimiter
```

`plan:` means the problem involves more than one part of the file.

## 7. The plan object (output)

`load_plan(...)` returns an `ImplementationPlan`. If you hold one, its data already passed every check above.

| Attribute or method                             | What it gives                                                                                                                                                                                                         |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `plan_id`                                       | Generated ID, for example `gorkha-earthquake-2015-housing-reconstruction-v1`. Built from disaster name, disaster start year, plan name and version, in lowercase letters and digits joined by dashes. Never typed in. |
| `plan`                                          | The plan details: names, version, dates, affected areas                                                                                                                                                               |
| `government`                                    | `federal`, `provinces`, `districts`, `local_levels`                                                                                                                                                                   |
| `regions`, `disaster_types`, `solution_types`   | The three lists                                                                                                                                                                                                       |
| `grant_matrix`                                  | The rows. Each row has `region`, `disaster_type`, `solution_type`, `tranches`, and `total` (the sum of its tranche amounts).                                                                                          |
| `covers(district, local_level=None, ward=None)` | `True` if that address is inside the affected areas (see below)                                                                                                                                                       |
| `model_dump_json()`                             | The whole plan as JSON, including `plan_id`                                                                                                                                                                           |

**How `covers` answers:**

| Address                                          | Result                                                                             |
| ------------------------------------------------ | ---------------------------------------------------------------------------------- |
| District listed with no local levels             | `True` for any local level and ward in it                                          |
| Local level listed with no wards                 | `True` for any ward in it                                                          |
| Local level listed with wards                    | `True` only for those wards. With no ward given, `False` (it cannot be confirmed). |
| Only a district given, and part of it is covered | `True`                                                                             |
| Anything else                                    | `False`                                                                            |

**Saving and reloading.** A plan saved with `model_dump_json()` can be loaded again with `load_plan`. The saved `plan_id` is ignored on input and generated again, so it always matches the plan details.

## 8. Using it in the overall system

### 8.1 Where it sits

```
 Frontend form                 fm_plan (this package)             Engine (package 2)
 ┌────────────────┐   JSON    ┌──────────────────────┐   plan    ┌────────────────────────┐
 │ cascading      │ ────────► │ validate + build the │ ────────► │ required fund per      │
 │ dropdowns,     │           │ Implementation Plan  │  object   │ tranche, government    │
 │ matrix entry   │ ◄──────── │ object               │           │ liability, district    │
 └────────────────┘  errors   └──────────────────────┘           │ collection             │
                                                                  └───────────▲────────────┘
                                                                              │ beneficiary counts
                                                                       Beneficiary data
```

| Part of the system | Role                                                                                                                                                                                                                                                       |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend form      | Lets an officer choose places with cascading dropdowns (province, district, local level, ward) and fill in the grant matrix. The same picks produce both `affected_areas` and the `government` list with its "belongs to" links. The form builds the JSON. |
| `fm_plan`          | Validates what the form sends, as a safety net, and builds the plan object                                                                                                                                                                                 |
| Engine (package 2) | Reads the plan object together with beneficiary data and does the calculations                                                                                                                                                                             |

### 8.2 What the engine takes from the plan

| The engine needs                                   | Where to find it in the plan                                                       |
| -------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Is a beneficiary's address covered by the plan?    | `plan.covers(district, local_level, ward)`                                         |
| A beneficiary's region (Himal, Pahad, Terai)       | The `region` of their district in `government.districts`                           |
| The tranches for that beneficiary                  | The `grant_matrix` row for their region, the disaster type and their solution type |
| Number of tranches, and amount of each             | `len(row.tranches)` and `row.tranches[i].amount`                                   |
| Total grant                                        | `row.total`                                                                        |
| Which province pays into which district            | The `province` on each district                                                    |
| Which local levels a district hands money to       | The local levels whose `district` matches                                          |
| Which stage a tranche belongs to (shelter or main) | `row.tranches[i].stage`                                                            |

The plan object holds the data. Convenience lookups for these (for example "give me the tranches for this region, disaster and solution") are not part of this version. The engine can read the lists directly, or small helpers can be added to the plan object later.

### 8.3 Ways to connect it

| Option                 | How                                                                                                    | When it fits                                       |
| ---------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------- |
| Import the package     | `from fm_plan import load_plan` inside the engine                                                      | The engine is Python and lives in the same project |
| Pass JSON              | Save the plan with `plan.model_dump_json()` and give it to the engine, which loads it with `load_plan` | The engine runs separately                         |
| Validate in a web view | Call `ImplementationPlan.model_validate(request_data)` when a form is submitted                        | A web backend receives the plan                    |

A sketch of the web view option in Django REST Framework (illustrative only, not part of the package):

```python
import json
from pydantic import ValidationError
from rest_framework.response import Response
from fm_plan import ImplementationPlan

def create_plan(request):
    try:
        plan = ImplementationPlan.model_validate(request.data)
    except ValidationError as error:
        return Response({"errors": json.loads(error.json(include_url=False))}, status=400)
    # store plan.model_dump_json() and plan.plan_id, then return them
    return Response({"plan_id": plan.plan_id})
```

### 8.4 Plan versions

Revising a plan means creating a new file with a higher `plan_version`. The new plan gets its own ID, ending in `-v2`, `-v3` and so on, and its own grant matrix and dates. The package sees one file at a time, so it does not check whether an ID already exists among stored plans. That check belongs to whatever stores the plans.

## 9. Assumptions and limits

| Assumption or limit                                            | Detail                                                                                                                                                                                              |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Every region × disaster × solution combination applies         | All are required. A "not applicable" option can be added if some do not.                                                                                                                            |
| Every district has exactly one region                          | Himal, Pahad or Terai                                                                                                                                                                               |
| Temporary shelter is a stage inside each grant                 | Tranches are labelled `shelter` or `main`, and shelter comes first                                                                                                                                  |
| Everyone in the same grid cell gets the same amount            | Damage level decides the solution type, not the amount                                                                                                                                              |
| Amounts are whole rupees                                       | Decimals are rejected                                                                                                                                                                               |
| Names use English letters for the plan ID                      | A name written only in Devanagari would give an empty part in the ID                                                                                                                                |
| The package checks consistency, not truth                      | If the file says a district belongs to the wrong province but the province exists, it is accepted. A trusted master list of places, such as the form's dropdowns, is what guarantees correct facts. |
| The government list and the affected areas are separate inputs | The package checks that the affected areas exist in the government list. It does not check that the government list contains every local level of an affected district.                             |
| `covers` with a ward-limited local level and no ward           | Returns `False`                                                                                                                                                                                     |

## 10. Package 2: Calculation Engine (`fm_calculation_engine`)

The calculation engine consumes an `ImplementationPlan` alongside beneficiary counts and donations to produce the three primary system outputs.

### 10.1 Output 1: Beneficiary (`receivable_amounts`)
Calculates the receivable tranche amounts and total grant for an individual beneficiary profile based on their geographical region, disaster type, and solution type.

```python
from fm_calculation_engine import receivable_amounts

profile = {
    "plan_id": plan.plan_id,
    "region": "Pahad",
    "disaster_type": "Earthquake",
    "solution_type": "Reconstruction"
}
result = receivable_amounts(plan, profile)
print(result.total)         # 300000
for t in result.tranches:
    print(f"Tranche {t.tranche} ({t.stage}): Rs. {t.amount}")
```

### 10.2 Output 2: Government Liability (`calculate_government_liability`)
Calculates the liability for Federal, Provincial, or Local government across tranches:
- **Boundary rules**: Federal is liable for all affected districts; Province is liable only for districts within its province boundary; Local government is liable only for its own district/local level.
- **Donation offset & rollover**: Donations received from INGO/NGO/donors offset the entity's cash liability. Surplus donations automatically roll over across successive tranches.
- **District breakdown**: Details the exact gross and net amounts payable to each district per tranche.
- **Beneficiary summary**: Reports total beneficiaries broken down by region, disaster type, and solution type.

```python
from fm_calculation_engine import calculate_government_liability

gov_profile = {
    "plan_id": plan.plan_id,
    "gov_entity_name": "Government of Nepal",   # or Province or Local Gov name
    "donations": [
        {"donor_name": "Red Cross", "amount": 100000, "received_date": "2015-10-01"}
    ]
}
beneficiary_counts = [
    {
        "district": "Gorkha",
        "local_level": "Gorkha Municipality",
        "region": "Pahad",
        "disaster_type": "Earthquake",
        "solution_type": "Reconstruction",
        "count": 10
    }
]

out = calculate_government_liability(plan, gov_profile, beneficiary_counts)
print(f"Gov Type: {out.gov_type}, Liability %: {out.liability_percentage}%")
print(f"Total Gross: {out.total_gross_liability}, Net Payable: {out.total_net_payable}")
for t in out.tranches:
    print(f"Tranche {t.tranche}: Gross={t.gross_liability}, Donation Used={t.donation_applied}, Net={t.net_payable}")
```

### 10.3 Output 3: District (`calculate_district_fund`)
Calculates the collectible funds and local government forwarding responsibilities for a specific district:
- **Collectibles**: Sums required tranche funds and splits collections by Federal %, Province %, and Local % liability.
- **Local Government Disbursements**: Breaks down funds to be forwarded to each local government under that district based on beneficiary counts and tranche amounts.

```python
from fm_calculation_engine import calculate_district_fund

dist_profile = {
    "plan_id": plan.plan_id,
    "district_name": "Sindhupalchok"
}

district_out = calculate_district_fund(plan, dist_profile, beneficiary_counts)
for t in district_out.tranches:
    print(f"Tranche {t.tranche} Total: Rs. {t.total_amount}")
    print(f"  Collectible - Fed: {t.collectible.federal}, Prov: {t.collectible.province}, Local: {t.collectible.local}")
    for d in t.disbursements:
        print(f"  Disburse to {d.local_gov}: Rs. {d.amount}")
```

## 11. Project layout and tests

```
Fund-Management/
├── README.md
├── pyproject.toml
├── example_plan.json               Sample input with government liability %
├── src/
│   ├── fm_plan/                    Package 1: Plan models, loader, validation
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── loader.py
│   │   └── models.py
│   └── fm_calculation_engine/      Package 2: Core calculation engine
│       ├── __init__.py
│       ├── common.py               Shared models (Donation, BeneficiaryCount, parse)
│       ├── beneficiary.py          Output 1: Beneficiary receivable amounts
│       ├── government.py           Output 2: Government liability & donation rollover
│       └── district.py             Output 3: District collectible & disbursement
└── tests/
    ├── test_plan.py                45 tests for plan validation
    └── test_engines.py             12 tests for calculation engine outputs
```

Run all tests:

```powershell
.\fundenv\Scripts\pytest.exe
```

