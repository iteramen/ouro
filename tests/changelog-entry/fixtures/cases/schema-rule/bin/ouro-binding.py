"""Demo binding validator, the subject of the binding-schema surface.

usage: python3 ouro-binding.py check [path]
"""

PREFIX = "demo"

POLICIES = ("solo", "team")

SCHEMA = {
    "repo": {"slug": str, "default_branch": str},
    "ship": {"policy": str, "review": str},
}
REQUIRED = ("schema", "repo.slug")


def validate(data):
    errors = []
    if data.get("ship", {}).get("policy") not in POLICIES:
        errors.append("ship.policy: must be one of " + " or ".join(POLICIES))
    return errors


def check(data):
    return sorted(SCHEMA)
