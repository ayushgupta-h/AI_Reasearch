# Known Issues

**Project:** Research AI Quest  
**Layer:** Backend API (FastAPI)  
**Date:** 2026-11-12  
**Status:** Open (unless marked Fixed)

---

## 1. Untyped Response Schemas (19 Endpoints)

### Issue
The following endpoints use `response_model=dict` instead of typed Pydantic response schemas, reducing OpenAPI documentation quality and client-side type safety:

| Endpoint | File | Line |
|----------|------|------|
| GET /notifications | `app/api/notifications.py` | 56 |
| GET /notifications/unread | `app/api/notifications.py` | 98 |
| PATCH /notifications/{id}/read | `app/api/notifications.py` | 114 |
| POST /notifications/read-all | `app/api/notifications.py` | 133 |
| DELETE /notifications/{notification_id} | `app/api/notifications.py` | 149 |
| GET /research/{id}/claims | `app/api/claims.py` | 45 |
| GET /research/{id}/claims/{id} | `app/api/claims.py` | 82 |
| GET /research/{id}/sources | `app/api/sources.py` | 74 |
| GET /sources/{source_id} | `app/api/sources.py` | 131 |
| GET /research/{id}/sources/statistics | `app/api/sources.py` | 147 |
| PATCH /sources/{source_id}/status | `app/api/sources.py` | 164 |
| DELETE /sources/{source_id} | `app/api/sources.py` | 190 |
| POST /research/{id}/completeness/evaluate | `app/api/completeness.py` | 65 |
| GET /research/{id}/completeness/objectives | `app/api/completeness.py` | 129 |
| GET /research/{id}/completeness/evaluations | `app/api/completeness.py` | 158 |
| GET /research/{id}/completeness/evaluations/latest | `app/api/completeness.py` | 187 |
| GET /memory/search | `app/api/memory.py` | 40 |
| GET /memory | `app/api/memory.py` | 80 |
| POST /memory | `app/api/memory.py` | 121 |
| GET /memory/{memory_id} | `app/api/memory.py` | 168 |
| PUT /memory/{memory_id} | `app/api/memory.py` | 193 |
| DELETE /memory/{memory_id} | `app/api/memory.py` | 227 |
| POST /research/{id}/report/generate | `app/api/report_generation.py` | 34 |

### Risk
Medium — Clients cannot validate response shapes from OpenAPI; schema drift may go undetected.

### Recommendation
Create Pydantic response schemas for each endpoint and replace `response_model=dict`.

---

## 2. Encapsulation Violation in Completeness API

### Issue
`app/api/completeness.py` accesses `service._repo` directly (lines 146, 173, 200) instead of using the service's public methods or a separate repository dependency.

### Risk
Medium — Breaks encapsulation; makes the API tightly coupled to the service's internal repository implementation. Harder to test and refactor.

### Recommendation
Either:
- Add repository methods to the `ResearchCompletenessEvaluator` service class and call them through the service, or
- Inject the repository directly as a separate FastAPI dependency alongside the service.

---

## 3. Dependency Injection Bypass in Memory API

### Issue
`app/api/memory.py` instantiates `ResearchMemoryService(settings=get_settings())` directly inside each route handler (lines 61, 101, 144, 186, 214, 245) instead of using the container's `get_memory_service` dependency.

### Risk
Medium — Bypasses the container's wiring, making the endpoint harder to test with mocks and inconsistent with the rest of the API layer which uses dependency injection.

### Recommendation
Wire `ResearchMemoryService` into `app/services/container.py` and use `Depends(get_memory_service)` in the route handlers.

---

## 4. Dead Code: Empty ErrorCode Class

### Issue
`app/schemas/enums.py` line 314 defines an empty `ErrorCode` class that is immediately redefined at line 340 with actual values. The first definition is dead code.

### Risk
Low — Cosmetic only, but confusing for readers.

### Recommendation
Remove the empty class definition at line 314.

---

## 5. Inconsistent ID Pattern Types

