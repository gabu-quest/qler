"""
Example: Property-based testing for SQL query builder.

Demonstrates:
- SQL injection prevention properties
- Placeholder count matching parameter count
- Bounded strategy generation (realistic query complexity)
"""

from typing import List, Tuple
from hypothesis import given, strategies as st
import pytest

# ============================================================================
# Application Code (Query Builder)
# ============================================================================


def build_query(filters: List[Tuple[str, str, str]]) -> Tuple[str, List[str]]:
    """
    Build a SELECT query with WHERE filters.

    Args:
        filters: List of (field, operator, value) tuples

    Returns:
        Tuple of (sql_string, parameters)
    """
    if not filters:
        return "SELECT * FROM users", []

    where_clauses = []
    parameters = []

    for field, operator, value in filters:
        # Validate operator (prevent injection)
        if operator not in ["=", "!=", ">", "<", ">=", "<=", "LIKE"]:
            raise ValueError(f"Invalid operator: {operator}")

        # Validate field name (simple alphanumeric check)
        if not field.replace("_", "").isalnum():
            raise ValueError(f"Invalid field name: {field}")

        # Use parameterized query (prevents injection)
        where_clauses.append(f"{field} {operator} ?")
        parameters.append(value)

    where_sql = " AND ".join(where_clauses)
    sql = f"SELECT * FROM users WHERE {where_sql}"

    return sql, parameters


# ============================================================================
# Property-Based Tests
# ============================================================================


# Strategy: Valid field names (alphanumeric + underscore)
valid_field_names = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=20
).filter(lambda s: s[0].isalpha())  # Must start with letter

# Strategy: Valid operators
valid_operators = st.sampled_from(["=", "!=", ">", "<", ">=", "<=", "LIKE"])

# Strategy: Filter values (any text, bounded size)
filter_values = st.text(max_size=100)

# Strategy: List of filters (bounded to reasonable size)
filter_lists = st.lists(
    st.tuples(valid_field_names, valid_operators, filter_values),
    min_size=0,
    max_size=10,  # No queries with >10 filters
)


@given(filter_lists)
def test_placeholder_count_matches_parameter_count(filters):
    """
    Property: Number of placeholders (?) must equal number of parameters.

    This prevents SQL injection and ensures correct binding.
    """
    sql, params = build_query(filters)

    # Count placeholders
    placeholder_count = sql.count("?")

    # Must match parameter count
    assert placeholder_count == len(params)


@given(filter_lists)
def test_no_user_input_in_sql_string(filters):
    """
    Property: User-provided values must NOT appear in SQL string.

    This ensures parameterized queries (prevents SQL injection).
    """
    sql, params = build_query(filters)

    # Extract all user values from filters
    user_values = [value for (_, _, value) in filters]

    # User values should be in params, not in SQL string
    for value in user_values:
        # Value is in params (good)
        assert value in params

        # Value is NOT directly in SQL string (prevents injection)
        # Exception: empty string is always in any string
        if value != "":
            assert value not in sql


@given(filter_lists)
def test_query_structure_is_valid(filters):
    """
    Property: Generated queries should follow expected structure.

    - Start with SELECT
    - WHERE clause only if filters exist
    - AND joins multiple filters
    """
    sql, params = build_query(filters)

    # Always starts with SELECT
    assert sql.startswith("SELECT * FROM users")

    if filters:
        # Has WHERE clause
        assert "WHERE" in sql

        # Has correct number of AND operators
        expected_and_count = len(filters) - 1
        actual_and_count = sql.count(" AND ")
        assert actual_and_count == expected_and_count
    else:
        # No WHERE clause for empty filters
        assert "WHERE" not in sql


@given(st.lists(st.tuples(valid_field_names, valid_operators, filter_values), min_size=1))
def test_parameters_preserve_order(filters):
    """
    Property: Parameters should be in same order as filters.

    This ensures correct binding when query is executed.
    """
    sql, params = build_query(filters)

    # Extract expected order from filters
    expected_params = [value for (_, _, value) in filters]

    # Parameters should match expected order
    assert params == expected_params


# ============================================================================
# Edge Case Tests (Found by Hypothesis, Now Regression Tests)
# ============================================================================


def test_single_quote_in_value_is_safe():
    """
    Regression test: Single quotes in values must not cause injection.

    Hypothesis found this case. Now it's a permanent regression test.
    """
    filters = [("name", "=", "O'Brien")]

    sql, params = build_query(filters)

    # Single quote should be in params, not in SQL
    assert "O'Brien" in params
    assert "O'Brien" not in sql

    # Query should use placeholder
    assert "name = ?" in sql


def test_semicolon_in_value_is_safe():
    """
    Regression test: Semicolons in values must not break query.
    """
    filters = [("description", "=", "Test; DROP TABLE users;")]

    sql, params = build_query(filters)

    # Dangerous value is parameterized
    assert "Test; DROP TABLE users;" in params
    assert "DROP TABLE" not in sql


def test_empty_filter_list():
    """
    Edge case: Empty filter list should return simple SELECT.
    """
    sql, params = build_query([])

    assert sql == "SELECT * FROM users"
    assert params == []


def test_invalid_operator_raises_error():
    """
    Property: Invalid operators should be rejected.
    """
    with pytest.raises(ValueError, match="Invalid operator"):
        build_query([("name", "OR 1=1 --", "value")])


def test_invalid_field_name_raises_error():
    """
    Property: Invalid field names should be rejected.
    """
    with pytest.raises(ValueError, match="Invalid field name"):
        build_query([("name'; DROP TABLE users--", "=", "value")])
