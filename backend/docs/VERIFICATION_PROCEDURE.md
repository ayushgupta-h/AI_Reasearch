# Local Environment Verification Procedure — Task 2.1 (PostgreSQL Migration)

> **Status: Task 2.1 is marked PENDING LIVE DATABASE VERIFICATION.**
> The code (models, engine, Alembic, repository, migration utility, tests) is
> complete and 30/30 unit tests pass. The **live data migration has NOT run**
> because the sandbox cannot reach Supabase (DNS/egress blocked). Run the
> procedure below from a machine that can reach `db.vxqalflbzpjkvmtbbqrv.supabase.co`
> and only then perform the data migration.

## Guardrails (the 12 requirements)

| # | Requirement | Where it is satisfied |
|---|-------------|----------------------|
| 1 | Do not modify/delete SQLite `sessions.db` | Migration utility opens it **read-only** (`mode=ro`); verify script is read-only |
| 2 | Do not change models/migrations unnecessarily | No schema changes in this procedure; migration is idempotent |
| 3 | Connectivity command | `python -m tools.verify_postgres` (below) |
| 4 | Verify SSL | Same harness, check #2 (`pg_stat_ssl`) |
| 5 | Alembic can connect | `alembic current` (below) |
| 6 | Inspect schema | `python -m tools.verify_postgres --schema` |
| 7 | Safe Alembic migration | `alembic upgrade head` (reversible via `downgrade`) |
| 8 | Separate SQLite→PG migration | `python -m tools.migrate_sessions_to_postgres` |
| 9 | Verify source/destination counts | `--verify-migration` flag |
| 10 | Verify ownership/relationships | ownership distribution printed by `--verify-migration` |
| 11 | Rollback/recovery | See "Rollback & recovery" below |
| 12 | Never print credentials | URL is read from `DATABASE_URL`, never logged |

## 0. Preparation

```bash
cd backend
# Ensure deps are installed (no credentials involved)
pip install "psycopg[binary]" alembic python-multipart
```

Make sure `DATABASE_URL` is set for the shell or in `backend/.env`
(the app loads it with `python-dotenv`; the value is never printed).

## 1. Connectivity + SSL (Gate check)

```bash
python -m tools.verify_postgres
```

Expected output:

```
=== 1. PostgreSQL connectivity ===
PASS: engine connected and SELECT 1 succeeded.
=== 2. SSL on the backend connection ===
PASS: the backend connection is SSL-encrypted.
--- SUMMARY ---
ALL CHECKS PASSED
```

**Do not continue past this gate if either check prints FAIL.**

## 2. Alembic can connect (no writes)

```bash
alembic current
```

- `alembic current` only reads the migration history. A successful connection
  prints the current head revision (initially `(head)` or the revision hash).
- An error means environment variables/URL are wrong for Alembic.

## 3. Inspect the current PostgreSQL schema (read-only)

```bash
python -m tools.verify_postgres --schema
```

This lists the `public` schema tables and their row counts.

## 4. Apply Alembic migrations (safe, reversible)

```bash
alembic upgrade head
```

Then confirm:

```bash
alembic current        # should now show the head revision
python -m tools.verify_postgres --schema   # tables now exist
```

## 5. Inspect source (SQLite) before migrating

The migration tool and verify harness both open SQLite **read-only**.

```bash
python -m tools.verify_postgres --schema
python -m tools.migrate_sessions_to_postgres --dry-run
```

`--dry-run` prints the plan without writing anything to PostgreSQL:
source total, planned migrations, and how many unowned rows are skipped.

## 6. Run the data migration (separate, explicit step)

```bash
# First a dry run to confirm the plan.
python -m tools.migrate_sessions_to_postgres --dry-run

# Then the actual migration. Provide --default-owner (a Supabase Auth sub)
# only if you want unowned sessions to be claimed; otherwise they are skipped.
python -m tools.migrate_sessions_to_postgres --default-owner <your-supabase-sub>
```

The migration:
- preserves each session UUID where the legacy `session_id` is a valid UUID;
- maps `user_id` → `owner_id`;
- is **idempotent** (existing destination rows are never overwritten/duplicated);
- never touches `sessions.db`.

## 7. Verify record counts and ownership (source vs destination)

```bash
python -m tools.verify_postgres --verify-migration
```

Expected:

```
=== 4. Source vs destination record counts / ownership ===
Source (SQLite, read-only): total=13, owned=8, unowned=5
Destination (PostgreSQL): total=8, owned=8, unowned=0
PASS: destination count covers all source records.
Ownership distribution (PostgreSQL):
  owner=... -> 8
```

- **Destination total should equal source `owned` count** (unowned rows are
  skipped unless `--default-owner` was given).
- If destination is smaller than source owned, the migration did **not** copy
  everything — do not delete source.

## 8. Relationship / integrity spot-check

Only if you created related data (projects/claims/etc.) — verify a few FKs:

```bash
python -c "
from app.database.session import get_engine
from sqlalchemy import text
e = get_engine()
with e.connect() as c:
    for name in ['research_sessions','research_sources','research_claims','research_reports','report_versions','review_requests','review_comments','audit_logs','model_usage']:
        n = c.execute(text(f'SELECT count(*) FROM \"public\".\"{name}\"')).scalar()
        print(f'{name}: {n}')
"
```

(Open only when the migration has run and you want to confirm related tables.)

## 9. Rollback & recovery

**Schema rollback (Alembic):**

```bash
cd backend
alembic downgrade base      # drops all tables created by the migration
```

To undo a partially-applied migration, downgrade one step:

```bash
alembic downgrade -1
```

**Data rollback (migrated sessions):**

The migration is idempotent and additive. To remove migrated records (so you
can re-run cleanly), delete only from PostgreSQL, never from SQLite:

```bash
python -c "
from app.database.session import get_engine
from sqlalchemy import text
e = get_engine()
with e.begin() as c:
    c.execute(text('DELETE FROM \"public\".\"research_sessions\"'))
"
```

> ⚠️ This deletes PostgreSQL records only; `sessions.db` remains the untouched
> source of truth until you are fully satisfied.

**Recovery note:** Because `sessions.db` is never modified, you can re-run the
migration at any time — it will re-insert any posts rows.

## 10. Marking the task done

Only after **steps 1, 2, 4, 6, and 7 all pass** on a machine that can reach
Supabase:

- Record the source and destination counts + ownership distribution.
- Update this file header status to **COMPLETE** (or “MIGRATED on <date>”).
- The old `sessions.db` can then be archived (do not delete).

Until then, **Task 2.1 remains PENDING LIVE DATABASE VERIFICATION.**
