import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import official_client_host as host


class HooksTests(unittest.TestCase):
    def test_merge_and_undo_preserve_user_hooks(self):
        original = {"description": "personal", "hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "my-hook"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "finish"}]}]}}
        baseline = copy.deepcopy(original)
        entries = host.managed_hook_entries("/tmp/host settings.json", python=sys.executable)
        merged, ownership = host.merge_managed_hooks(original, entries)
        self.assertEqual(original, baseline)
        self.assertEqual(host.remove_managed_hooks(merged, ownership), original)
        repeated, repeated_ownership = host.merge_managed_hooks(merged, entries)
        self.assertEqual(repeated, merged)
        self.assertEqual(repeated_ownership["added"], {})

    def test_undo_refuses_user_modified_managed_entry(self):
        entries = host.managed_hook_entries("/tmp/host.json", python=sys.executable)
        merged, ownership = host.merge_managed_hooks({}, entries)
        merged["hooks"]["SessionStart"][0]["matcher"] = "resume"
        with self.assertRaises(host.HostError):
            host.remove_managed_hooks(merged, ownership)

    def test_windows_requires_pythonw_without_powershell(self):
        with tempfile.TemporaryDirectory() as temp:
            python = Path(temp) / "python.exe"
            python.write_bytes(b"fixture")
            with self.assertRaises(host.HostError):
                host.managed_hook_entries("/tmp/settings.json", python=python, platform="win32")
            python.with_name("pythonw.exe").write_bytes(b"fixture")
            entries = host.managed_hook_entries("/tmp/settings.json", python=python, platform="win32")
            command = entries["SessionStart"]["hooks"][0]["command"]
            self.assertIn("pythonw.exe", command)
            self.assertNotIn("powershell", command)
            self.assertEqual(entries["SessionStart"]["hooks"][0]["timeout"], 20)


class EnsureTests(unittest.TestCase):
    def settings(self):
        return {"adapter_command": [str(Path(sys.executable).resolve()), "/tmp/adapter.py", "serve", "/tmp/profile.json"],
                "health_url": "http://127.0.0.1:9876/private-token/health",
                "health_identity": "test-installation", "edition": "ds"}

    def test_existing_identity_does_not_spawn(self):
        with patch.object(host, "healthy", return_value=True), patch.object(host.subprocess, "Popen") as spawn:
            self.assertEqual(host.ensure_adapter(self.settings()), {"status": "ready", "started": False})
            spawn.assert_not_called()

    def test_concurrent_winner_accepted_after_losing_child_exit(self):
        with patch.object(host, "healthy", side_effect=[False, False, True]), \
                patch.object(host.subprocess, "Popen") as spawn:
            spawn.return_value.poll.return_value = 1
            value = host.ensure_adapter(self.settings(), poll_interval=0)
            self.assertEqual(value["status"], "ready")
            kwargs = spawn.call_args.kwargs
            self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
            self.assertNotIn("shell", kwargs)

    def test_startup_timeout_redacts_private_url(self):
        with patch.object(host, "healthy", return_value=False), patch.object(host.subprocess, "Popen"):
            with self.assertRaises(host.HostError) as captured:
                host.ensure_adapter(self.settings(), timeout=0)
        self.assertNotIn("private-token", str(captured.exception))

    def test_non_loopback_or_credentials_rejected_before_spawn(self):
        for url in ("https://127.0.0.1:1/health", "http://localhost:1/health", "http://127.0.0.1:1/health?q=x",
                    "http://token@127.0.0.1:1/health", "http://example.com/health"):
            settings = self.settings()
            settings["health_url"] = url
            with patch.object(host.subprocess, "Popen") as spawn, self.assertRaises(host.HostError):
                host.ensure_adapter(settings)
            spawn.assert_not_called()

    def test_health_checks_identity_and_edition(self):
        class Response(io.BytesIO):
            status = 200
        settings = self.settings()
        for identity, edition, expected in (("test-installation", "ds", True), ("other", "ds", False),
                                             ("test-installation", "proxy", False)):
            response = Response(json.dumps({"ok": True, "identity": identity, "edition": edition}).encode())
            with patch.object(host.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = response
                self.assertEqual(host.healthy(settings), expected)

    def test_mcp_waits_for_health_and_exposes_no_tools(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        output = io.StringIO()
        def ensure(settings):
            self.assertEqual(output.getvalue(), "")
        with patch.object(host, "ensure_adapter", side_effect=ensure) as called:
            host.mcp_lifecycle(self.settings(), io.StringIO("\n".join(map(json.dumps, messages))), output)
        called.assert_called_once()
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(replies[1]["result"], {"tools": []})

    def test_mcp_failure_does_not_expose_private_endpoint(self):
        output = io.StringIO()
        with patch.object(host, "ensure_adapter", side_effect=ValueError("private-token")):
            host.mcp_lifecycle(self.settings(), io.StringIO('{"id":1,"method":"initialize"}\n'), output)
        self.assertNotIn("private-token", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], -32603)

    def test_service_plans_keep_original_app_out_of_startup_command(self):
        settings = self.settings()
        mac = host.adapter_launchd_plist(settings)
        self.assertEqual(mac["ProgramArguments"], settings["adapter_command"])
        self.assertTrue(mac["KeepAlive"])
        self.assertEqual(mac["ProcessType"], "Standard")
        settings["adapter_command"][0] = str(Path(sys.executable).resolve().with_name("pythonw.exe"))
        xml = host.adapter_windows_task_xml(settings, "S-1-5-21-123-456-789-1001")
        # Parse the actual bytes written by the Windows installer. A Unicode
        # string parse alone misses a UTF-8 declaration inside UTF-16 bytes.
        root = ET.fromstring(xml.encode("utf-16"))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns), settings["adapter_command"][0])
        self.assertEqual(root.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=ns), "LeastPrivilege")
        self.assertEqual(root.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=ns), "PT0S")
        self.assertNotIn("ChatGPT.exe", xml)
        self.assertNotIn("private-token", xml)


class OfficialHostPlanTests(unittest.TestCase):
    def test_mac_plan_isolates_state_and_keeps_official_executable(self):
        with patch.dict(host.os.environ, {"CODEX_CLI_PATH": "/untrusted/wrapper", "OPENAI_API_KEY": "secret"}):
            plan = host.isolated_mac_plan("/Official.app", "/private/probe", verify=False)
        self.assertEqual(plan["argv"][0], str(Path("/Official.app") / "Contents/MacOS/ChatGPT"))
        self.assertIn("--use-mock-keychain", plan["argv"])
        self.assertEqual(plan["environment"]["CODEX_HOME"], str(Path("/private/probe").resolve() / "home"))
        self.assertEqual(plan["environment"]["CODEX_ELECTRON_USER_DATA_PATH"], str(Path("/private/probe").resolve() / "browser"))
        self.assertNotIn("CODEX_CLI_PATH", plan["environment"])
        self.assertNotIn("OPENAI_API_KEY", plan["environment"])

    def test_mac_plan_rejects_socket_paths_that_cannot_bind(self):
        with self.assertRaises(host.HostError):
            host.isolated_mac_plan("/Official.app", "/tmp/" + "x" * 110, verify=False)


if __name__ == "__main__":
    unittest.main()
