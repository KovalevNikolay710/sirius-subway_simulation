"""Validate contract package files."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from metro_control.contracts import PACKAGES


def validate_file(path: Path | str) -> list[str]:
    """Return error strings '<file>: <field.path>: <msg>'; empty list means ok."""
    p = Path(path)
    name = str(p)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{name}: $: cannot read JSON ({e})"]
    kind = data.get("kind") if isinstance(data, dict) else None
    model = PACKAGES.get(kind) if isinstance(kind, str) else None
    if model is None:
        return [f"{name}: kind: unknown kind {kind!r}; expected one of {sorted(PACKAGES)}"]
    try:
        pkg = model.model_validate(data)
    except ValidationError as e:
        return [
            f"{name}: {'.'.join(str(x) for x in err['loc']) or '$'}: {err['msg']}"
            for err in e.errors()
        ]
    except (OSError, ValueError) as e:
        return [f"{name}: $: cannot load config/line.json or validate ({e})"]
    if kind == "forecast":
        from metro_control.team_schema import to_team_rows, validate_team_rows

        return [f"{name}: team: {m}" for m in validate_team_rows(to_team_rows(pkg))]
    return []
