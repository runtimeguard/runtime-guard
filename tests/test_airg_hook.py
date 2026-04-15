import io
import json
import unittest
from unittest.mock import patch

import airg_hook


class AirgHookTests(unittest.TestCase):
    def _run_hook(self, payload: dict) -> tuple[int, str]:
        stdin = io.StringIO(json.dumps(payload))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            code = airg_hook.main()
        return code, stdout.getvalue().strip()

    def test_pretooluse_shell_is_denied(self) -> None:
        code, out = self._run_hook(
            {
                "hook_event_name": "preToolUse",
                "tool_name": "Shell",
                "tool_input": {"command": "ls -la"},
            }
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload.get("permission"), "deny")

    def test_pretooluse_airg_mcp_is_allowed(self) -> None:
        code, out = self._run_hook(
            {
                "hook_event_name": "preToolUse",
                "tool_name": "MCP:ai-runtime-guard:execute_command",
                "tool_input": {},
            }
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_before_mcp_non_airg_is_denied(self) -> None:
        code, out = self._run_hook(
            {
                "hook_event_name": "beforeMCPExecution",
                "tool_name": "github:list_issues",
                "tool_input": {},
            }
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload.get("permission"), "deny")

    def test_before_read_sensitive_target_is_denied(self) -> None:
        code, out = self._run_hook(
            {
                "hook_event_name": "beforeReadFile",
                "file_path": "/tmp/secret.pem",
            }
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload.get("permission"), "deny")


if __name__ == "__main__":
    unittest.main()
