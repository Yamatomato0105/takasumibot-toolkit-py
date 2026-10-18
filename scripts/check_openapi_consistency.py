#!/usr/bin/env python3
"""Check that the client only uses HTTP paths present in the OpenAPI doc."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    doc = json.loads((ROOT / "TakasumiBOT-OpenAPI-Document.json").read_text(encoding="utf-8"))
    http_paths = {p for p, ops in doc["paths"].items() if "get" in ops}
    client_src = (ROOT / "src/takasumibot_toolkit/client.py").read_text(encoding="utf-8")
    tree = ast.parse(client_src)
    used: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("/v3/")
        ):
            used.add(node.value)
    # Stock derivation must not invent /v3/stock/{id}
    problems = []
    if used != http_paths:
        problems.append(f"path mismatch: used={sorted(used)} openapi={sorted(http_paths)}")
    all_src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py"))
    if "/v3/realtime" in all_src:
        problems.append("websocket path must not be implemented")
    if "/v3/stock/{" in all_src:
        problems.append("invented stock detail path found")
    cfg = (ROOT / "src/takasumibot_toolkit/config.py").read_text(encoding="utf-8")
    # BasicConfig must not declare base_url; resolve_base_url is allowed.
    if (
        re.search(r"\bbase_url\s*:", cfg)
        and "class BasicConfig" in cfg
        and "base_url: str" in cfg.split("class BasicConfig")[1].split("class ")[0]
    ):
        problems.append("BasicConfig must not contain base_url")
    default_url = doc["servers"][0]["url"].rstrip("/")
    if default_url not in cfg:
        problems.append("DEFAULT_BASE_URL does not match OpenAPI servers[0].url")
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 1
    print(f"openapi consistency ok: {len(used)} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
