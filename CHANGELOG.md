# Changelog

## v1.6.0 — 2026-11-23 — CI/CD Delivery Pipeline Alignment (Phase A)

- **Optional `delivery_pipeline` Schema & Pure-Stdlib CI Auto-Detection (`team-init` / `jira_sync.py`):**
  - Added optional `delivery_pipeline` block (`integration_branch`, `production_branch`, `environments`, `jira_deployment_sync`) with safe empty defaults in `team-config-template.json` and `DEFAULT_TEAM_CONFIG`, sanitized via `_sanitize_delivery_pipeline()` (strict git ref validation, `"branch"`/`"tag"` trigger allowlist, strict boolean parsing so `"false"`/`"0"`/`"no"` never evaluate truthy, and zero literal `"None"` leaks).
  - Added pure-stdlib `_detect_ci_delivery_pipeline(repo_root)` scanning `.gitlab-ci.yml` and `.github/workflows/*.yml|*.yaml` (resilient to binary, invalid UTF-8, unreadable, or malformed files) to detect `deployment_tier` (`development`/`testing`/`staging`/`production`), branch/tag triggers (`qa-*`, `uat-*`, `release`/`develop`/`main`), `jira_deployment_sync`, deduplicated local lint/test check hints (`detected_ci_checks`), and `suggested_qa_handoff_mode` (`"gated_by_deploy"` when a dedicated QA/testing deploy trigger exists).
  - Surfaced `detected_delivery_pipeline`, `detected_ci_checks`, and `suggested_qa_handoff_mode` in `init --inspect` and `init`, and added optional additive flags on `init` (`--integration-branch`, `--production-branch`, `--qa-tag-prefix`, `--uat-tag-prefix`, `--jira-deployment-sync`).
- **Integration Branch Base-Branch Resolution (`team-dev`, `team-review`, `status`):**
  - Added `_resolve_base_branch(repo_root, config)` honoring `delivery_pipeline.integration_branch` when configured (with graceful local-ref fallback to `main`/`master`) and wired it into `_detect_git_changed_files` and `status` (`integration_branch` / `base_branch` exposed only when configured).
  - Updated `team-dev` (including CI-mirrored local lint/test checks in the 5-check exit gate) and `team-review` to branch from and diff against `integration_branch` when configured.
- **QA Deploy Ref Traceability & Natural-Language `qa_deploy_hint` (`promote-qa`, `record-qa`, `status`):**
  - Added `--deploy-ref` (alongside `--environment`) on `promote-qa` and `--deploy-ref` on `record-qa --mode verify`, persisting `deploy_ref`/`environment` on `story["qa_deploy_gate"]` and `verified_deploy_ref` on `story["qa"]` (inheriting from aligned `qa_deploy_gate.deploy_ref` when omitted, rendering valid ADF in Jira comments, preserving/updating metadata cleanly on idempotent `promote-qa` without duplicate Jira mutations, and clearing stale `deploy_ref`/`environment` on new `candidate_commit` review cycles).
  - Added `qa_deploy_hint` in `evaluate_story_next_step` when `awaiting_qa_deploy` with configured `delivery_pipeline.environments.qa` (tag or branch trigger), strictly upholding `can_auto_chain_next_station: false`, `requires_user_confirmation_for_next_station: true`, and zero AI auto-tagging/auto-merging.
- **Lightweight Non-Blocking Multi-Stage Release Reminders (`team-release`, `close-story`, `status`):**
  - Emitted strictly non-blocking `release_checklist_reminders` in `evaluate_story_next_step` (Station 4) and `close-story` whenever `delivery_pipeline` configures a `uat`/`staging` environment or a `production_branch` distinct from `integration_branch` (`[]` when unconfigured; never blocks `close-story` and introduces no UAT state machine).
- **Verification & Backward Compatibility:** 100% backward compatible for projects without `delivery_pipeline`; expanded `tools/test_workflow.py` to 37 tests across 8 byte-identical skill bundles (`./tools/check_shared.sh` 8/8 OK).

## v1.5.0 — 2026-11-23 — Existing-Story Spec Backfill & Platform-Neutral Epic Decomposition

