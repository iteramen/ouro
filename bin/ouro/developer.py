"""Developer-owned runtime and reviewer selection; repository shipping policy is never merged here."""
import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .sh import DriverError, load_binding_module
from .coordination import Coordination
from .runtime.backends import BACKENDS, names

ROLES = ("triager", "checkpointer", "builder", "builder_trivial", "reviewer")
INHERITS = {"triager": "builder", "checkpointer": "builder"}  # a role nothing names takes this role's model
ROLE_KEYS = {"backend", "model", "effort", "advisor"}
REVIEWERS = ("adversarial-review", "external-audit", "copilot")  # ship.review's values less none: no developer opts out of review


def default_path():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ouro" / "developer.toml"


@dataclass
class Settings:
    backend: str = "claude"
    models: dict = field(default_factory=dict)
    effort: str | None = None
    coordination: Coordination | None = None
    assignees: list = field(default_factory=list)
    roles: dict = field(default_factory=dict)
    serves: dict = field(default_factory=dict)  # {role: contract} one backend's preflight proves, set by node.preflight; empty means the backend's own default
    review: dict = field(default_factory=dict)  # [review], under the ship.* names its keys stand for: review, external_cli, external_model, external_effort
    advisor: str | None = None  # "up" or a model alias; None leaves the host's settings to decide

    def backend_of(self, role):
        return self.roles.get(role, {}).get("backend", self.backend)

    def effort_of(self, role):
        return self.roles.get(role, {}).get("effort", self.effort if self.backend_of(role) == self.backend else None)

    def advisor_of(self, role):
        return self.roles.get(role, {}).get("advisor", self.advisor if self.backend_of(role) == self.backend else None)

    def mixed(self, roles=ROLES):
        """Whether any of `roles` selects a backend other than runtime.backend."""
        return any(self.backend_of(role) != self.backend for role in roles)

    def serving(self, backend, default=ROLES):
        """The roles a preflight of `backend` proves: those node.preflight scoped it to, else `default`, each that selects it."""
        return [role for role in (self.serves or default) if self.backend_of(role) == backend]

    def of(self, role):
        """The settings an adapter executes `role` with: its backend, effort and advisor. Read nothing else from them."""
        backend, effort, advisor = self.backend_of(role), self.effort_of(role), self.advisor_of(role)
        return (self if (backend, effort, advisor) == (self.backend, self.effort, self.advisor) else
                replace(self, backend=backend, effort=effort, advisor=advisor))

    def model(self, role, legacy):
        """Role table, then runtime.models, for the role and then the role it inherits; else the backend's default.

        A role inherits only from a role on its own backend. `legacy` is the repository's [models] value (or today's
        default) for the role, or for the role it inherits."""
        backend = self.backend_of(role)
        for name in (role, INHERITS.get(role)):
            named = self.roles.get(name, {}).get("model", self.models.get(name)) if self.backend_of(name) == backend else None
            if named is not None:
                return named
        return BACKENDS[backend].default_model(legacy)


def _roles(runtime, backend, models):
    roles = runtime.get("roles", {})
    if not isinstance(roles, dict) or set(roles) - set(ROLES):
        raise ValueError("unknown runtime.roles role")
    for role, table in roles.items():
        where = f"runtime.roles.{role}"
        if not isinstance(table, dict) or set(table) - ROLE_KEYS:
            raise ValueError(f"unknown {where} key")
        selected = table.get("backend", backend)
        if selected not in BACKENDS:
            raise ValueError(f"{where}.backend must be {names()}")
        BACKENDS[selected].validate_runtime({f"{where}.model": table["model"]} if "model" in table else {},
                                            table.get("effort"), f"{where}.effort", table.get("advisor"), f"{where}.advisor")
        if selected != backend and role in models:  # runtime.models was checked against runtime.backend, not this one
            raise ValueError(f"runtime.models.{role}: {where}.backend selects {BACKENDS[selected].label}; "
                             f"name its model as {where}.model")
    return roles


def _review(table):
    """[review] under the ship.* names; each key keeps the values and rules of the ship key it stands for."""
    if not isinstance(table, dict) or set(table) - {"reviewer", "external_cli", "external_model", "external_effort"}:
        raise ValueError("unknown review key: [review] holds reviewer, external_cli, external_model and external_effort")
    if table.get("reviewer") == "none":
        raise ValueError('review.reviewer = "none": a developer cannot opt out of review')
    if "reviewer" in table and table["reviewer"] not in REVIEWERS:
        raise ValueError(f"review.reviewer must be one of {', '.join(REVIEWERS)}")
    if "external_cli" in table:
        clis = load_binding_module().EXTERNAL_CLIS
        if table["external_cli"] not in clis:
            raise ValueError(f"review.external_cli must be one of {', '.join(clis)}")
    for key in ("external_model", "external_effort"):
        if key in table and (not isinstance(table[key], str) or not table[key]):
            raise ValueError(f"review.{key} must be a non-empty string")
    return {("review" if k == "reviewer" else k): v for k, v in table.items()}


def load(path=None):
    explicit = path or os.environ.get("OURO_DEVELOPER_CONFIG")
    p = Path(explicit) if explicit else default_path()
    if not p.exists() and not explicit:
        return Settings()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8-sig"))
        if not data or set(data) - {"runtime", "coordination", "followup", "review"}:
            raise ValueError("only [runtime], [coordination], [followup] and [review] are allowed")
        runtime = data.get("runtime", {})
        if not isinstance(runtime, dict):
            raise ValueError("runtime must be a table")
        coordination = Coordination.parse(data["coordination"]) if "coordination" in data else None
        if set(runtime) - {"backend", "models", "effort", "advisor", "roles"}:
            raise ValueError("unknown runtime key")
        backend = runtime.get("backend", "claude")
        if backend not in BACKENDS:
            raise ValueError(f"runtime.backend must be {names()}")
        models = runtime.get("models", {})
        if not isinstance(models, dict) or set(models) - set(ROLES):
            raise ValueError("unknown runtime.models role")
        effort, advisor = runtime.get("effort"), runtime.get("advisor")
        BACKENDS[backend].validate_runtime({f"runtime.models.{r}": v for r, v in models.items()}, effort, advisor=advisor)
        roles = _roles(runtime, backend, models)
        followup = data.get("followup", {})
        assignees = followup.get("assignees", []) if isinstance(followup, dict) else None
        if not isinstance(followup, dict) or set(followup) - {"assignees"} or not isinstance(assignees, list)                 or not all(isinstance(a, str) and a.strip() and a == a.strip() for a in assignees):
            raise ValueError("followup.assignees must be a list of GitHub logins (or @me)")
        return Settings(backend, models, effort, coordination, assignees, roles, review=_review(data.get("review", {})),
                        advisor=advisor)
    except (OSError, ValueError, TypeError) as ex:
        raise DriverError(f"developer binding {p}: {ex}") from ex
