"""Mission objective evaluation tests (Phase 37)."""

from __future__ import annotations

from wraithnet.objectives import MissionObjective, evaluate_mission


def _destroy(*subject_ids: str, required: bool = True) -> MissionObjective:
    return MissionObjective(
        objective_id="OBJ_DESTROY",
        objective_type="DESTROY",
        subject_ids=list(subject_ids),
        required=required,
    )


def _protect(*subject_ids: str, required: bool = True) -> MissionObjective:
    return MissionObjective(
        objective_id="OBJ_PROTECT",
        objective_type="PROTECT",
        subject_ids=list(subject_ids),
        required=required,
    )


def _survive(*subject_ids: str, until_tick: int = 10, required: bool = True) -> MissionObjective:
    return MissionObjective(
        objective_id="OBJ_SURVIVE",
        objective_type="SURVIVE",
        subject_ids=list(subject_ids),
        required=required,
        until_tick=until_tick,
    )


def _return(*subject_ids: str, required: bool = True) -> MissionObjective:
    return MissionObjective(
        objective_id="OBJ_RETURN",
        objective_type="RETURN",
        subject_ids=list(subject_ids),
        required=required,
    )


def test_destroy_completes_when_all_subjects_destroyed():
    obj = _destroy("T1", "T2")
    pending = evaluate_mission([obj], tick=1, destroyed_ids={"T1"})
    assert pending.result == "RUNNING"
    assert pending.objective_status["OBJ_DESTROY"] == "PENDING"

    done = evaluate_mission([obj], tick=2, destroyed_ids={"T1", "T2"})
    assert done.result == "SUCCESS"
    assert done.objective_status["OBJ_DESTROY"] == "COMPLETE"
    assert done.solved is True


def test_protect_fails_on_subject_loss_and_completes_with_others():
    destroy = MissionObjective(
        objective_id="OBJ_D",
        objective_type="DESTROY",
        subject_ids=["Enemy"],
        required=True,
    )
    protect = _protect("VIP")

    failed = evaluate_mission(
        [destroy, protect],
        tick=1,
        destroyed_ids={"VIP"},
    )
    assert failed.result == "FAILURE"
    assert failed.objective_status["OBJ_PROTECT"] == "FAILED"

    complete = evaluate_mission(
        [destroy, protect],
        tick=2,
        destroyed_ids={"Enemy"},
    )
    assert complete.result == "SUCCESS"
    assert complete.objective_status["OBJ_PROTECT"] == "COMPLETE"
    assert complete.objective_status["OBJ_D"] == "COMPLETE"


def test_survive_fails_before_until_tick_and_completes_at_deadline():
    obj = _survive("Jet_1", until_tick=5)
    early = evaluate_mission([obj], tick=3, destroyed_ids=set())
    assert early.result == "RUNNING"
    assert early.objective_status["OBJ_SURVIVE"] == "PENDING"

    lost = evaluate_mission([obj], tick=3, destroyed_ids={"Jet_1"})
    assert lost.result == "FAILURE"
    assert lost.objective_status["OBJ_SURVIVE"] == "FAILED"

    done = evaluate_mission([obj], tick=5, destroyed_ids=set())
    assert done.result == "SUCCESS"
    assert done.objective_status["OBJ_SURVIVE"] == "COMPLETE"


def test_return_completes_and_fails_on_destroy_or_unreachable():
    obj = _return("Jet_1", "Jet_2")
    pending = evaluate_mission(
        [obj], tick=1, destroyed_ids=set(), returned_ids={"Jet_1"}
    )
    assert pending.result == "RUNNING"

    done = evaluate_mission(
        [obj], tick=2, destroyed_ids=set(), returned_ids={"Jet_1", "Jet_2"}
    )
    assert done.result == "SUCCESS"

    destroyed = evaluate_mission(
        [obj], tick=2, destroyed_ids={"Jet_1"}, returned_ids={"Jet_2"}
    )
    assert destroyed.result == "FAILURE"

    unreachable = evaluate_mission(
        [obj],
        tick=2,
        destroyed_ids=set(),
        returned_ids={"Jet_1"},
        unreachable_ids={"Jet_2"},
    )
    assert unreachable.result == "FAILURE"


def test_required_failure_precedes_simultaneous_success():
    destroy = MissionObjective(
        objective_id="OBJ_D",
        objective_type="DESTROY",
        subject_ids=["T1"],
        required=True,
    )
    protect = MissionObjective(
        objective_id="OBJ_P",
        objective_type="PROTECT",
        subject_ids=["VIP"],
        required=True,
    )
    # DESTROY complete and PROTECT failed in the same evaluation.
    result = evaluate_mission(
        [destroy, protect],
        tick=4,
        destroyed_ids={"T1", "VIP"},
    )
    assert result.result == "FAILURE"
    assert result.objective_status["OBJ_D"] == "COMPLETE"
    assert result.objective_status["OBJ_P"] == "FAILED"


def test_timeout_when_required_still_pending_at_tick_limit():
    obj = _destroy("T1")
    result = evaluate_mission(
        [obj],
        tick=100,
        destroyed_ids=set(),
        tick_limit=100,
    )
    assert result.result == "TIMEOUT"
    assert result.solved is False


def test_aborted_when_no_friendlies_and_active_progress_unresolved():
    destroy = _destroy("T1")
    survive = MissionObjective(
        objective_id="OBJ_S",
        objective_type="SURVIVE",
        subject_ids=["Ally"],
        required=True,
        until_tick=50,
    )

    aborted = evaluate_mission(
        [destroy],
        tick=10,
        destroyed_ids=set(),
        no_friendly_platforms=True,
        friendly_can_progress=False,
    )
    assert aborted.result == "ABORTED"

    # Passive SURVIVE alone keeps RUNNING when friendlies are gone.
    waiting = evaluate_mission(
        [survive],
        tick=10,
        destroyed_ids=set(),
        no_friendly_platforms=True,
    )
    assert waiting.result == "RUNNING"
    assert waiting.objective_status["OBJ_S"] == "PENDING"


def test_optional_objectives_do_not_gate_global_result():
    required = _destroy("T1", required=True)
    optional = MissionObjective(
        objective_id="OBJ_OPT",
        objective_type="DESTROY",
        subject_ids=["Bonus"],
        required=False,
    )
    result = evaluate_mission(
        [required, optional],
        tick=1,
        destroyed_ids={"T1"},
    )
    assert result.result == "SUCCESS"
    assert result.objective_status["OBJ_OPT"] == "PENDING"
