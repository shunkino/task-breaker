"""Unit tests for max_tasks_formula module."""
import pytest
from task_breaker.max_tasks_formula import evaluate_max_tasks_formula


class TestEvaluateMaxTasksFormula:
    """Tests for evaluate_max_tasks_formula function."""

    def test_auto_mode(self):
        """Test 'auto' returns None to let LLM decide."""
        assert evaluate_max_tasks_formula("auto", 0) is None
        assert evaluate_max_tasks_formula("AUTO", 1) is None
        assert evaluate_max_tasks_formula("Auto", 2) is None

    def test_simple_integer(self):
        """Test simple integer values."""
        assert evaluate_max_tasks_formula("5", 0) == 5
        assert evaluate_max_tasks_formula("10", 1) == 10
        assert evaluate_max_tasks_formula("1", 2) == 1

    def test_simple_formula_5_minus_L(self):
        """Test the default formula '5-L'."""
        assert evaluate_max_tasks_formula("5-L", 0) == 5
        assert evaluate_max_tasks_formula("5-L", 1) == 4
        assert evaluate_max_tasks_formula("5-L", 2) == 3
        assert evaluate_max_tasks_formula("5-L", 3) == 2
        assert evaluate_max_tasks_formula("5-L", 4) == 1
        # Should never go below 1
        assert evaluate_max_tasks_formula("5-L", 5) == 1
        assert evaluate_max_tasks_formula("5-L", 10) == 1

    def test_complex_formulas(self):
        """Test more complex mathematical formulas."""
        assert evaluate_max_tasks_formula("10-2*L", 0) == 10
        assert evaluate_max_tasks_formula("10-2*L", 1) == 8
        assert evaluate_max_tasks_formula("10-2*L", 2) == 6

        assert evaluate_max_tasks_formula("3*L+2", 0) == 2
        assert evaluate_max_tasks_formula("3*L+2", 1) == 5
        assert evaluate_max_tasks_formula("3*L+2", 2) == 8

    def test_formula_with_parentheses(self):
        """Test formulas with parentheses."""
        assert evaluate_max_tasks_formula("(10-L)*2", 0) == 20
        assert evaluate_max_tasks_formula("(10-L)*2", 1) == 18
        assert evaluate_max_tasks_formula("10/(L+1)", 0) == 10
        assert evaluate_max_tasks_formula("10/(L+1)", 1) == 5

    def test_minimum_one_task(self):
        """Test that result is always at least 1."""
        assert evaluate_max_tasks_formula("L-10", 0) == 1
        assert evaluate_max_tasks_formula("-5", 0) == 1
        assert evaluate_max_tasks_formula("0", 0) == 1

    def test_invalid_formulas(self):
        """Test that invalid formulas return None (auto mode)."""
        # Invalid characters
        assert evaluate_max_tasks_formula("5&L", 0) is None
        assert evaluate_max_tasks_formula("import os", 0) is None
        assert evaluate_max_tasks_formula("__import__", 0) is None

        # Syntax errors
        assert evaluate_max_tasks_formula("5++L", 0) is None
        assert evaluate_max_tasks_formula("(5-L", 0) is None

    def test_edge_cases(self):
        """Test edge cases."""
        assert evaluate_max_tasks_formula("", 0) is None
        assert evaluate_max_tasks_formula(None, 0) is None
        # Non-string input
        assert evaluate_max_tasks_formula(5, 0) is None

    def test_division_by_zero(self):
        """Test that division by zero is handled gracefully."""
        assert evaluate_max_tasks_formula("10/L", 0) is None
        assert evaluate_max_tasks_formula("10/(L-1)", 1) is None

    def test_float_results(self):
        """Test that float results are converted to integers."""
        assert evaluate_max_tasks_formula("10/3", 0) == 3
        assert evaluate_max_tasks_formula("7/2", 1) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
