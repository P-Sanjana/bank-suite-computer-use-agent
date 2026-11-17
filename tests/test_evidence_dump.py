from pathlib import Path

from src.observability.dump import dump_json


def test_dump_json_writes_steps_file(tmp_path: Path) -> None:
    path = dump_json(
        tmp_path,
        "steps.json",
        {"goal": "lookup", "steps": [{"tool": "type", "success": True}]},
    )
    assert path.name == "steps.json"
    text = path.read_text(encoding="utf-8")
    assert "lookup" in text
    assert '"tool": "type"' in text
