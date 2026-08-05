"""Deprecated: MySQL replaced by SQLite. Kept as import alias."""

from app.services.sqlite_repo import SQLiteRepo as MySQLRepo
from app.services.sqlite_repo import SQLiteRepo

__all__ = ["MySQLRepo", "SQLiteRepo"]
