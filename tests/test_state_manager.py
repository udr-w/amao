import random

import pytest

import amao.state_manager as state_manager_module
from amao._native_progress_stats import compute_progress_stats as _native_compute_progress_stats
from amao.models import MilestoneStatus
from amao.state_manager import StateManager

_NATIVE_AVAILABLE = _native_compute_progress_stats is not None


def _sm(tmp_path, name="state.db"):
    return StateManager(str(tmp_path / name))


def test_create_and_fetch_next_pending(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones(
        [{"title": "A", "description": "do a"}, {"title": "B", "description": "do b"}]
    )

    m = sm.get_next_pending_milestone()

    assert m.title == "A"
    assert m.status == MilestoneStatus.PENDING
    assert m.attempts == 0
    assert m.last_error is None


def test_update_status_and_attempts(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()

    sm.update_milestone_status(m.id, MilestoneStatus.IN_PROGRESS, attempts=1, last_error="oops")
    m2 = sm.get_next_pending_milestone()

    assert m2.attempts == 1
    assert m2.last_error == "oops"
    assert m2.status == MilestoneStatus.IN_PROGRESS


def test_completed_milestones_are_not_returned(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()

    sm.update_milestone_status(m.id, MilestoneStatus.COMPLETED)

    assert sm.get_next_pending_milestone() is None


def test_count_milestones(tmp_path):
    sm = _sm(tmp_path)
    assert sm.count_milestones() == 0

    sm.create_milestones([{"title": "A", "description": "d"}])

    assert sm.count_milestones() == 1


def test_duplicate_titles_are_ignored(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d1"}])
    sm.create_milestones([{"title": "A", "description": "d2"}])

    assert sm.count_milestones() == 1


def test_log_records_audit_entry_without_raising(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()

    sm.log(m.id, "STEP", {"key": "value"})


def test_milestones_are_returned_in_id_order(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones(
        [
            {"title": "First", "description": "d"},
            {"title": "Second", "description": "d"},
        ]
    )

    first = sm.get_next_pending_milestone()
    sm.update_milestone_status(first.id, MilestoneStatus.COMPLETED)
    second = sm.get_next_pending_milestone()

    assert first.title == "First"
    assert second.title == "Second"


def test_constructing_state_manager_twice_against_same_db_does_not_raise(tmp_path):
    db_path = str(tmp_path / "state.db")
    StateManager(db_path)

    sm = StateManager(db_path)
    sm.create_milestones([{"title": "A", "description": "d"}])

    with sm._connect() as conn:
        row = conn.execute("SELECT started_at, completed_at FROM milestones").fetchone()

    assert row == (None, None)


def test_transition_to_in_progress_sets_started_at(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()

    sm.update_milestone_status(m.id, MilestoneStatus.IN_PROGRESS)

    with sm._connect() as conn:
        (started_at,) = conn.execute(
            "SELECT started_at FROM milestones WHERE id = ?", (m.id,)
        ).fetchone()
    assert started_at is not None


def test_repeated_in_progress_transition_does_not_reset_started_at(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()

    sm.update_milestone_status(m.id, MilestoneStatus.IN_PROGRESS)
    with sm._connect() as conn:
        (first_started_at,) = conn.execute(
            "SELECT started_at FROM milestones WHERE id = ?", (m.id,)
        ).fetchone()

    sm.update_milestone_status(m.id, MilestoneStatus.IN_PROGRESS, attempts=2, last_error="retry")
    with sm._connect() as conn:
        (second_started_at,) = conn.execute(
            "SELECT started_at FROM milestones WHERE id = ?", (m.id,)
        ).fetchone()

    assert second_started_at == first_started_at


def test_transition_to_completed_sets_completed_at(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()

    sm.update_milestone_status(m.id, MilestoneStatus.IN_PROGRESS)
    sm.update_milestone_status(m.id, MilestoneStatus.COMPLETED)

    with sm._connect() as conn:
        (completed_at,) = conn.execute(
            "SELECT completed_at FROM milestones WHERE id = ?", (m.id,)
        ).fetchone()
    assert completed_at is not None


def test_pending_milestone_has_no_started_or_completed_at(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()

    with sm._connect() as conn:
        row = conn.execute(
            "SELECT started_at, completed_at FROM milestones WHERE id = ?", (m.id,)
        ).fetchone()

    assert row == (None, None)


def test_add_milestone_adds_a_new_pending_milestone(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])

    sm.add_milestone("B", "added later")

    assert sm.count_milestones() == 2


def test_add_milestone_is_queued_after_existing_pending_milestones(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])

    sm.add_milestone("B", "added later")

    first = sm.get_next_pending_milestone()
    assert first.title == "A"
    sm.update_milestone_status(first.id, MilestoneStatus.COMPLETED)

    second = sm.get_next_pending_milestone()
    assert second.title == "B"


def test_add_milestone_from_second_state_manager_instance_is_visible_to_first(tmp_path):
    db_path = str(tmp_path / "state.db")
    sm1 = StateManager(db_path)
    sm1.create_milestones([{"title": "A", "description": "d"}])

    sm2 = StateManager(db_path)
    sm2.add_milestone("B", "added mid-flight")

    first = sm1.get_next_pending_milestone()
    assert first.title == "A"
    sm1.update_milestone_status(first.id, MilestoneStatus.COMPLETED)

    second = sm1.get_next_pending_milestone()
    assert second.title == "B"


def test_get_audit_logs_returns_all_entries_unfiltered(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}, {"title": "B", "description": "d"}])
    m1 = sm.get_next_pending_milestone()
    sm.log(m1.id, "STEP1", {"a": 1})
    sm.update_milestone_status(m1.id, MilestoneStatus.COMPLETED)
    m2 = sm.get_next_pending_milestone()
    sm.log(m2.id, "STEP2", {"b": 2})

    logs = sm.get_audit_logs()

    assert len(logs) == 2


def test_get_audit_logs_respects_limit(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()
    for i in range(5):
        sm.log(m.id, f"STEP{i}", {"i": i})

    logs = sm.get_audit_logs(limit=2)

    assert len(logs) == 2


def test_get_audit_logs_filters_by_milestone_id(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}, {"title": "B", "description": "d"}])
    m1 = sm.get_next_pending_milestone()
    sm.log(m1.id, "STEP1", {"a": 1})
    sm.update_milestone_status(m1.id, MilestoneStatus.COMPLETED)
    m2 = sm.get_next_pending_milestone()
    sm.log(m2.id, "STEP2", {"b": 2})

    logs = sm.get_audit_logs(milestone_id=m2.id)

    assert len(logs) == 1
    assert logs[0]["milestone_id"] == m2.id


def test_get_audit_logs_details_round_trip_as_dict(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()
    sm.log(m.id, "STEP", {"key": "value", "n": 1})

    logs = sm.get_audit_logs()

    assert logs[0]["details"] == {"key": "value", "n": 1}


def test_progress_summary_for_empty_project(tmp_path):
    sm = _sm(tmp_path)

    summary = sm.get_progress_summary()

    assert summary.total == 0
    assert summary.pending == 0
    assert summary.in_progress == 0
    assert summary.completed == 0
    assert summary.halted == 0
    assert summary.current_milestone_title is None
    assert summary.average_completed_seconds is None
    assert summary.estimated_remaining_seconds is None


def test_progress_summary_counts_mixed_statuses(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones(
        [
            {"title": "A", "description": "d"},
            {"title": "B", "description": "d"},
            {"title": "C", "description": "d"},
            {"title": "D", "description": "d"},
        ]
    )
    a = sm.get_next_pending_milestone()
    sm.update_milestone_status(a.id, MilestoneStatus.COMPLETED)
    b = sm.get_next_pending_milestone()
    sm.update_milestone_status(b.id, MilestoneStatus.IN_PROGRESS)
    # C, D remain PENDING

    summary = sm.get_progress_summary()

    assert summary.total == 4
    assert summary.completed == 1
    assert summary.in_progress == 1
    assert summary.pending == 2
    assert summary.halted == 0
    assert summary.current_milestone_title == "B"


def test_progress_summary_computes_average_completed_seconds(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])
    m = sm.get_next_pending_milestone()
    sm.update_milestone_status(m.id, MilestoneStatus.IN_PROGRESS)
    sm.update_milestone_status(m.id, MilestoneStatus.COMPLETED)

    with sm._connect() as conn:
        conn.execute(
            "UPDATE milestones SET started_at = ?, completed_at = ? WHERE id = ?",
            ("2024-01-01 00:00:00", "2024-01-01 00:01:30", m.id),
        )

    summary = sm.get_progress_summary()

    assert summary.average_completed_seconds == 90.0


def test_progress_summary_estimated_remaining_is_none_without_a_completed_duration(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}])

    summary = sm.get_progress_summary()

    assert summary.estimated_remaining_seconds is None


def test_progress_summary_estimated_remaining_is_average_times_pending_and_in_progress(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones(
        [
            {"title": "A", "description": "d"},
            {"title": "B", "description": "d"},
            {"title": "C", "description": "d"},
        ]
    )
    a = sm.get_next_pending_milestone()
    sm.update_milestone_status(a.id, MilestoneStatus.IN_PROGRESS)
    sm.update_milestone_status(a.id, MilestoneStatus.COMPLETED)
    with sm._connect() as conn:
        conn.execute(
            "UPDATE milestones SET started_at = ?, completed_at = ? WHERE id = ?",
            ("2024-01-01 00:00:00", "2024-01-01 00:01:00", a.id),
        )
    b = sm.get_next_pending_milestone()
    sm.update_milestone_status(b.id, MilestoneStatus.IN_PROGRESS)
    # C remains PENDING

    summary = sm.get_progress_summary()

    assert summary.average_completed_seconds == 60.0
    assert summary.pending == 1
    assert summary.in_progress == 1
    assert summary.estimated_remaining_seconds == 120.0


def test_progress_summary_halted_milestone_counts_toward_total_not_estimate(tmp_path):
    sm = _sm(tmp_path)
    sm.create_milestones(
        [
            {"title": "A", "description": "d"},
            {"title": "B", "description": "d"},
        ]
    )
    a = sm.get_next_pending_milestone()
    sm.update_milestone_status(a.id, MilestoneStatus.IN_PROGRESS)
    sm.update_milestone_status(a.id, MilestoneStatus.COMPLETED)
    with sm._connect() as conn:
        conn.execute(
            "UPDATE milestones SET started_at = ?, completed_at = ? WHERE id = ?",
            ("2024-01-01 00:00:00", "2024-01-01 00:01:00", a.id),
        )
    b = sm.get_next_pending_milestone()
    sm.update_milestone_status(b.id, MilestoneStatus.HALTED, last_error="boom")

    summary = sm.get_progress_summary()

    assert summary.halted == 1
    assert summary.total == 2
    assert summary.pending == 0
    assert summary.in_progress == 0
    assert summary.estimated_remaining_seconds == 0.0


def test_progress_summary_falls_back_to_python_when_native_unavailable(tmp_path, monkeypatch):
    # Forces the pure-Python aggregation path regardless of whether the
    # native/progress_stats extension happens to be built in this
    # environment -- CI never builds it, so this is what CI always runs,
    # but a local dev machine with the extension built would otherwise
    # never exercise this path via the public method.
    monkeypatch.setattr(state_manager_module, "_native_compute_progress_stats", None)
    sm = _sm(tmp_path)
    sm.create_milestones([{"title": "A", "description": "d"}, {"title": "B", "description": "d"}])
    a = sm.get_next_pending_milestone()
    sm.update_milestone_status(a.id, MilestoneStatus.IN_PROGRESS)

    summary = sm.get_progress_summary()

    assert summary.total == 2
    assert summary.in_progress == 1
    assert summary.current_milestone_title == "A"


@pytest.mark.skipif(
    not _NATIVE_AVAILABLE,
    reason="native/progress_stats extension not built -- see NATIVE_EXTENSIONS.md",
)
def test_native_and_python_aggregation_agree_on_random_inputs():
    # Differential test: the native (pybind11/C++) and pure-Python
    # aggregation paths must agree on every field, across many randomly
    # generated synthetic milestone lists -- this is the actual proof that
    # swapping to the native path doesn't change behavior, not just an
    # assumption.
    statuses = ["PENDING", "IN_PROGRESS", "COMPLETED", "HALTED"]
    rng = random.Random(42)

    for _ in range(200):
        n = rng.randint(0, 15)
        parsed = []
        for i in range(n):
            status = rng.choice(statuses)
            has_duration = rng.random() < 0.5
            duration = rng.uniform(0, 10_000) if has_duration else 0.0
            parsed.append((status, f"milestone-{i}", rng.randint(0, 5), has_duration, duration))

        python_result = StateManager._aggregate_progress_python(parsed)
        native_result = StateManager._aggregate_progress_native(parsed)

        assert python_result.total == native_result.total
        assert python_result.pending == native_result.pending
        assert python_result.in_progress == native_result.in_progress
        assert python_result.completed == native_result.completed
        assert python_result.halted == native_result.halted
        assert python_result.current_milestone_title == native_result.current_milestone_title
        assert python_result.current_milestone_attempts == native_result.current_milestone_attempts
        if python_result.average_completed_seconds is None:
            assert native_result.average_completed_seconds is None
        else:
            assert native_result.average_completed_seconds is not None
            assert python_result.average_completed_seconds == pytest.approx(
                native_result.average_completed_seconds
            )
        if python_result.estimated_remaining_seconds is None:
            assert native_result.estimated_remaining_seconds is None
        else:
            assert native_result.estimated_remaining_seconds is not None
            assert python_result.estimated_remaining_seconds == pytest.approx(
                native_result.estimated_remaining_seconds
            )
