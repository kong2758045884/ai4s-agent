import importlib.util
from pathlib import Path

import pytest


def test_activation_space_gate_rejects_before_service_stop(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "release_space_gate", Path(__file__).resolve().parents[2] / "scripts/deploy_strategic_optimization.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "large.db"
    with database.open("wb") as stream:
        stream.truncate(310 * 1024 * 1024)
    with pytest.raises(RuntimeError, match="Insufficient deployment disk before stopping service"):
        module.require_backup_space([database], 340 * 1024 * 1024)
    assert module.require_backup_space([database], 600 * 1024 * 1024) >= 438 * 1024 * 1024
