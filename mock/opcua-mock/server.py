"""
Montrac OPC UA Mock Server (ns=4)

Exposes the full W1 address space used by the RICAIP testbed:
  Root.Objects.W1.Robots.Robots[0..2]  — robot status + program nodes
  Root.Objects.W1.Control              — StartRobotOperation / ToggleRobot methods
  Root.Objects.W1.Status               — E-stop, safety flags
  Root.Objects.W1.Montrac.Status       — conveyor system running

When StartRobotOperation is called, the server simulates execution:
  robot goes Busy=True → waits op_duration ± 20% → Done=True, Result_Code=0

Endpoint: opc.tcp://0.0.0.0:4840/ricaip
Namespace: http://prague.ti40.cz/factory/  (index 4)
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

from asyncua import Server, ua
from asyncua.server.history import HistoryManager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("opcua-mock")

CONFIG_PATH = Path(os.environ.get("MOCK_CONFIG", "/app/config/montrac_mock.json"))


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    # fallback inline defaults
    return {
        "endpoint": "opc.tcp://0.0.0.0:4840/ricaip",
        "namespace": "http://prague.ti40.cz/factory/",
        "robots": [
            {"index": 0, "id": 1, "label": "R1", "tool_id": 2,  "tool_attached": True},
            {"index": 1, "id": 2, "label": "R2", "tool_id": 10, "tool_attached": True},
            {"index": 2, "id": 3, "label": "R3", "tool_id": 2,  "tool_attached": True},
        ],
        "toolchangers_per_robot": 4,
        "op_durations_s": {
            "10": 2.0, "11": 1.5, "12": 1.2, "13": 0.8,
            "20": 3.2, "21": 2.8, "30": 3.0, "31": 2.6,
            "40": 1.8, "41": 1.5, "50": 1.0, "51": 0.4,
            "200": 3.8, "201": 4.2,
        },
        "failure_probability": 0.05,
    }


class MontracMockServer:
    def __init__(self, config: dict) -> None:
        self.cfg = config
        self.server = Server()
        self.ns: int = 4
        # robot_nodes[robot_index] → dict of node handles
        self.robot_nodes: dict[int, dict[str, Any]] = {}
        self._call_counter = 1000

    async def init(self) -> None:
        await self.server.init()
        self.server.set_endpoint(self.cfg["endpoint"])
        self.server.set_server_name("RICAIP Montrac Mock Server")

        # Force namespace index 4 to match the real testbed (ns=0 OPC UA std,
        # ns=1 server, ns=2 and ns=3 are dummies, ns=4 is the factory namespace).
        uri = self.cfg["namespace"]
        await self.server.register_namespace("urn:mock:dummy:ns2")
        await self.server.register_namespace("urn:mock:dummy:ns3")
        idx = await self.server.register_namespace(uri)
        if idx != 4:
            log.warning("Expected ns=4 for factory namespace, got %d — clients must adjust", idx)
        self.ns = idx

        objects = self.server.nodes.objects

        # ── W1 root ──────────────────────────────────────────────────────────
        w1 = await objects.add_object(self.ns, "W1")

        # ── W1.Status ────────────────────────────────────────────────────────
        status = await w1.add_object(self.ns, "Status")
        self.estop_ok = await status.add_variable(self.ns, "Emergency_Stop_OK", True)
        self.safety_ok = await status.add_variable(self.ns, "Operator_Safety_OK", True)
        await self.estop_ok.set_writable()
        await self.safety_ok.set_writable()

        # ── W1.Control ───────────────────────────────────────────────────────
        control = await w1.add_object(self.ns, "Control")
        await self._add_start_robot_operation(control)
        await self._add_toggle_robot(control)

        # ── W1.Robots ────────────────────────────────────────────────────────
        robots_folder = await w1.add_object(self.ns, "Robots")
        for robot_cfg in self.cfg["robots"]:
            await self._build_robot(robots_folder, robot_cfg)

        # ── W1.Montrac ───────────────────────────────────────────────────────
        montrac = await w1.add_object(self.ns, "Montrac")
        montrac_status = await montrac.add_object(self.ns, "Status")
        self.conveyor_running = await montrac_status.add_variable(
            self.ns, "montracSystemRunning", True
        )
        await self.conveyor_running.set_writable()

        log.info("Address space built — ns=%d, endpoint=%s", self.ns, self.cfg["endpoint"])

    async def _build_robot(self, parent, robot_cfg: dict) -> None:
        idx = robot_cfg["index"]
        name = f"Robots[{idx}]"
        robot_obj = await parent.add_object(self.ns, name)

        # Enabled
        enabled = await robot_obj.add_variable(self.ns, "Enabled", True)
        await enabled.set_writable()

        # Status
        status = await robot_obj.add_object(self.ns, "Status")
        nodes: dict[str, Any] = {"enabled": enabled}

        async def av(parent_node, n, val):
            v = await parent_node.add_variable(self.ns, n, val)
            await v.set_writable()
            return v

        nodes["id"]                = await av(status, "ID", robot_cfg["id"])
        nodes["mode"]              = await av(status, "Mode", 0)
        nodes["in_home"]           = await av(status, "In_Home", True)
        nodes["in_standstill"]     = await av(status, "In_Standstill", True)
        nodes["manually_disabled"] = await av(status, "Manually_Disabled", False)
        nodes["error_id"]          = await av(status, "Error_ID", ua.UInt32(0))
        nodes["base_frame_id"]     = await av(status, "Base_Frame_ID", 11)
        nodes["tool_frame_id"]     = await av(status, "Tool_Frame_ID", robot_cfg["tool_id"])

        # TCP position (stored as 6 floats)
        nodes["tcp_x"] = await av(status, "TCP_X", 0.0)
        nodes["tcp_y"] = await av(status, "TCP_Y", 0.0)
        nodes["tcp_z"] = await av(status, "TCP_Z", 0.0)
        nodes["tcp_a"] = await av(status, "TCP_A", 0.0)

        # Program status
        prog = await status.add_object(self.ns, "Program")
        nodes["prog_busy"]    = await av(prog, "Busy", False)
        nodes["prog_done"]    = await av(prog, "Done", False)
        nodes["prog_error"]   = await av(prog, "Error", False)
        nodes["prog_ready"]   = await av(prog, "Ready", True)

        prog_data = await prog.add_object(self.ns, "Program_Data")
        nodes["prog_number"]  = await av(prog_data, "Program_Number", ua.UInt32(0))
        nodes["call_id"]      = await av(prog_data, "Call_ID", ua.UInt32(0))
        nodes["comm_id"]      = await av(prog_data, "Communication_ID", "")
        nodes["result_code"]  = await av(prog_data, "Result_Code", ua.UInt32(0))

        param_nodes = []
        result_nodes = []
        for k in range(1, 21):
            p = await av(prog_data, f"Parameter_Array[{k}]", 0)
            r = await av(prog_data, f"Result_Array[{k}]", 0)
            param_nodes.append(p)
            result_nodes.append(r)
        nodes["param_array"]  = param_nodes
        nodes["result_array"] = result_nodes

        # Tool
        tool_obj = await robot_obj.add_object(self.ns, "Tool")
        tool_status = await tool_obj.add_object(self.ns, "Status")
        nodes["tool_attached"] = await av(tool_status, "Attached", robot_cfg["tool_attached"])
        nodes["tool_id"]       = await av(tool_status, "ID", robot_cfg["tool_id"])

        # Toolchangers
        tc_folder = await robot_obj.add_object(self.ns, "Toolchangers")
        for slot in range(1, self.cfg["toolchangers_per_robot"] + 1):
            tc = await tc_folder.add_object(self.ns, f"Toolchangers[{slot}]")
            tc_s = await tc.add_object(self.ns, "Status")
            await av(tc_s, "Available", True)
            await av(tc_s, "Occupied", slot == 1)
            tc_tool = await tc.add_object(self.ns, "Tool")
            tc_tool_s = await tc_tool.add_object(self.ns, "Status")
            await av(tc_tool_s, "Attached", False)
            await av(tc_tool_s, "ID", 0)

        self.robot_nodes[idx] = nodes
        log.info("  robot %s (index %d) built", robot_cfg["label"], idx)

    async def _add_start_robot_operation(self, parent) -> None:
        method_node = await parent.add_method(
            self.ns,
            "StartRobotOperation",
            self._start_robot_operation_cb,
            [
                ua.VariantType.Int32,   # Robot ID
                ua.VariantType.Int32,   # Program Number
                ua.VariantType.Int32,   # Parameter_Array (flat, 20 ints)
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
                ua.VariantType.Int32,
            ],
            [ua.VariantType.UInt32, ua.VariantType.UInt32],  # call_id, result_code
        )

    async def _start_robot_operation_cb(self, parent, robot_id, program_number, *params):
        robot_id = int(robot_id)
        program_number = int(program_number)
        params = [int(p) for p in params]

        idx = robot_id - 1
        if idx not in self.robot_nodes:
            return [ua.UInt32(0), ua.UInt32(99)]  # unknown robot

        self._call_counter += 1
        call_id = self._call_counter

        log.info("StartRobotOperation robot=%d prog=%d call_id=%d", robot_id, program_number, call_id)
        asyncio.create_task(self._simulate_op(idx, program_number, call_id, params))
        return [ua.UInt32(call_id), ua.UInt32(0)]

    async def _simulate_op(self, idx: int, prog: int, call_id: int, params: list[int]) -> None:
        n = self.robot_nodes[idx]
        duration = float(self.cfg["op_durations_s"].get(str(prog), 2.0))
        duration *= random.uniform(0.8, 1.2)

        # Mark busy
        await n["prog_busy"].write_value(True)
        await n["prog_done"].write_value(False)
        await n["prog_error"].write_value(False)
        await n["prog_ready"].write_value(False)
        await n["prog_number"].write_value(ua.UInt32(prog))
        await n["call_id"].write_value(ua.UInt32(call_id))
        for i, v in enumerate(params[:20]):
            await n["param_array"][i].write_value(v)

        await asyncio.sleep(duration)

        # Update TCP position from params if linear/pick/place
        if prog in (11, 12, 20, 21, 30, 31, 40, 41) and len(params) >= 4:
            await n["tcp_x"].write_value(params[0] / 1000.0)
            await n["tcp_y"].write_value(params[1] / 1000.0)
            await n["tcp_z"].write_value(params[2] / 1000.0)
            await n["tcp_a"].write_value(params[3] / 1000.0)

        fail = random.random() < self.cfg["failure_probability"]
        result_code = random.randint(1, 8) if fail else 0

        await n["prog_busy"].write_value(False)
        await n["prog_done"].write_value(True)
        await n["prog_error"].write_value(fail)
        await n["prog_ready"].write_value(True)
        await n["result_code"].write_value(ua.UInt32(result_code))

        log.info("op done robot_idx=%d prog=%d call_id=%d result=%d", idx, prog, call_id, result_code)

    async def _add_toggle_robot(self, parent) -> None:
        await parent.add_method(
            self.ns,
            "ToggleRobot",
            self._toggle_robot_cb,
            [ua.VariantType.Int32, ua.VariantType.Boolean],
            [ua.VariantType.UInt32],
        )

    async def _toggle_robot_cb(self, parent, robot_id, enable):
        idx = int(robot_id) - 1
        if idx in self.robot_nodes:
            await self.robot_nodes[idx]["enabled"].write_value(bool(enable))
        return [ua.UInt32(0)]

    async def run(self) -> None:
        async with self.server:
            log.info("OPC UA mock server running at %s", self.cfg["endpoint"])
            while True:
                await asyncio.sleep(1)


async def main() -> None:
    cfg = _load_config()
    srv = MontracMockServer(cfg)
    await srv.init()
    await srv.run()


if __name__ == "__main__":
    asyncio.run(main())
