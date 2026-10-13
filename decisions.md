# Fund Management System: Architectural & Implementation Decisions

This document records the architectural and business logic decisions made for both packages in the Fund Management System:

- **Package 1 (`fm_plan`)**: Plan definition, master data, validation, and configuration.
- **Package 2 (`fm_calculation_engine`)**: Core calculation engine producing Outputs 1, 2, and 3.

---

## 1. Package 1: Plan Builder (`fm_plan`)

### 1.1 Government Liability Configuration

- **Decision**: Government liability percentages (Federal %, Province %, Local %) are configured **at the plan level** inside the Fund Management Plan configuration JSON (`ImplementationPlan.government_liability`).
- **Validation Rules**:
  - Each percentage must be non-negative ($\ge 0$).
  - The sum of Federal + Province + Local must equal exactly $100\%$.
  - The same percentage split applies across all tranches (both shelter and main stages).
- **Default Baseline**: Federal 50%, Province 30%, Local 20%.
- **No silent default**: if a plan file has no `government_liability`, the default is still used but loading raises a `DefaultLiabilityWarning`, because the split changes every money result. A plan saved with `model_dump_json()` always contains the field, so reloading it never warns. (Making the field required would be the strict alternative.)

### 1.2 Deterministic Plan ID

- **Decision**: `plan_id` is never manually typed or edited. It is automatically computed from plan details:
  $$\text{plan\_id} = \text{slug}(\text{disaster\_name}) - \text{start\_year} - \text{slug}(\text{plan\_name}) - \text{v}\{\text{plan\_version}\}$$
  Example: `gorkha-earthquake-2015-housing-reconstruction-v1`.
- Any existing `plan_id` in raw JSON is discarded upon load and re-derived to ensure integrity.

### 1.3 Government Hierarchy & Spatial Referential Integrity

- **Decision**: Hierarchy follows the Nepal administrative structure:
  $$\text{Federal} \longrightarrow \text{Provinces} \longrightarrow \text{Districts} \longrightarrow \text{Local Levels}$$
- **Integrity Constraints**:
  - Each District must reference a valid Province in `government.provinces`.
  - Each District must specify its ecological region (`Himal`, `Pahad`, or `Terai`).
  - Each Local Level must reference an existing District in `government.districts`.
  - Affected areas in `plan.affected_areas` must reference valid Districts and Local Levels from the `government` structure.

### 1.4 Grant Matrix Completeness

- **Decision**: The grant matrix requires **complete cartesian coverage**:
  $$\text{Regions} \times \text{Disaster Types} \times \text{Solution Types}$$
  Every combination must have exactly one defined row in `grant_matrix`. Gaps or duplicates are rejected during plan validation.

### 1.5 Tranche Structure and Ordering

- **Decision**: Tranches are explicitly tagged by stage (`"shelter"` or `"main"`).
- **Ordering Rule**: All temporary shelter tranches must strictly precede any main reconstruction/retrofitting tranches.
- **Currencies**: All amounts are whole integers in Nepalese Rupees (no decimals).

### 1.6 Unique Administrative Codes alongside Names
- **Decision**: Every government body (`Federal`, `Province`, `District`, `LocalLevel`) supports an optional unique administrative code (`code`, e.g. MoFAGA / CBS Geocodes: `P3`, `23`, `23001`) alongside its official `name`.
- **Referential Flexibility**:
  - Administrative links can use **either name or code**:
    - District `province` can specify the province name (e.g. `"Bagmati Province"`) or code (e.g. `"P3"`).
    - Local level `district` can specify the district name (e.g. `"Sindhupalchok"`) or code (e.g. `"23"`).
    - Affected areas can reference districts and local levels by name or code.
- **Uniqueness Constraints**:
  - Province codes, district codes, and local level codes must each be unique across the plan.

---

## 2. Package 2: Calculation Engine (`fm_calculation_engine`)

### 2.1 Package Naming & Organization

- **Decision**: Standardized package name to `fm_calculation_engine` (resolving typos) and renamed `beneificary.py` to `beneficiary.py`.
- **Module Structure**:
  - `core.py`: The shared calculation core. One schedule per district (required money per local level per tranche, and the federal/province/local split) that both `government.py` and `district.py` use, so the outputs cannot disagree. Also `split_by_weights`.
  - `common.py`: Shared models (`BeneficiaryCount`, `Donation`), input validation helpers, and `EngineInputError`.
  - `beneficiary.py`: Output 1 (Beneficiary receivable amounts).
  - `government.py`: Output 2 (Federal, Province, Local liabilities and donation offsets).
  - `district.py`: Output 3 (District collection pooling and local government disbursements).

### 2.2 Tranche Progression & Synchronization

- **Decision**: Universal / global tranche rounds:
  - Beneficiaries progress through tranches in unison: all beneficiaries complete Tranche 1 before the system initiates fund calls for Tranche 2.
  - For grant matrix combinations with fewer tranches (e.g., Retrofitting having 2 tranches while Reconstruction has 5), their tranche grant is Rs. 0 in subsequent rounds once their tranches are exhausted.

### 2.3 Beneficiary Counts Input

- **Decision**: Beneficiary counts are fixed for the entire plan duration.
- **Granularity**: Input per group:
  - `district`: District name.
  - `region`: (Optional) Himal, Pahad, or Terai. A district has exactly one region in the plan, so it is taken from the plan. If given, it must match the plan's region for that district, otherwise the count is rejected.
  - `disaster_type`: e.g. Earthquake, Flood.
  - `solution_type`: e.g. Reconstruction, Retrofitting.
  - `count`: Number of beneficiary households.
  - `local_level`: (Optional) Specific municipality or rural municipality name for fine-grained forwarding.
