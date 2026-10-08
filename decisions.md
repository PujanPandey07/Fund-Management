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

---

## 2. Package 2: Calculation Engine (`fm_calculation_engine`)

### 2.1 Package Naming & Organization
- **Decision**: Standardized package name to `fm_calculation_engine` (resolving typos) and renamed `beneificary.py` to `beneficiary.py`.
- **Module Structure**:
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
  - `region`: Himal, Pahad, or Terai.
  - `disaster_type`: e.g. Earthquake, Flood.
  - `solution_type`: e.g. Reconstruction, Retrofitting.
  - `count`: Number of beneficiary households.
  - `local_level`: (Optional) Specific municipality or rural municipality name for fine-grained forwarding.

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

### 2.5 Donation Offsets & Multi-Tranche Rollover
- **Decision**: Donations from INGOs, NGOs, or public/private donors are transparently recorded with `donor_name`, `amount`, and `received_date`.
- **Liability Impact**:
  - A donation received by a government entity **offsets only that entity's cash outflow** (`net_payable`). The overall liability percentage and other governments' obligations remain unaffected.
- **Multi-Tranche Rollover**:
  - Donations accumulate across tranches.
  - If total donations exceed an entity's liability for Tranche 1, the surplus automatically **rolls over** to offset liability in Tranche 2, Tranche 3, etc.
- **District Offset Distribution**:
  - When a government entity applies donations in a tranche, the donation relief is proportionally reflected across the payable amounts to its beneficiary districts.

### 2.6 District Collection & Forwarding (Output 3)
- **Collection Pooling**:
  - Total required fund per tranche:
    $$F_{D, t} = \sum_{\text{beneficiaries in } D} \text{count} \times \text{grant\_amount}(r, dis, sol, t)$$
  - Collected according to the committed percentages:
    - Federal Collectible = $\text{round}(F_{D, t} \times P_{\text{fed}})$
    - Province Collectible = $\text{round}(F_{D, t} \times P_{\text{prov}})$
    - Local Collectible = $F_{D, t} - \text{Federal} - \text{Province}$ (guaranteeing exact sum equality).
- **Forwarding to Local Governments**:
  - Each local government receives funds on a **fixed-per-beneficiary basis**:
    $$\text{Forwarded Amount} = \sum_{b \in \text{local\_gov}} b.\text{count} \times \text{grant\_amount}_t$$
  - If counts omit specific local level breakdown:
    - Single local level in district $\to$ receives 100% of district funds.
    - Multiple local levels $\to$ allocated equally across local levels in that district.

---

## 3. Summary of System Interfaces

| Interface | Engine Function | Input | Output Key Details |
|---|---|---|---|
| **Output 1** | `receivable_amounts` | `BeneficiaryProfile` (plan_id, region, disaster, solution) | Tranche amounts (stage, amount) and total grant. |
| **Output 2** | `calculate_government_liability` | `GovEntityProfile` (plan_id, entity_name, donations) + `beneficiary_counts` | Gross liability, donation applied, net payable, remaining donation, district payments breakdown, and beneficiary summary per tranche. |
| **Output 3** | `calculate_district_fund` | `DistrictProfile` (plan_id, district_name) + `beneficiary_counts` | Collectibles from Fed/Prov/Local and list of forwarding amounts per local government for each tranche. |
