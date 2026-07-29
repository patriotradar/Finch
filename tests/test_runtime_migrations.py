from sqlalchemy import inspect

from core.database import build_engine, migrate_to_head


def test_runtime_migration_creates_and_reuses_schema(monkeypatch, tmp_path):
    database_path = tmp_path / "runtime-migrations.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    migrate_to_head()
    migrate_to_head()

    tables = set(inspect(build_engine()).get_table_names())
    assert "workspaces" in tables
    assert "workspace_users" in tables
    assert "alembic_version" in tables
