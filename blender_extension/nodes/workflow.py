"""Post-mutation workflow assertions."""

from __future__ import annotations

from ..errors import BlenderMCPAddonError


def _blendermcp_check_workflow_assertions(stats, assertions):
    """Evaluate a deliberately small assertion language against dry-run stats."""
    if assertions is None:
        assertions = []
    if not isinstance(assertions, list) or len(assertions) > 32:
        raise BlenderMCPAddonError(
            "invalid_request", "assertions must be a list containing at most 32 items"
        )
    allowed_fields = {"node_count", "link_count", "interface_item_count"}
    comparisons = {
        "eq": lambda actual, expected: actual == expected,
        "gte": lambda actual, expected: actual >= expected,
        "lte": lambda actual, expected: actual <= expected,
    }
    results = []
    for index, assertion in enumerate(assertions):
        if not isinstance(assertion, dict):
            raise BlenderMCPAddonError(
                "invalid_request", f"assertions[{index}] must be an object"
            )
        field = assertion.get("field")
        operator = assertion.get("op", "eq")
        expected = assertion.get("value")
        if field not in allowed_fields or operator not in comparisons:
            raise BlenderMCPAddonError(
                "invalid_request",
                f"assertions[{index}] must use a supported stats field and eq/gte/lte",
            )
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise BlenderMCPAddonError(
                "invalid_request", f"assertions[{index}].value must be a non-negative integer"
            )
        actual = int(stats.get(field, -1))
        passed = comparisons[operator](actual, expected)
        results.append({
            "field": field,
            "op": operator,
            "expected": expected,
            "actual": actual,
            "passed": passed,
        })
    return results
