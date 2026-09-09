"""Mission objectives and termination evaluation (Phase 37)."""

from __future__ import annotations

from typing import Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wraithnet.taxonomy import Faction

ObjectiveType = Literal["DESTROY", "PROTECT", "SURVIVE", "RETURN"]
ObjectiveStatus = Literal["PENDING", "COMPLETE", "FAILED"]
MissionResult = Literal["RUNNING", "SUCCESS", "FAILURE", "ABORTED", "TIMEOUT"]


class MissionObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_id: str
    objective_type: ObjectiveType
    subject_ids: list[str] = Field(min_length=1)
    required: bool = True
    faction: Faction = "FRIENDLY"
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    until_tick: int | None = None

    @model_validator(mode="after")
    def _validate_until_tick(self) -> MissionObjective:
        if self.objective_type == "SURVIVE":
            if self.until_tick is None or self.until_tick < 1:
                raise ValueError(
                    f"SURVIVE objective '{self.objective_id}' requires "
                    "until_tick >= 1."
                )
        elif self.until_tick is not None:
            raise ValueError(
                f"{self.objective_type} objective '{self.objective_id}' "
                "must not set until_tick."
            )
        return self


def synthesize_legacy_destroy_objective(
    target_ids: Sequence[str],
    *,
    objective_id: str = "OBJ_DESTROY_ALL",
) -> MissionObjective:
    """Legacy win condition: destroy every target and SAM subject."""
    return MissionObjective(
        objective_id=objective_id,
        objective_type="DESTROY",
        subject_ids=list(target_ids),
        required=True,
        faction="FRIENDLY",
        priority=0.5,
    )


class MissionEvaluation:
    """Result of evaluating all objectives at a tick."""

    __slots__ = ("result", "objective_status", "solved", "mission_aborted", "swarm_wiped_out")

    def __init__(
        self,
        result: MissionResult,
        objective_status: dict[str, ObjectiveStatus],
        *,
        solved: bool,
        mission_aborted: bool,
        swarm_wiped_out: bool,
    ) -> None:
        self.result = result
        self.objective_status = objective_status
        self.solved = solved
        self.mission_aborted = mission_aborted
        self.swarm_wiped_out = swarm_wiped_out


def _destroyed(subject_id: str, destroyed_ids: Mapping[str, bool] | set[str]) -> bool:
    if isinstance(destroyed_ids, set):
        return subject_id in destroyed_ids
    return bool(destroyed_ids.get(subject_id, False))


def _evaluate_one(
    obj: MissionObjective,
    *,
    tick: int,
    destroyed_ids: Mapping[str, bool] | set[str],
    returned_ids: set[str],
    unreachable_ids: set[str],
    non_protect_required_complete: bool,
) -> ObjectiveStatus:
    subjects = obj.subject_ids
    if obj.objective_type == "DESTROY":
        if all(_destroyed(s, destroyed_ids) for s in subjects):
            return "COMPLETE"
        return "PENDING"

    if obj.objective_type == "PROTECT":
        if any(_destroyed(s, destroyed_ids) for s in subjects):
            return "FAILED"
        if non_protect_required_complete and all(
            not _destroyed(s, destroyed_ids) for s in subjects
        ):
            return "COMPLETE"
        return "PENDING"

    if obj.objective_type == "SURVIVE":
        assert obj.until_tick is not None
        if any(_destroyed(s, destroyed_ids) for s in subjects):
            return "FAILED"
        if tick >= obj.until_tick:
            return "COMPLETE"
        return "PENDING"

    # RETURN
    if any(_destroyed(s, destroyed_ids) for s in subjects):
        return "FAILED"
    if any(s in unreachable_ids for s in subjects):
        return "FAILED"
    if all(s in returned_ids for s in subjects):
        return "COMPLETE"
    return "PENDING"


def evaluate_mission(
    objectives: Sequence[MissionObjective],
    *,
    tick: int,
    destroyed_ids: Mapping[str, bool] | set[str],
    returned_ids: set[str] | None = None,
    unreachable_ids: set[str] | None = None,
    friendly_can_progress: bool = True,
    tick_limit: int | None = None,
    no_friendly_platforms: bool = False,
    any_friendly_returned: bool = False,
) -> MissionEvaluation:
    """Apply Phase 37 objective precedence and derive legacy metric flags."""
    returned_ids = returned_ids or set()
    unreachable_ids = unreachable_ids or set()
    if not objectives:
        # No objectives → treat as immediate success (should not pass schema).
        return MissionEvaluation(
            "SUCCESS",
            {},
            solved=True,
            mission_aborted=False,
            swarm_wiped_out=False,
        )

    # First pass: non-PROTECT statuses (PROTECT completion depends on them).
    provisional: dict[str, ObjectiveStatus] = {}
    for obj in objectives:
        if obj.objective_type == "PROTECT":
            continue
        provisional[obj.objective_id] = _evaluate_one(
            obj,
            tick=tick,
            destroyed_ids=destroyed_ids,
            returned_ids=returned_ids,
            unreachable_ids=unreachable_ids,
            non_protect_required_complete=False,
        )

    non_protect_required = [
        o for o in objectives if o.required and o.objective_type != "PROTECT"
    ]
    non_protect_required_complete = all(
        provisional.get(o.objective_id) == "COMPLETE" for o in non_protect_required
    )

    statuses: dict[str, ObjectiveStatus] = dict(provisional)
    for obj in objectives:
        if obj.objective_type != "PROTECT":
            continue
        statuses[obj.objective_id] = _evaluate_one(
            obj,
            tick=tick,
            destroyed_ids=destroyed_ids,
            returned_ids=returned_ids,
            unreachable_ids=unreachable_ids,
            non_protect_required_complete=non_protect_required_complete,
        )

    required = [o for o in objectives if o.required]
    required_statuses = [statuses[o.objective_id] for o in required]

    if any(s == "FAILED" for s in required_statuses):
        result: MissionResult = "FAILURE"
    elif all(s == "COMPLETE" for s in required_statuses):
        result = "SUCCESS"
    elif tick_limit is not None and tick >= tick_limit:
        result = "TIMEOUT"
    elif no_friendly_platforms:
        # Active-progress objectives unresolved with no friendlies left.
        active_unresolved = [
            o
            for o in required
            if statuses[o.objective_id] == "PENDING"
            and o.objective_type in ("DESTROY", "RETURN")
        ]
        if active_unresolved and not friendly_can_progress:
            result = "ABORTED"
        elif active_unresolved:
            result = "ABORTED"
        else:
            result = "RUNNING"
    else:
        result = "RUNNING"

    solved = result == "SUCCESS"
    # Legacy flags: wipeout vs aborted (fuel RTB).
    swarm_wiped_out = (
        result in ("FAILURE", "ABORTED")
        and no_friendly_platforms
        and not any_friendly_returned
    )
    mission_aborted = (
        result == "ABORTED" and any_friendly_returned
    ) or (
        result == "ABORTED" and no_friendly_platforms and any_friendly_returned
    )

    return MissionEvaluation(
        result,
        statuses,
        solved=solved,
        mission_aborted=mission_aborted,
        swarm_wiped_out=swarm_wiped_out,
    )
