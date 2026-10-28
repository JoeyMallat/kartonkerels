"""Export the local database as one self-contained file to upload to Cloudflare R2.

Usage: python export_for_cloud.py   -> kartonkerels-cloud.db
Upload that file to your R2 bucket as  seed/kartonkerels.db  before the first cloud start.
"""
import os
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parent
target = root / "kartonkerels-cloud.db"

# Importing the app runs migrations, including copying member photos from uploads/ into the database.
import app  # noqa: E402,F401
from backend.core import DB  # noqa: E402

if target.exists():
    target.unlink()
with sqlite3.connect(DB) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
with sqlite3.connect(target) as c:
    c.execute("PRAGMA journal_mode=DELETE")
    cards = c.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    members = c.execute("SELECT COUNT(*) FROM members").fetchone()[0]
print(f"Wrote {target} ({os.path.getsize(target) // 1024} KB, {cards} cards, {members} members).")
print("Upload it to your R2 bucket as: seed/kartonkerels.db")
