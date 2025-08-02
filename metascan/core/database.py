# Temporary wrapper to use SQLite implementation due to plyvel ABI issues on macOS
from metascan.core.database_sqlite import DatabaseManager

__all__ = ['DatabaseManager']