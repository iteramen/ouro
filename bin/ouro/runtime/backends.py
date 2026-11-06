"""The worker backends a developer can select: their developer.toml rules here, their runtime members in the adapter.

Each adapter module exports INTERFACE, an Interface whose members are callables or an explicit marker.
"""
from dataclasses import dataclass
from importlib import import_module
from typing import Callable

CLAUDE_MODELS = ("opus", "sonnet", "haiku", "fable")
NOT_REQUIRED = "not-required"  # the backend has nothing to prove at this point
UNSUPPORTED = "unsupported"    # the shared check does not run for this backend
UNCONFINED = "unconfined"      # PROBE commands run without a write boundary
CONFINED = "confined"          # PROBE commands run inside the adapter's write boundary


@dataclass(frozen=True)
class Interface:
    preflight: Callable                 # (settings, capabilities) -> iterable of (session, usage, cost) attempts
    worktree_readiness: Callable | str  # (settings, main, target, branch, base, *, scratch) or NOT_REQUIRED
    execute: Callable                   # (name, prompt, model, tools, cwd, timeout, denied, schema, settings, diagnostic)
    probe_confinement: str              # CONFINED or UNCONFINED
    sandbox_route: Callable | str       # (platform) -> missing prerequisites, None for no route there; NOT_REQUIRED or UNSUPPORTED
    validate_answer: Callable | str     # (name, answer) or UNSUPPORTED
    admits: Callable                    # (capabilities, None for full readiness) -> whether this host's backend serves it


@dataclass(frozen=True)
class Backend:
    name: str
    label: str
    claude_aliases: bool  # True: a model must be a Claude alias; False: it must not be one
    efforts: tuple = ()   # runtime.effort values it takes; empty refuses the key

    @property
    def interface(self):
        return import_module(f".{self.name}", __package__).INTERFACE

    def default_model(self, legacy):
        return legacy if self.claude_aliases else "default"

    def validate_runtime(self, models, effort, effort_key="runtime.effort"):
        """`models` maps each developer.toml key (runtime.models.builder, runtime.roles.reviewer.model) to its value."""
        for key, value in models.items():
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{key} must be a non-empty model")
            if self.claude_aliases and value not in CLAUDE_MODELS:
                raise ValueError(f"{key}: {self.label} requires a supported alias")
            if not self.claude_aliases and value in CLAUDE_MODELS:
                raise ValueError(f"{key}: Claude aliases cannot select {self.label} models")
        if effort is not None and effort not in self.efforts:
            ((label, efforts),) = [(b.label, b.efforts) for b in BACKENDS.values() if b.efforts]
            raise ValueError(f"{effort_key} requires {label} and {', '.join(efforts[:-1])} or {efforts[-1]}")


BACKENDS = {b.name: b for b in (
    Backend("claude", "Claude", claude_aliases=True),
    Backend("codex", "Codex", claude_aliases=False, efforts=("low", "medium", "high", "xhigh", "max")),
    Backend("qwen", "Qwen Code", claude_aliases=False),
)}


def names():
    *rest, last = BACKENDS
    return f"{', '.join(rest)} or {last}" if rest else last
