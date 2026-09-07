import json

from green_mobility.manifest import write_manifest


def test_write_manifest_records_command_params_and_timestamp(tmp_path):
    output = tmp_path / "some_output.parquet"
    manifest_path = write_manifest(output, "flows", {"city": "zaragoza", "modes": ["car", "walk"]})

    assert manifest_path.name == "some_output.parquet.manifest.json"
    data = json.loads(manifest_path.read_text())
    assert data["command"] == "flows"
    assert data["params"]["city"] == "zaragoza"
    assert "created_utc" in data
    # green_mobility_commit may be None outside a git checkout, but the key
    # must always be present so consumers can distinguish "not recorded"
    # from "field doesn't exist".
    assert "green_mobility_commit" in data
    assert "nomad_submodule_commit" in data
