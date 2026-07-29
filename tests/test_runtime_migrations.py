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


def test_runtime_migration_explicitly_commits_postgres(monkeypatch, tmp_path):
    events = []

    class Dialect:
        name = "postgresql"

    class Connection:
        dialect = Dialect()

        def execute(self, statement):
            events.append(str(statement))

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr("core.database.build_engine", lambda: Engine())
    monkeypatch.setattr("core.database.command.upgrade", lambda *_: events.append("upgrade"))

    migrate_to_head()

    assert events == [
        "SELECT pg_advisory_lock(641347204716)",
        "commit",
        "upgrade",
        "commit",
        "SELECT pg_advisory_unlock(641347204716)",
        "commit",
    ]
