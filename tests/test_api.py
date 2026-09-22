"""End-to-end API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_no_overlap_input_is_returned_unchanged():
    payload = {
        "segments": [
            {"id": "a", "start_ms": 0, "end_ms": 1000, "text": "hi"},
            {"id": "b", "start_ms": 1000, "end_ms": 2000, "text": "there"},
        ],
        "min_gap_ms": 0,
    }
    resp = client.post("/normalize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total_abs_shift_ms"] == 0
    assert [s["start_ms"] for s in data["segments"]] == [0, 1000]
    assert [s["shift_ms"] for s in data["segments"]] == [0, 0]
    assert [s["text"] for s in data["segments"]] == ["hi", "there"]


def test_overlapping_cues_are_repaired_with_minimum_shift():
    payload = {
        "segments": [
            {"start_ms": 0, "end_ms": 1000, "text": "A"},
            {"start_ms": 500, "end_ms": 900, "text": "B"},
        ],
        "min_gap_ms": 0,
    }
    resp = client.post("/normalize", json=payload)
    assert resp.status_code == 200
    segs = resp.json()["segments"]
    # Minimum L1 repair: keep cue A at 0 and push B to 1000 (total shift
    # 500); moving both would cost 1500.
    assert [s["start_ms"] for s in segs] == [0, 1000]
    assert segs[1]["end_ms"] == 1400  # duration preserved
    assert resp.json()["total_abs_shift_ms"] == 500


def test_min_gap_is_enforced():
    payload = {
        "segments": [
            {"start_ms": 0, "end_ms": 100, "text": "A"},
            {"start_ms": 100, "end_ms": 200, "text": "B"},
        ],
        "min_gap_ms": 50,
    }
    resp = client.post("/normalize", json=payload)
    segs = resp.json()["segments"]
    assert segs[1]["start_ms"] - segs[0]["end_ms"] == 50


def test_locked_cue_stays_in_place():
    payload = {
        "segments": [
            {"start_ms": 0, "end_ms": 1000, "text": "A"},
            {"start_ms": 1200, "end_ms": 1800, "text": "B", "locked": True},
            {"start_ms": 1500, "end_ms": 2000, "text": "C"},
        ],
        "min_gap_ms": 0,
    }
    resp = client.post("/normalize", json=payload)
    assert resp.status_code == 200
    segs = resp.json()["segments"]
    assert segs[1]["start_ms"] == 1200
    assert segs[1]["end_ms"] == 1800
    assert segs[1]["shift_ms"] == 0
    assert segs[2]["start_ms"] >= 1800
    assert segs[0]["end_ms"] <= segs[1]["start_ms"]


def test_infeasible_returns_409_with_conflicts():
    payload = {
        "segments": [
            {"id": "a", "start_ms": 0, "end_ms": 1000, "text": "A", "locked": True},
            {"id": "b", "start_ms": 500, "end_ms": 900, "text": "B", "locked": True},
        ],
        "min_gap_ms": 0,
    }
    resp = client.post("/normalize", json=payload)
    assert resp.status_code == 409
    data = resp.json()
    assert data["status"] == "conflict"
    assert len(data["conflicts"]) == 1
    c = data["conflicts"][0]
    assert c["first_id"] == "a"
    assert c["second_id"] == "b"
    assert c["required_ms"] == 1000
    assert c["available_ms"] == 500
    assert c["shortfall_ms"] == 500
    assert "b" in c["reason"]


def test_default_ids_are_index_strings():
    payload = {
        "segments": [
            {"start_ms": 0, "end_ms": 100, "text": "A"},
            {"start_ms": 5, "end_ms": 10, "text": "B"},
        ],
        "min_gap_ms": 0,
    }
    segs = client.post("/normalize", json=payload).json()["segments"]
    assert [s["id"] for s in segs] == ["0", "1"]


def test_validation_errors():
    # end <= start
    resp = client.post(
        "/normalize",
        json={"segments": [{"start_ms": 100, "end_ms": 100, "text": "x"}]},
    )
    assert resp.status_code == 422

    # negative times
    resp = client.post(
        "/normalize",
        json={"segments": [{"start_ms": -1, "end_ms": 100, "text": "x"}]},
    )
    assert resp.status_code == 422

    # empty segment list
    resp = client.post("/normalize", json={"segments": []})
    assert resp.status_code == 422

    # negative gap
    resp = client.post(
        "/normalize",
        json={
            "segments": [{"start_ms": 0, "end_ms": 100, "text": "x"}],
            "min_gap_ms": -1,
        },
    )
    assert resp.status_code == 422
