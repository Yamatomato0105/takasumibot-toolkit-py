#!/usr/bin/env python3
"""Generate src/takasumibot_toolkit/models/generated/models.py from the OpenAPI doc.

Deterministic, stdlib-only generator (datamodel-code-generator is declared as
a dev dependency per docs, but this script keeps regeneration reproducible
without it). All documented properties become required fields; nullable
properties become ``T | None``; snake_case field names carry the OpenAPI name
as alias. The /v3/realtime/ WebSocket path is excluded (it has no schemas).

Where the OpenAPI document and the live API disagree, the document is corrected
here through the ``NULLABLE_OVERRIDES`` / ``OPTIONAL_OVERRIDES`` tables below
instead of hand-editing the generated module.

OpenAPI ``enum`` properties are generated as ``LooseEnum`` (any non-empty str,
like the NPM package's ``looseEnum``) so a value added to the API later cannot
break response parsing; the documented values are emitted as a ``Literal``
alias plus a tuple constant named in ``ENUM_NAMES``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "TakasumiBOT-OpenAPI-Document.json"
OUT = ROOT / "src" / "takasumibot_toolkit" / "models" / "generated" / "models.py"

INLINE_NAMES = {
    ("StatisticsResponse", "user"): "UserStatistics",
    ("StatisticsResponse", "company"): "CompanyAggregate",
    ("StatisticsResponse", "economy"): "EconomyStatistics",
    ("StatisticsResponse", "event"): "EventStatistics",
    ("EventStatistics", "messageCreate"): "EventWindowStatistics",
    ("EventStatistics", "interactionCreate"): "EventWindowStatistics",
    ("EventStatistics", "guildMemberAdd"): "EventCounterStatistics",
    ("EventStatistics", "guildMemberRemove"): "EventCounterStatistics",
    ("EventStatistics", "guildCreate"): "EventCounterStatistics",
    ("EventStatistics", "guildDelete"): "EventCounterStatistics",
}

# (schema, property) pairs the live API sends as ``null`` even though the OpenAPI
# document does not declare ``nullable: true``.
NULLABLE_OVERRIDES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # Discord users without an accent colour are returned as
        # ``"accentColor": null`` (observed for ~70% of ranking entries).
        ("DiscordUserSearchResponse", "accentColor"),
    }
)

# (schema, property) pairs the live API omits entirely in some responses, so the
# generated field is ``T | None = None`` instead of required.
OPTIONAL_OVERRIDES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # ``GET /v3/company/{id}`` omits ``employees`` when ``{id}`` is the
        # owner's Discord user id; it is present for a company id.
        ("CompanyDetailResponse", "employees"),
    }
)


# (schema, property) -> (Literal alias, tuple constant) for every OpenAPI enum.
# The names follow the NPM package (``GIFT_TYPES``, ``GIFT_STATUSES``, ``STOCK_IDS``).
ENUM_NAMES: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("GiftResponse", "type"): ("GiftType", "GIFT_TYPES"),
    ("GiftResponse", "status"): ("GiftStatus", "GIFT_STATUSES"),
    ("StockEntry", "id"): ("StockId", "STOCK_IDS"),
}


def to_snake(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def type_expr(schema: dict, parent: str, prop: str, inline: dict[str, dict]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    t = schema.get("type")
    if "enum" in schema:
        if (parent, prop) not in ENUM_NAMES:
            raise ValueError(f"name the enum {parent}.{prop} in ENUM_NAMES")
        if t != "string":
            raise ValueError(f"unsupported non-string enum for {parent}.{prop}")
        return "LooseEnum"
    if t == "string":
        return "AwareDatetime" if schema.get("format") == "date-time" else "str"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        return f"list[{type_expr(schema['items'], parent, prop, inline)}]"
    if t == "object" and "properties" in schema:
        name = INLINE_NAMES[(parent, prop)]
        inline[name] = schema
        return name
    raise ValueError(f"unsupported schema for {parent}.{prop}: {schema!r}")


def render_class(name: str, schema: dict, inline: dict[str, dict]) -> str:
    lines = [
        f"class {name}(TakasumiBotModel):",
        f'    """Generated from OpenAPI schema ``{name}``."""',
        "",
    ]
    for prop, pschema in schema["properties"].items():
        field = to_snake(prop)
        expr = type_expr(pschema, name, prop, inline)
        optional = (name, prop) in OPTIONAL_OVERRIDES
        nullable_override = (name, prop) in NULLABLE_OVERRIDES
        if pschema.get("nullable") or nullable_override or optional:
            expr += " | None"
        args = []
        if optional:
            args.append("default=None")
        if field != prop:
            args.append(f"alias={json.dumps(prop, ensure_ascii=False)}")
        if "description" in pschema:
            args.append(f"description={json.dumps(pschema['description'], ensure_ascii=False)}")
        if "enum" in pschema:
            constant = ENUM_NAMES[(name, prop)][1]
            lines.append(f"    # LooseEnum: documented values are {constant}; others are accepted.")
        if optional:
            lines.append(
                "    # OPTIONAL_OVERRIDES: absent from some live responses"
                " (see scripts/generate_models.py)."
            )
        elif nullable_override:
            lines.append(
                "    # NULLABLE_OVERRIDES: null in some live responses"
                " (see scripts/generate_models.py)."
            )
        if not args:
            lines.append(f"    {field}: {expr}")
            continue
        single = f"    {field}: {expr} = Field({', '.join(args)})"
        if len(single) <= 100:
            lines.append(single)
        else:
            lines.append(f"    {field}: {expr} = Field(")
            for arg in args:
                lines.append(f"        {arg},")
            lines.append("    )")
    return "\n".join(lines)


def render_enums(schemas: dict) -> tuple[str, list[str]]:
    """Render the Literal aliases and tuple constants of every OpenAPI enum."""
    blocks: list[str] = []
    names: list[str] = []
    for (schema_name, prop), (alias, constant) in ENUM_NAMES.items():
        pschema = schemas[schema_name]["properties"][prop]
        values = pschema["enum"]
        literal = ", ".join(json.dumps(v) for v in values)
        items = ", ".join(json.dumps(v) for v in values) + ("," if len(values) == 1 else "")
        field = f"{schema_name}.{to_snake(prop)}"
        blocks.append(
            "\n".join(
                [
                    f"{alias}: TypeAlias = Literal[{literal}]",
                    f'"""Documented values of ``{field}`` (OpenAPI enum)."""',
                    f"{constant}: Final[tuple[{alias}, ...]] = ({items})",
                    f'"""``{field}`` values listed by the OpenAPI document."""',
                ]
            )
        )
        names.extend([alias, constant])
    return "\n\n".join(blocks), names


def generate() -> str:
    doc = json.loads(OPENAPI.read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]
    inline: dict[str, dict] = {}
    classes: dict[str, str] = {}
    # Render non-statistics first, then inline statistics classes before StatisticsResponse.
    order = [n for n in schemas if n != "StatisticsResponse"]
    for name in order:
        classes[name] = render_class(name, schemas[name], inline)
    stats_schema = schemas["StatisticsResponse"]
    # Rendering StatisticsResponse populates `inline` recursively in steps.
    # First pass: collect inline classes iteratively.
    pending = {"StatisticsResponse": stats_schema}
    rendered_inline: dict[str, str] = {}
    while pending:
        cname, cschema = pending.popitem()
        before = set(inline)
        text = render_class(cname, cschema, inline)
        if cname == "StatisticsResponse":
            classes[cname] = text
        else:
            rendered_inline[cname] = text
        for new_name in set(inline) - before:
            if new_name not in rendered_inline and new_name != "StatisticsResponse":
                pending[new_name] = inline[new_name]

    # Order inline classes so dependencies come first (counters/windows before event, etc).
    def depth(n: str) -> int:
        return {
            "EventCounterStatistics": 0,
            "EventWindowStatistics": 0,
            "UserStatistics": 0,
            "CompanyAggregate": 0,
            "EconomyStatistics": 0,
            "EventStatistics": 1,
        }.get(n, 0)

    inline_order = sorted(rendered_inline, key=lambda n: (depth(n), n))
    parts = [
        '"""Generated response models. DO NOT EDIT BY HAND.',
        "",
        "Regenerate with: python scripts/generate_models.py",
        "",
        "Deviations from the OpenAPI document are declared in that script",
        "(NULLABLE_OVERRIDES / OPTIONAL_OVERRIDES); each one is marked with a comment.",
        "OpenAPI enums are LooseEnum fields (any non-empty str, like the NPM looseEnum);",
        "their documented values are the Literal aliases / tuples below.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final, Literal, TypeAlias",
        "",
        "from pydantic import Field",
        "",
        "from takasumibot_toolkit.models._base import AwareDatetime, LooseEnum, TakasumiBotModel",
        "",
    ]
    enum_block, enum_names = render_enums(schemas)
    parts.extend([enum_block, "", ""])
    body = []
    for name in order:
        body.append(classes[name])
    for name in inline_order:
        body.append(rendered_inline[name])
    body.append(classes["StatisticsResponse"])
    parts.append("\n\n\n".join(body))
    all_names = enum_names + order + inline_order + ["StatisticsResponse"]
    all_literal = ",\n    ".join(json.dumps(n) for n in all_names)
    parts.append(f"\n\n__all__ = [\n    {all_literal},\n]\n")
    return "\n".join(parts)


def _format(content: str) -> str:
    if importlib.util.find_spec("ruff") is None:
        raise SystemExit(
            "ruff is required to format generated models; install the project dev dependencies"
        )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8", dir=OUT.parent
    ) as fh:
        fh.write(content)
        tmp = Path(fh.name)
    try:
        subprocess.run(
            [sys.executable, "-m", "ruff", "format", str(tmp)], check=True, capture_output=True
        )
        return tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if generated file is stale")
    args = ap.parse_args()
    content = _format(generate())
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != content:
            print(
                "generated models are stale; run: python scripts/generate_models.py",
                file=sys.stderr,
            )
            return 1
        print("generated models are up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