### Issue
Different endpoints use different patterns for ID parameters:
- `projects.py`: Uses `UUID` type (FastAPI built-in)
- `documents.py`, `sources.py`, `notifications.py`, `research_sessions.py`: Uses `str` with regex pattern
- `report_management.py`: Uses custom `hex32` URL convertor
- `reviews.py`: Uses `^[a-f0-9-]{32,36}$` pattern (accepts both hex32 and UUID-with-dashes)

### Risk
Low — Inconsistency may cause client-side confusion and OpenAPI schema variation.

### Recommendation
Standardize on one pattern (preferably `str` with `^[a-f0-9]{32}$` to match the pipeline's uuid4().hex format).

---

## 6. Custom URL Convertor in Report Management

### Issue
`app/api/report_management.py` registers a custom `hex32` URL convertor (lines 33-46) and uses it in route paths (`/reports/{report_id:hex32}`). This is non-standard FastAPI usage.

### Risk
Low — May cause issues with some OpenAPI generators or reverse proxy routing.

### Recommendation
Use standard path parameters with regex patterns instead of custom URL convertors.

---

## 7. Missing Database Failure Tests

### Issue
No tests inject database failures (e.g., repository commit raising an exception) to verify that endpoints return proper 503 responses.

### Risk
Medium — Error handling for database failures is not verified at the API level.

### Recommendation
Add tests with mock repositories that raise exceptions on commit, verifying 503 responses.

---

## 8. Missing Storage Failure Tests

### Issue
No tests inject storage failures (e.g., document storage raising an exception) to verify proper error responses.

### Risk
Medium — Document upload error handling is not verified for storage failures.

### Recommendation
Add tests with mock storage services that raise exceptions, verifying proper error responses.

---

## 9. Missing Queue Failure Tests

### Issue
No tests inject event bus/queue failures to verify SSE endpoint behavior when the event transport is unavailable.

### Risk
Low — SSE error handling for queue failures is not verified.

### Recommendation
Add tests with mock event buses that raise exceptions on publish.

---

## 10. No Idempotency Key Support

### Issue
POST create endpoints (`/projects`, `/documents/upload`, `/research`, etc.) do not support idempotency keys (e.g., `Idempotency-Key` header). Duplicate submissions are only prevented for documents via SHA-256 hash.

### Risk
Low — Clients cannot safely retry failed POST requests without risking duplicate resources.

### Recommendation
Add optional `Idempotency-Key` header support for create endpoints.

---

## 11. Expired Authentication Not Explicitly Tested

### Issue
Authentication tests verify missing tokens return 401, but no test explicitly uses an expired JWT token.

### Risk
Low — Expired token handling is covered by the JWT validator but not tested at the API level.

### Recommendation
Add a test with an expired JWT to verify 401 response.

---

## 12. Sessions Endpoint Uses Optional Auth

### Issue
`GET /sessions` uses `get_optional_user` instead of `get_current_user`, allowing anonymous access. This is documented as backward-compatible legacy behavior.

### Risk
Low — Intentional for backward compatibility, but should be documented.

### Recommendation
Document the intentional anonymous access in the endpoint docstring.

---

## 13. No Rate Limiting

### Issue
No rate limiting is implemented on any API endpoint.

### Risk
Low — Not a current requirement, but should be considered for production deployment.

### Recommendation
Document as out of scope for current release; consider adding for production.

---

## Fix Status

| Issue | Status | Fix |
|-------|--------|-----|
| Untyped response schemas | Open | Requires schema creation |
| Encapsulation violation (completeness) | Open | Requires refactoring |
| DI bypass (memory) | Open | Requires container wiring |
| Dead ErrorCode class | Fixed | Remove empty class definition |
| Inconsistent ID patterns | Open | Requires standardization |
| Custom URL convertor | Open | Requires refactoring |
| Missing DB failure tests | Open | Requires new tests |
| Missing storage failure tests | Open | Requires new tests |
| Missing queue failure tests | Open | Requires new tests |
| No idempotency keys | Open | Requires new feature |
| Expired auth not tested | Open | Requires new test |
| Sessions optional auth | Documented | No action needed |
| No rate limiting | Documented | Out of scope |

---
