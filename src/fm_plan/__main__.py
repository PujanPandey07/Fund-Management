import sys

from fm_plan.loader import PlanLoadError, load_plan


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m fm_plan <plan.json>")
        return 2
    try:
        plan = load_plan(sys.argv[1])
    except PlanLoadError as error:
        print(error)
        return 1
    print(f"OK: plan_id = {plan.plan_id}")
    print(
        f"  {len(plan.plan.affected_areas)} affected area(s), "
        f"{len(plan.government.districts)} district(s), "
        f"{len(plan.grant_matrix)} matrix row(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
