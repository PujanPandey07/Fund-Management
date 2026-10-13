# disaster-fund-management-engine

**Turn a validated disaster plan plus beneficiary counts into exact rupee figures: what a household receives, what each government owes, and what each district collects and forwards.**

It is the calculation half of the system. The plan itself (structure, grant rates, liability split) comes from [`disaster-fund-plan`](https://pypi.org/project/disaster-fund-plan/), which is installed automatically.

- Python 3.10 or newer
- Dependencies: `disaster-fund-plan`, `pydantic>=2`
- Import name: `fm_calculation_engine` (the PyPI name is `disaster-fund-management-engine`)

```bash
pip install disaster-fund-management-engine
```

---

## The three outputs at a glance

| Question you are asking | Function | Who asks it |
| --- | --- | --- |
| *"What does one household receive, tranche by tranche?"* | `receivable_amounts(plan, profile)` | Caseworker, beneficiary-facing app |
| *"How much does the Federal / a Province / a Local government owe, after donations?"* | `calculate_government_liability(plan, profile, counts)` | Finance office of that government |
| *"How much must a district collect from each level, and how much does it forward to each local government?"* | `calculate_district_fund(plan, profile, counts)` | District coordination office |

All three read the **same** internal schedule, so they can never disagree with each other (see [Guarantees](#guarantees)).

## Inputs

**1. The plan**: loaded with `disaster-fund-plan`:

```python
from fm_plan import load_plan
plan = load_plan("example_plan.json")
```

**2. Beneficiary counts** (outputs 2 and 3): how many households fall in each category. A list of dictionaries:

```python
counts = [
    {"district": "Gorkha",        "local_level": "Gorkha Municipality",
     "disaster_type": "Earthquake", "solution_type": "Reconstruction", "count": 10},
    {"district": "Sindhupalchok", "local_level": "Melamchi Municipality",
     "disaster_type": "Earthquake", "solution_type": "Reconstruction", "count": 20},
    {"district": "Sindhupalchok", "local_level": "Helambu Rural Municipality",
     "disaster_type": "Earthquake", "solution_type": "Retrofitting",   "count": 5},
]
```

| Field | Required | Notes |
| --- | --- | --- |
| `district` | yes | Name **or code** (`"Sindhupalchok"` or `"23"`) |
| `disaster_type`, `solution_type` | yes | Must exist in the plan; capital letters do not matter |
| `count` | yes | Number of households, `0` or more |
| `local_level` | no | Name or code. If omitted, see [use case 6](#6-counts-without-a-local-level) |
| `region` | no | Taken from the plan's district. If you supply it, it must match the plan |

**3. A profile** for the question you are asking (shown in each use case below). Every profile carries the `plan_id`, which must match the loaded plan: this stops results being calculated against the wrong plan version.

---

## Use cases

### 1. What does one household receive?

Given a household's region, disaster type and solution type, get its tranche schedule.

```python
from fm_calculation_engine import receivable_amounts

out = receivable_amounts(plan, {
    "plan_id": plan.plan_id,
    "region": "Pahad",
    "disaster_type": "Earthquake",
    "solution_type": "Reconstruction",
})

print(out.total)                       # 300000
for t in out.tranches:
    print(t.tranche, t.stage, t.amount)
# 1 shelter 15000
# 2 shelter 15000
# 3 main 100000
# 4 main 100000
# 5 main 70000
```

### 2. What does a government owe? (Federal, Province or Local)

The same function works for every level. The engine works out which level you mean from the name or code.

```python
from fm_calculation_engine import calculate_government_liability

fed = calculate_government_liability(
    plan,
    {"plan_id": plan.plan_id, "gov_entity_name": "Government of Nepal"},   # or "FED"
    counts,
)
print(fed.gov_type, fed.liability_percentage)   # federal 50.0
print(fed.total_gross_liability)                # 4750000
print(fed.total_net_payable)                    # 4750000 (no donations given)
```

Other levels, using the plan from the example file:

| `gov_entity_name` | Resolves to | Pays |
| --- | --- | --- |
| `"Government of Nepal"` or `"FED"` | federal | 50% of every district's cost |
| `"Gandaki Province"` or `"P4"` | province | 30%, **only** for districts inside that province (Gorkha) |
| `"Melamchi Municipality"` or `"23001"` | local | 20% of its district's cost, split between the district's local levels in proportion to what each needs |

Each result has a per-tranche, per-district breakdown (`fed.tranches[i].districts`) and a summary of who the beneficiaries are (`fed.beneficiary_summary`).

### 3. Apply donations, with automatic rollover

INGO, NGO and other donors give money to a government. It reduces that government's **cash outflow**, starting at tranche 1; whatever is not used rolls over to the next tranche.

```python
fed = calculate_government_liability(
    plan,
    {
        "plan_id": plan.plan_id,
        "gov_entity_name": "Government of Nepal",
        "donations": [
            {"donor_name": "Red Cross", "amount": 250000, "received_date": "2015-10-01"},
            {"donor_name": "UNICEF",    "amount": 150000, "received_date": "2015-10-05"},
        ],
    },
    counts,
)
for t in fed.tranches[:3]:
    print(t.tranche, t.gross_liability, t.donation_applied, t.net_payable, t.remaining_donation)
```

```text
tranche  gross      donation applied  net payable  donation left
1        350,000    350,000           0            50,000      ← 400,000 donated, tranche 1 consumes 350,000
2        350,000     50,000           300,000      0           ← the 50,000 left over rolls into tranche 2
3        1,500,000         0          1,500,000    0           ← donations are used up
```

Totals: Rs. 400,000 received, Rs. 4,750,000 gross, **Rs. 4,350,000 net cash payable**. The relief inside each tranche is shared across districts in proportion to what each district needs (`tranches[i].districts[j].donation_offset`).

Notes: `received_date` is optional and only affects the order donations are listed (oldest first). It does not decide *which tranche* a donation can pay for, because the plan has no tranche due dates.

### 4. What must a district collect, and whom does it forward to?

A district office needs two answers per tranche: how much to **collect** from Federal, Province and Local, and how much to **forward** to each local government.

```python
from fm_calculation_engine import calculate_district_fund

d = calculate_district_fund(
    plan,
    {"plan_id": plan.plan_id, "district_name": "Sindhupalchok"},   # or "23"
    counts,
)
print(d.province_name, d.total_beneficiaries, d.grand_total)   # Bagmati Province 25 6500000

t1 = d.tranches[0]
print(t1.collectible)    # federal=275000 province=165000 local=110000 total=550000
for x in t1.disbursements:
    print(x.local_gov, x.amount)
# Melamchi Municipality 300000
# Helambu Rural Municipality 250000
```

Tranche 1 here is 550,000 in total: Rs. 275,000 (50%) from Federal, Rs. 165,000 (30%) from the Province and Rs. 110,000 (20%) from Local, which is then forwarded as 300,000 and 250,000 to the two local governments according to their own beneficiaries.

### 5. Reconcile everything (audit and sanity check)

Because all outputs come from one schedule, they must add up. This is a useful check for auditors and for your own integration tests:

```python
levels = ["FED", "P3", "P4", "Gorkha Municipality", "Melamchi Municipality", "Helambu Rural Municipality"]
total_by_government = sum(
    calculate_government_liability(plan, {"plan_id": plan.plan_id, "gov_entity_name": g}, counts).total_gross_liability
    for g in levels
)
total_by_district = sum(
    calculate_district_fund(plan, {"plan_id": plan.plan_id, "district_name": d}, counts).grand_total
    for d in ("Gorkha", "Sindhupalchok")
)
assert total_by_government == total_by_district == 9_500_000
```

### 6. Counts without a local level

Sometimes only district-level totals are known. Leave `local_level` out:

```python
calculate_district_fund(plan, {"plan_id": plan.plan_id, "district_name": "23"},
    [{"district": "23", "disaster_type": "Earthquake", "solution_type": "Reconstruction", "count": 10}])
# tranche 1 -> Melamchi Municipality 75000, Helambu Rural Municipality 75000
```

The money for such households is shared **equally** between the district's local levels (odd rupees go to the first ones in the plan). For a local government's own report, these households are shown separately as `unassigned_beneficiaries` and are not included in `total_beneficiaries`.

### 7. Catch bad input with readable errors

Every problem in the input is reported at once, in plain words, as an `EngineInputError`:

```python
from fm_calculation_engine import EngineInputError

try:
    calculate_district_fund(plan, {"plan_id": plan.plan_id, "district_name": "Gorkha"}, bad_counts)
except EngineInputError as error:
    print(error)
```

```text
3 problem(s) in beneficiary counts:
  - item 0: district 'Kathmandu' is not in plan's districts ['Gorkha', 'Sindhupalchok']
  - item 0: disaster_type 'Flood' is not in plan's disaster types ['Earthquake']
  - item 1: region 'Terai' does not match district 'Gorkha', which is in region 'Pahad' in this plan
```

You can also validate a count list on its own, before calculating, and get it back normalised to the plan's spelling and codes:

```python
from fm_calculation_engine import validate_beneficiary_counts
clean = validate_beneficiary_counts(plan, counts)
```

### 8. A local government whose name exists in two districts

Nepal has repeated municipality names. If a local government's name appears in more than one district, the engine refuses to guess; say which district you mean:

```python
calculate_government_liability(plan, {
    "plan_id": plan.plan_id,
    "gov_entity_name": "Some Municipality",
    "district": "Sindhupalchok",
}, counts)
```

(`district` is only accepted for local governments. Using the official code, such as `"23001"`, avoids the ambiguity altogether.)

---

## Guarantees

- **Exact rupees, no drift.** Every split (liability shares, local-level shares, donation offsets) uses the *largest remainder method* on whole rupees: each part gets what it is owed, and leftover rupees go to the parts with the largest fractions. The parts always add up to the total; `round()` and floating point are never used for money.
- **One schedule per district.** District total = Federal + Province + Local share, and the local share = the sum of the shares given to its local levels, exactly. Outputs 2 and 3 therefore always reconcile.
- **Deterministic.** The same input always gives the same output (ties are broken by plan order).
- **Wrong plan is rejected.** A `plan_id` that does not match the loaded plan is an error.
- **Names or codes everywhere.** Districts, provinces, local levels and the Federal government can all be given by name or official code, in any capitalisation.

## API summary

| Name | Purpose |
| --- | --- |
| `receivable_amounts(plan, profile)` → `ReceivableAmounts` | Output 1: tranche schedule for one household |
| `calculate_government_liability(plan, profile, counts)` → `GovernmentLiabilityOutput` | Output 2: what a government owes, with donation offsets and rollover |
| `calculate_district_fund(plan, profile, counts)` → `DistrictOutput` | Output 3: what a district collects and forwards |
| `validate_beneficiary_counts(plan, counts)` | Check counts against the plan; returns normalised `BeneficiaryCount` objects |
| `EngineInputError` | Raised for any invalid input; the message lists every problem |
| `BeneficiaryProfile`, `GovEntityProfile`, `DistrictProfile`, `BeneficiaryCount`, `Donation` | Input models (a plain `dict` with the same keys works too) |

Profiles and counts can be passed as dictionaries or as these model objects. Unknown keys are rejected, so a typo is an error rather than being silently ignored.

## Limits

- Amounts are whole rupees.
- The Federal/Province/Local split is the same for every tranche and every stage (shelter and main).
- Donations are pooled per government and used from tranche 1 onward; they are not tied to a particular tranche or district.
- A tranche round exists up to the longest grant in the plan. A grant with fewer tranches simply has Rs. 0 in the later rounds.

## License

MIT
