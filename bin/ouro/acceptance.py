"""Acceptance parsing and command-policy checks; workflow transitions stay in Run."""
import re

from . import validation
from .sh import DriverError


ACCEPTANCE = ("python3 ", "python ", "pwsh ", "dotnet ", "npm ", "npx ", "pytest", "bash ", "go test")


def fenced(lines):
    """Whether each line is a fence marker or inside a ``` or ~~~ fence, which closes only on a bare marker of its character at least as long.

    A backtick run followed by another backtick on its line is an inline code span, not a marker (CommonMark's info-string rule)."""
    fence = None
    for line in lines:
        marker = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", line)
        if marker and not (marker.group(1)[0] == "`" and "`" in marker.group(2)):
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence) and not marker.group(2).strip(" \t"):
                fence = None
            yield True
        else:
            yield fence is not None


def sections(body):
    """Each body line but the headings that open or close a section, with whether an acceptance/test/verification section holds it; a fenced heading is a line."""
    level = 0
    lines = body.splitlines()
    for inside, line in zip(fenced(lines), lines):
        h = None if inside else re.match(r"(#{2,3})\s+(.*)", line)
        if h and (not level or len(h.group(1)) <= level):
            level = len(h.group(1)) if re.search(r"\b(acceptance|tests?|testing|verif\w*)\b", h.group(2), re.I) else 0
        else:
            yield bool(level), line


def acceptance_text(body):
    """The approved body's acceptance/test/verification sections, including prose requirements."""
    return "\n".join(line for held, line in sections(body) if held)


def acceptance_spans(body):
    """Backticked spans in the sections whose heading names acceptance, tests or verification."""
    return quoted_lines(acceptance_text(body))


def quoted_lines(joined):
    """The backticked spans and shell-fence lines of a text."""
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


FIELD = re.compile(r"(?i)^[ ]{0,3}(?:[-*][ ]+)?(?:Acceptance command:|\*\*Acceptance command:\*\*)[ \t]*(.*?)[ \t]*$")


def prose(pairs):
    """The (held, line) pairs whose line is prose: not inside a fence, not an indented block."""
    pairs = list(pairs)
    for inside, (held, line) in zip(fenced(line for _, line in pairs), pairs):
        if not inside and not line.startswith(("    ", "\t")):
            yield held, line


def acceptance_declarations(text):
    """An explicit field is prose, never a fenced or indented historical example."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [cmd for _, line in prose((False, line) for line in lines) for cmd in DECLARED_ACCEPTANCE.findall(line)]


def granted_below(rows, allowed):
    """Whether the held rows below a label, up to the next field or the end of the held run, hold a span or a shell fence that grants a command."""
    run = []
    for held, ln in rows:
        if not held or FIELD.match(ln):
            break
        run.append(ln)
    return any(s in allowed for s in quoted_lines("\n".join(run)))


def unread_fields(body, allowed):
    """Each `Acceptance command:` line that grants nothing, as neither an acceptance span nor a declaration, with its fix."""
    problems, rows = [], list(sections(body))
    for (i, held), line in prose(((i, held), line) for i, (held, line) in enumerate(rows)):
        field = FIELD.match(line)
        if not field or DECLARED_ACCEPTANCE.match(line):
            continue  # a declaration: acceptance_problems judges its command
        span = re.search(r"`([^`\n]+)`", field.group(1))
        cmd = span.group(1).strip() if span else field.group(1)
        where, quoted, move = held, bool(span), "move the line under an `## Acceptance` heading"
        if not cmd:  # a bare label's command is its next line, read as a span or a shell fence where that line sits
            where, below = next(((h, ln) for h, ln in rows[i + 1:] if ln.strip()), (held, ""))
            quoted = bool(re.match(r"```(?:sh|bash|shell|powershell|ps1)?[ \t]*$|.*`[^`\n]+`", below))
            move = "move the line and its command under an `## Acceptance` heading"
        if (where and quoted) or cmd in allowed:
            continue
        if where and not span and not PREFIXED.sub("", cmd).startswith(ACCEPTANCE) and granted_below(rows[i + 1:], allowed):
            continue  # a bare or prose label above prose or a sub-heading, then the command it introduces
        fixes = ([] if quoted else ["put the command in backticks"]) + ([] if where else [move])
        problems.append((f"`{cmd}`: " if cmd else "") + "this `Acceptance command:` line grants nothing as written; " + " and ".join(fixes))
    return problems


def declared_source(body, declared):
    """The body with the commands trusted comments declare, as the run reads its acceptance."""
    return body + ("\n## Acceptance commands\n" + "\n".join(f"Acceptance command: `{cmd}`" for cmd in declared) if declared else "")


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
            if cmd not in allowed and cmd.startswith(ACCEPTANCE):  # a runner a held line would grant: the line sits outside the sections
                raise DriverError("this `Acceptance command:` line is outside an Acceptance, Tests or Verification heading, so it grants nothing; "
                                  "move the line under an `## Acceptance` heading")
            if cmd not in allowed:
                raise DriverError("this explicit command is outside the approved acceptance runner forms")
        except DriverError as ex:
            problems.append(f"`{cmd}`: {ex}")
    return problems + unread_fields(body, allowed)


def empty_list(body):
    """Why a run whose BUILD node would get an empty command list stops before the first build: it would ship code no node ran."""
    named = named_commands(body)
    return (("The acceptance names " + ", ".join(f"`{s}`" for s in named) + ", and the" if named else "The")
            + " BUILD node would get an empty command list: no acceptance command it is granted, and no `[[gate]]` for the area marked `cheap`, "
            "run here, or declaring `paths` (an environment prefix is not a runner the brief grants). A build would ship code no node ran. "
            "Name the acceptance command in the issue, or mark a gate `cheap = true`, or give it `paths` or a local run.")
