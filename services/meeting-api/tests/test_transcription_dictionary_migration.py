import importlib.util
from pathlib import Path


def _migration():
    path = Path(__file__).parents[3] / "scripts" / "migrations" / "20260712_add_transcription_dictionary.py"
    spec = importlib.util.spec_from_file_location("dictionary_migration", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_is_idempotent_and_has_rollback():
    migration = _migration()
    assert "CREATE TABLE IF NOT EXISTS" in migration.UP[0]
    assert "UNIQUE (user_id, normalized_term)" in migration.UP[0]
    assert "DROP TABLE IF EXISTS" in migration.DOWN[0]
