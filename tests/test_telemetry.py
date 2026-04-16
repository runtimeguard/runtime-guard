import datetime
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import reports
import telemetry


class _ImmediateThread:
    def __init__(self, target=None, **_kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class TelemetryTests(unittest.TestCase):
    def test_bucket_boundaries(self) -> None:
        cases = {
            0: "0",
            1: "1",
            2: "2-5",
            5: "2-5",
            6: "6-10",
            10: "6-10",
            11: "11-50",
            50: "11-50",
            51: "51-100",
            100: "51-100",
            101: "101-1000",
            1000: "101-1000",
            1001: "1000+",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(telemetry.bucket(value), expected)

    def test_platform_mapping(self) -> None:
        self.assertEqual(telemetry._platform_string("Darwin"), "macos")
        self.assertEqual(telemetry._platform_string("Linux"), "linux")
        self.assertEqual(telemetry._platform_string("Windows"), "windows")
        self.assertEqual(telemetry._platform_string("Plan9"), "unknown")

    def test_version_sanitization(self) -> None:
        self.assertEqual(telemetry._sanitize_version("2.1.1", max_len=32), "2.1.1")
        self.assertEqual(telemetry._sanitize_version("v2.1.1🔥", max_len=32), "v2.1.1")
        self.assertEqual(telemetry._sanitize_version("!!!!", max_len=32), "unknown")

    def test_agent_type_mapping_dedup_sort_and_cap(self) -> None:
        raw = ["cursor", "claude_desktop", "custom", "codex", "cursor", "madeup"]
        mapped = telemetry.normalize_agent_types(raw)
        self.assertEqual(mapped, ["claude_code", "codex", "cursor", "other"])

        many = [f"custom-{idx}" for idx in range(32)]
        capped = telemetry.normalize_agent_types(many)
        self.assertLessEqual(len(capped), 16)
        self.assertEqual(capped, ["other"])

    def test_payload_validation_catches_invalid_shapes(self) -> None:
        valid = {
            "airg_version": "2.1.1",
            "platform": "linux",
            "python_version": "3.12.3",
            "install_method": "unknown",
            "agents_bucket": "1",
            "agent_types": ["cursor"],
            "events_bucket": "0",
            "blocked_bucket": "0",
            "approvals_bucket": "0",
            "sentinel_enabled": True,
            "sentinel_flagged_bucket": "0",
            "sentinel_blocked_bucket": "0",
            "period_days": 1,
        }
        telemetry.validate_payload(valid)

        for key, bad_value in [
            ("platform", "mac"),
            ("install_method", "default"),
            ("agents_bucket", "7"),
            ("agent_types", ["cursor", "invalid"]),
            ("sentinel_enabled", "yes"),
            ("period_days", 0),
            ("airg_version", "x" * 33),
            ("python_version", "3.12.3/rc"),
        ]:
            with self.subTest(key=key):
                invalid = dict(valid)
                invalid[key] = bad_value
                with self.assertRaises(ValueError):
                    telemetry.validate_payload(invalid)

    def test_last_sent_date_logic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            policy_path = base / "policy.json"
            reports_db = base / "reports.db"
            approval_db = base / "approvals.db"
            log_path = base / "activity.log"
            reports.init_reports_store(reports_db)
            log_path.write_text("")

            policy = {
                "telemetry": {
                    "enabled": True,
                    "endpoint": "https://telemetry.runtime-guard.ai/v1/telemetry",
                    "last_sent_date": "2026-04-16",
                },
                "reports": {"enabled": True},
                "script_sentinel": {"enabled": True},
            }
            policy_path.write_text(json.dumps(policy))

            now_same_day = datetime.datetime(2026, 4, 16, 10, 0, 0, tzinfo=datetime.UTC)
            sent = telemetry.maybe_send_daily(
                policy_path=policy_path,
                reports_db_path=reports_db,
                approval_db_path=approval_db,
                log_path=log_path,
                now=now_same_day,
            )
            self.assertFalse(sent)

            with patch.object(telemetry.threading, "Thread", _ImmediateThread), patch.object(
                telemetry, "_send_once", return_value=204
            ):
                now_next_day = datetime.datetime(2026, 4, 17, 1, 0, 0, tzinfo=datetime.UTC)
                sent_next = telemetry.maybe_send_daily(
                    policy_path=policy_path,
                    reports_db_path=reports_db,
                    approval_db_path=approval_db,
                    log_path=log_path,
                    now=now_next_day,
                )
                self.assertTrue(sent_next)

            updated = json.loads(policy_path.read_text())
            self.assertEqual(updated.get("telemetry", {}).get("last_sent_date"), "2026-04-17")

