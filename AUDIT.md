# Quest — UI Audit Report (Oct 17, 2026)

Scope: `frontend/` (index, new, report, history, dashboard, login, desk) + `backend/` (FastAPI :8000). Source code treated as truth; every control traced from click → handler → API → UI state.

Architecture summary:
- 6 single-file dark-luxury pages, one IIFE each, Google Fonts only, toast-only feedback, `__nav` veil + lens-scroll shared injections.
- Backend FastAPI with SSE research pipeline (8 agents), `/chat` with provider fallback chain (gemini → groq → mistral → huggingface), `/sessions` + SQLite persistence, `/reports` static mount, `/providers`, `/health`.
- Auth: intentionally local demo (no user accounts in backend) — flows validate locally, then open the desk.

## Findings

| # | Page | Element | Class | Status |
|---|------|---------|-------|--------|
| A1 | report | Share btn / Copy link ("copied" toast, **clipboard never touched**) | **E — misleading** | ✅ fixed: real clipboard copy (API + execCommand fallback), correct URL incl. `?run=` |
| A2 | report | Add Source ("upload stubbed" toast) | **D** | ✅ fixed: inline URL-add form, validation, adds real source row (local, honestly labeled) |
| A3 | report | Studio gen tools: slides/flash/quiz/table/timeline/mindmap/data all demo-only | **D** | ✅ fixed: all generate real content from the loaded report body; downloadable `oc-dl` works; honest demo card + honest empty viewer when no run loaded |
| A4 | report | Audio Brief (demo) | **D** | ✅ fixed: real Web Speech synthesis of exec summary / intro + downloadable transcript |
| A5 | report | `tryIt` (Critic inline revisions) stub toast | **E** | ✅ fixed: actionable — switches to reviewer mode & highlights first unresolved Critic note |
| A6 | report | Approve / Request changes: fake "v3 is now shipping" claims | **E** | ✅ fixed: real local state (draft tag, article state) + honest copy (no backend review state — stated) |
| A7 | report | Studio demo outputs: "generated just now" label on nothing, dead download btn | **E** | ✅ fixed: honest per-card labels + reworded download message |
| A8 | report | Version switch v1/v2: "No snapshot stored" | C (honest) | keep — truthful |
| A9 | new | Sidebar pinned/projects rows toast "Opening…" and open nothing | **E** | ✅ fixed: row click prefills the composer (real local action), not "opening" |
| A10 | new | Schedule popover: "Scheduled — see you tomorrow" schedules nothing | **E** | ✅ fixed: honest message (no backend scheduler exists) |
| A11 | new | Mic = stub toast | **D** | ✅ already fixed this session (Web Speech API, graceful fallback) — verified |
| A12 | new | histBtn, depth, srcChips, mode toggle, queue, abort | A | verified live |
| A13 | login | Forgot password stub toast | D | ✅ reworded honestly (no backend auth exists) |
| A14 | login | Google sign-in demo (1.2 s then navigates) | C | kept, copy honest — no OAuth backend exists |
| A15 | login | Email/password form: validation, shake, flip | A/B | verified: real local validation; navigates on success |
| A16 | history | Search / status+depth chips / sort / clear / pagination / rows→report | A | verified live: real session data, correct `?run=` links |
| A17 | dashboard | Tiles/feed/weeks all computed from `/sessions`; rows link real reports | A | verified live |
| A18 | index | nav/burger/overlays/demo login | B | verified: real navigation; login modal demo by design |
| A19 | desk | redirect to new.html (carries query/hash) | C | correct behavior — kept |
| A20 | new | Hardcoded SESSIONS/SUGG demo data (user-added) | C | kept as suggestion/fallback data; sidebar recents replaced by live `/sessions` fetch when backend on |
| A21 | new | "Scheduled" sidebar row: popover opens **and** page navigates to dashboard (two handlers fire) | **E** | ✅ fixed: popover wins the click (stopImmediatePropagation); dashboard reachable via a link inside the popover |
| A22 | report | Studio generators returned demo cards on real reports — heading regex failed on CRLF markdown (`(.+)$` can't match a line ending in `\r`) | **E** | ✅ fixed: CR normalized before section extraction; all 8 generators verified against the real Groq report |
| A23 | dashboard | Date-range select (7/30/90 d) | A | verified live: tiles/chart/feed recompute from real sessions on change |

Backend checks this pass: untouched except previously applied fixes; `/health`, `/sessions`, `/reports`, `/providers` re-probed live. pytest 58/58 unaffected by frontend-only edits.

Implementation boundary decisions:
- No fake backend data was invented; offline paths keep honest demo/empty states.
- Features needing a nonexistent backend (scheduling, password reset, OAuth, source crawling) are either disabled with clear copy or implemented client-side where legitimate (clipboard, source list row, composer prefill, content generation from the already-loaded report).
