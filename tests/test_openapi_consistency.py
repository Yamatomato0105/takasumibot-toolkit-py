import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_only_openapi_http_paths_are_used():
    doc = json.loads((ROOT / "TakasumiBOT-OpenAPI-Document.json").read_text(encoding="utf-8"))
    http_paths = {p for p, ops in doc["paths"].items() if "get" in ops}
    src = (ROOT / "src/takasumibot_toolkit/client.py").read_text(encoding="utf-8")
    used = {
        node.value
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/v3/")
    }
    assert used == http_paths
    assert len(used) == 13
    all_src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py"))
    assert "/v3/realtime" not in all_src
    assert "/v3/stock/{" not in all_src


def test_generated_models_match_openapi_schemas():
    doc = json.loads((ROOT / "TakasumiBOT-OpenAPI-Document.json").read_text(encoding="utf-8"))
    from takasumibot_toolkit import models

    for schema_name in doc["components"]["schemas"]:
        model = getattr(models, schema_name)
        expected_aliases = set(doc["components"]["schemas"][schema_name]["properties"])
        actual_aliases = {(f.alias if f.alias else name) for name, f in model.model_fields.items()}
        # Statistics inline properties are covered by nested models.
        if schema_name == "StatisticsResponse":
            continue
        assert actual_aliases == expected_aliases, schema_name
