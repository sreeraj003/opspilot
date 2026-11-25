"""Policy definition validation (no database needed)."""

from typing import Any

import pytest

from app.authorization.policy import (
    PolicyLimits,
    PolicyValidationError,
    merge_override,
    validate_override,
    validate_policy,
)

DATA = "database_data"


def _sales(**overrides: Any) -> dict[str, Any]:
    policy: dict[str, Any] = {"allowed_tables": ["sales"], "allowed_columns": ["amount", "region"]}
    policy.update(overrides)
    return policy


def _errors(resource_type: str, policy: Any, **kwargs: Any) -> list[str]:
    with pytest.raises(PolicyValidationError) as caught:
        validate_policy(resource_type, policy, **kwargs)
    return [issue.message for issue in caught.value.issues]


def test_spec_example_is_normalised() -> None:
    policy = validate_policy(
        DATA,
        {
            "allowed_tables": ["sales"],
            "allowed_columns": ["product_id", "amount", "sale_date", "region"],
            "row_filters": [{"column": "region", "operator": "equals", "value": "Karnataka"}],
            "allow_export": False,
            "allow_sql_view": False,
        },
    )

    assert policy == {
        "version": 1,
        "effect": "allow",
        "allowed_tables": ["sales"],
        "allowed_columns": ["sales.amount", "sales.product_id", "sales.region", "sales.sale_date"],
        "allowed_operations": ["aggregate", "read"],
        "row_filters": [{"column": "sales.region", "operator": "equals", "value": "Karnataka", "value_type": "string"}],
        "allow_export": False,
        "allow_sql_view": False,
    }


@pytest.mark.parametrize(
    "policy",
    [
        [],
        "allow everything",
        {"allowed_tables": []},
        {"allowed_tables": ["sales"]},  # columns are required
        {"allowed_tables": "sales", "allowed_columns": ["amount"]},
        _sales(row_filters={"column": "region"}),
        _sales(row_filters=["region = 'Karnataka'"]),
        _sales(allow_export="yes"),
        _sales(version=2),
        _sales(effect="maybe"),
        _sales(where="1=1"),  # unknown keys are refused, never ignored
    ],
)
def test_malformed_policies_are_rejected(policy: Any) -> None:
    assert _errors(DATA, policy)


@pytest.mark.parametrize("operator", ["like", "contains", "regex", "raw_sql", "=", "EQUALS"])
def test_unsupported_operators_are_rejected(operator: str) -> None:
    messages = _errors(DATA, _sales(row_filters=[{"column": "region", "operator": operator, "value": "x"}]))
    assert any("Unsupported operator" in m for m in messages)


def _nested(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"column": "amount", "operator": "greater_than", "value": 1}
    for level in range(depth):
        node = {"logic": "and" if level % 2 else "or", "conditions": [node]}
    return node


def test_nesting_up_to_the_limit_is_accepted_and_beyond_is_rejected() -> None:
    assert validate_policy(DATA, _sales(row_filters=[_nested(4)]))
    messages = _errors(DATA, _sales(row_filters=[_nested(5)]))
    assert any("nested too deeply" in m for m in messages)
    # The limit is configurable.
    assert validate_policy(DATA, _sales(row_filters=[_nested(5)]), limits=PolicyLimits(max_depth=5))


def test_condition_count_is_limited() -> None:
    filters = [{"column": "amount", "operator": "not_equals", "value": i} for i in range(6)]
    assert _errors(DATA, _sales(row_filters=filters), limits=PolicyLimits(max_conditions=5))


@pytest.mark.parametrize(
    "table",
    ["sales; DROP TABLE users", "sales--", "1sales", "sales.x.y", "\"sales\"", "sales OR 1=1", ""],
)
def test_sql_fragments_cannot_be_used_as_identifiers(table: str) -> None:
    assert _errors(DATA, {"allowed_tables": [table], "allowed_columns": ["*"]})


