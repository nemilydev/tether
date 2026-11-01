"""Unit tests for Lovense payload normalization and toy metadata parsing."""
import json
import unittest
from types import SimpleNamespace

from custom_components.tether.utils import (
    build_function_action,
    get_battery_level,
    is_device_connected,
    merge_callback_toys,
    next_actuator_state,
    normalize_toys,
    parse_toy_functions,
    stroke_control_type,
)


class StrokeControlTypeTests(unittest.TestCase):
    def test_options_override_setup_data_and_default_is_lights(self) -> None:
        key = "stroke_control_type"
        self.assertEqual(stroke_control_type(SimpleNamespace(data={}, options={})), "lights")
        self.assertEqual(
            stroke_control_type(SimpleNamespace(data={key: "numbers"}, options={})), "numbers"
        )
        self.assertEqual(
            stroke_control_type(
                SimpleNamespace(data={key: "numbers"}, options={key: "both"})
            ),
            "both",
        )


class ToyPayloadTests(unittest.TestCase):
    def test_normalize_toys_accepts_mapping_and_stringified_mapping(self) -> None:
        toys = {"toy-id": {"name": "lush", "status": "1"}}
        self.assertEqual(normalize_toys(toys), toys)
        self.assertEqual(normalize_toys(json.dumps(toys)), toys)

    def test_normalize_toys_extracts_data_toys_envelope(self) -> None:
        toys = {"toy-id": {"name": "lush"}}
        self.assertEqual(normalize_toys({"toys": json.dumps(toys)}), toys)

    def test_normalize_toys_rejects_malformed_values(self) -> None:
        self.assertEqual(normalize_toys("{not-json"), {})
        self.assertEqual(normalize_toys(["toy"]), {})

    def test_parse_toy_functions_maps_short_and_full_names(self) -> None:
        self.assertEqual(
            set(
                parse_toy_functions(
                    {
                        "shortFunctionNames": ["v", "r", "p"],
                        "fullFunctionNames": ["Thrusting"],
                    }
                )
            ),
            {"Vibrate", "Rotate", "Pump", "Thrusting"},
        )

    def test_parse_toy_functions_accepts_stringified_names(self) -> None:
        self.assertEqual(
            set(
                parse_toy_functions(
                    {
                        "shortFunctionNames": '["v", "r"]',
                        "fullFunctionNames": '["vibrate1", "Position"]',
                    }
                )
            ),
            {"Vibrate", "Rotate", "Vibrate1", "Position"},
        )

    def test_connection_and_battery_values_are_normalized(self) -> None:
        self.assertTrue(is_device_connected({"status": 1}))
        self.assertTrue(is_device_connected({"connected": "true"}))
        self.assertFalse(is_device_connected({"status": "0"}))
        self.assertEqual(get_battery_level({"battery": "87"}), 87)
        self.assertIsNone(get_battery_level({"battery": "invalid"}))


class CallbackToyMergeTests(unittest.TestCase):
    def test_callback_keeps_metadata_only_get_toys_reports(self) -> None:
        current = {"a": {"name": "lush", "status": "1", "battery": 80}}
        merged = merge_callback_toys(current, {"a": {"name": "lush", "status": "0"}})
        self.assertEqual(merged, {"a": {"name": "lush", "status": "0", "battery": 80}})

    def test_callback_decides_which_toys_exist(self) -> None:
        current = {"a": {"battery": 80}, "b": {"battery": 50}}
        merged = merge_callback_toys(current, {"b": {"status": "1"}, "c": {"status": "1"}})
        self.assertEqual(
            merged, {"b": {"battery": 50, "status": "1"}, "c": {"status": "1"}}
        )

    def test_merge_does_not_mutate_inputs(self) -> None:
        current = {"a": {"battery": 80}}
        merge_callback_toys(current, {"a": {"status": "1"}})
        self.assertEqual(current, {"a": {"battery": 80}})


class FunctionActionTests(unittest.TestCase):
    def test_running_actuators_are_kept_and_unchanged_offs_omitted(self) -> None:
        state = {"vibration": 5, "thrusting": 0, "pump": 3}
        self.assertEqual(build_function_action(state, {"vibration"}), "Vibrate:5,Pump:3")

    def test_changed_actuator_turned_off_is_sent_as_zero(self) -> None:
        state = {"vibration": 0, "pump": 3}
        self.assertEqual(build_function_action(state, {"vibration"}), "Vibrate:0,Pump:3")
        self.assertEqual(build_function_action({"pump": None}, {"pump"}), "Pump:0")

    def test_stroke_is_only_sent_with_active_thrusting(self) -> None:
        self.assertEqual(
            build_function_action({"stroke_range": "20-80", "thrusting": 4}),
            "Stroke:20-80,Thrusting:4",
        )
        self.assertEqual(
            build_function_action({"stroke_range": "20-80", "thrusting": 0}, {"thrusting"}),
            "Thrusting:0",
        )
        self.assertEqual(build_function_action({"stroke_range": None}, {"stroke_range"}), "")

    def test_nothing_to_send(self) -> None:
        self.assertEqual(build_function_action({}), "")


class ActuatorStateTests(unittest.TestCase):
    def test_stop_previous_defaults_to_replacing_state(self) -> None:
        self.assertEqual(
            next_actuator_state({"pump": 3}, "Function", {"action": "Vibrate:5"}),
            {"vibration": 5},
        )

    def test_stop_previous_zero_keeps_other_actuators(self) -> None:
        for flag in (0, "0"):
            self.assertEqual(
                next_actuator_state(
                    {"pump": 3},
                    "Function",
                    {"action": "Vibrate:5,Stroke:20-80", "stopPrevious": flag},
                ),
                {"pump": 3, "vibration": 5, "stroke_range": "20-80"},
            )

    def test_timed_actions_are_forgotten(self) -> None:
        state = {"vibration": 5, "pump": 3}
        for timing in ({"timeSec": 5}, {"timeSec": 0, "loopRunningSec": 2}):
            self.assertEqual(
                next_actuator_state(
                    state,
                    "Function",
                    {"action": "Vibrate:10", "stopPrevious": 0, **timing},
                ),
                {"pump": 3},
            )

    def test_stop_and_other_commands_clear_state(self) -> None:
        state = {"vibration": 5}
        self.assertEqual(next_actuator_state(state, "Function", {"action": "Stop"}), {})
        for command in ("Preset", "Pattern", "Position"):
            self.assertEqual(next_actuator_state(state, command, {"stopPrevious": 0}), {})

    def test_untracked_actions_leave_state_alone(self) -> None:
        self.assertEqual(
            next_actuator_state(
                {"vibration": 5}, "Function", {"action": "Rotate:7", "stopPrevious": 0}
            ),
            {"vibration": 5},
        )

    def test_actions_overlapping_tracked_actuators_clear_state(self) -> None:
        state = {"vibration": 5, "thrusting": 8}
        for action in ("All:0", "Vibrate1:0", "Rotate:3,All:0"):
            with self.subTest(action=action):
                self.assertEqual(
                    next_actuator_state(
                        state, "Function", {"action": action, "stopPrevious": 0}
                    ),
                    {},
                )


if __name__ == "__main__":
    unittest.main()
