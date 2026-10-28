"""Acceptance parsing and command-policy checks; workflow transitions stay in Run."""
import re

from . import validation
from .sh import DriverError


ACCEPTANCE = ("python3 ", "python ", "pwsh ", "dotnet ", "npm ", "npx ", "pytest", "bash ", "go test")


def acceptance_text(body):
    """The approved body's acceptance/test/verification sections, including prose requirements."""
    level, text = 0, []
    for line in body.splitlines():
        h = re.match(r"(#{2,3})\s+(.*)", line)
        if h and (not level or len(h.group(1)) <= level):
            level = len(h.group(1)) if re.search(r"\b(acceptance|tests?|testing|verif\w*)\b", h.group(2), re.I) else 0
        elif level:
            text.append(line)
    return "\n".join(text)


def acceptance_spans(body):
    """Backticked spans in the sections whose heading names acceptance, tests or verification."""
    joined = acceptance_text(body)
    spans = [s.strip() for s in re.findall(r"`([^`\n]+)`", joined)]
    for block in re.findall(r"(?ms)^```(?:sh|bash|shell|powershell|ps1)?[ \t]*\n(.*?)^```", joined):
        spans.extend(line.strip() for line in block.splitlines() if line.strip())
    return spans


def acceptance_commands(body):
    """The acceptance spans that start like a runner: what the brief grants."""
    return list(dict.fromkeys(s for s in acceptance_spans(body) if s.startswith(ACCEPTANCE)))


PREFIXED = re.compile(r"^(?:[A-Za-z_]\w*=\S*\s+)+")  # NAME=value words before a runner, which no grant covers


def named_commands(body):
    """The acceptance spans that name a command: a runner after any leading NAME=value words."""
    return list(dict.fromkeys(s for s in acceptance_spans(body) if PREFIXED.sub("", s).startswith(ACCEPTANCE)))


DECLARED_ACCEPTANCE = re.compile(r"(?mi)^[ ]{0,3}(?:[-*][ ]+)?(?:Acceptance command:|\*\*Acceptance command:\*\*)\s*`([^`\n]+)`[ \t]*$")


def acceptance_declarations(text):
    """An explicit field is prose, never a fenced or indented historical example."""
    result, fence = [], None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        marker = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence) and not marker.group(2).strip(" \t"):
                fence = None
            continue
        if fence is None and not line.startswith(("    ", "\t")):
            result.extend(DECLARED_ACCEPTANCE.findall(line))
    return result


def acceptance_problems(body, caller_commands=()):
    problems = []
    allowed = set(acceptance_commands(body))
    for cmd in dict.fromkeys(named_commands(body) + acceptance_declarations(body)):
        try:
            _, environment = validation.command(cmd)
            if environment:
                if cmd not in caller_commands:
                    raise DriverError("leading environment assignments require an already-approved caller validation command")
                continue  # caller handoff only; acceptance_commands never makes this a worker grant
            if cmd not in allowed:
                raise DriverError("this explicit command is outside the approved acceptance runner forms")
        except DriverError as ex:
            problems.append(f"`{cmd}`: {ex}")
    return problems


def empty_list(body):
    """Why a run whose BUILD node would get an empty command list stops before the first build: it would ship code no node ran."""
    named = named_commands(body)
    return (("The acceptance names " + ", ".join(f"`{s}`" for s in named) + ", and the" if named else "The")
            + " BUILD node would get an empty command list: no acceptance command it is granted, and no `[[gate]]` for the area marked `cheap`, "
            "run here, or declaring `paths` (an environment prefix is not a runner the brief grants). A build would ship code no node ran. "
            "Name the acceptance command in the issue, or mark a gate `cheap = true`, or give it `paths` or a local run.")
