from amao.models import MilestoneStatus
from amao.state_manager import StateManager


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
