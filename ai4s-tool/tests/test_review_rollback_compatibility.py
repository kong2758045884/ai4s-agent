import importlib.util
import sqlite3
from pathlib import Path

import pytest


def test_code_rollback_never_ignores_saved_human_decisions(tmp_path):
    spec = importlib.util.spec_from_file_location("release_gate", Path(__file__).resolve().parents[2] / "scripts/deploy_strategic_optimization.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "copy.db"
    old = tmp_path / "previous_tool"
    old.mkdir()
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE original_data (id TEXT)")
        conn.execute("INSERT INTO original_data VALUES ('must-remain')")
    module.require_review_compatible(database, old)
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE strategic_claim_review (id TEXT)")
    module.require_review_compatible(database, old)
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO strategic_claim_review VALUES ('signed-review')")
    with pytest.raises(RuntimeError, match="cannot enforce saved human claim reviews"):
        module.require_review_compatible(database, old)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT * FROM original_data").fetchall() == [('must-remain',)]
        assert conn.execute("SELECT * FROM strategic_claim_review").fetchall() == [('signed-review',)]
    compatible = old / "ai4s_tool/api/claim_reviews.py"
    compatible.parent.mkdir(parents=True)
    compatible.write_text("# compatible projection retained", encoding="utf-8")
    module.require_review_compatible(database, old)
