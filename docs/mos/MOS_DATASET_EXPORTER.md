# MOS Dataset Exporter

`MOS Dataset Exporter` creates a consistent archive of the full MOS dataset
without stopping the collector and without modifying the live databases.

## Included databases

```text
mos_research.db
mos_manual.db
history.db
manifest.json
```

The SQLite online backup API copies committed records from the database and its
WAL into one standalone `.db` file. The resulting ZIP does not require separate
`-wal` or `-shm` files.

## Run on the collector computer

From the project root:

```powershell
python backend/scripts/mos_dataset_exporter.py
```

Or double-click:

```text
export_mos_dataset.bat
```

Default destination:

```text
backend/exports/mos_baseline_YYYY-MM-DDTHHMMSSZ.zip
```

Custom destination and label:

```powershell
python backend/scripts/mos_dataset_exporter.py `
  --output-dir D:\MOS_Exports `
  --label collector_01
```

## Safety guarantees

- all source connections are opened read-only;
- the collector may continue writing during export;
- every copied database passes `PRAGMA integrity_check` and `quick_check`;
- the archive is published atomically only after all three backups succeed;
- missing databases produce an error and no incomplete ZIP;
- `.env`, API keys, tokens, logs, `-wal`, and `-shm` are never included.

## Manifest

`manifest.json` records:

- UTC export start and completion time;
- collector hostname, platform, timezone, and Python version;
- Git commit and branch when available;
- MOS runtime versions;
- source and backup sizes;
- SHA-256 for every database backup;
- SQLite integrity results;
- row counts for every table;
- time ranges for key tables;
- runtime version groups already stored in the databases.

Exporter v1.1 also records the row count and time range of
`history.db.option_contract_snapshots` when Particle Logic v2 collection is
active. The contract table is included automatically as part of `history.db`.

On Particle Logic branches the manifest also records
`particle_logic_version`; the shadow replay itself remains outside the live
database archive.

Keep the collector on one MOS version during a baseline period. Start a new
dataset label after any change to MOS runtime logic or schema.
