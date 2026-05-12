"""
OPC UA client abstraction with Mock (Mode A/B) and Real (Mode C) implementations.

Mock simulates realistic state transitions and execution timing.
Real client uses asyncua to call StartRobotOperation on the Montrac server.
"""
from __future__ import annotations
import asyncio
import random
from typing import Callable, Awaitable

# µs execution times per operation ID
_OP_DURATIONS: dict[int, float] = {
    10: 2.0,   # MOVE TO HOME
    11: 1.5,   # MOVE LIN CART
    12: 1.2,   # MOVE PTP CART
    13: 0.8,   # MOVE PTP AXIS
    20: 3.2,   # PICK
    21: 2.8,   # PICK VERTICAL
    30: 3.0,   # PLACE
    31: 2.6,   # PLACE VERTICAL
    40: 1.8,   # APPROACH
    41: 1.5,   # APPROACH VERTICAL
    50: 1.0,   # BRAKE TEST
    51: 0.4,   # RUN TOOL CMD
    200: 3.8,  # PICK PTP
    201: 4.2,  # PICK PTP RSI
}

StateChangeFn = Callable[..., Awaitable[None]]


class MockOpcUaClient:
    """
    Simulates OPC UA robot operations without a live server.
    Used in Mode A (R&D) and Mode B (Planning/dry-run).
    Failure probability: 5% per operation.
    """

    def __init__(self, on_state_change: StateChangeFn) -> None:
        self._on = on_state_change

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def start_robot_operation(
        self, robot_id: int, program_number: int, params: list[int]
    ) -> tuple[int, asyncio.Future]:
        """Returns (call_id, future) — future resolves to result_code when done."""
        call_id = random.randint(1000, 9999)
        loop = asyncio.get_running_loop()
        done_future: asyncio.Future[int] = loop.create_future()
        asyncio.create_task(
            self._simulate(robot_id, program_number, call_id, done_future, params)
        )
        return call_id, done_future

    async def _simulate(
        self,
        robot_id: int,
        program_number: int,
        call_id: int,
        done_future: asyncio.Future,
        params: list[int],
    ) -> None:
        tcp_x = params[0] / 1000.0 if len(params) > 0 else 0.0
        tcp_y = params[1] / 1000.0 if len(params) > 1 else 0.0
        tcp_z = params[2] / 1000.0 if len(params) > 2 else 0.0
        tcp_a = params[3] / 1000.0 if len(params) > 3 else 0.0

        # Robot goes busy
        await self._on(
            robot_id,
            busy=True,
            ready=False,
            program_busy=True,
            program_done=False,
            program_error=False,
            program_number=program_number,
            call_id=call_id,
        )

        duration = _OP_DURATIONS.get(program_number, 2.0)
        # Add ±20% jitter for realism
        await asyncio.sleep(duration * random.uniform(0.8, 1.2))

        # Simulate TCP position update mid-move
        await self._on(robot_id, tcp_x=tcp_x, tcp_y=tcp_y, tcp_z=tcp_z, tcp_a=tcp_a)

        success = random.random() > 0.05
        result_code = 0 if success else random.randint(1, 8)

        await self._on(
            robot_id,
            busy=False,
            ready=True,
            program_busy=False,
            program_done=True,
            program_error=not success,
            result_code=result_code,
        )

        if not done_future.done():
            done_future.set_result(result_code)


