import pytest

from churn import config
from churn.data import DataValidationError, load_data, validate


def test_generated_data_is_valid():
    df = load_data()
    assert len(df) == 3000
    validate(df)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.drop(columns=["contract_type"]), "Missing columns"),
        (lambda d: d.assign(contract_type=["lifetime"] + list(d.contract_type[1:])), "unknown categories"),
        (lambda d: d.assign(tenure_months=[-3] + list(d.tenure_months[1:])), "below"),
        (lambda d: d.assign(senior_citizen=[2] + list(d.senior_citizen[1:])), "0/1"),
        (lambda d: d.assign(churned=0), "single class"),
        (lambda d: d.assign(customer_id="SAME"), "duplicate"),
        (lambda d: d.assign(monthly_charges=None), "Too many nulls"),
    ],
)
def test_bad_data_rejected(mutate, message):
    with pytest.raises(DataValidationError, match=message):
        validate(mutate(load_data()))


def test_api_schema_and_batch_validation_share_contract():
    """The API schema is generated from config — they can't diverge."""
    from churn.schemas import Customer

    props = Customer.model_json_schema()["properties"]
    assert set(props["contract_type"]["enum"]) == set(config.CATEGORIES["contract_type"])
    assert props["tenure_months"]["maximum"] == config.NUMERIC_RANGES["tenure_months"][1]
