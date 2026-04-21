import unittest
from unittest.mock import patch

import executor


class ExecutorEnvTests(unittest.TestCase):
    def test_safe_subprocess_env_allowlist_and_dangerous_drops(self):
        source = {
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "USER": "tester",
            "AIRG_AGENT_ID": "agent-a",
            "OPENAI_API_KEY": "secret",
            "LD_PRELOAD": "/tmp/evil.so",
            "DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib",
            "PYTHONPATH": "/tmp/hijack",
            "IFS": ",",
            "GIT_SSH_COMMAND": "ssh -i bad",
            "RANDOM_CUSTOM_VAR": "value",
        }
        with patch.dict("os.environ", source, clear=True):
            safe = executor.safe_subprocess_env()

        self.assertEqual(safe.get("AIRG_AGENT_ID"), "agent-a")
        self.assertIn("PATH", safe)
        self.assertIn("LANG", safe)
        self.assertNotIn("OPENAI_API_KEY", safe)
        self.assertNotIn("LD_PRELOAD", safe)
        self.assertNotIn("DYLD_INSERT_LIBRARIES", safe)
        self.assertNotIn("PYTHONPATH", safe)
        self.assertNotIn("IFS", safe)
        self.assertNotIn("GIT_SSH_COMMAND", safe)
        self.assertNotIn("RANDOM_CUSTOM_VAR", safe)
        self.assertTrue(safe.get("HOME"))


if __name__ == "__main__":
    unittest.main()