def test_sql_fragments_in_column_references_are_rejected() -> None:
    messages = _errors(
        DATA, _sales(row_filters=[{"column": "region) OR (1=1", "operator": "equals", "value": "x"}])
    )
    assert any("not a valid column name" in m for m in messages)


def test_values_stay_data() -> None:
    # A value that looks like SQL is just a string compared for equality; it is never interpreted.
    policy = validate_policy(DATA, _sales(row_filters=[{"column": "region", "operator": "equals", "value": "x' OR '1'='1"}]))
    assert policy["row_filters"][0]["value"] == "x' OR '1'='1"


@pytest.mark.parametrize(
    ("condition", "fragment"),
    [
        ({"column": "region", "operator": "greater_than", "value": "K"}, "cannot be used with string"),
        ({"column": "active", "operator": "in", "value": [True, False]}, "cannot be used with boolean"),
        ({"column": "amount", "operator": "between", "value": [10]}, "exactly two values"),
        ({"column": "amount", "operator": "between", "value": [10, 5]}, "must not be greater"),
        ({"column": "amount", "operator": "in", "value": []}, "between 1 and"),
        ({"column": "amount", "operator": "in", "value": [1, "1"]}, "same type"),
        ({"column": "amount", "operator": "in", "value": [1, 1]}, "duplicate"),
        ({"column": "amount", "operator": "equals"}, "needs a value"),
        ({"column": "amount", "operator": "equals", "value": None}, "is_null"),
        ({"column": "amount", "operator": "is_null", "value": 3}, "takes no value"),
        ({"column": "day", "operator": "greater_than", "value": "31/01/2026", "value_type": "date"}, "ISO 8601"),
        ({"column": "at", "operator": "less_than", "value": "2026-01-31T09:00:00", "value_type": "datetime"}, "ISO 8601"),
        ({"column": "amount", "operator": "equals", "value": "ten", "value_type": "number"}, "not a number"),
        ({"column": "amount", "operator": "equals", "value": {"$gt": 1}}, "single value"),
    ],
)
def test_value_types_and_operator_compatibility(condition: dict[str, Any], fragment: str) -> None:
    policy = {"allowed_tables": ["sales"], "allowed_columns": ["*"], "row_filters": [condition]}
    messages = _errors(DATA, policy)
    assert any(fragment in m for m in messages), messages


def test_typed_ranges_are_accepted() -> None:
    policy = validate_policy(
        DATA,
        _sales(
            row_filters=[
                {"column": "sale_date", "operator": "between", "value": ["2026-01-01", "2026-03-31"], "value_type": "date"},
                {"column": "amount", "operator": "greater_than_or_equal", "value": 100},
                {"column": "region", "operator": "in", "value": ["Karnataka", "Kerala"]},
                {"column": "deleted_at", "operator": "is_null"},
            ]
        ),
    )
    assert [f["value_type"] for f in policy["row_filters"] if "value_type" in f] == ["date", "number", "string"]


@pytest.mark.parametrize(
    "filters",
    [
        [{"column": "region", "operator": "equals", "value": "A"}, {"column": "region", "operator": "equals", "value": "B"}],
        [{"column": "region", "operator": "equals", "value": "A"}, {"column": "region", "operator": "not_equals", "value": "A"}],
        [{"column": "region", "operator": "is_null"}, {"column": "region", "operator": "is_not_null"}],
        [{"column": "region", "operator": "in", "value": ["A"]}, {"column": "region", "operator": "not_in", "value": ["A"]}],
        [{"column": "amount", "operator": "greater_than", "value": 10}, {"column": "amount", "operator": "less_than", "value": 10}],
        [{"column": "region", "operator": "equals", "value": "A"}, {"column": "region", "operator": "equals", "value": "A"}],
    ],
)
def test_contradictory_or_duplicate_conditions_are_rejected(filters: list[dict[str, Any]]) -> None:
    assert _errors(DATA, _sales(row_filters=filters))


