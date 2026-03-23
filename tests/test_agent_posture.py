import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import agent_posture


class AgentPostureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.home = self.base / "home"
        self.home.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _mcp_payload() -> dict:
        return {
            "mcpServers": {
                "ai-runtime-guard": {
                    "command": "/tmp/airg-server",
                    "args": [],
                }
            }
        }

    def test_claude_posture_detects_project_local_user_managed_scopes(self) -> None:
        (self.workspace / ".mcp.json").write_text(json.dumps(self._mcp_payload(), indent=2))
        (self.home / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": self._mcp_payload()["mcpServers"],
                    "projects": {
                        str(self.workspace): self._mcp_payload(),
                    },
                },
                indent=2,
            )
        )
        managed_path = self.base / "managed-mcp.json"
        managed_path.write_text(json.dumps(self._mcp_payload(), indent=2))

        profile = {
            "profile_id": "p1",
            "name": "Claude Code 1",
            "agent_type": "claude_code",
            "agent_id": "claude-code-1",
            "workspace": str(self.workspace),
        }

        with patch("agent_posture.pathlib.Path.home", return_value=self.home), patch(
            "agent_posture._claude_managed_paths",
            return_value=[managed_path],
        ):
            row = agent_posture.build_posture_for_profile(profile)

        scopes = set(row.get("mcp_detected_scopes", []))
        self.assertEqual(scopes, {"project", "local", "user", "managed"})
        self.assertTrue(row.get("signals", {}).get("airg_mcp_present"))

    def test_claude_posture_detects_local_scope_only(self) -> None:
        (self.home / ".claude.json").write_text(
            json.dumps(
                {
                    "projects": {
                        str(self.workspace): self._mcp_payload(),
                    }
                },
                indent=2,
            )
        )
        profile = {
            "profile_id": "p2",
            "name": "Claude Local",
            "agent_type": "claude_code",
            "agent_id": "claude-local",
            "workspace": str(self.workspace),
        }

        with patch("agent_posture.pathlib.Path.home", return_value=self.home), patch(
            "agent_posture._claude_managed_paths",
            return_value=[],
        ):
            row = agent_posture.build_posture_for_profile(profile)

        self.assertIn("local", row.get("mcp_detected_scopes", []))
        self.assertTrue(row.get("signals", {}).get("airg_mcp_present"))

    def test_claude_posture_missing_mcp_files_is_safe(self) -> None:
        profile = {
            "profile_id": "p3",
            "name": "Claude Missing",
            "agent_type": "claude_code",
            "agent_id": "claude-missing",
            "workspace": str(self.workspace),
        }

        with patch("agent_posture.pathlib.Path.home", return_value=self.home), patch(
            "agent_posture._claude_managed_paths",
            return_value=[],
        ):
            row = agent_posture.build_posture_for_profile(profile)

        self.assertEqual(row.get("status"), "gray")
        self.assertFalse(row.get("signals", {}).get("airg_mcp_present"))
        self.assertEqual(row.get("mcp_detected_scopes", []), [])

    def test_claude_desktop_posture_detects_desktop_config(self) -> None:
        desktop_cfg = self.home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        desktop_cfg.parent.mkdir(parents=True, exist_ok=True)
        desktop_cfg.write_text(json.dumps(self._mcp_payload(), indent=2))
        profile = {
            "profile_id": "p4",
            "name": "Claude Desktop",
            "agent_type": "claude_desktop",
            "agent_id": "claude-desktop-1",
            "workspace": str(self.workspace),
        }

        with patch("agent_posture.pathlib.Path.home", return_value=self.home):
            row = agent_posture.build_posture_for_profile(profile)

        self.assertEqual(row.get("status"), "green")
        self.assertTrue(row.get("signals", {}).get("airg_mcp_present"))
        self.assertEqual(row.get("mcp_detected_scopes", []), ["desktop"])

    def test_claude_desktop_posture_missing_config_is_gray(self) -> None:
        profile = {
            "profile_id": "p5",
            "name": "Claude Desktop Missing",
            "agent_type": "claude_desktop",
            "agent_id": "claude-desktop-1",
            "workspace": str(self.workspace),
        }

        with patch("agent_posture.pathlib.Path.home", return_value=self.home):
            row = agent_posture.build_posture_for_profile(profile)

        self.assertEqual(row.get("status"), "gray")
        self.assertFalse(row.get("signals", {}).get("airg_mcp_present"))
        self.assertEqual(row.get("mcp_detected_scopes", []), [])

    def test_detect_unregistered_excludes_registered_claude_desktop_config(self) -> None:
        desktop_cfg = self.home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        desktop_cfg.parent.mkdir(parents=True, exist_ok=True)
        desktop_cfg.write_text(json.dumps(self._mcp_payload(), indent=2))
        profiles = [
            {
                "profile_id": "p5b",
                "name": "Claude Desktop",
                "agent_type": "claude_desktop",
                "agent_id": "claude-desktop-1",
                "workspace": str(self.workspace),
            }
        ]

        with patch("agent_posture.pathlib.Path.home", return_value=self.home):
            discovered = agent_posture.detect_unregistered_for_profiles(profiles)

        self.assertEqual(discovered, [])

    def test_codex_posture_detects_global_scope(self) -> None:
        codex_cfg = self.home / ".codex" / "config.toml"
        codex_cfg.parent.mkdir(parents=True, exist_ok=True)
        codex_cfg.write_text(
            '[mcp_servers.ai-runtime-guard]\n'
            'command = "/tmp/airg-server"\n'
            'args = []\n'
            '\n'
            '[mcp_servers.ai-runtime-guard.env]\n'
            'AIRG_AGENT_ID = "codex-1"\n'
            f'AIRG_WORKSPACE = "{self.workspace}"\n'
        )
        profile = {
            "profile_id": "p6",
            "name": "Codex Global",
            "agent_type": "codex",
            "agent_scope": "global",
            "agent_id": "codex-1",
            "workspace": str(self.workspace),
        }

        with patch("agent_posture.pathlib.Path.home", return_value=self.home):
            row = agent_posture.build_posture_for_profile(profile)

        self.assertEqual(row.get("status"), "red")
        self.assertTrue(row.get("signals", {}).get("airg_mcp_present"))
        self.assertIn("global", row.get("mcp_detected_scopes", []))

    def test_codex_posture_detects_project_scope(self) -> None:
        codex_cfg = self.workspace / ".codex" / "config.toml"
        codex_cfg.parent.mkdir(parents=True, exist_ok=True)
        codex_cfg.write_text(
            '[mcp_servers.ai-runtime-guard]\n'
            'command = "/tmp/airg-server"\n'
            'args = []\n'
            '\n'
            '[mcp_servers.ai-runtime-guard.env]\n'
            'AIRG_AGENT_ID = "codex-1"\n'
            f'AIRG_WORKSPACE = "{self.workspace}"\n'
        )
        profile = {
            "profile_id": "p7",
            "name": "Codex Project",
            "agent_type": "codex",
            "agent_scope": "project",
            "agent_id": "codex-1",
            "workspace": str(self.workspace),
        }

        with patch("agent_posture.pathlib.Path.home", return_value=self.home):
            row = agent_posture.build_posture_for_profile(profile)

        self.assertEqual(row.get("status"), "red")
        self.assertTrue(row.get("signals", {}).get("airg_mcp_present"))
        self.assertIn("project", row.get("mcp_detected_scopes", []))


if __name__ == "__main__":
    unittest.main()
