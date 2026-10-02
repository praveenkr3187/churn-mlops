"""API contracts, generated from the data contract in config.py.

Pydantic rejects bad requests with a clear 422 *before* they reach the model.
Because the ranges and categories come from config, the API can never accept
something the training validation would reject (or vice versa).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from churn import config

_R = config.NUMERIC_RANGES
_C = config.CATEGORIES

ContractType = Literal[_C["contract_type"]]  # type: ignore[valid-type]
PaymentMethod = Literal[_C["payment_method"]]  # type: ignore[valid-type]
InternetService = Literal[_C["internet_service"]]  # type: ignore[valid-type]


class Customer(BaseModel):
    model_config = ConfigDict(
        extra="forbid",  # unknown fields are a client bug — reject them
        json_schema_extra={
            "example": {
                "customer_id": "C123456",
                "tenure_months": 3,
                "monthly_charges": 89.5,
                "total_charges": 268.5,
                "num_support_tickets": 4,
                "contract_type": "month-to-month",
                "payment_method": "electronic_check",
                "internet_service": "fiber",
                "senior_citizen": 0,
            }
        },
    )

    customer_id: str | None = Field(None, max_length=64)
    tenure_months: int = Field(..., ge=_R["tenure_months"][0], le=_R["tenure_months"][1])
    monthly_charges: float = Field(..., ge=_R["monthly_charges"][0], le=_R["monthly_charges"][1])
    total_charges: float | None = Field(None, ge=_R["total_charges"][0], le=_R["total_charges"][1])
    num_support_tickets: int = Field(
        0, ge=_R["num_support_tickets"][0], le=_R["num_support_tickets"][1]
    )
    contract_type: ContractType
    payment_method: PaymentMethod
    internet_service: InternetService
    senior_citizen: Literal[0, 1] = 0


class Prediction(BaseModel):
    customer_id: str | None
    churn_probability: float
    will_churn: bool
    risk_band: Literal["low", "medium", "high"]
    model_version: str


class BatchRequest(BaseModel):
    customers: list[Customer] = Field(..., min_length=1, max_length=1000)


class BatchResponse(BaseModel):
    predictions: list[Prediction]
    model_version: str


class Feedback(BaseModel):
    """Ground truth arriving later (e.g. from the CRM): did the customer churn?"""

    customer_id: str = Field(..., max_length=64)
    churned: bool
