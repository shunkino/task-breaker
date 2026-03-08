"""Formula evaluator for max_tasks_per_level setting."""
import re
from typing import Optional


def evaluate_max_tasks_formula(formula: str, level: int) -> Optional[int]:
    """
    Evaluate a formula for max tasks at a given level.

    Args:
        formula: Formula string, e.g., "5-L", "10", "auto", "3*L+2"
        level: Current task level

    Returns:
        Maximum number of tasks (None for "auto" mode, meaning let LLM decide)

    Examples:
        >>> evaluate_max_tasks_formula("5-L", 0)
        5
        >>> evaluate_max_tasks_formula("5-L", 2)
        3
        >>> evaluate_max_tasks_formula("10", 1)
        10
        >>> evaluate_max_tasks_formula("auto", 0)
        None
    """
    if not formula or not isinstance(formula, str):
        return None

    formula = formula.strip()

    # Handle "auto" mode - let LLM decide
    if formula.lower() == "auto":
        return None

    # Try to parse as a simple integer
    try:
        return max(1, int(formula))
    except ValueError:
        pass

    # Formula contains 'L' - evaluate as expression
    # Replace L with the actual level value
    # Only allow safe mathematical operations
    safe_formula = formula.replace("L", str(level))

    # Validate that only safe characters are present
    if not re.match(r'^[0-9+\-*/() ]+$', safe_formula):
        # Invalid characters in formula, return None (auto mode)
        return None

    try:
        # Evaluate the mathematical expression
        result = eval(safe_formula, {"__builtins__": {}}, {})
        # Ensure at least 1 task
        return max(1, int(result))
    except (SyntaxError, ValueError, ZeroDivisionError, NameError):
        # If evaluation fails, return None (auto mode)
        return None
