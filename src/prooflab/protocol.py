"""Public semantic states. Integrity never substitutes for scientific acceptance."""

from enum import StrEnum


class ExecutionState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"


class MeasurementState(StrEnum):
    OBSERVED = "observed"
    NOT_APPLICABLE = "not_applicable"
    NOT_EVALUATED = "not_evaluated"
    ERROR = "error"


class AcceptanceState(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"


class IntegrityState(StrEnum):
    VERIFIED = "verified"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"
    UNSUPPORTED = "unsupported"
