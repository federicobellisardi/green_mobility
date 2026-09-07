"""End-to-end smoke test against NOMAD's own tiny Liechtenstein test extract
(external/nomad/tests/integration/download_test_data.sh) with synthetic
gravity demand — validates the nomad_wrapper.runner plumbing (config
materialization, subprocess invocation, output-dir prediction) without
needing any of our 4 real cities' OSM/MITMA/FUA data. Skips automatically if
NOMAD hasn't been built or the test extract hasn't been downloaded, per
README "Testing".
"""
from __future__ import annotations

import subprocess

import pytest

from green_mobility.config import NOMAD_DIR
from green_mobility.nomad_wrapper.paths import NomadNotBuiltError, require_nomad_cli

LIECHTENSTEIN_PBF = NOMAD_DIR / "data" / "test_osm" / "liechtenstein.osm.pbf"


@pytest.fixture
def nomad_cli_binary():
    try:
        return require_nomad_cli()
    except NomadNotBuiltError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def liechtenstein_pbf():
    if not LIECHTENSTEIN_PBF.exists():
        pytest.skip(
            f"{LIECHTENSTEIN_PBF} not downloaded — run "
            f"external/nomad/tests/integration/download_test_data.sh first"
        )
    return LIECHTENSTEIN_PBF


def test_nomad_cli_quick_run_on_liechtenstein(nomad_cli_binary, liechtenstein_pbf, tmp_path):
    """Mirrors external/nomad/TESTING.txt section 2 ("CLI — QUICK RUN"):
    loads OSM, generates synthetic gravity-model agents, writes GeoJSON —
    proves the built nomad_cli binary this repo's wrapper shells out to
    actually runs end-to-end in this environment."""
    result = subprocess.run(
        [str(nomad_cli_binary), "--osm", str(liechtenstein_pbf)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    output_dirs = list((tmp_path / "nomad_output").glob("*")) if (tmp_path / "nomad_output").exists() else []
    assert output_dirs, f"expected nomad_cli to create output under {tmp_path}/nomad_output"
