import sqlite3
import os

paths = ['data/mos_research.db', 'data/history.db']
for p in paths:
    if not os.path.exists(p):
        print(f'{p} not found.')
        continue
    try:
        conn = sqlite3.connect(p)
        c = conn.cursor()
        print(f'\n--- {p} ---')
        c.execute('SELECT name FROM sqlite_master WHERE type="table"')
        tables = [t[0] for t in c.fetchall()]
        print(f'Tables: {tables}')
        for table in tables:
            c.execute(f'SELECT COUNT(*) FROM {table}')
            count = c.fetchone()[0]
            print(f'{table}: {count} rows')
            if count > 0:
                c.execute(f'SELECT * FROM {table} ORDER BY rowid DESC LIMIT 1')
                row = c.fetchone()
                if table == 'snapshots':
                    print(f'  Latest row timestamp: {row[0]}, spot: {row[1]}')
                else:
                    print(f'  Latest row start: {str(row)[:100]}')
    except Exception as e:
        print(f'Error reading {p}: {e}')
