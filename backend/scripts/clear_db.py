"""Clear mos_research.db for fresh data collection."""
import sqlite3
import os

db = os.path.join(os.path.dirname(__file__), '..', 'data', 'mos_research.db')
if not os.path.exists(db):
    print(f"DB not found: {db}")
    exit(0)

conn = sqlite3.connect(db)
c = conn.cursor()
tables = ['snapshots', 'events', 'future_labels', 'bad_snapshots', 'bookmarks', 'event_cooldowns']
total = 0
for t in tables:
    try:
        c.execute(f"SELECT COUNT(*) FROM {t}")
        count = c.fetchone()[0]
        c.execute(f"DELETE FROM {t}")
        total += count
        print(f"  {t}: {count} deleted")
    except Exception as e:
        print(f"  {t}: skip ({e})")

conn.commit()
conn.execute("VACUUM")
c.execute("PRAGMA integrity_check")
print(f"\nIntegrity: {c.fetchone()[0]}")
print(f"Total deleted: {total}")
conn.close()
