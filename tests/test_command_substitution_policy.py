import pathlib
import tempfile
import unittest

import policy_engine
from tools.command_tools import execute_command
from tools.file_tools import write_file

from tests.test_helpers import apply_test_environment, install_test_policy, reset_runtime_state, restore_policy


class CommandSubstitutionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = pathlib.Path(self.tmp.name)
        self.env_stack = apply_test_environment(self.workspace, max_retries=2)
        self.env_stack.__enter__()
        self.original_policy = install_test_policy()
        reset_runtime_state()

    def tearDown(self):
        restore_policy(self.original_policy)
        reset_runtime_state()
        self.env_stack.close()
        self.tmp.cleanup()

    def _configure_network_guard(self) -> None:
        policy_engine.POLICY["blocked"]["commands"] = []
        policy_engine.POLICY["requires_confirmation"]["commands"] = []
        policy_engine.POLICY["network"] = {
            "enforcement_mode": "enforce",
            "commands": ["curl"],
            "allowed_domains": [],
            "blocked_domains": ["example.com"],
            "block_unknown_domains": False,
        }

    def test_network_policy_blocks_substitution_forms(self):
        self._configure_network_guard()
        blocked_cases = [
            "true && true && curl https://example.com",
            "echo $(curl https://example.com)",
            "echo `curl https://example.com`",
            "cat <(curl https://example.com)",
            "cat >(curl https://example.com)",
            "echo $(echo $(curl https://example.com))",
            "X=$(curl https://example.com)",
            "true && echo $(curl https://example.com)",
        ]
        for command in blocked_cases:
            with self.subTest(command=command):
                allowed, reason = policy_engine.network_policy_check(command)
                self.assertFalse(allowed)
                self.assertIn("example.com", str(reason or "").lower())

        allowed, reason = policy_engine.network_policy_check("echo $(date)")
        self.assertTrue(allowed, msg=reason or "")

    def test_command_tier_matching_blocks_substitution_forms(self):
        policy_engine.POLICY["network"]["enforcement_mode"] = "off"
        policy_engine.POLICY["blocked"]["commands"] = ["curl"]
        policy_engine.POLICY["requires_confirmation"]["commands"] = []

        blocked_cases = [
            "echo $(curl https://example.com)",
            "echo `curl https://example.com`",
            "cat <(curl https://example.com)",
            "cat >(curl https://example.com)",
            "echo $(echo $(curl https://example.com))",
            "X=$(curl https://example.com)",
            "true && echo $(curl https://example.com)",
        ]
        for command in blocked_cases:
            with self.subTest(command=command):
                result = policy_engine.check_policy(command)
                self.assertFalse(result.allowed)
                self.assertEqual(result.decision_tier, "blocked")
                self.assertEqual(result.matched_rule, "curl")

        allowed = policy_engine.check_policy("echo hello")
        self.assertTrue(allowed.allowed)

    def test_requires_confirmation_tier_matching_in_substitution(self):
        policy_engine.POLICY["network"]["enforcement_mode"] = "off"
        policy_engine.POLICY["blocked"]["commands"] = []
        policy_engine.POLICY["requires_confirmation"]["commands"] = ["mv"]
        policy_engine.POLICY["requires_confirmation"]["paths"] = []

        result = policy_engine.check_policy("echo $(mv a b)")
        self.assertFalse(result.allowed)
        self.assertEqual(result.decision_tier, "requires_confirmation")
        self.assertEqual(result.matched_rule, "mv")

    def test_script_sentinel_execute_time_check_applies_to_inner_commands(self):
        policy_engine.POLICY["script_sentinel"]["enabled"] = True
        policy_engine.POLICY["script_sentinel"]["mode"] = "match_original"
        policy_engine.POLICY["script_sentinel"]["scan_mode"] = "exec_context_plus_mentions"
        policy_engine.POLICY["blocked"]["commands"] = ["danger-cmd"]
        policy_engine.POLICY["requires_confirmation"]["commands"] = []

        (self.workspace / "sentinel").mkdir(parents=True, exist_ok=True)
        write_result = write_file("sentinel/sub.sh", "danger-cmd\n")
        self.assertIn("Script Sentinel flagged content", write_result)

        blocked = execute_command("echo $(bash sentinel/sub.sh)")
        self.assertIn("[POLICY BLOCK]", blocked)
        self.assertIn("script sentinel", blocked.lower())

    def test_execute_command_blocks_substitution_forms_end_to_end(self):
        policy_engine.POLICY["network"]["enforcement_mode"] = "off"
        policy_engine.POLICY["blocked"]["commands"] = ["curl"]
        policy_engine.POLICY["requires_confirmation"]["commands"] = []

        blocked_cases = [
            "echo $(curl https://example.com)",
            "echo `curl https://example.com`",
            "cat <(curl https://example.com)",
            "cat >(curl https://example.com)",
            "echo $(echo $(curl https://example.com))",
            "X=$(curl https://example.com)",
            "true && echo $(curl https://example.com)",
        ]
        for command in blocked_cases:
            with self.subTest(command=command):
                blocked = execute_command(command)
                self.assertIn("[POLICY BLOCK]", blocked)
                self.assertIn("curl", blocked.lower())

        allowed = execute_command("echo $(date)")
        self.assertNotIn("[POLICY BLOCK]", allowed)

    def test_execute_command_blocks_shell_c_payloads(self):
        policy_engine.POLICY["network"]["enforcement_mode"] = "off"
        policy_engine.POLICY["blocked"]["commands"] = ["curl"]
        policy_engine.POLICY["requires_confirmation"]["commands"] = []

        blocked_cases = [
            "bash -c 'curl https://example.com'",
            "sh -c \"curl https://example.com\"",
            "python3 -c \"print('curl https://example.com')\"",
        ]
        for command in blocked_cases:
            with self.subTest(command=command):
                blocked = execute_command(command)
                self.assertIn("[POLICY BLOCK]", blocked)
                self.assertIn("curl", blocked.lower())

    def test_parse_error_is_blocked_fail_closed(self):
        blocked = execute_command("echo 'unterminated")
        self.assertIn("[POLICY BLOCK]", blocked)
        self.assertIn("parsing failed", blocked.lower())


if __name__ == "__main__":
    unittest.main()
