# Unified Specification & "抓大放小" Method

## 1. One-Pass Change-Surface Analysis

Before drafting, inspect the repository to classify the change surface:

1. **`has_ui: true`** when any frontend component, page, template, stylesheet, or user-visible copy changes.
   - Include:
     - **Screen States:** `normal`, `loading`, `empty`, `invalid` (validation error), `403/404` (permission/not-found), and `offline/error` where relevant.
     - **Layout / Wireframe:** Concise ASCII layout or canonical Figma frame URL.
     - **Key Copy:** Button labels, empty-state guidance, and error messages (preserving untranslated product terms from `docs/shared/glossary.md`).
   - When `has_ui: false`, omit all UI sections completely.

2. **`has_api: true`** when any HTTP/gRPC/GraphQL route, webhook, message-queue payload, or cross-module contract changes.
   - Include:
     - **Endpoint / Event Signature:** Method, path/topic, auth requirement.
     - **Request & Response Contract:** Fields, types, validation limits, idempotency.
     - **Error Cases:** Status codes (`400`, `401`, `403`, `404`, `409`, `422`, `500`) and error payload structure.
   - When `has_api: false`, omit all API contract sections completely.

3. **`has_data: true`** when database tables, indexes, migrations, cache keys, or file-storage formats change.
   - Include schema changes, backward compatibility, and rollback/forward-fix strategy inside `--design-summary`.

---

## 2. Writing Testable Acceptance Criteria (`AC-001..N`)

Every `AC-*` must satisfy built-in QA testability before being presented:
- **Observable & Deterministic:** State the trigger/input and the exact observable result (UI state change, API response status/body, persisted record, or emitted event).
- **Boundary & Negative Coverage:** Include at least one boundary/validation or unauthorized/error criterion alongside happy-path criteria.
- **No Vague Prose:** Replace "fast response" with an explicit threshold or existing standard reference; replace "handle errors gracefully" with the specific error code/message/state.

---

## 3. Inline "抓大放小" Review Filter

To eliminate review fatigue, classify every potential question through this filter before talking to the user:

### 3.1 Must Be Auto-Decided by AI ("放小" — Put in `auto_decisions`)
- Standard HTTP status codes and error envelope format matching existing controllers.
- Default pagination limits, sorting orders, or debounce timings matching existing pages.
- Standard loading skeletons, toast notifications, and form validation behavior.
- Naming of internal functions, classes, helper modules, or database columns that follow repo conventions.
- Standard unit/integration test granularity required by `docs/shared/engineering-standards.md`.

### 3.2 Eligible as Material Questions ("抓大" — Max 3 Questions)
Ask the human **only** if the codebase and prompt leave a genuine ambiguity in:
1. **Irreversible Architecture / Data Risk:** Destructive schema migration, external third-party billing/service lock-in, or breaking public API change.
2. **Requirement Contradiction:** Two stated goals directly conflict (e.g., "allow anonymous access" vs. "audit every action by tenant user ID").
3. **Major Scope / Cost / UX Trade-off:** A choice that multiplies implementation effort or alters core user workflow (e.g., synchronous export vs. background job with email delivery).

**Question Format (when 1–3 questions exist):**
- **問題 `Q1`：** Clear 1-sentence question naming the exact behavior or boundary.
- **為什麼重要：** 1-sentence impact on user experience, security, or cost.
- **建議方案 (Recommended)：** Concrete recommendation with rationale, plus 1 alternative.
