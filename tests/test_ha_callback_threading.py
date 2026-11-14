"""Guard Home Assistant dispatcher and timer callbacks against executor dispatch.

Home Assistant calls unmarked callbacks in its executor. These tests exercise the
registered callback bodies and verify they carry the @callback marker so that
hass.async_create_task stays on the event loop. No HA installation is required.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "powerlink"


def _load_nested_callback(filename: str, outer: str, name: str, globals_: dict):
    """Compile the real nested callback with test-controlled dependencies."""
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    parent = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == outer)
    inner = next(node for node in parent.body if isinstance(node, ast.FunctionDef) and node.name == name)
    code = compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(inner)], type_ignores=[])), filename, "exec")
    namespace = dict(globals_)
    exec(code, namespace)
    return namespace[name]


def _callback(func):
    """Mimic Home Assistant's callback marker as used for thread selection."""
    func._hass_callback = True
    return func


class CallbackThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensor_dispatcher_callback_schedules_update_on_ha_loop(self):
        loop = asyncio.get_running_loop()
        calls = []
        tasks = []

        async def update():
            calls.append("updated")

        class Hass:
            def async_create_task(self, coro):
                self_loop = asyncio.get_running_loop()
                if self_loop is not loop:
                    raise RuntimeError("Called from a thread outside the event loop")
                task = loop.create_task(coro)
                tasks.append(task)
                return task

        changed = _load_nested_callback(
            "sensor.py", "async_setup_entry", "changed",
            {"callback": _callback, "hass": Hass(), "update": update},
        )
        self.assertTrue(getattr(changed, "_hass_callback", False))
        changed()
        await asyncio.gather(*tasks)
        self.assertEqual(calls, ["updated"])

        # The registered dispatcher target must be this callback, not a lambda.
        code = ast.parse((ROOT / "sensor.py").read_text(encoding="utf-8"))
        setup = next(n for n in code.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_setup_entry")
        connects = [n for n in ast.walk(setup) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "async_dispatcher_connect"]
        self.assertEqual(len(connects), 1)
        self.assertEqual(connects[0].args[-1].id, "changed")

    async def test_dashboard_timer_callback_stays_on_loop_and_triggers_refresh(self):
        loop = asyncio.get_running_loop()
        calls = []

        def changed():
            self.assertIs(asyncio.get_running_loop(), loop)
            calls.append("refresh")

        clock_tick = _load_nested_callback(
            "dashboard_ws.py", "ws_subscribe_dashboard", "clock_tick",
            {"callback": _callback, "changed": changed},
        )
        self.assertTrue(getattr(clock_tick, "_hass_callback", False))
        clock_tick(None)
        self.assertEqual(calls, ["refresh"])

        # The timer must receive the decorated callback, not an unmarked lambda.
        code = ast.parse((ROOT / "dashboard_ws.py").read_text(encoding="utf-8"))
        setup = next(n for n in code.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "ws_subscribe_dashboard")
        timers = [n for n in ast.walk(setup) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "async_track_time_interval"]
        self.assertEqual(len(timers), 1)
        self.assertIsInstance(timers[0].args[1], ast.Name)
        self.assertEqual(timers[0].args[1].id, "clock_tick")


if __name__ == "__main__":
    unittest.main()
