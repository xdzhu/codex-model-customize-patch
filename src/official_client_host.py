"""Native-client lifecycle helpers; never patch, sign, or replace an app bundle.

The native hooks call ``ensure SETTINGS`` synchronously and produce no context.
SETTINGS contains an argv array, a private loopback health URL and its identity.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class HostError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise HostError(message)


def windowless_python(executable, platform=None):
    """Require a real pythonw sibling on Windows; never fall back to a console."""
    executable = Path(executable).resolve()
    if (platform or sys.platform) == "win32":
        if executable.name.lower() == "python.exe":
            executable = executable.with_name("pythonw.exe")
        _require(executable.name.lower() == "pythonw.exe" and executable.is_file(),
                 "A pythonw.exe runtime is required for native Windows hooks")
    _require(executable.is_file(), "Python runtime is missing")
    return executable


def managed_hook_entries(settings_path, python=None, script=None, platform=None):
    """Build owned user hooks. Native trust review still applies to these entries."""
    platform = platform or sys.platform
    executable = windowless_python(python or sys.executable, platform)
    args = [str(executable), str(Path(script or __file__).resolve()), "ensure",
            str(Path(settings_path).resolve())]
    command = subprocess.list2cmdline(args) if platform == "win32" else shlex.join(args)
    handler = {"type": "command", "command": command, "timeout": 20}
    return {
        "SessionStart": {"matcher": "^(startup|resume|clear|compact|fork)$",
                         "hooks": [copy.deepcopy(handler)]},
        "UserPromptSubmit": {"hooks": [copy.deepcopy(handler)]},
    }


def merge_managed_hooks(existing, entries):
    """Pure merge. Return exact added entries for a later ownership-safe undo."""
    result = copy.deepcopy(existing or {})
    _require(isinstance(result, dict), "Hooks file must be a JSON object")
    hooks = result.setdefault("hooks", {})
    _require(isinstance(hooks, dict), "Existing hooks field must be an object")
    added = {}
    for event, entry in entries.items():
        values = hooks.setdefault(event, [])
        _require(isinstance(values, list), "Existing hook event must be an array: " + event)
        matches = values.count(entry)
        _require(matches <= 1, "Duplicate managed hook entries require review: " + event)
        if matches == 0:
            values.append(copy.deepcopy(entry))
            added[event] = copy.deepcopy(entry)
    return result, {"format": 1, "added": added}


def remove_managed_hooks(existing, ownership):
    """Remove only unchanged entries recorded as added by this installation."""
    _require(ownership.get("format") == 1, "Unknown managed-hook ownership format")
    result = copy.deepcopy(existing)
    hooks = result.get("hooks", {})
    for event, entry in ownership.get("added", {}).items():
        values = hooks.get(event, [])
        _require(isinstance(values, list) and values.count(entry) == 1,
                 "Managed hook changed or disappeared; review before undo: " + event)
        values.remove(entry)
        if not values:
            hooks.pop(event, None)
    if not hooks:
        result.pop("hooks", None)
    return result


def _settings(value):
    result = copy.deepcopy(value)
    command = result.get("adapter_command")
    _require(isinstance(command, list) and len(command) >= 2 and
             all(isinstance(part, str) and part and "\0" not in part for part in command),
             "Adapter command must be a nonempty argv array")
    _require(Path(command[0]).is_absolute(), "Adapter runtime must use an absolute path")
    url = urllib.parse.urlsplit(result.get("health_url", ""))
    _require(url.scheme == "http" and url.hostname == "127.0.0.1" and url.port and
             not url.username and not url.password and not url.query and not url.fragment,
             "Adapter health endpoint must be numeric loopback HTTP")
    _require(isinstance(result.get("health_identity"), str) and result["health_identity"],
             "Adapter health identity is required")
    return result


def health_status(settings, timeout=0.4):
    """Return bounded, credential-free diagnostics for the exact local identity."""
    try:
        request = urllib.request.Request(settings["health_url"], method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                return {'ready': False, 'reason': 'http_status'}
            value = json.loads(response.read(8193))
        ready = (value.get("ok") is True and value.get("identity") == settings["health_identity"]
                 and (settings.get("edition") is None or value.get("edition") == settings["edition"]))
        return {'ready': ready, 'reason': 'ready' if ready else 'identity_mismatch'}
    except TimeoutError:
        return {'ready': False, 'reason': 'timeout'}
    except urllib.error.URLError as error:
        return {'ready': False, 'reason': 'timeout' if isinstance(error.reason, TimeoutError) else 'unreachable'}
    except Exception:
        return {'ready': False, 'reason': 'invalid_response'}


def healthy(settings, timeout=0.4):
    """Check the installation identity, never accept an unrelated local listener."""
    return health_status(settings, timeout)['ready']


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise HostError("Adapter health redirect refused")


def ensure_adapter(value, timeout=12, poll_interval=0.1):
    """Start on demand; concurrent starters accept the matching winner's health.

    The adapter owns its listener/bind race. A second child may exit because the
    first child won the port; readiness is always checked before handling exit.
    """
    settings = _settings(value)
    if healthy(settings):
        return {"status": "ready", "started": False}
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
    child = subprocess.Popen(settings["adapter_command"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=settings.get("working_directory") or str(Path(settings["adapter_command"][0]).parent),
                             **options)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy(settings):
            return {"status": "ready", "started": True}
        # A winning concurrent starter can still be initializing after this
        # child exits. Wait through the same bounded readiness deadline.
        time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    raise HostError("The local model adapter did not become ready before the startup deadline")


def lifecycle_mcp_config(settings_path, python=None, script=None):
    """A native stdio service with no tools and no model-visible output."""
    executable = Path(python or sys.executable).resolve()
    _require(executable.is_file(), "A stdio-capable Python runtime is required")
    return {"command": str(executable),
            "args": [str(Path(script or __file__).resolve()), "mcp-ensure", str(Path(settings_path).resolve())],
            "enabled": True, "startup_timeout_sec": 20}


def adapter_launchd_plist(value, label="com.limyum.codex-model-adapter"):
    """Plan a per-user relay service; the official app launches independently."""
    settings = _settings(value)
    _require(label and all(c.isalnum() or c in ".-" for c in label), "Invalid service label")
    return {"Label": label, "ProgramArguments": settings["adapter_command"],
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 5,
            # This listener is on the critical path of foreground model turns.
            # Background I/O throttling can starve a cold start during updates.
            "ProcessType": "Standard", "WorkingDirectory": settings.get("working_directory") or
            str(Path(settings["adapter_command"][0]).parent)}


def adapter_windows_task_xml(value, user_sid):
    """Plan original-icon-compatible startup without a replacement shortcut."""
    import re
    settings = _settings(value)
    _require(re.fullmatch(r"S-\d+(?:-\d+)+", user_sid or ""), "Invalid Windows user SID")
    command = list(settings["adapter_command"])
    _require(Path(command[0]).name.lower() == "pythonw.exe", "The adapter task requires pythonw.exe")
    ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", ns)
    def node(parent, name, text=None, **attributes):
        value = ET.SubElement(parent, "{" + ns + "}" + name, attributes)
        if text is not None:
            value.text = str(text)
        return value
    task = ET.Element("{" + ns + "}Task", {"version": "1.2"})
    triggers = node(task, "Triggers")
    logon = node(triggers, "LogonTrigger")
    node(logon, "Enabled", "true")
    node(logon, "UserId", user_sid)
    principals = node(task, "Principals")
    principal = node(principals, "Principal", id="Author")
    node(principal, "UserId", user_sid)
    node(principal, "LogonType", "InteractiveToken")
    node(principal, "RunLevel", "LeastPrivilege")
    options = node(task, "Settings")
    node(options, "MultipleInstancesPolicy", "IgnoreNew")
    node(options, "DisallowStartIfOnBatteries", "false")
    node(options, "StopIfGoingOnBatteries", "false")
    node(options, "StartWhenAvailable", "true")
    node(options, "Enabled", "true")
    node(options, "Hidden", "true")
    node(options, "ExecutionTimeLimit", "PT0S")
    restart = node(options, "RestartOnFailure")
    node(restart, "Interval", "PT1M")
    node(restart, "Count", "3")
    actions = node(task, "Actions", Context="Author")
    execute = node(actions, "Exec")
    node(execute, "Command", command[0])
    node(execute, "Arguments", subprocess.list2cmdline(command[1:]))
    node(execute, "WorkingDirectory", settings.get("working_directory") or str(Path(command[0]).parent))
    return ET.tostring(task, encoding="utf-16", xml_declaration=True).decode("utf-16")


def mcp_lifecycle(settings, input_stream=None, output_stream=None):
    """Initialization waits for health; the service exposes an empty tool list."""
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    for line in input_stream:
        if len(line) > 1_048_576:
            raise HostError("Oversized lifecycle protocol message")
        message = json.loads(line)
        method, identifier = message.get("method"), message.get("id")
        if identifier is None:
            continue
        if method == "initialize":
            try:
                ensure_adapter(settings)
                result = {"protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "codex-model-adapter-lifecycle", "version": "1"}}
                response = {"jsonrpc": "2.0", "id": identifier, "result": result}
            except Exception:
                response = {"jsonrpc": "2.0", "id": identifier,
                            "error": {"code": -32603, "message": "Local model adapter startup failed"}}
        elif method == "tools/list":
            response = {"jsonrpc": "2.0", "id": identifier, "result": {"tools": []}}
        elif method == "ping":
            response = {"jsonrpc": "2.0", "id": identifier, "result": {}}
        else:
            response = {"jsonrpc": "2.0", "id": identifier,
                        "error": {"code": -32601, "message": "Method not found"}}
        output_stream.write(json.dumps(response) + "\n")
        output_stream.flush()


def mac_host_identity(app):
    """Read and validate the untouched official bundle and its bundled CLI."""
    app = Path(app).resolve()
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    _require(info.get("CFBundleIdentifier") == "com.openai.codex", "Unexpected Mac app identity")
    executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
    cli = app / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
    node = app / "Contents/Resources/cua_node/bin/node"
    identities = {}
    for item in (app, cli, node):
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(item)],
                       check=True, capture_output=True, timeout=60)
        result = subprocess.run(["/usr/bin/codesign", "-d", "--verbose=4", str(item)],
                                check=True, capture_output=True, text=True, timeout=20)
        _require("TeamIdentifier=2DC432GLL2" in result.stderr,
                 "The selected app/CLI is not signed by OpenAI")
        identities[str(item)] = next((line.removeprefix("Identifier=") for line in result.stderr.splitlines()
                                      if line.startswith("Identifier=")), None)
    return {"app": str(app), "executable": str(executable), "cli": str(cli), "node": str(node),
            "code_identifiers": identities,
            "bundle_id": info["CFBundleIdentifier"], "team_id": "2DC432GLL2",
            "version": info.get("CFBundleShortVersionString"), "build": info.get("CFBundleVersion")}


def isolated_mac_plan(app, directory, verify=True):
    """Plan an isolated official-host run; execution is explicitly separate."""
    identity = mac_host_identity(app) if verify else {"executable": str(Path(app) / "Contents/MacOS/ChatGPT")}
    directory = Path(directory).resolve()
    _require(len(os.fsencode(directory / "home/ipc/ipc.sock")) <= 103,
             "The isolated directory is too long for a Mac Unix socket; choose a short temporary path")
    environment = {key: value for key, value in os.environ.items()
                   if key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE")}
    environment.update(HOME=str(directory / "user"), CODEX_HOME=str(directory / "home"),
                       CODEX_ELECTRON_USER_DATA_PATH=str(directory / "browser"),
                       TMPDIR=str(directory / "temp"), NO_PROXY="*", no_proxy="*")
    return {"argv": [identity["executable"], "--user-data-dir=" + str(directory / "browser"),
                     "--no-first-run", "--use-mock-keychain"],
            "environment": environment, "cwd": str(directory), "identity": identity,
            "directories": [str(directory / part) for part in ("user", "home", "browser", "temp")]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["ensure", "mcp-ensure", "mac-plan"])
    parser.add_argument("settings", type=Path)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.action == "mcp-ensure":
        mcp_lifecycle(json.loads(args.settings.read_text(encoding="utf-8")))
        return 0
    if args.action == "ensure":
        # Hook inputs can contain prompts: consume and discard, never retain.
        if sys.stdin is not None and not sys.stdin.isatty():
            sys.stdin.read(1_048_577)
        ensure_adapter(json.loads(args.settings.read_text(encoding="utf-8")))
        return 0
    _require(args.directory is not None, "An isolated directory is required")
    print(json.dumps(isolated_mac_plan(args.settings, args.directory), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HostError, OSError, subprocess.SubprocessError, ValueError):
        # No private URL/token, prompt, path, or raw exception enters hook logs.
        if sys.stderr is not None:
            print("Local model adapter startup failed; inspect the adapter installation.", file=sys.stderr)
        raise SystemExit(1)
