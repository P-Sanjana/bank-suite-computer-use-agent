"""
Artifact writer: validates an Artifact against its Pydantic schema
and writes it to disk as a versioned JSON file.
"""
from __future__ import annotations
 
import json
import uuid
from pathlib import Path
 
from ..schema.artifact import Artifact

ARTIFACTS_DIR = Path("artifacts")

def write_artifact(artifact: Artifact, directory: Path | None = None) -> Path:
    """
    Validate and write the artifact to disk.
    Returns the path of the written file.
 
    File naming: {name}-v{version}-{id[:8]}.json
    This makes artifacts human-browsable and avoids collisions across versions.
    """
    out_dir = directory or ARTIFACTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pydantic validates on model_dump and will raise ValidationError if schema is broken
    data = artifact.model_dump(mode="json")

    filename = f"{artifact.name}-v{artifact.version}-{artifact.id[:8]}.json"
    path = out_dir / filename
    path.write_text(json.dumps(data, indent=2))

    print(f" Artifact written: {path}")
    return path

def load_artifact(path: Path) -> Artifact:
    """
    Load and validate an artifact from disk.
    Raises ValidationError if the file doesn't match the schema.
    Raises ValueError if schema_version is unsupported.
    """
    from ..schema.artifact import CURRENT_SCHEMA_VERSION

    raw = json.loads(path.read_text())
    schema_version = raw.get("schema_version", "unknown")
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"Artifact schema version mismatch: file has '{schema_version}', "
            f"runner expects '{CURRENT_SCHEMA_VERSION}'. "
            "Re-run discovery or migrate the artifact."
            )

    return Artifact.model_validate(raw)

def new_artifact_id() -> str:
    return str(uuid.uuid4())
