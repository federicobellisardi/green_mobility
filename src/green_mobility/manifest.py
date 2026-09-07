"""Provenance manifests — one JSON file per pipeline-stage output, recording
what produced it and when, so no artifact is ever ambiguous about its origin
(required for "output riproducibili": a flows/exposure/intervention table
without a manifest next to it should be treated as untrusted).
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from green_mobility.config import NOMAD_DIR, REPO_ROOT


def _git_commit(repo_dir: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def write_manifest(output_path: Path, command: str, params: dict[str, Any]) -> Path:
    """Write `<output_path>.manifest.json` recording provenance for
    `output_path` (which may or may not exist yet when this is called)."""
    manifest = {
        "output": str(output_path),
        "command": command,
        "params": params,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "green_mobility_commit": _git_commit(REPO_ROOT),
        "nomad_submodule_commit": _git_commit(NOMAD_DIR),
    }
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest_path