- **Existing-Story Backfill Mode & `has_data` Clarification (PR #17, Issue #2 scoped):**
  - Added `sync-story --backfill`, `--preserve-owner`, `--risks`, and `--open-questions-json` to document or enrich existing Jira Stories without altering `current_owner_role`, Jira Assignee, Jira status, or preserved lifecycle fields (`tasks`, `review`, `qa`, `bugs`, `qa_deploy_gate`, `triage_closed`, etc.).
  - Enforced backfill safety guards: rejects `--backfill --status approved` (including nested `spec.spec_status: "approved"`), `--backfill --create`, 404 create fallback, duplicate `Q-###` IDs, and non-boolean `blocking` values; honors the Description Drift Guard on untracked existing prose and verifies `last_synced_description_hash` after sync.
  - Added optional `Risks & Open Questions` (`風險與待確認問題`) Story Description rendering in strict ADF only when `--risks` or `--open-questions-json` is non-empty (omitted on simple stories).
  - Clarified `has_data` in `team-spec/SKILL.md` and `spec-method.md` (schema/migration/storage-format/data-model changes only; calling existing persistence APIs is `has_data: false`), formalized 5-category evidence separation and 3-state completion reporting (`material_backfilled`, `spec_pending_confirmation`, `approved`), and upheld the Jira-only boundary (no Verification Scenarios table in Story Descriptions and no `analysis.md` / per-feature Git Markdown).
- **Platform-Neutral Epic Decomposition & Progress Aggregation (PR #17, Issue #5 Phase 1):**
  - Added `epic-decompose` (`--epic`, `--stories-json`, `--apply`, `--issue-type`) with mandatory `dry_run: true` preview (zero writes) before explicit confirmation, topological `execution_waves` calculation, validation for blank titles / duplicate IDs / dependency cycles / `story_points` / 404 Epic, remote snapshot cross-machine duplicate detection, and partial-failure tracking.
  - Under `--apply`, creates child Stories in topological wave order so downstream `depends_on_keys` resolve to real upstream Jira keys, links Epics via `fields.parent` with graceful fallback to configured `epic_link_field` in `team-config.json` (or unlinked warning on HTTP 400), and persists `.ai-workflow/active/<NEW-KEY>.json` (`spec_status: "draft"`).
  - Added `status --epic <EPIC-KEY>` progress aggregation with `parent` / `"Epic Link"` JQL fallback, cached remote upstream dependency lookup, secondary worktree merge, localized `status_mapping.todo` handling, `close-story` sibling `resolved_depends_on_keys` propagation, and HITL stop gates (`can_auto_chain_next_station: false`, `requires_user_confirmation: true`). Documented in `team-plan` (`decompose` mode) and `team-next-step`, keeping core `team-*` skills strictly platform-neutral without coupling `run-workflow` or Phase 2 multi-Story Sub-agent execution.
- **Verification:** Expanded `tools/test_workflow.py` to 36 end-to-end and mock Jira/Qase tests covering 16 CLI subcommands across 8 byte-identical skill bundles.

## v1.4.0 — 2026-11-23 — Commit & Sub-task Governance, Deploy-Gated QA Handoff, Bug Triage & Root-Cause Capture

- **Commit Gate, Project Commit Conventions & Rich Sub-tasks (PR #15, Issues #3, #7, #8):**
  - Added `commit_policy` in `team-config.json` (`init --commit-policy`), defaulting to `"confirm_before_commit"` (pause after local tests pass to await human confirmation before `git commit` and `complete-task`) with optional `"auto_on_test_pass"` opt-in, plus optional `commit_message_pattern` (`init --commit-message-pattern`) for non-blocking `commit_message_warnings` in `complete-task`.
  - Enforced project Commit convention precedence (`docs/shared/engineering-standards.md` §4 first, fallback `AGENTS.md`) and Dual Jira Key Traceability (`<STORY-KEY>` + `<SUBTASK-KEY>` whenever `jira_subtask_key` exists) across `team-dev`, `team-review`, and `complete-task`.
  - Upgraded `create-subtasks` (`format_subtask_description`) to render rich structured Jira Sub-task Descriptions (`description`, `implementation_steps`, target `files`, `ac_coverage`, `depends_on`, `verification`).
- **Multi-Environment QA Deploy Decoupling (PR #16, Issue #4):**
  - Added optional `qa_handoff_mode` (`"immediate"` default vs `"gated_by_deploy"`, via `init --qa-handoff-mode`) and `status_mapping.dev_done` (`init --status-dev-done`).
  - Under `"gated_by_deploy"`, `handoff --review-status approved` transitions to `dev_done`, records commit-bound `qa_deploy_gate` (`status: "awaiting_qa_deploy"`), and holds QA reassignment/`@mention`; `record-qa --mode plan` remains allowed in parallel while `record-qa --mode verify` is gated until CI/CD transitions Jira to `in_qa` (synced via `status`/`pull`) or the new `promote-qa` subcommand (`--key`, `--from-role`, `--tag`, `--environment`, `--note`, `--to-assignee`) runs explicitly.
- **Jira Bug Triage & Controlled Release Gate (PR #16, Issue #11):**
  - Added `triage-bug` subcommand (`--key`, `--bug-id`, `--resolution`, `--reason`, `--decided-by`, `--duplicate-of`, `--waive-tcs`, `--tc-disposition`) and `team-qa` Mode 4 (`triage`) supporting `not-a-bug`, `wont-fix`, `duplicate`, `cannot-reproduce`, and `fixed` (including live Jira transition out of `Done` on reopen) with `fields.resolution` sync (and graceful retry if Jira rejects `fields.resolution` on the transition screen).
  - Added parity-checked, auditable `waived` / `not-applicable` TC release gating in `close-story` and `status` (never marking waived TCs as `pass`), **Bug Triage & Risk Acceptance Disclosure** in the Release Summary, and `triage_closed` handling/filtering in `status`.
- **Bug Fix Root Cause & Solution Capture (PR #16, Issue #12):**
  - Added `complete-task --root-cause` and `--solution` persisted in active JSON (`bug_report` / `bug_fix_summary` / bug entry) and rendered in Jira completion comments without modifying original reporter Descriptions, emitting non-blocking `bug_fix_warnings` if omitted on bug fixes.

## v1.3.1 — 2026-11-23 — Inline Markdown ADF Parsing & Strict HITL / SoD Clean Stop

- **Inline Markdown to Jira ADF (PR #14, Issue #6):**
  - Upgraded `jira_sync.py` ADF conversion to parse inline bold, italic, inline code, links, strikethrough, and nested formatting into valid Jira ADF marks with zero empty text nodes and no illegal mark combinations.
- **Strict HITL Confirmation Gate & Separation of Duties Clean Stop (PR #14, Issues #9, #13):**
  - Eliminated unconfirmed `same_person: true` auto-chaining across all stations; `same_person: true` now suppresses redundant Jira self-`@mention` noise (`handoff_mode: "auto-consolidated"`) only and pauses with one short confirmation question.
  - Enforced mandatory cross-person SoD Clean Stop (`same_person: false`) forbidding prompts for the next role's slash command, emitted explicit governance flags (`can_auto_chain_next_station: false`, `requires_user_confirmation_for_next_station: true`, `cross_person_clean_stop`, `next_station_prompt_allowed`, `next_station_owner`), added `team-qa` entry identity guardrail (`qa_identity_guardrail`), and enforced the `Stop & Yield on Fix` hard boundary across all 8 skills.
  - *(Note: Issue #10 was closed as `wontfix` to uphold the Jira-only First-Principles Asset Boundary.)*

## v1.3.0 — 2026-11-13 — Epic/Sprint Planning Layer

- Added `team-plan` for Epic context, Future/Active Sprint planning, preview-before-apply membership changes, and read-only Sprint status.
- Added `sprint-plan` and `sprint-status` to the shared stdlib-only Jira CLI, including board resolution, JQL/explicit issue selection, and 50-issue move batching.
- Existing Sprint plans now update the Jira Sprint name/goal/date fields before moving issues when those fields are supplied.
- Updated `team-next-step`, shared governance, README, and bundle checks from 7 to 8 self-contained skills.
- Refreshed `docs/training-manual.md` to document `team-plan`, 13 CLI subcommands, and the 18-test verification suite.


## v1.2.0 — 2026-11-11 — Multi-Member Collaboration, Qase & Media Sync, QA `report` Mode, Living Manuals & `run-workflow`

- **Interactive Auth, Progressive Role Claiming & 1-to-N Role Pools (`acf5c4a`, `0e99eac`, `b66a91f`, `45728e4`):**
  - **`team-init` (`init` / `auth`):** Added pre-flight `init --inspect`, interactive role/credential inquiry, per-role Jira API token resolution stored exclusively in gitignored `.ai-workflow/credentials.json` (`0600`), and hidden Jira Issue Property (`ai_workflow_state`) synchronization.
  - **Collaborator Onboarding & Progressive Claiming:** Founders can initialize only their own roles (`claimed_roles` vs `unclaimed_roles`); teammates cloning the repo bind credentials and claim/join roles via `jira_sync.py auth --role <roles> [--specialty <tag>]` without overwriting existing role members (`1-to-N` `roles.<role>.members` arrays). Mutating commands block with `requires_member_auth: true` instead of silently dropping to offline mode when live Jira is configured without local credentials.
  - **End-to-End Jira Auto-Assignment & `zh-TW` Default Prose:** Automatic `PUT /rest/api/3/issue/{key}/assignee` across all stations — Story assignment (`pm` on `draft`, `developer` on `approved`), per-Sub-task developer assignment with `specialty` matching (`backend`/`frontend`/etc.), automatic peer-reviewer selection from `roles.reviewer.members`, and Bug assignment traced via `tc_id -> ac_id -> TASK-###`. Set **Traditional Chinese (繁體中文)** as the default prose language across generated Jira descriptions and comments.
- **Bidirectional Jira Web UI Reconciliation & Remote Story Discovery (`0e99eac`, `45728e4`):**
  - `status`, `pull`, and all mutating station commands reconcile live Jira state (`reconcile_story_with_jira`): discover newly created non-Done project Stories via `POST /rest/api/3/search/jql`, sync manual Jira Web UI Sub-task/linked-Bug additions or status changes, update `story.role_owners` on manual assignee edits, and guard against overwriting human Story Description edits (`last_synced_description_hash`, `--accept-remote-description`, `--force-overwrite`).
- **`team-dev` Specialist Skill Hook & Review-Aligned Exit Gate (`15f9938`):**
  - Added Bring-Your-Own-Skill composition guidance (loading environment TDD, UI/UX, or framework skills alongside `engineering-standards.md`) and a 4-point pre-checkoff exit gate (module reuse, red-to-green boundary tests, change-surface completeness, clean verification & static checks) aligned with `$team-review`.
- **`team-qa` Qase TestOps Backend, Media Attachments & `report` Mode (`4883300`, `5561779`, `4dd2a07`):**
  - **Remembered Dual QA Backend (`jira-comment` / `qase`):** One-time `record-qa --inspect` choice persisted in `team-config.json`; `qase` mode synchronizes Suites, Test Cases, Test Runs, bulk results, and External Issue links via Qase TestOps API v1 (`qase_api_token` in `credentials.json`) and eliminates self-`@mention` on `plan`.
  - **Media Evidence Sync (`--attach` / `--tc-attach`):** Uploads screenshots, recordings, and logs to Jira `Bug` Attachments and — when `qa_test_backend == "qase"` — attaches media to Qase Test Run Results and linked Qase Defects (auto-resolved on `complete-task`).
  - **New `report` Mode (Exploratory & Standalone Production Bugs):** Supports filing unscripted exploratory Story defects (`create-bug --key <STORY-KEY>` without `--tc-id` → appends `TC-EXP-###` without disturbing passing `TC-001..N`) and registering or adopting standalone production/CS `Bug` tickets (`create-bug --standalone [--adopt --key <BUG-KEY>]`) that flow through a lightweight `team-dev -> team-review -> team-qa (verify) -> team-release` track without `team-spec` or Sub-task overhead.
- **`team-release` Scale-Aware Living HTML Manual Workflow (`029b6c5`):**
  - Added a 4-tier decision matrix (`0 -> 1` greenfield, major feature, minor enhancement, bug fix/refactor skip) maintaining standalone dark-sidebar HTML manuals under `docs/manuals/<manual-name>/<manual-name>.html` (`./screenshots/`, `./diagrams/`) with 5 quality gates while preserving `<candidate-commit-sha>` gate alignment on `close-story`.
- **System Skill `skills/system/run-workflow` (`05138bc`):**
  - Added system skill documenting dynamic Python multi-agent workflow scripts (`run_workflow` tool) with `agent`, `parallel`, `pipeline`, `phase`, `trajectory`, multi-turn agent handles, and `call_tool`.

## v1.1.0 — 2026-11-04 ~ 2026-11-09 — Scale-Aware Guards, Cross-Machine Hydration, 3-Tier QA & Scenario Training Suite

- **Cross-Machine Hydration, Rich ADF & Zero-CLI Ergonomics (`4b68164`, `28848ff`):**
  - Added automatic cross-machine and Git worktree state hydration from live Jira issues, native Atlassian Document Format (ADF) tables/panels/badges, Git auto-detection of `--commit` and `--files` on `complete-task`, comma-separated multi-ID completion (`TASK-001,TASK-002` / `REV-001,REV-002`), incremental `AC-###` upsert on `sync-story`, colon/note preservation in QA `--tc` / `--result`, and context-aware `status` hints.
- **Scale-Aware Spec/Dev Tracks & 3-Tier QA Anti-False-Pass Guardrail (`3d42ef3`):**
  - **`team-spec` (`one-pass` vs `draft`):** Added iterative multi-round `draft` deep-dive mode and 6-Dimension AC Completeness Checklist for complex/ambiguous stories.
  - **Dev Fast-Track Complexity Guard:** Allowed single-step `complete-task` fast-track (`TASK-001` auto-creation) strictly for small stories (`<= 3` ACs and `low`/`medium` risk); larger or high-risk stories must run `create-subtasks` (`plan` mode).
  - **3-Tier Hybrid QA (`automated` / `external` / `manual`):** Protected `external` and `manual` test cases from `--all-pass` so unexecuted external/manual checks remain `not-executed` (`qa_status: "incomplete"`) until verified via report or human confirmation.
- **Scenario-Based Training Manual & 16-Slide Presentation (`c5e48d6`, `5bf9cdc`, `543c005`, `48946a4`, `5775bb9`):**
  - Added comprehensive `zh-TW` scenario-driven training documentation in `docs/training-manual.md`, standalone `docs/training-manual.html`, `docs/training-manual.pdf`, Google Docs-ready `docs/training-manual.docx`, annotated dual-pane diagrams (`docs/screenshots/`), and a 16-slide training deck (`docs/training-manual.pptx` + `docs/ppt-assets/`).

## v1.0.0 — 2026-11-04 — 4-Station Jira-Centered Small-Team Workflow (Issue #1)

Consolidated the 11-skill markdown-heavy pipeline into **7 streamlined, Jira-centered skills** tailored for small teams where one person may hold multiple roles:

- **`team-init` (New):** Initializes `.ai-workflow/team-config.json` (role-to-person mappings supporting multi-role consolidation, Jira project key, base URL, and status mappings), ensures `.gitignore` ignores `.ai-workflow/active/`, and extracts `docs/shared/engineering-standards.md` from `AGENTS.md` while trimming duplicated rules in `AGENTS.md` into a reference link.
- **`team-next-step` (Updated):** Read-only router inspecting `.ai-workflow/team-config.json`, `.ai-workflow/active/<JIRA-KEY>.json`, and Jira state (`jira_sync.py status`) to recommend the exact next station, mode, and owner. Updated `evals/evals.json` for the 7-skill workflow.
- **`team-spec` (Station 1 — replaces `team-requirements`, `team-ux`, `team-design`, `team-doc-review`):** One-pass change-surface analysis producing Scope, testable `AC-001..N`, technical design summary, conditional UI states/wireframe/copy (`has_ui`), and conditional API contract (`has_api`). Enforces inline **"抓大放小"** review (routine details auto-decided by AI; at most 3 material questions asked) and syncs directly to the Jira `User Story` via `jira_sync.py sync-story`.
- **`team-dev` (Station 2 — replaces `team-dev-prep` + adds coding execution):** Decoupled from QA test planning. `plan` mode decomposes approved specs into `TASK-001..N` and creates Jira `Sub-task` items (`create-subtasks`). `implement` mode loads minimal context, implements code & tests, and marks tasks/bugs `completed` (`Done`) in both `.ai-workflow/active/<JIRA-KEY>.json` and Jira (`complete-task`).
- **`team-review` (Station 3a — merges `team-dev-handoff` and `team-review`):** Inspects the actual code diff, completed task evidence, ACs, and `docs/shared/engineering-standards.md` (with fallback to `AGENTS.md`). Posts the combined handoff & 3-axis review summary to Jira and executes role-aware handoff (`jira_sync.py handoff`: auto-consolidated when same person; status transition + assignee update + `@mention` notification when different person).
- **`team-qa` (Station 3b — merges `team-qa` and `team-qa-evidence`):** Streamlined into `plan` mode (parallel `TC-001..N` checklist with AI pre-filled `automated`/`manual` execution types) and `verify` mode (ingests JUnit XML, JSON, or manual results via `record-qa`; automatically files linked Jira `Bug` tickets via `create-bug` and routes back to Developer on failure).
- **`team-release` (Station 4 — updated):** Verifies all 5 readiness gates and zero open Bugs, posts the release summary, transitions the Jira Story to `Done`, and deletes `.ai-workflow/active/<JIRA-KEY>.json` via `jira_sync.py close-story` so zero per-feature process files remain in Git.
- **Shared Files & Tooling:** `scripts/jira_sync.py` (stdlib-only Python 3 CLI supporting live Jira REST API v3 and offline mode) and `references/common-workflow-governance.md` are kept byte-identical across all 7 skills and verified by `tools/check_shared.sh` and `tools/test_workflow.py`.

---

Source of historical issue IDs (v0.4–v0.7): TeamBoard validation report (`docs/skill-validation/VALIDATION-REPORT.md` in wilsonliu123/team-board) and the team notes: D-xx (full-stack), T-xx (Tech Leader), UX-xx / UX-Sxx (UI/UX), F-xx (PM), "QA: topic" (QA notes, unnumbered).

## v0.7.0 — final release (KnowBase run)

- #8 (residual): `frontend-template.md`, `backend-template.md`, `interface-template.md` frontmatter add `review_status: not-started` and `validity: current` (reviewed with `sad.md`; validators already read `validity` on these files). `risk_level` stays on `sad.md` only.
- #9 (residual): team-design SKILL.md states ADRs live only in `docs/shared/adr/`, even if an instruction says `docs/adr/`; `validate_design.py` fails when `docs/adr/ADR-*.md` exists, because those ADRs would skip the approved-ADR check.
- #11 (residual): team-design SKILL.md says a draft blocked only by missing engineering standards cannot enter review; get the standards approved first, then set the design `complete`.
- #15: still deferred (reason below).
- Self-check: `tools/check_shared.sh` OK; gate scenario (stamp, record, --downstream with/without args, AT-RISK, chain STALE, no-record BLOCK) identical on all 9 copies; validate_requirements, validate_design, validate_qa and validate_dev_prep re-run on KnowBase `400f790`, all OK; validate_review, validate_release, validate_dev_handoff and validate_review_session not re-run (no inputs prepared).

### Final status of all 30 KnowBase issues (#1–#29 plus #18b)

| # | Status | Commit | Note |
|---|---|---|---|
| 1 | fixed | `2419045` | team-requirements SKILL headings v0.4 → v0.6 |
| 2 | fixed | `2419045` | first stage has no `--record`; baseline via `--stamp-approved` |
| 3 | fixed | `a752712` | UX directory upstreams are status-gated (README.md) |
| 4 | fixed | `a752712` | `--record` on a missing upstream stores `sha256: pending` |
| 5 | fixed | `a752712` | gate checks upstreams declared in frontmatter |
| 6 | fixed | `a752712` | `in-review` removed from governance (five review states) |
| 7 | fixed | `e2e715c` | unapproved same-stage upstreams recorded as at-risk |
| 8 | fixed | `6ea24e2 + v0.7.0` | canonical keys (`artifact_type` only, `reviewer_roles`); v0.7.0: frontend/backend/interface templates add `review_status`, `validity` |
| 9 | fixed | `6ea24e2 + v0.7.0` | path is `docs/shared/adr/`; v0.7.0: SKILL.md says so explicitly, validate_design fails on ADRs in `docs/adr/` |
| 10 | fixed | `e2e715c` | UX upstream recorded at-risk, so design can declare `ux_reference` before UX exists |
| 11 | fixed | `6ea24e2 + v0.7.0` | validate_design fails on draft / missing standards / Blocking OQ, full REQ/AC coverage; v0.7.0: SKILL.md says how a standards-blocked draft enters review |
| 12 | fixed | `b7e2f51` | validate_qa fails on stale/unknown |
| 13 | fixed | `9ebbbe8` | shared `validity_problems()` in every validator |
| 14 | fixed | `444f68e` | validators re-hash recorded upstream baselines |
| 15 | deferred | — | see Deferred below: needs a UX skill / `validate_ux.py`; no new skills in this run |
| 16 | fixed | `5f064ed` | ad-hoc `*_sha256` fields BLOCKed |
| 17 | fixed | `3133ff1` | Unicode-aware role-name normalization |
| 18 | fixed | `a9dfdea` | engineering-standards is a governed upstream (docs) |
| 18b | fixed | `291b4bc` | `engineering_standards` in DECLARED_KEYS |
| 19 | fixed | `27e9619` | `GOTOOLCHAIN=local` is not pinning |
| 20 | fixed | `76cb23c` | requirements' `ux_reference`/`engineering_standards` are references, not upstreams |
| 21 | fixed | `b5d62dc` | dev-prep `.gitignore` check removed |
| 22 | closed | `b5d62dc` | status-mode BLOCK vs record AT-RISK is intended; documented |
| 23 | fixed | `b5d62dc` | `- Enabling work: <reason>` accepted |
| 24 | fixed | `c5f8f89` | coding-stage per-task contract in team-dev-prep (no new skill) |
| 25 | fixed | `57b0642` | cross-reference corrected (step 4) |
| 26 | fixed | `6606ea6` | design constraint coverage table in test-cases |
| 27 | fixed | `f004576` | XSS TCs verify escaping and CSP separately |
| 28 | fixed | `45c8a13` | `--downstream` with no args reads recorded upstreams |
| 29 | fixed | `45c8a13` | one-level chain STALE; full transitive walk deferred |

## v0.6.8

- KnowBase #17: `check_upstream_gate.py` `norm()` kept only `[a-z0-9]`, so CJK-only role names (e.g. 全栈工程师) collapsed to '' and distinct roles compared equal, defeating the author-cannot-self-approve check. Now Unicode-aware (`[\W_]`, casefold, strips full-width parens). Shared across all 9 skills.
- KnowBase #18: governance now states `docs/shared/engineering-standards.md` is a governed upstream (canonical frontmatter, approved once, recorded, STALE on edit) instead of leaving its status undefined. Docs only; gate unchanged.
- KnowBase #19: team-dev-prep cited `GOTOOLCHAIN=local` as a pinning example, but it disables toolchain download and uses the local Go. Example now requires installing the exact toolchain in `make setup` plus a `go version` check in `make ci`.
- KnowBase #18b: gate DECLARED_KEYS now includes `engineering_standards`, so a downstream that declares the standards but never records them is BLOCKed; `engineering_standards: unavailable` is skipped. Shared across all 9 skills.
- KnowBase #20: in a requirements document the gate no longer treats `ux_reference:` / `engineering_standards:` as declared upstreams (both are downstream of requirements; recording them made a cycle). Governance states they are references only. Other artifact types unchanged.
- KnowBase #21: team-dev-prep no longer asks to merge `.gitignore.ai-workflow` (the file never existed); git-identity check kept.
- KnowBase #22: gate usage documents that plain status mode BLOCKs an unapproved upstream while --record/--downstream report AT-RISK (intended). Shared across 9 skills.
- KnowBase #23: `validate_dev_prep.py` accepts `- Enabling work: <reason>` instead of a REQ citation, matching SKILL.md; task-template gains that line.
- KnowBase #24: no coding skill existed. Without adding a skill, team-dev-prep "Handoff to the coding stage" now defines the per-task contract: entry gate (`--downstream` on dev-prep index), per-task evidence, review record path, E2E execution path (only the production path counts), and UX acceptance under `docs/ux-acceptance/`. team-dev-handoff step 4 cites it. Validators unchanged.
- KnowBase #25: coding contract item 2 pointed at team-dev-handoff "§5"; it is workflow step 4. Wording only.

## v0.6.7 — KnowBase #16

- No skill template defines `requirements_approved_sha256`; it was an ad-hoc copy that went stale. Shared `validity_problems()` now BLOCKs any frontmatter `*_sha256` key other than `approved_sha256`; governance says `approved_sha256` and `upstream:` are the only hash fields.

## v0.6.11
- #28: `check_upstream_gate.py --downstream <down.md>` with no upstream args now checks the upstreams recorded in its `upstream:` frontmatter (previously printed usage, contradicting "passed or recorded"); BLOCKs if nothing is recorded. `--record` still requires the explicit list (usage says so). Shared across 9 skills.
- #29: gate re-hashes each upstream's own recorded `upstream:` baselines (one level); a changed one reports `STALE: chain <up> -> <its upstream>` and fails (exit 1) in status, --record and --downstream modes. Deferred: full transitive walk. Shared across 9 skills.

## v0.6.10
- #27: team-qa step 2 notes XSS TCs must verify escaping and CSP separately (no-CSP positive control, DOM assertions as primary evidence, zero `securitypolicyviolation`).

## v0.6.9
- #26: team-qa test-cases step must list design security constraints in `## Design constraint coverage` (TC or `n/a: reason`); `validate_qa.py` enforces it; template updated.

## Deferred (not in scope; no new skill during fixes)

- Deferred in v0.7.0: KnowBase #15 needs either a new UX skill or a `validate_ux.py` owned by no skill; both mean adding a skill, which this run's rule forbids. Gate status/hash checks still cover the UX package. Revisit as a separate UX skill. Also deferred: full transitive chain walk (#29), cited-but-missing ADR files are not yet an error (#9).

- KnowBase #15 — `validate_ux.py` for the UX package (no UX skill exists in this set). Candidate checks proposed by UI/Ux: (1) README state matrix maps cell-by-cell to requirement §5, no blank cells, N/A with reason matching the requirement; (2) README §1 lists every screen-related AC by full id (e.g. `AC-033`, not `/033`); (3) no 【提案】 copy left; copy not given verbatim by the requirement cites a PD id in decisions; (4) every PNG/SVG listed in README exists. Meanwhile: gate/--downstream cover governance and hashes; `validate_design.py` covers screen, situation-matrix and full REQ/AC coverage downstream.

## v0.6.6 — KnowBase #14

- `validity_problems()` (shared, all 9 copies) also re-hashes every recorded `upstream:` baseline: a changed upstream => STALE, a `pending` one => PENDING, a missing path => BLOCK. Every stage validator now agrees with `check_upstream_gate.py --downstream` even if the owner has not hand-marked `validity: stale`.

## v0.6.5 — KnowBase #13 (and sweep for the same gap)

- New shared `validity_problems()` in `check_upstream_gate.py` (all 9 copies): `validity: stale|unknown` => BLOCK.
- Called by every stage validator next to `approval_problems()`: requirements, design (sad + frontend/backend/interface), dev-prep, review, QA (all QA files incl. test-cases; replaces the v0.6.4 local check), dev-handoff, release.
- `validate_review_session.py` is not changed: review sessions carry no `validity` field.

## v0.6.4 — KnowBase #12

- `validate_qa.py` fails (exit 1) when any QA artifact has `validity: stale` or `unknown`; it used to report OK.

## v0.6.3 — KnowBase run fixes (skill-issues #8, #9, #11)

- #11 (important) `validate_design.py` now fails when `sad.md` is `draft`, when `docs/shared/engineering-standards.md` is missing, or when §8.3 has a `Blocking: Yes` open question; REQ/AC coverage checks every id in the requirement across sad/frontend/backend/interface and lists missing ids (was: any one match).
- #8 Templates use canonical governance keys: `artifact_type` only (no `document_type`), `reviewer_roles` list (no `reviewer_role`).
- #9 No skill change: every skill file and `validate_design.py` already use `docs/shared/adr/`; `docs/adr/` came from a delegating instruction, not the skill.

## v0.5 — 2026-10-13

### P0-1 Gate compares upstream content (report §3, P0-1)
Fixes: D-15, T-01, D-05, D-07, D-18, T-05, F-18, F-20, UX-02, UX-07, UX-S11, QA: deliberate-injection / post-approval edits.
- `check_upstream_gate.py` (same file in team-design, team-dev-prep, team-dev-handoff, team-review, team-qa, team-release):
  - `--stamp-approved <artifact>` writes `approved_sha256`; later content changes under `approved` → `STALE`, exit 1.
  - `--record <downstream> <upstream...>` gates, then writes `upstream: [{path, sha256}]` into the downstream frontmatter.
  - `--downstream <downstream> <upstream...>` → `STALE`, exit 1 when any upstream changed; `BLOCK` when no baseline was recorded.
  - Hash ignores frontmatter; `sad.md` / `index.md` hash their whole package (attachments); a directory (e.g. `docs/ux/<slug>/`) is hashed as a unit, so wireframes and UI acceptance are covered without new fields.
  - Plain `check_upstream_gate.py <upstream...>` behaves as before (plus the `approved_sha256` check when that field is present).
- `common-artifact-governance.md` (all 9 copies): "Baseline and stale detection" now describes the script-maintained fields; it replaces hand-written approval carry-forward notes.
- SKILL.md gate instructions (5 gated skills plus team-qa) use `--record` / `--downstream`; team-doc-review stamps `approved_sha256` on approval.

### P0-2 Evidence bound to baseline, voidable (report §3, P0-2)
Fixes: QA: rev3 evidence void (EVD-024/025/009), QA: validate_qa does not check the evidence commit, root cause 3.
- `normalize_evidence.py` / `write_manual_evidence.py`: `--requirements` / `--test-plan` record `baseline.{requirements,test_plan}.{path,sha256}`; records carry `validity: current|void` and `void_reason`.
- New `void_evidence.py` (≈15 lines, team-qa-evidence) marks evidence void with a reason and an optional `superseded_by`.
- `validate_qa.py --evidence-dir <dir>` rejects: evidence whose baseline content changed, evidence without a baseline, void evidence without a reason, and `target.commit` ≠ `tested_commit`. Void records are kept and excluded. Without `--evidence-dir` it behaves as before.
- evidence-contract.md, team-qa-evidence SKILL.md, and team-qa Mode 5 updated.

### P2 (former P0-3) `decided_by`
Fixes: QA: execution-assignment (clarified by 剑秋 on 10/13: the design is correct).
- `execution-matrix-template.md` adds the `decided_by` field. `validate_qa.py` prints a WARN (not an error) when it is missing. The process is unchanged.

### P1/P2 small fixes
- Usage line on bad arguments for every validator (T-02, QA: validate_qa takes a directory; also states "run from repo root").
- `normalize_evidence.py`: EVD numbering continues after the highest existing file (QA: EVD numbering overwrites). Tests without a TC id get a WARN and a `dropped_without_tc` count (D-08, QA: regex drops tests silently). `executed_at` is `unknown` rather than the import time. The TC regex and Go naming are documented (D-16, T-06).
- Archiving to `evidence/<sha>/` is now a standard step in team-qa-evidence (UX-S10, UX-S16, QA: run2).
- qa-report template: §8.1 Known limitations, §8.2 Accepted non-defect issues (QA: final-assessment, QA: template).
- team-requirements glossary: list product terms that stay untranslated (UX-S14, D-10).

### Follow-up after QA / Tech Leader review
- `check_upstream_gate.py` (6 identical copies): an `approved` upstream without `approved_sha256` now reports `BLOCK` (exit 1) with a `--stamp-approved` hint, whether or not `--downstream` is used. **Migration:** run `--stamp-approved` once per approved v0.4 document; before that, every gate on v0.4 artifacts BLOCKs (intended).
- `validate_qa.py`: when `qa-report.md` exists, `--evidence-dir` is required. **Migration:** evidence without a baseline must be re-imported with `--requirements` / `--test-plan`.
- team-qa SKILL.md: also `--record` / `--downstream` the test-cases baseline; migration note added. The qa-report template and governance doc mention both rules.

## v0.6.2

- `check_upstream_gate.py --record`: an upstream that exists but is not approved (draft, in progress, or changes requested) is now recorded with its hash and `status: at-risk`. The script prints AT-RISK and exits 1. Pending handling is unchanged.
- Gate and `--downstream`:
  - An at-risk entry gives AT-RISK and exits 1.
  - If the at-risk upstream's hash has changed, the result is STALE.
  - Once the upstream is approved, the gate asks you to re-run `--record`, which clears the status.
- Rejected and void upstreams still BLOCK.
- Governance: when two artifacts of the same stage are reviewed together, approve and stamp the upstream first, then re-record the downstream, then approve the downstream.
- Tests: the QA test-cases case (skill-issues #7) is added to `knowbase-gate.sh`, and its log is in `docs/experiments/stale-gate.md`.

## v0.6.1 — 2026-10-13 (KnowBase live fixes, check_upstream_gate.py in all 9 copies)
- A directory upstream (e.g. `docs/ux/<slug>/`) now takes its governance from `<dir>/README.md` frontmatter. It is status- and approval-checked like a file. `--stamp-approved <dir>` writes into the README; the hash covers the whole directory and excludes the README frontmatter. A missing README or a missing directory BLOCKs. Previously a directory always passed the status check.
- `--record` on an upstream that does not exist yet stores `sha256: pending`. The gate reports `PENDING` and exits 1 until `--record` is re-run after the upstream exists. Re-running `--record` adds or updates entries.
- In `--record` / `--downstream` mode the gate reads the upstreams declared in the downstream frontmatter (`requirements`, `design`, `test_spec`, `test_cases`, `test_plan`, `execution_matrix`, `review`, `dev_prep`, `dev_delivery`, `ux`, `ux_reference`, `source_requirements`). It BLOCKs if a declared upstream is neither passed nor recorded. `--downstream` also re-checks every recorded upstream.
- Governance (all copies): documents the three rules above, and states that `review_status` has only five values (no `in-review`).
- Experiments: `knowbase-gate.sh` added; `migrate-teamboard.sh` adapted (UX README frontmatter, test-cases records test-spec).

## v0.6 — 2026-10-13: every deferred item fixed

Commits: `22612ad` core · `a8c6fc3` requirements · `04058f2` design · `674628e` dev-prep/handoff · `471bc5a` review/release · `791380e` QA+evidence · `36620ef` doc-review/telemetry · `8bb8d1e` fixes · `cdcf367` experiment. Earlier: `8bd5b73` P0-1 · `dbbd1b6` P0-2 · `68beea9` decided_by · `4a088c5` small fixes · `025dc0b` gate/QA blockers.

**Migration from v0.4:** see "Migrating v0.4 artifacts" in `common-artifact-governance.md`, and the worked example `docs/experiments/migrate-teamboard.sh`.

### Issue → commit

| Issue IDs | Fix | Commit |
|---|---|---|
| D-15, T-01, D-05, D-07, D-18, F-18, F-20, UX-02, UX-07, UX-S11, QA: injection experiment / post-approval edits (a4f891d, d322f75, 02f2daf, 3ca9aa4) | content-hash gate, package and UX-directory hashing, approved_sha256 | 8bd5b73, 025dc0b |
| QA: rev3 evidence void, QA: evidence commit check, report root cause 3 | evidence baseline + void; validate_qa rejects | dbbd1b6, 025dc0b |
| Hash unification, evidence binds sad.md, `--evidence-dir` usage | one content_hash() imported everywhere; design baseline | 22612ad |
| "approved but changed" in stage validators | approval_problems() in every validator | 22612ad |
| F-11, F-19, F-20, QA: delegated approver = author | approved_by_role, delegation_reference, independent_reviewer_role (enforced) | 22612ad, 8bb8d1e |
| D-04, D-14, QA: execution-handoff with unapproved upstream | proceed_at_risk + AT-RISK; downstream cannot be approved while at risk; pre-check runs | 22612ad, 674628e, 791380e |
| F-02, F-04, F-03, F-07, QA: unconfirmed assumptions | initial values; Blocking OQ ⇒ draft (validated); no-human rule; artifact_type only + feature | 22612ad, a8c6fc3 |
| F-15, F-16, F-17, QA: testability approval/stale | testability has no separate approval and goes stale via `--record`; scope changes re-trigger it | 22612ad |
| F-21, D-18 (shared worktree), T-08, QA: git identity | git add own paths; repo-local identity in env step | 22612ad, 674628e |
| F-22 | review_mode batch | 22612ad, 36620ef |
| P2-5, QA: duplication | kept per-skill (self-contained skills); `tools/check_shared.sh` drift guard; fixed pre-existing drift in team-dev-handoff review-protocol.md | 22612ad |
| F-05, F-14, QA: traceability consistency (G-03) | multi-Covers, unknown REQ, Must→AC, §13 == Covers | a8c6fc3 |
| F-06, F-10, F-12, F-13, UX-01, UX-06, QA: five situations / screen list | requirements template: screens, situation matrix (403/404), delivery constraints, decisions, ux_reference | a8c6fc3 |
| D-10, UX-S14 | copy canonical in requirements; glossary incl. untranslated terms | 4a088c5, a8c6fc3, 04058f2 |
| UX-03, UX-04, UX-05, UX-08 | frontend situation matrix + guidance; validate_design checks screens/matrix/attachments | 04058f2 |
| D-03 | ADR approved + stamped with design; validator | 04058f2, 36620ef |
| T-04 | design change from review routed to team-design | 04058f2, 471bc5a |
| D-01, D-06, D-13, F-00, F-01, QA: environment | dev-prep step 8a + index §5a (check only; make setup owned by first coding task) | 674628e |
| D-02 | validate_dev_prep: REQ citations, coverage, deps, cycles | 674628e |
| D-09 | dev-delivery `upstream:` incl. test plan and UX | 674628e |
| T-02 | usage messages | 4a088c5 |
| T-03 | validate_review: head_commit exists and no later code commits | 471bc5a |
| T-05 | carry-forward notes replaced by re-stamp/re-record; editorial rule | 22612ad, 36620ef |
| T-06, D-16 | QA owns TC IDs/sub-test names; `Automated test name` field; review maps regressions via QA | 471bc5a, 791380e |
| T-07, D-12, F-08 | emit_event `end` alias, current-session file, documented invocation | 36620ef |
| T-09 | incremental re-review §9a | 471bc5a |
| QA: severity names | one scale blocker/major/minor/note | 471bc5a |
| D-08, QA: regex drop / numbering / executed_at / skipped | WARN + count, continuing numbering, unknown time, skipped→not-executed | 4a088c5, 791380e |
| QA: flaky, partial, negative (CI-must-fail), report numbers vs evidence, void listing, generated table, decided_by strict, AC coverage, test-cases fields, testability conclusion/rounds/closed gaps, reviewer roles, Mode 4/5 upstream sets, manual attachments, evidence-index schema, commit normalized JSON | validate_qa + templates + qa-method | 791380e, 8bb8d1e |
| P2-2 (generated table, trim test-cases) | `--emit-table`; optional fields omitted | 791380e |
| D-11, D-17, D-19 | qa-method test data / isolation / server logs | 791380e |
| UX-S9, UX-S12, UX-S13, UX-S15 | ui-acceptance template, `<Screen>-<situation>` naming, manual owner + evidence link, interaction-state evidence | 791380e |
| UX-S10, UX-S16, QA: run2 archive | `evidence/<sha>/` standard step | 4a088c5, 791380e |
| Pre-existing review-session failures | completed sessions don't need current_item / Remaining items (skill bug) | 36620ef |
| P0-3 → P2 | decided_by (now an error) | 68beea9, 791380e |
| F-09 | no change needed (gate already blocked unapproved upstream) | — |

### Remaining, with justification
- **One shared copy instead of duplicates (P2-5):** not done, on purpose. Each skill must stay self-contained (each ships its own zip), and the constraint was to keep the copies byte-identical. `tools/check_shared.sh` enforces that.
- **"REQ rule → TC" numeric index (QA suggestion 5 in the core defect):** not added. It would be a hand-maintained index. Impact analysis is covered by the content-hash gate (STALE), evidence binding, and the every-AC-has-a-TC check.
- **Legacy TeamBoard review sessions:** 5 sessions miss `review_session_status`, `artifact_path` or `## Resolved items`. That is authoring content, not a skill defect, so they still fail until someone edits them. The skill bug (requiring an open-session queue on completed sessions) is fixed.

### History: two hash formats (resolved in 22612ad)
The evidence `requirements` hash and the gate's `approved_sha256` are both computed with frontmatter stripped, but with different algorithms: `check_upstream_gate.py` hashes `name + \0 + sha256(body) + \n` per file (so it also works on directories), while `validate_qa.py` / evidence import hash the body directly. Each detects staleness correctly on its own; the values are just not comparable to each other. Deferred: unify into one function.

Resolved in 22612ad: hash unified; evidence binds `sad.md` (design changes are caught by the gate only); usage line still shows `[--evidence-dir]` in brackets.