- **Validation against the plan**: a count is rejected if its district, disaster type or solution type is not in the plan, if its local level is not in its district, or if its district (or local level) is not inside the plan's affected areas. The check uses `plan.is_affected(...)`, which ignores wards: a local level listed with only some wards still counts as affected, because counts have no ward.
- Names in the returned counts always use the plan's own spelling.

### 2.4 Government Boundary & Payment Rules (Output 2)

- **Federal Government**:
  - Liable to **all affected districts** in the plan where beneficiaries reside.
  - Share: `government_liability.federal` (e.g., 50%).
- **Province Government**:
  - Liable **only to districts within its province boundary** (`district.province == province.name`).
  - Beneficiaries in districts outside this province are excluded from its liability.
  - Share: `government_liability.province` (e.g., 30%).
- **Local Government**:
  - Liable **only to its own district / local jurisdiction** (`local_level.district == district.name`).
  - Share: `government_liability.local` (e.g., 20%).
  - The district's local share is split between its local levels in proportion to what each one must pay out (largest remainder), so the local governments together owe exactly the district's local share.
  - A local-level name may exist in more than one district. Then the profile must say which one with `district`, otherwise it is rejected. `district` is not allowed for federal or province.
  - Households that did not name a local level are shared equally between the district's local levels (see 2.6). For a local government they are reported as `unassigned_beneficiaries` and are not part of `total_beneficiaries`.

### 2.5 Donation Offsets & Multi-Tranche Rollover

- **Decision**: Donations from INGOs, NGOs, or public/private donors are transparently recorded with `donor_name`, `amount`, and `received_date`.
- **Liability Impact**:
  - A donation received by a government entity **offsets only that entity's cash outflow** (`net_payable`). The overall liability percentage and other governments' obligations remain unaffected.
- **Timing rule**: donations are **pooled** and available from tranche 1. `received_date` is recorded and the output lists donations oldest first, but it does not hold a donation back from a tranche, because the plan gives tranches no due dates. To make dates matter, the plan would need a date per tranche.
- **Multi-Tranche Rollover**:
  - Donations accumulate across tranches.
  - If total donations exceed an entity's liability for Tranche 1, the surplus automatically **rolls over** to offset liability in Tranche 2, Tranche 3, etc.
- **District Offset Distribution**:
  - When a government entity applies donations in a tranche, the donation relief is proportionally reflected across the payable amounts to its beneficiary districts (largest remainder, so the district offsets add up exactly to the donation applied).

### 2.6 District Collection & Forwarding (Output 3)

- **Collection Pooling**:
  - Total required fund per tranche:
    $$F_{D, t} = \sum_{\text{beneficiaries in } D} \text{count} \times \text{grant\_amount}(r, dis, sol, t)$$
  - Collected according to the committed percentages. $F_{D, t}$ is split between Federal, Province and Local in proportion to $P_{\text{fed}} : P_{\text{prov}} : P_{\text{local}}$ using the **largest remainder method** on whole rupees: each part gets its whole rupees and the few left over go to the parts with the largest fractions (ties go to Federal, then Province, then Local). The three parts always add up to $F_{D, t}$, with no rounding drift. This replaces the earlier `round()` approach, whose results could differ between the government and district outputs for percentages that do not divide evenly.
  - The same split is used by the government output, so what the governments owe together always equals what the districts need.
- **Forwarding to Local Governments**:
  - Each local government receives funds on a **fixed-per-beneficiary basis**:
    $$\text{Forwarded Amount} = \sum_{b \in \text{local\_gov}} b.\text{count} \times \text{grant\_amount}_t$$
  - If counts omit specific local level breakdown:
    - Single local level in district $\to$ receives 100% of district funds.
    - Multiple local levels $\to$ allocated equally across local levels in that district (odd rupees go to the first local levels in the plan's order).
    - A district that has beneficiaries but no local levels in the plan's government section is rejected, because its money could not be forwarded (before, it was silently left out of the disbursements).

### 2.7 Dual Code & Name Querying and Reporting
- **Decision**: All calculation engine interfaces accept administrative bodies by **either code or name**, and report both in the outputs:
  - `DistrictProfile`: `district_name` accepts either name (e.g. `"Sindhupalchok"`) or code (e.g. `"23"`).
  - `GovEntityProfile`: `gov_entity_name` accepts name or code for Federal, Province (e.g. `"P3"`), or Local Level (e.g. `"23001"`).
  - `BeneficiaryCount`: `district` and `local_level` accept either name or code.
  - Output models include both identifiers:
    - `DistrictOutput` reports `district_name`, `district_code`, `province_name`, `province_code`.
    - `LocalGovDisbursement` reports `local_gov` and `local_gov_code`.
    - `GovDistrictPayment` reports `district` and `district_code`.
    - `GovernmentLiabilityOutput` reports `gov_entity_name` and `gov_entity_code`.

---

## 3. Summary of System Interfaces

| Interface    | Engine Function                  | Input                                                                       | Output Key Details                                                                                                                    |
| ------------ | -------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **Output 1** | `receivable_amounts`             | `BeneficiaryProfile` (plan_id, region, disaster, solution)                  | Tranche amounts (stage, amount) and total grant.                                                                                      |
| **Output 2** | `calculate_government_liability` | `GovEntityProfile` (plan_id, entity_name, donations) + `beneficiary_counts` | Gross liability, donation applied, net payable, remaining donation, district payments breakdown, and beneficiary summary per tranche. (Includes entity and district codes). |
| **Output 3** | `calculate_district_fund`        | `DistrictProfile` (plan_id, district_name) + `beneficiary_counts`           | Collectibles from Fed/Prov/Local and list of forwarding amounts per local government for each tranche. (Includes district, province, and local government codes). |
