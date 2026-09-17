"""Why a run failed: one stable code, one fixed user-safe message (Sections 10.3, 11, 21)."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class FailureCode(StrEnum):
    RUN_BUDGET_EXCEEDED = "RUN_BUDGET_EXCEEDED"
    TENANT_BUDGET_EXCEEDED = "TENANT_BUDGET_EXCEEDED"
    BUDGET_UNAVAILABLE = "BUDGET_UNAVAILABLE"
    STAGE_TIMEOUT = "STAGE_TIMEOUT"
    RUN_TIMEOUT = "RUN_TIMEOUT"
    CANCELLED = "CANCELLED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    REQUEST_NOT_SUPPORTED = "REQUEST_NOT_SUPPORTED"
    NO_DATA_SOURCE = "NO_DATA_SOURCE"
    DATA_SOURCE_SELECTION_REQUIRED = "DATA_SOURCE_SELECTION_REQUIRED"
    DATA_SOURCE_NOT_ACTIVE = "DATA_SOURCE_NOT_ACTIVE"
    NO_RELEVANT_DATA = "NO_RELEVANT_DATA"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    QUERY_REJECTED = "QUERY_REJECTED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    QUERY_FAILED = "QUERY_FAILED"
    CHART_INVALID = "CHART_INVALID"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_REFUSED = "MODEL_REFUSED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    RUN_ENQUEUE_FAILED = "RUN_ENQUEUE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


MESSAGES: Final[dict[FailureCode, str]] = {
    FailureCode.RUN_BUDGET_EXCEEDED: "This request exceeded its processing budget.",
    FailureCode.TENANT_BUDGET_EXCEEDED: "Your organization has reached today's analysis limit.",
    FailureCode.BUDGET_UNAVAILABLE: "Usage limits could not be checked. Try again shortly.",
    FailureCode.STAGE_TIMEOUT: "A step took too long to finish.",
    FailureCode.RUN_TIMEOUT: "The request took too long to finish.",
    FailureCode.CANCELLED: "The run was cancelled.",
    FailureCode.NOT_AUTHORIZED: "You are not allowed to run this analysis.",
    FailureCode.REQUEST_NOT_SUPPORTED: "This kind of request is not supported yet.",
    FailureCode.NO_DATA_SOURCE: "No active data source is available.",
    FailureCode.DATA_SOURCE_SELECTION_REQUIRED: "Choose which data source to use.",
    FailureCode.DATA_SOURCE_NOT_ACTIVE: "The selected data source is not available.",
    FailureCode.NO_RELEVANT_DATA: "No data relevant to this request was found.",
    FailureCode.OUTPUT_INVALID: "The analysis could not be completed.",
    FailureCode.QUERY_REJECTED: "A safe query for this request could not be built.",
    FailureCode.QUERY_TIMEOUT: "The query took too long to run.",
    FailureCode.QUERY_FAILED: "The query could not be run.",
    FailureCode.CHART_INVALID: "A chart for this result could not be built.",
    FailureCode.MODEL_UNAVAILABLE: "The analysis service is unavailable. Try again shortly.",
    FailureCode.MODEL_REFUSED: "This request could not be processed.",
    FailureCode.UPSTREAM_UNAVAILABLE: "A required service is unavailable. Try again shortly.",
    FailureCode.RUN_ENQUEUE_FAILED: "The request could not be started. Try again.",
    FailureCode.INTERNAL_ERROR: "Something went wrong.",
}


class RunFailedError(Exception):
    """Ends a run with a code. Carries nothing else: no model text, SQL, or upstream detail."""

    def __init__(self, code: FailureCode) -> None:
        super().__init__(code.value)
        self.code = code

    @property
    def message(self) -> str:
        return MESSAGES[self.code]
