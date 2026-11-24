"""Qwen Code isolation shared by the worker and the review reader: the system settings, the
allow-list hook, the isolated environment, and the transcript checks over Qwen Code's JSON envelope.
Not a backend module, so a caller outside the registry may import it.
"""
import json
import os
import re
import shlex
import sys
import uuid
from collections import Counter
from pathlib import Path

from .process import DriverError, worker_env

ALLOWED = ("read_file", "glob", "grep_search", "structured_output")
SHELL = "run_shell_command"
WRITES = ("write_file", "edit", "notebook_edit")
DENIED = (SHELL, "exec", "monitor", *WRITES, "web_fetch", "agent")
SHARED_TOOLS = {"read_file": "read", "glob": "list", "grep_search": "search", SHELL: "run", "write_file": "write", "edit": "edit"}
HOOK = Path(__file__).with_name("qwen_hook.py")
# Qwen Code's tool execution sandbox: commands write only in the workspace (the cwd) and reach no network; bwrap is
# required, so no Landlock fallback, which cannot close the network.
SANDBOX = {"backend": "bwrap", "filesystem": "workspace-write", "network": "closed"}
SANDBOX_DENIED = ("Bash(git push *)", "Bash(git commit *)", "Bash(gh *)")


# Qwen Code starts a Windows hook as [ComSpec, /d, /s, /c, command] through Node without verbatim arguments, so a `"`
# reaches cmd.exe as `\"` and no argument can be quoted: one holding whitespace, a quote or cmd.exe's own syntax
# (% expands even inside quotes) cannot be passed at all.
CMD_UNSAFE = frozenset(' \t\r\n"&|<>^%')


def hook_shell(env=os.environ, windows=os.name == "nt"):
    """The shell Qwen Code runs a command hook under, picked as it picks it: bash off Windows and where the environment
    marks Git Bash, otherwise the ComSpec shell, `cmd` or `powershell`."""
    if not windows:
        return "bash"
    term = env.get("TERM", "")
    if env.get("MSYSTEM", "").startswith(("MINGW", "MSYS")) or "msys" in term or "cygwin" in term:
        return "bash"
    return "powershell" if (env.get("ComSpec") or "cmd.exe").lower().endswith(("powershell.exe", "pwsh.exe")) else "cmd"


def hook_command(evidence, policy=None, edit=False):
    """The hook's command line, quoted for the hook shell; a `policy` file also grants the shell, and with `edit` the
    write tools, which the hook decides per call. Under cmd.exe the arguments are joined unquoted, and one cmd.exe
    could not hand the hook unchanged is refused."""
    allowed = (*ALLOWED, *((SHELL,) if policy else ()), *(WRITES if edit else ()))
    argv = [sys.executable, str(HOOK), ",".join(allowed), str(evidence), *([str(policy)] if policy else [])]
    shell = hook_shell()
    if shell == "bash":
        return shlex.join(argv)
    if shell == "powershell":
        raise DriverError("qwen: ComSpec names PowerShell, which Qwen Code would run the allow-list hook under; "
                          "ouro quotes the hook for cmd.exe or bash only, so set ComSpec to cmd.exe")
    for arg in argv:
        if bad := next((c for c in arg if c in CMD_UNSAFE), None):
            raise DriverError(f"qwen: the allow-list hook's argument {arg!r} holds {bad!r}, which cmd.exe cannot pass to "
                              "the hook unquoted, and Qwen Code's hook launch leaves no quoting that survives; "
                              "move it to a path without whitespace, quotes or &|<>^%")
    return " ".join(argv)


def hook_argv(command):
    """The argv `command` from hook_command starts: the inverse of its quoting for this hook shell."""
    return shlex.split(command) if hook_shell() == "bash" else command.split(" ")