def test_or_groups_may_combine_values_of_one_column() -> None:
    group = {
        "logic": "or",
        "conditions": [
            {"column": "region", "operator": "equals", "value": "Karnataka"},
            {"column": "region", "operator": "equals", "value": "Kerala"},
        ],
    }
    assert validate_policy(DATA, _sales(row_filters=[group]))["row_filters"][0]["logic"] == "or"


def test_column_references_must_belong_to_listed_tables() -> None:
    assert _errors(DATA, _sales(allowed_columns=["customers.email"]))
    two_tables = {"allowed_tables": ["sales", "customers"], "allowed_columns": ["amount"]}
    assert any("Qualify" in m for m in _errors(DATA, two_tables))
    assert any("No columns" in m for m in _errors(DATA, {"allowed_tables": ["sales", "customers"], "allowed_columns": ["sales.amount"]}))


def test_each_top_level_filter_refers_to_one_table() -> None:
    policy = {
        "allowed_tables": ["sales", "customers"],
        "allowed_columns": ["sales.*", "customers.*"],
        "row_filters": [
            {
                "logic": "or",
                "conditions": [
                    {"column": "sales.region", "operator": "equals", "value": "A"},
                    {"column": "customers.country", "operator": "equals", "value": "IN"},
                ],
            }
        ],
    }
    assert any("single table" in m for m in _errors(DATA, policy))


def test_deny_policies() -> None:
    policy = validate_policy(
        DATA,
        {
            "effect": "deny",
            "denied_columns": ["sales.customer_email", "payroll.*"],
            "excluded_rows": [{"column": "sales.region", "operator": "equals", "value": "Internal"}],
        },
    )
    assert policy["denied_tables"] == ["payroll"]
    assert policy["denied_columns"] == ["sales.customer_email"]
    assert _errors(DATA, {"effect": "deny"})  # must deny something
    assert _errors(DATA, {"effect": "deny", "excluded_rows": [{"column": "region", "operator": "equals", "value": "x"}]})


def test_non_data_resources_take_flags_only() -> None:
    assert validate_policy("executive_reports", {"allow_export": True})["allow_export"] is True
    assert _errors("executive_reports", {"allowed_tables": ["sales"]})


class _Catalog:
    """A schema catalog like Module 06 will provide."""

    columns = {("sales", "amount"): "number", ("sales", "region"): "string"}

    def has_table(self, table: str) -> bool | None:
        return table == "sales"

    def column_type(self, table: str, column: str) -> str | None:
        return self.columns.get((table, column))


def test_registered_schema_is_used_when_available() -> None:
    catalog = _Catalog()
    assert validate_policy(DATA, _sales(), catalog=catalog)
    assert any("not a registered table" in m for m in _errors(DATA, {"allowed_tables": ["hr"], "allowed_columns": ["*"]}, catalog=catalog))
    assert any("does not exist" in m for m in _errors(DATA, _sales(allowed_columns=["salary"]), catalog=catalog))
    messages = _errors(DATA, _sales(row_filters=[{"column": "amount", "operator": "equals", "value": "ten"}]), catalog=catalog)
    assert any("holds number values" in m for m in messages)


def test_override_can_only_narrow() -> None:
    base = validate_policy(DATA, _sales(allow_export=False))
    override = validate_override(base, {"row_filters": [{"column": "region", "operator": "equals", "value": "Kerala"}]})
    merged = merge_override(base, override)
    assert merged["row_filters"][-1]["value"] == "Kerala"

    for broadening in (
        {"allow_export": True},
        {"allowed_columns": ["sales.customer_email"]},
        {"allowed_tables": ["customers"]},
        {"allowed_operations": ["read", "write"]},
    ):
        with pytest.raises(PolicyValidationError):
            validate_override(base, broadening)


def test_merged_override_never_exceeds_a_later_narrowed_permission() -> None:
    wide = validate_policy(DATA, _sales(allowed_columns=["*"]))
    override = validate_override(wide, {"allowed_columns": ["amount", "region"]})
    narrowed_permission = validate_policy(DATA, _sales(allowed_columns=["amount"]))
    assert merge_override(narrowed_permission, override)["allowed_columns"] == ["sales.amount"]
