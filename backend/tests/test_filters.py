"""The filter contract: validation, and the schema generated from it."""

import pytest
from pydantic import ValidationError

from app.domain.filters import ContractFilter
from app.domain.models import PRODUCTS, STATUSES


def test_blank_filter_is_empty():
    assert ContractFilter().is_empty()
    assert ContractFilter().active() == {}


def test_active_reports_only_supplied_criteria():
    f = ContractFilter(product="Cyber", expiring_within_days=30)
    assert f.active() == {"product": "Cyber", "expiring_within_days": 30}


@pytest.mark.parametrize(
    "payload",
    [
        {"product": "Aviation"},
        {"status": "pending"},
        {"expiring_within_days": -1},
        {"min_sum_insured": -1},
        {"unknown_field": 1},
    ],
)
def test_rejects_bad_input(payload):
    with pytest.raises(ValidationError):
        ContractFilter(**payload)


def test_tool_properties_are_flat_json_schema():
    """
    Tool schemas are generated, so this is the test that stops them drifting from
    the filter the API actually applies.
    """
    props = ContractFilter.tool_properties()

    assert set(props) == set(ContractFilter.model_fields)

    # Optional[X] must be flattened, not left as anyOf with a null branch.
    for name, spec in props.items():
        assert "anyOf" not in spec, name
        assert "title" not in spec, name
        assert spec.get("description"), f"{name} has no description for the model"

    assert props["product"]["enum"] == list(PRODUCTS)
    assert set(props["status"]["enum"]) == set(STATUSES)
    assert props["renewal_pending"]["type"] == "boolean"
    assert props["expiring_within_days"]["type"] == "integer"
