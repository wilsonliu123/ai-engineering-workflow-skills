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

3. **`has_data: true`** **ONLY** when database schemas/tables, indexes, migrations, storage formats, or persistent data models are added or modified.
   - Merely calling an existing persistence or repository API does **NOT** trigger `has_data: true` (`has_data: false`).
   - When `has_data: true`, include schema changes, backward compatibility, and rollback/forward-fix strategy inside `--design-summary`.

---

## 2. Writing Testable Acceptance Criteria (`AC-001..N`) & The 6-Dimension Checklist

Every `AC-*` must satisfy built-in QA testability before being presented:
- **Observable & Deterministic:** State the trigger/input and the exact observable result (UI state change, API response status/body, persisted record, or emitted event).
- **No Vague Prose:** Replace "fast response" with an explicit threshold or existing standard reference; replace "handle errors gracefully" with the specific error code/message/state.

### Mandatory 6-Dimension AC Completeness Checklist
Before marking any Story `--status approved`, verify that the Story's `AC-001..N` (or explicit N/A notes in `design_summary`) cover all applicable dimensions:
1. **Happy Path (核心主流程):** End-to-end primary business outcome and expected outputs.
2. **Role / AuthZ / Tenant Isolation (角色權限與租戶隔離):** Who is allowed vs. forbidden (`401`/`403`), cross-tenant data isolation, and audit logging.
3. **Input Boundary & Idempotency (輸入邊界、格式與冪等性):** Empty/null inputs, size/rate limits, duplicate submission / retry idempotency (`400`/`409`/`422`).
4. **State Transitions & Concurrency (狀態流轉與併發衝突):** Valid/invalid state transitions, optimistic/pessimistic locking, or concurrent edit handling.
5. **External Dependency Failure & Compensation (外部依賴失敗与補償回滾):** Behavior when third-party APIs, queues, storage, or databases time out or fail (retry, fallback, transaction rollback).
6. **Legacy Data Compatibility & Migration (舊資料相容性與遷移影響):** Handling of existing historical records, null columns, or backward-compatible API fields.

---

## 3. Scale-Aware "抓大放小" Review Filter (`One-Pass` vs. `Iterative Draft`)

To balance review efficiency on small features with completeness on complex features, classify both the **questions** and the **feature scale**:

### 3.1 Must Be Auto-Decided by AI ("放小" — Put in `auto_decisions`)
- Standard HTTP status codes and error envelope format matching existing controllers.
- Default pagination limits, sorting orders, or debounce timings matching existing pages.
- Standard loading skeletons, toast notifications, and form validation behavior.
- Naming of internal functions, classes, helper modules, or database columns that follow repo conventions.
- Standard unit/integration test granularity required by `docs/shared/engineering-standards.md`.

### 3.2 Eligible as Material Questions ("抓大")
Ask the human **only** if the codebase and prompt leave a genuine ambiguity in:
1. **Irreversible Architecture / Data Risk:** Destructive schema migration, external third-party billing/service lock-in, or breaking public API change.
2. **Requirement Contradiction:** Two stated goals directly conflict (e.g., "allow anonymous access" vs. "audit every action by tenant user ID").
3. **Major Scope / Cost / UX Trade-off:** A choice that multiplies implementation effort or alters core user workflow (e.g., synchronous export vs. background job with email delivery).

### 3.3 Choosing Track 1 (One-Pass) vs. Track 2 (Iterative `draft` Deep-Dive)
- **Track 1 — Small / Clear Feature (`One-Pass`):**
  - When the feature is self-contained (`<= 3` ACs, `low`/`medium` risk) and has at most 3 material questions, ask them in a single round and then run `sync-story --status approved`.
- **Track 2 — Medium / Large or Ambiguous Feature (`Iterative Draft`):**
  - When a feature spans multiple roles/modules, involves data migrations or external integrations, or has `> 3` material ambiguities across the 6 AC dimensions:
    1. Run `jira_sync.py sync-story --key <KEY> ... --status draft` after each round to checkpoint progress and accumulate `material_decisions` and `auto_decisions`.
    2. Ask **3–5 focused material questions per round** (never dump 15 questions at once, and never silently guess major business rules just to fit a 1-pass limit).
    3. Once all rounds complete and all 6 AC dimensions are verified, run `sync-story --key <KEY> ... --status approved`.

**Question Format:**
- **問題 `Q1`：** Clear 1-sentence question naming the exact behavior or boundary.
- **為什麼重要：** 1-sentence impact on user experience, security, or cost.
- **建議方案 (Recommended)：** Concrete recommendation with rationale, plus 1 alternative.

---

## 4. Existing-Story Backfill Mode, Optional Risks/`Q-###`, & Evidence Discipline

When using `sync-story --key <EXISTING-KEY> --backfill` (or `--preserve-owner`) to reconstruct or complete specs for an existing Story:

1. **Preserve Live State:** `--backfill` implies `--preserve-owner`, refuses `--status approved`, preserves `current_owner_role`, Jira Assignee, Jira status, and existing `tasks`/`review`/`qa`/`bugs`, respects the Description Drift Guard, and verifies `last_synced_description_hash` after syncing.
2. **Evidence Separation (5 Distinct Categories):** Never merge these together:
   - **Original Requirement / Approved Decision:** Quoted Jira requirement or human-confirmed decision.
   - **Observed Code Reality:** Facts read directly from source files/APIs (never promote existing code constants/behaviors into "approved requirements" without confirmation).
   - **AI Suggestions:** Recommended safeguards or implied requirements surfaced in `auto_decisions` or `--risks`.
   - **Open Questions (`Q-###`):** Unresolved decisions passed via `--open-questions-json` (`id`, `question`, `owner`, `proposed_default`, `impacted_acs`, `blocking`; plain strings auto-number `Q-001`, `Q-002`, ...).
   - **Verified Evidence:** Only real executed test output; never report test code existence or Jira status as executed verification.
3. **Scoped Story Description & Artifact Boundaries:**
   - Render `### 風險與待確認問題 (Risks & Open Questions)` (English: `### Risks & Open Questions`) **only** when `--risks` or `--open-questions-json` is non-empty; simple stories stay concise without extra sections.
   - Do **not** add a Verification Scenarios table to the Story Description (test scenarios/cases `TC-001..N` belong in Station 3b `team-qa`) and do **not** create `analysis.md` or per-feature Git Markdown files.
4. **Report Completion State Precisely:** Distinguish **Material Backfilled** (`backfill_stage: "material_backfilled"`), **Spec Pending Confirmation** (`backfill_stage: "spec_pending_confirmation"` when blocking `Q-###` exist), and **Spec Approved** (`spec_status: "approved"` after explicit user confirmation).
