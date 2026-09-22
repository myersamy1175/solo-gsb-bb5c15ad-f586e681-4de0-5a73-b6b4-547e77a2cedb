"""Pydantic request/response models."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class SegmentIn(BaseModel):
    """A single subtitle cue, in playback order."""

    id: Optional[str] = Field(
        default=None,
        description="Stable identifier of the cue; defaults to its index.",
        examples=["cue-1"],
    )
    start_ms: int = Field(..., ge=0, description="Start time in milliseconds.")
    end_ms: int = Field(..., ge=0, description="End time in milliseconds.")
    text: str = Field(..., description="Subtitle text, preserved unchanged.")
    locked: bool = Field(
        default=False,
        description="If true, the cue cannot move from start_ms/end_ms.",
    )

    @field_validator("end_ms")
    @classmethod
    def _end_after_start(cls, value: int, info) -> int:
        start = info.data.get("start_ms")
        if start is not None and value <= start:
            raise ValueError("end_ms must be greater than start_ms")
        return value


class NormalizeRequest(BaseModel):
    """Payload for POST /normalize."""

    segments: List[SegmentIn] = Field(..., min_length=1)
    min_gap_ms: int = Field(
        default=0,
        ge=0,
        description="Required gap between two consecutive cues.",
    )


class SegmentOut(BaseModel):
    """A normalized cue."""

    id: str
    start_ms: int
    end_ms: int
    text: str
    locked: bool
    shift_ms: int = Field(
        ..., description="Applied displacement (new start minus original start)."
    )


class NormalizeResponse(BaseModel):
    """Successful normalization result."""

    status: Literal["ok"] = "ok"
    segments: List[SegmentOut]
    min_gap_ms: int
    total_abs_shift_ms: int = Field(
        ..., description="Sum of absolute displacements, the minimized objective."
    )


class Conflict(BaseModel):
    """Two locked cues (or the timeline origin) that cannot both be honored."""

    first_id: str
    second_id: str
    first_position_ms: int
    second_position_ms: int
    required_ms: int = Field(
        ..., description="Minimum span needed between the two locked positions."
    )
    available_ms: int = Field(
        ..., description="Span actually available between the locked positions."
    )
    shortfall_ms: int
    reason: str


class ConflictResponse(BaseModel):
    """Returned with HTTP 409 when the constraints are infeasible."""

    status: Literal["conflict"] = "conflict"
    message: str
    conflicts: List[Conflict]


class HealthResponse(BaseModel):
    """Returned by GET /health."""

    status: Literal["ok"] = "ok"
