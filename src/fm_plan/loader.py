import json
from pathlib import Path

from pydantic import ValidationError

from fm_plan.models import ImplementationPlan


class PlanLoadError(Exception):
    """The input file could not be turned into a valid plan."""


def load_plan(path) -> ImplementationPlan:
    """Read a plan JSON file and return a checked ImplementationPlan."""
    path = Path(path)

    try:
        # utf-8-sig also accepts files that Windows editors save with a BOM
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise PlanLoadError(f"File not found: {path}") from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PlanLoadError(
            f"{path.name} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}"
        ) from None

    try:
        return ImplementationPlan.model_validate(data)
    except ValidationError as e:
        lines = []
        for err in e.errors():
            where = ".".join(str(part) for part in err["loc"]) or "plan"
            message = err["msg"].removeprefix("Value error, ")
            for problem in message.split("; "):
                lines.append(f"  - {where}: {problem}")
        raise PlanLoadError(
            f"{path.name} has {len(lines)} problem(s):\n" + "\n".join(lines)
        ) from None
