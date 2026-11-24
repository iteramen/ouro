"""Briefs: the templates under prompts/, the contract sections quoted into them, the anchors' current lines."""
import re
from pathlib import Path

from . import records


PROMPTS = Path(__file__).resolve().parent / "prompts"


def contract_text(env):
    root = env.ob.plugin_root() or env.main
    p = Path(root) / "docs" / "contract.md"
    if not p.is_file():
        p = env.main / "docs" / "contract.md"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def contract_sections(env, numbers):
    """The `## <n>. ...` sections of docs/contract.md, verbatim, so a node's rules have one copy."""
    parts = re.split(r"(?m)^(?=## )", contract_text(env))
    want = [p for p in parts for n in numbers if re.match(rf"## {n}\. ", p)]
    return "\n".join(want) if want else "(docs/contract.md has no such section here)"


def render(name, **vals):
    tpl = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"<<(\w+)>>", lambda m: str(vals.get(m.group(1), m.group(0))), tpl)


def anchors_block(worktree, body):
    """For each list item under an `Anchors` heading that names a file, as the anchor gate reads one: each of its quoted
    fragments' current lines."""
    out = []
    for line, anchors in records.anchor_sections(body):
        m = re.match(r"\s*(?:[-*+]|\d+[.)])\s[^`\r\n]*`([^`\s]+/[^`\s]+|[^`\s]+\.\w+)`", line) if anchors else None
        if not m:
            continue
        rel = re.split(r"[:#]", m.group(1), 1)[0]  # the gate cuts a span at its first : or # (`path:171`, `path#L171`)
        frags = records.anchor_fragments(line)
        f = Path(worktree) / rel
        if not f.is_file():
            out.append(f"{rel}: not in the tree")
            continue
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        for frag in frags:
            hits = [i + 1 for i, t in enumerate(lines) if frag in t]
            out.append("\n".join(f"{rel}:{i}: {lines[i - 1].strip()}" for i in hits[:3]) or f"{rel}: fragment not found: {frag!r}")
    return "\n".join(out) or "(the issue names no anchor)"


def implement_docs(env, worktree):
    docs = env.lookup("overlays.implement", []) or []
    out = []
    for rel in docs:
        f = Path(worktree) / rel
        out.append(f"### {rel}\n\n" + (f.read_text(encoding="utf-8", errors="replace") if f.is_file() else "(missing)"))
    return "\n\n".join(out) or "(none named)"