def settings_for(evidence, policy=None, edit=False, sandbox=False):
    """System settings: the allow-list hook is the boundary, or with `sandbox`, where Qwen Code runs no executable hook,
    the tool execution sandbox and the git push, git commit and gh deny rules; folder trust keeps the repository's own
    settings, hooks and context files out, and the unguessable context file name keeps every one out."""
    deny = [t for t in DENIED if not ((policy and t == SHELL) or (edit and t in WRITES))]
    boundary = ({"tools": {"executionSandbox": SANDBOX}, "permissions": {"deny": [*deny, *SANDBOX_DENIED]}} if sandbox else
                {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
                    {"type": "command", "command": hook_command(evidence, policy, edit), "name": "ouro-allow", "timeout": 30}]}]},
                 "permissions": {"deny": deny}})
    return {**boundary,
            "security": {"folderTrust": {"enabled": True}},
            "context": {"fileName": f"ouro-no-context-{uuid.uuid4().hex}.md"},
            "memory": {"enableManagedAutoMemory": False, "enableManagedAutoDream": False},
            "privacy": {"usageStatisticsEnabled": False},
            "general": {"enableAutoUpdate": False}}


def trusted_folders(trust):
    """The private trust file's map: `trust` (a resolved working directory) as TRUST_FOLDER, so `--approval-mode yolo`
    holds, and each ancestor as DO_NOT_TRUST, so no parent's `.env` loads; with no `trust`, nothing is trusted."""
    return {str(trust): "TRUST_FOLDER", **{str(p): "DO_NOT_TRUST" for p in trust.parents}} if trust else {}


def isolated_env(settings, scratch, trust=None):
    """Every inherited QWEN_* variable is removed: several relocate trust, approvals or runtime state.
    The home may already exist: the reader keeps it across rounds for --resume."""
    home = scratch / "home"
    home.mkdir(exist_ok=True)
    (scratch / "defaults.json").write_text("{}", encoding="utf-8")
    (scratch / "trusted-folders.json").write_text(json.dumps(trusted_folders(trust)), encoding="utf-8")
    return {**{key: None for key in os.environ if key.upper().startswith("QWEN_")}, **worker_env(settings),
            "HOME": str(home), "USERPROFILE": str(home), "QWEN_HOME": str(home / ".qwen"),
            "QWEN_RUNTIME_DIR": str(scratch / "runtime"),
            "QWEN_CODE_TRUSTED_FOLDERS_PATH": str(scratch / "trusted-folders.json"),
            "QWEN_CODE_MCP_APPROVALS_PATH": str(scratch / "mcp-approvals.json"),
            "QWEN_CODE_SYSTEM_SETTINGS_PATH": str(scratch / "system.json"),
            "QWEN_CODE_SYSTEM_DEFAULTS_PATH": str(scratch / "defaults.json"),
            "QWEN_USAGE_STATISTICS_ENABLED": "0", "QWEN_TELEMETRY_ENABLED": "0"}


def pairs(events):
    """(tool_use, tool_result) for each answered call, structured_output included."""
    asked, out = {}, []
    for event in events:
        content = (event.get("message") or {}).get("content") if isinstance(event, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                asked[block.get("id")] = block
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in asked:
                out.append((asked.pop(block["tool_use_id"]), block))
    return out


def tool_calls(events):
    """Each tool call in the shared vocabulary: tool, path (or pattern), a shell call's command, and the error text
    of a refused or failed call."""
    calls = []
    for use, result in pairs(events):
        if use.get("name") == "structured_output":
            continue
        args = use.get("input") if isinstance(use.get("input"), dict) else {}
        calls.append({"tool": SHARED_TOOLS.get(use.get("name"), use.get("name")),
                      "path": args.get("file_path") or args.get("notebook_path") or args.get("path") or args.get("pattern"),
                      **({"command": args.get("command")} if use.get("name") == SHELL else {}),
                      "error": str(result.get("content"))[:200] if result.get("is_error") else None})
    return calls


def unhooked(events, hook):
    """Tools that ran more often than the allow-list hook granted them: a hook that never started fails open."""
    granted = Counter(h.get("tool") for h in hook if h.get("granted"))
    ran = Counter(use.get("name") for use, result in pairs(events)
                  if not result.get("is_error") and use.get("name") != "structured_output")
    return sorted(str(tool) for tool, count in ran.items() if count > granted[tool])


def final_text(events):
    """The JSON object the last assistant turn wrote as text alone, or None."""
    turns = [e for e in events if isinstance(e, dict) and e.get("type") == "assistant"]
    content = (turns[-1].get("message") or {}).get("content") if turns else None
    if not isinstance(content, list) or not content or any(not isinstance(b, dict) or b.get("type") != "text" for b in content):
        return None
    text = "".join(str(b.get("text", "")) for b in content).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