class RealOpcUaClient:
    """
    Real OPC UA client for Mode C (live testbed).
    Requires asyncua. URL: opc.tcp://<host>:4840
    """

    OPC_METHOD_NODE = [
        "0:Objects", "4:W1", "4:Control", "4:StartRobotOperation"
    ]
    OPC_ROBOT_BASE = "Root.Objects.W1.Robots.Robots"

    def __init__(self, url: str, on_state_change: StateChangeFn) -> None:
        self._url = url
        self._on = on_state_change
        self._client = None
        self._sub = None
        self._sub_tasks: list[asyncio.Task] = []

    async def connect(self) -> None:
        from asyncua import Client
        self._client = Client(self._url)
        await self._client.connect()
        await self._start_subscriptions()

    async def disconnect(self) -> None:
        for t in self._sub_tasks:
            t.cancel()
        if self._sub:
            await self._sub.delete()
        if self._client:
            await self._client.disconnect()

    async def start_robot_operation(
        self, robot_id: int, program_number: int, params: list[int]
    ) -> tuple[int, asyncio.Future]:
        assert self._client, "Not connected"
        idx = robot_id - 1
        method_path = (
            f"Root.Objects.W1.Control.StartRobotOperation"
        )
        node = await self._client.nodes.root.get_child(
            ["0:Objects", "4:W1", "4:Control"]
        )
        result = await node.call_method(
            "4:StartRobotOperation",
            robot_id,
            program_number,
            params + [0] * (20 - len(params)),
        )
        call_id = int(result[0])
        loop = asyncio.get_running_loop()
        done_future: asyncio.Future[int] = loop.create_future()
        # Poll Program.Done for this robot
        asyncio.create_task(self._wait_for_done(robot_id, call_id, done_future))
        return call_id, done_future

    async def _wait_for_done(
        self, robot_id: int, call_id: int, done_future: asyncio.Future
    ) -> None:
        idx = robot_id - 1
        base = f"Root.Objects.W1.Robots.Robots[{idx}].Status.Program"
        done_node = await self._client.nodes.root.get_child(
            [f"0:Objects", f"4:W1", f"4:Robots", f"4:Robots[{idx}]",
             f"4:Status", f"4:Program", f"4:Done"]
        )
        result_node = await self._client.nodes.root.get_child(
            [f"0:Objects", f"4:W1", f"4:Robots", f"4:Robots[{idx}]",
             f"4:Status", f"4:Program", f"4:Program_Data", f"4:Result_Code"]
        )
        while True:
            await asyncio.sleep(0.2)
            done_val = await done_node.read_value()
            if done_val:
                result_code = await result_node.read_value()
                if not done_future.done():
                    done_future.set_result(int(result_code))
                return

    async def _start_subscriptions(self) -> None:
        # Subscribe to key robot state nodes for all 3 robots
        from asyncua import ua
        handler = _OpcUaSubHandler(self._on)
        self._sub = await self._client.create_subscription(100, handler)
        nodes_to_watch = []
        for idx in range(3):
            robot_id = idx + 1
            prefix = f"Root.Objects.W1.Robots.Robots[{idx}]"
            paths = [
                ("Enabled", "enabled"),
                ("Status.In_Home", "ready"),
                ("Status.Error_ID", "error_id"),
                ("Status.Program.Busy", "program_busy"),
                ("Status.Program.Ready", "program_ready"),
                ("Tool.Status.ID", "tool_id"),
                ("Tool.Status.Attached", "tool_attached"),
            ]
            for path, attr in paths:
                full_path = [f"0:Objects", f"4:W1", f"4:Robots",
                             f"4:Robots[{idx}]"] + [f"4:{p}" for p in path.split(".")]
                node = await self._client.nodes.root.get_child(full_path)
                nodes_to_watch.append((node, robot_id, attr))

        handles = await self._sub.subscribe_data_change(
            [n for n, _, _ in nodes_to_watch]
        )
        handler.register(nodes_to_watch)


class _OpcUaSubHandler:
    def __init__(self, on_state_change: StateChangeFn) -> None:
        self._on = on_state_change
        self._node_map: dict = {}

    def register(self, nodes: list) -> None:
        for node, robot_id, attr in nodes:
            self._node_map[node.nodeid] = (robot_id, attr)

    def datachange_notification(self, node, val, data) -> None:
        if node.nodeid in self._node_map:
            robot_id, attr = self._node_map[node.nodeid]
            asyncio.create_task(self._on(robot_id, **{attr: val}))
