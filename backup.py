"""Create a consistent copy of the shared database while the app is running.

Usage: python backup.py            -> backups/kartonkerels-YYYYMMDD-HHMMSS.db (keeps the newest 30)
"""
import datetime
import os
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parent
data_dir = Path(os.getenv("DATA_DIR", str(root)))
source = Path(os.getenv("DATABASE_PATH", str(data_dir / "kartonkerels.db")))
target_dir = data_dir / "backups"
target_dir.mkdir(exist_ok=True)
target = target_dir / f"kartonkerels-{datetime.datetime.now():%Y%m%d-%H%M%S}.db"

with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
for old in sorted(target_dir.glob("kartonkerels-*.db"))[:-30]:
    old.unlink()
print(f"Backup written to {target}")
