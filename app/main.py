"""FastAPI application: subtitle timeline normalization."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .optimizer import InfeasibleError, solve
from .schemas import (
    Conflict,
    ConflictResponse,
    HealthResponse,
    NormalizeRequest,
    NormalizeResponse,
    SegmentOut,
)

app = FastAPI(
    title="Subtitle Timeline Normalizer",
    version=__version__,
    description=(
        "Repair subtitle timelines so consecutive cues never overlap and keep "
        "at least a minimum gap, while texts, durations and locked cue "
        "positions are preserved. The total absolute displacement is minimized."
    ),
)


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Liveness/readiness probe."""
    return HealthResponse(status="ok")


@app.post(
    "/normalize",
    response_model=NormalizeResponse,
    tags=["normalize"],
    responses={409: {"model": ConflictResponse}},
)
def normalize(request: NormalizeRequest):
    """Normalize the timeline.

    Segments must be submitted in their fixed playback order. Locked segments
    stay at their exact position; every other segment may move but keeps its
    duration and text. Returns the timeline with minimum total absolute
    displacement, or HTTP 409 with the locked cues that conflict.
    """
    segments = request.segments
    gap = request.min_gap_ms

    ids = [seg.id if seg.id is not None else str(i) for i, seg in enumerate(segments)]
    starts = [seg.start_ms for seg in segments]
    durations = [seg.end_ms - seg.start_ms for seg in segments]
    locked = [seg.locked for seg in segments]

    try:
        cues, total_shift = solve(starts, durations, locked, ids, gap)
    except InfeasibleError as exc:
        conflicts = [
            Conflict(
                first_id=c.first_id,
                second_id=c.second_id,
                first_position_ms=c.first_position_ms,
                second_position_ms=c.second_position_ms,
                required_ms=c.required_ms,
                available_ms=c.available_ms,
                shortfall_ms=c.required_ms - c.available_ms,
                reason=(
                    f"Locked cue '{c.second_id}' at {c.second_position_ms} ms is "
                    f"too close to '{c.first_id}' at {c.first_position_ms} ms: "
                    f"the cues between them need {c.required_ms} ms "
                    f"(durations plus the {gap} ms gap) but only "
                    f"{c.available_ms} ms are available "
                    f"({c.required_ms - c.available_ms} ms short). Unlock one of "
                    f"the cues or move it to an earlier position."
                ),
            )
            for c in exc.conflicts
        ]
        body = ConflictResponse(
            message=(
                "No overlap-free timeline exists while keeping every locked "
                "cue in place. See conflicts for details."
            ),
            conflicts=conflicts,
        )
        return JSONResponse(status_code=409, content=body.model_dump())

    out = [
        SegmentOut(
            id=cue.id,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            text=segments[i].text,
            locked=cue.locked,
            shift_ms=cue.start_ms - starts[i],
        )
        for i, cue in enumerate(cues)
    ]
    return NormalizeResponse(
        segments=out, min_gap_ms=gap, total_abs_shift_ms=total_shift
    )
