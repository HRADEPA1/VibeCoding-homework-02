from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional

from models import (
    WorkcellState, RobotState, Plan, PlanStep,
    StepStatus, PlanStatus, DigitalTwinSnapshot,
)


class DigitalTwin:
    def __init__(self) -> None:
        self._workcell = WorkcellState(
            estop_ok=True,
            conveyor_running=True,
            robots=[
                RobotState(
                    robot_id=i,
                    enabled=True,
                    ready=True,
                    tool_attached=True,
                    tool_id=2,
                    base_frame_id=11,
                )
                for i in [1, 2, 3]
            ],
        )
        self._plan: Optional[Plan] = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    # ── Snapshot ───────────────────────────────────────────────────────────

    def get_snapshot(self) -> DigitalTwinSnapshot:
        return DigitalTwinSnapshot(
            workcell=self._workcell.model_copy(deep=True),
            plan=self._plan.model_copy(deep=True) if self._plan else None,
        )

    # ── Mutations ──────────────────────────────────────────────────────────

    async def update_robot_state(self, robot_id: int, **kwargs) -> None:
        async with self._lock:
            for robot in self._workcell.robots:
                if robot.robot_id == robot_id:
                    for key, value in kwargs.items():
                        setattr(robot, key, value)
            self._workcell.timestamp = datetime.now(timezone.utc)
        await self._broadcast()

    async def set_plan(self, plan: Plan) -> None:
        async with self._lock:
            self._plan = plan
        await self._broadcast()

    async def update_step(self, step_index: int, **kwargs) -> None:
        async with self._lock:
            if self._plan:
                for step in self._plan.steps:
                    if step.step_index == step_index:
                        for key, value in kwargs.items():
                            setattr(step, key, value)
                        break
        await self._broadcast()

    async def set_plan_status(self, status: PlanStatus, **kwargs) -> None:
        async with self._lock:
            if self._plan:
                self._plan.status = status
                for key, value in kwargs.items():
                    setattr(self._plan, key, value)
        await self._broadcast()

    async def reset_plan(self) -> None:
        async with self._lock:
            if self._plan:
                for step in self._plan.steps:
                    step.status = StepStatus.PENDING
                    step.started_at = None
                    step.completed_at = None
                    step.call_id = None
                    step.result_code = None
                    step.duration_ms = None
                    step.error_message = None
                self._plan.status = PlanStatus.IDLE
                self._plan.started_at = None
                self._plan.completed_at = None
        await self._broadcast()

    async def toggle_estop(self, ok: bool) -> None:
        async with self._lock:
            self._workcell.estop_ok = ok
        await self._broadcast()

    async def toggle_conveyor(self, running: bool) -> None:
        async with self._lock:
            self._workcell.conveyor_running = running
        await self._broadcast()

    # ── Pub/sub ────────────────────────────────────────────────────────────

    def subscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.add(queue)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def _broadcast(self) -> None:
        snapshot = self.get_snapshot()
        msg = snapshot.model_dump_json()
        dead: set[asyncio.Queue] = set()
        for q in self._subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.add(q)
        self._subscribers -= dead
