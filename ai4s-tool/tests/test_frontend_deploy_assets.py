"""Publishing older open tabs must not duplicate every frontend chunk."""
import importlib.util
import os
from pathlib import Path


def test_preserve_lazy_assets_hardlinks_previous_chunks(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "deploy_canonical_frontend.py"
    spec = importlib.util.spec_from_file_location("deploy_canonical_frontend", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = tmp_path / "previous"
    current = tmp_path / "current"
    (previous / "assets").mkdir(parents=True)
    (current / "assets").mkdir(parents=True)
    (previous / "assets" / "old-hash.js").write_bytes(b"old")
    (previous / "assets" / "current-hash.js").write_bytes(b"previous")
    (current / "assets" / "current-hash.js").write_bytes(b"current")
    module.preserve_lazy_assets(previous, current)
    assert (current / "assets" / "old-hash.js").read_bytes() == b"old"
    assert os.path.samefile(previous / "assets" / "old-hash.js", current / "assets" / "old-hash.js")
    assert (current / "assets" / "current-hash.js").read_bytes() == b"current"
