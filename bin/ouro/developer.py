"""Developer-owned runtime selection; repository workflow policy is never merged here."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .sh import DriverError
from .coordination import Coordination

ROLES = ("builder", "builder_trivial", "reviewer")
CLAUDE_MODELS = ("opus", "sonnet", "haiku", "fable")


def default_path():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ouro" / "developer.toml"


@dataclass
class Settings:
    backend: str = "claude"
    models: dict = field(default_factory=dict)
    effort: str | None = None
    coordination: Coordination | None = None
    assignees: list = field(default_factory=list)

    def model(self, role, legacy):
        return self.models.get(role, legacy if self.backend == "claude" else "default")


def load(path=None):
    explicit = path or os.environ.get("OURO_DEVELOPER_CONFIG")
    p = Path(explicit) if explicit else default_path()
    if not p.exists() and not explicit:
        return Settings()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8-sig"))
        if not data or set(data) - {"runtime", "coordination", "followup"}:
            raise ValueError("only [runtime], [coordination] and [followup] are allowed")
        runtime = data.get("runtime", {})
        if not isinstance(runtime, dict):
            raise ValueError("runtime must be a table")
        coordination = Coordination.parse(data["coordination"]) if "coordination" in data else None
        if set(runtime) - {"backend", "models", "effort"}:
            raise ValueError("unknown runtime key")
        backend = runtime.get("backend", "claude")
        if backend not in ("claude", "codex"):
            raise ValueError("runtime.backend must be claude or codex")
        models = runtime.get("models", {})
        if not isinstance(models, dict) or set(models) - set(ROLES):
            raise ValueError("unknown runtime.models role")
        for role, value in models.items():
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"runtime.models.{role} must be a non-empty model")
            if backend == "claude" and value not in CLAUDE_MODELS:
                raise ValueError(f"runtime.models.{role}: Claude requires a supported alias")
            if backend == "codex" and value in CLAUDE_MODELS:
                raise ValueError(f"runtime.models.{role}: Claude aliases cannot select Codex models")
        effort = runtime.get("effort")
        if effort is not None and (backend != "codex" or effort not in ("low", "medium", "high", "xhigh", "max")):
            raise ValueError("runtime.effort requires Codex and low, medium, high, xhigh or max")
        followup = data.get("followup", {})
        assignees = followup.get("assignees", []) if isinstance(followup, dict) else None
        if not isinstance(followup, dict) or set(followup) - {"assignees"} or not isinstance(assignees, list)                 or not all(isinstance(a, str) and a.strip() and a == a.strip() for a in assignees):
            raise ValueError("followup.assignees must be a list of GitHub logins (or @me)")
        return Settings(backend, models, effort, coordination, assignees)
    except (OSError, ValueError, TypeError) as ex:
        raise DriverError(f"developer binding {p}: {ex}") from ex
