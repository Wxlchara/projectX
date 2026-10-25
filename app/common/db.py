"""One SQLite connection per short operation; never hold it across OCR/AI."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError("Unsupported database schema version")
            connection.execute("PRAGMA journal_mode = WAL")
            if version == 0:
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n" + schema
                        + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
                    )
                except BaseException:
                    connection.rollback()
                    raise

    def is_ready(self) -> bool:
        with self.connection() as connection:
            return (
                connection.execute("SELECT 1").fetchone()[0] == 1
                and connection.execute("PRAGMA user_version").fetchone()[0]
                == SCHEMA_VERSION
            )
