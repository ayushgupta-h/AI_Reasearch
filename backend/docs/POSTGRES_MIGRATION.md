# PostgreSQL Migration — TASK 2.1

This document describes the PostgreSQL integration for the Research AI Quest
backend: the models, connection management, migrations, the migration utility,
and how to switch the app over safely. It is the code companion to the
read-only infrastructure inspection.

## Current state (read-only inspection)

| Item | Finding |
|---|---|
| Source DB | `backend/sessions.db` — single table `research_sessions` (13 rows; 8 owned, 5 unowned) |
| ORM | none — legacy uses raw `sqlite3` via `session_manager.SessionManager` |
| New `Database` port | `app/infrastructure/sqlite_database.py` (`SQLiteDatabase`) |
| Composition root | `app/services/container.py` wires `SessionManager` + `SQLiteDatabase` |
| Destination connectivity | **Not verifiable from this sandbox** (DNS/egress blocked); see below |
| `DATABASE_URL` | now read by `Settings.from_env()` (was commented out) |

## What was added

```
backend/app/database/
    __init__.py            # persistence boundary (placeholder → now real)
    models/
        base.py            # Base, UUID PK/FK helpers, timestamp & soft-delete mixins
        domain.py          # User, Project, ResearchSession, ResearchPlan, ResearchTask, AgentExecution
        research.py        # ResearchSource, ResearchClaim, ResearchEvidence, ClaimEvidence, ResearchContradiction
        report.py          # ResearchReport, ReportVersion, ReviewRequest, ReviewComment
        asset.py           # UploadedDocument, AuditLog, ModelUsage
        __init__.py        # registers every table on Base.metadata
    session.py             # engine + pooled sessions, get_db() dependency, check_connection()
backend/app/repositories/postgres/
    base.py                # PostgresRepository (ownership scoping + pagination)
    users.py projects.py   # concrete repositories
backend/alembic.ini
backend/alembic/env.py
backend/alembic/script.py.mako
backend/alembic/versions/0001_initial.py
backend/tools/migrate_sessions_to_postgres.py
backend/docs/POSTGRES_MIGRATION.md   # this file
```

## Models

18 tables, all with UUID primary keys, foreign-key constraints, indexes,
created/updated timestamps, and ownership columns where applicable:

1. `users` — Supabase Auth identity; no password/credential storage.
2. `projects` — owned by a user (soft-delete).
3. `research_sessions` — owned, belongs to a project (soft-delete).
4. `research_plans` — belongs to a session.
5. `research_tasks` — belongs to a plan.
6. `agent_executions` — belongs to a session.
7. `research_sources` — belongs to a session.
8. `research_claims` — belongs to a session.
9. `research_evidence` — belongs to a session, optional source reference.
10. `claim_evidence` — association object (claim ↔ evidence) with stance/weight.
11. `research_contradictions` — two-claim contradiction records.
12. `research_reports` — belongs to a session (soft-delete).
13. `report_versions` — versioned report iterations.
14. `review_requests` — reviewer decision on a report.
15. `review_comments` — comments on a review request.
16. `uploaded_documents` — owned, project-scoped.
17. `audit_logs` — append-only security log.
18. `model_usage` — LLM usage/cost records.

### Relationships (highlights)

- `User 1—N Project`; `Project 1—N ResearchSession`
- `ResearchSession 1—N {AgentExecution, ResearchSource, ResearchClaim, ResearchEvidence, ResearchReport}` and `1—N ResearchPlan`/`ResearchContradiction`
- `ResearchReport 1—N ReportVersion` and `1—N ReviewRequest`
- `ReviewRequest 1—N ReviewComment`
- `ResearchClaim N—M ResearchEvidence` via `ClaimEvidence` (association object)
- Reviewer flow: `ReviewRequest.reviewer_id → users.id`; decision values from `ApprovalDecision`

### Ownership (security)

Every user-owned query must scope by `owner_id` taken **only** from the
validated JWT (`sub`). The repository base enforces this: `owner_id=None` for
an owned model raises instead of returning cross-user rows. No `user_id`
supplied by the frontend is ever trusted.

## Connection & sessions

`app/database/session.py`:

- `build_engine(url)` — `create_engine(..., pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=1800)`.
- `get_db()` — FastAPI dependency that yields a `Session`, commits on success, rolls back on error.
- `check_connection()` — `SELECT 1` probe; never raises, never prints.
- The URL comes from `settings.database_url` (i.e. the `DATABASE_URL` env var). It is `repr=False` on `Settings`, so it never appears in logs.

## Migrations (Alembic)

Run from `backend/`:

```
alembic upgrade head
```

- `alembic/env.py` injects the URL from the environment (never stored in the `.ini`).
- `versions/0001_initial.py` creates the full schema from `Base.metadata` (guaranteed to match the models).
- For future changes, use `alembic revision --autogenerate -m "..."` and inspect the diff.

## Data migration utility

`python -m tools.migrate_sessions_to_postgres`:

- `--dry-run` (default) — reads `sessions.db` **read-only** and prints a plan.
- `--default-owner <sub>` — adopt unowned records under a Supabase Auth `sub`.
- Live mode connects via `DATABASE_URL`, upserts with preserved IDs, and verifies destination counts.

Guarantees:

- The SQLite file is opened `?mode=ro` — never modified or deleted.
- Session IDs are preserved when they parse as UUIDs; invalid ones get a fresh UUID (reported).
- Unowned rows are skipped unless `--default-owner` is given.
- Credentials are never printed.

## Cutover plan (not yet performed)

1. **Install deps**: `pip install "psycopg[binary]" alembic python-multipart`.
2. **Set `DATABASE_URL`** in `backend/.env` or the environment.
3. **Verify connectivity** from a machine that can reach Supabase (`python -c "from app.database.session import check_connection; print(check_connection())"`).
4. **Apply schema**: `alembic upgrade head`.
5. **Dry-run the migration**, review the report (counts + unowned list).
6. **Run the migration** with `--default-owner` as appropriate.
7. **Verify** record counts and ownership before/after.
8. **Switch the composition root** (`app/services/container.py`) to the PostgreSQL backend only after the app is verified in a staging environment. Until then, SQLite remains the active store so existing functionality is preserved.

## Known constraints

- Live destination connectivity could not be verified from this sandbox (DNS/egress blocked). **The data migration must therefore not be executed until step 3 passes.**
- The safety test suite currently fails to collect 8 modules because `python-multipart` is not installed — install it (step 1) before running `pytest`.
