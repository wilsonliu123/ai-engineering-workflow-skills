---
name: team-ux
description: Standardize UI/UX design packages under docs/ux/{feature-slug}/ from reviewed requirements. Use when a feature has a user interface and needs wireframe specifications, screen situation coverage (loading, empty, error, offline), copy decks, or UI acceptance criteria, or when linking external Figma designs into repository governance. The skill does not modify product code, invent unreviewed business rules, or approve its own package.
---

# Team UX

## Lifecycle governance (v0.6, authoritative)

Read `references/common-artifact-governance.md` before creating or updating lifecycle artifacts. If older wording in this skill conflicts with that reference, the governance reference takes precedence.

Use `artifact_status`, `review_status`, `validity`, `risk_level`, source baselines, and package-level gates. Do not use a single `status` field for both completion and human approval.

UX packages live in directory format:
`docs/ux/<feature-slug>/`

Directory packages carry governance in `<dir>/README.md` frontmatter. After human approval, `--stamp-approved docs/ux/<feature-slug>` hashes the entire directory (excluding `README.md` frontmatter) and records `approved_sha256` in `README.md`. Downstream skills (`team-design`, `team-dev-prep`, `team-qa`) gate against this directory hash.

Enter the review protocol in `references/review-protocol.md` when presenting the completed package. Ask one material question at a time. Only an explicit human decision or authoritative external record changes `review_status` to `approved`, `changes-requested`, or `rejected`.

## Workflow observability

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit stage events at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and must never block artifact generation, validation, or gate behavior.

## Hard boundaries

- Write documentation only under `docs/ux/<feature-slug>/`. Do not modify product code, stylesheets, components, tests, backend models, or deployment configurations.
- Default output language: Write all UX documentation, wireframe explanations, copy decks, and UI acceptance criteria in Traditional Chinese (繁體中文) by default, preserving technical tokens, frame IDs, and standard English terms.
- Maintain strict traceability to `docs/requirements/<feature-slug>.md`. Every screen and acceptance criterion must map to an approved `REQ-*` or `AC-*`. Do not invent business logic or product features outside the approved requirement.
- Do not mark the UX package `approved`. The skill sets `artifact_status: complete` when all files and criteria are generated; `review_status` remains `not-started` or `in-progress` until an explicit human approval.
- When an interaction decision or edge case is unresolved, document it explicitly under Risks and Open Questions and stop the affected screen flow rather than guessing.

## Repository contract

### Primary inputs

1. `docs/requirements/<feature-slug>.md` — reviewed requirements and acceptance criteria source of truth.
2. `docs/shared/engineering-standards.md` — shared repository standards (e.g. accessibility, supported browsers/devices).
3. Optional external design references — Figma URLs, design system component library links, or existing application screenshots.

If the requirement file is missing or has unresolved blocking decisions, report the gap before producing a UX package for downstream handoff.

### Standard outputs

Produce or update the directory package:
`docs/ux/<feature-slug>/`

Required files:
- `README.md` — package governance frontmatter, screen inventory, and upstream traceability (template: `references/ux-package-template.md`).
- `ui-acceptance.md` — pre-populated screen situation coverage matrix and testable criteria for QA (template: `references/ui-acceptance-template.md`).

Required based on mode:
- `wireframes.md` — screen layouts, ASCII/Mermaid visual hierarchy, and situation specifications (required in `generative` mode; template: `references/wireframes-template.md`).
- `copy-deck.md` — centralized UI strings, error notices, button labels, and empty states (template: `references/copy-deck-template.md`).

---

## Operating Modes

Determine the operating mode before drafting:

### Mode 1: Figma Bridge Mode (`design_mode: figma-bridge`)
Use when a dedicated designer provides Figma frames or external mockups.
- Figma is the visual design of record.
- The skill does not redraw or reinvent visual layouts.
- Record the canonical Figma URL, file keys, and frame IDs in `README.md`.
- Extract the screen catalog, user flows, and situation matrix.
- Pre-populate `ui-acceptance.md` with explicit, testable criteria matching Figma states so QA and frontend engineers have unambiguous verification boundaries.

### Mode 2: Generative Design Mode (`design_mode: generative`)
Use when no dedicated designer or external design artifact exists (e.g., prototype, early feature, or AI-led development).
- The repository is the design of record.
- Create `wireframes.md` containing ASCII layout structures, Mermaid interaction flows, and responsive breakpoint rules.
- Define explicit situation states for every screen.
- Create `copy-deck.md` for consistent UI copy and actionable error messages.
- Pre-populate `ui-acceptance.md`.

---

## Workflow Steps

### 1. Locate context and gate upstream

1. Resolve the feature slug and confirm `docs/requirements/<feature-slug>.md` exists.
2. Run upstream gate check to record the requirements baseline:
   ```bash
   python3 scripts/check_upstream_gate.py --record docs/ux/<feature-slug>/README.md docs/requirements/<feature-slug>.md
   ```
3. Check whether external Figma/design links exist in the request or conversation. Select `figma-bridge` if an external design source is provided; otherwise select `generative`.

**Done:** Upstream requirements baseline is recorded, and the operating mode is identified.

### 2. Catalog screens and trace requirements

1. Extract all user-facing requirements (`REQ-*`) and acceptance criteria (`AC-*`) from `docs/requirements/<feature-slug>.md`.
2. Identify all required screens and assign stable identifiers (`S01`, `S02`, ...).
3. Build the upstream traceability matrix in `README.md`. Every screen must trace to at least one `REQ` and `AC`.

**Done:** Traceability matrix maps every user-facing requirement and acceptance criterion to at least one screen ID without unmapped requirements.

### 3. Specify wireframes and situation coverage

1. For every screen (`S01`, `S02`, ...), document the seven canonical situation states:
   - **normal:** default display with typical data.
   - **loading:** skeleton or loading indicator placement.
   - **invalid:** validation errors, missing required inputs, or out-of-range data.
   - **403:** unauthorized or permission-denied views.
   - **404:** resource not found or deleted item views.
   - **empty:** zero-data state with a clear call-to-action.
   - **offline:** network disconnected or sync-pending states.
2. In `generative` mode: write `wireframes.md` specifying ASCII layout hierarchy, component placement, and Mermaid navigation flows.
3. In `figma-bridge` mode: record the exact Figma frame URL / node ID for each screen and situation state.

**Done:** All screens have concrete layout hierarchy and explicit rules for all seven situation states recorded in `wireframes.md` or Figma links.

### 4. Produce copy deck and UI acceptance criteria

1. Draft `copy-deck.md` listing all user-facing strings (headings, primary/secondary buttons, empty state guidance, error messages).
2. Generate `ui-acceptance.md` using `references/ui-acceptance-template.md`:
   - Pre-populate Section 1 (Coverage matrix) with all screens and situations marked as `planned`.
   - Pre-populate Section 2 (Test cases) with planned manual verification steps mapping to screenshot filename convention `<ScreenID>-<situation>[-<detail>].png`.
   - Pre-populate Section 3 (Interaction states) for transitions, modal overlays, and keyboard focus order.

**Done:** `copy-deck.md` and `ui-acceptance.md` provide complete, actionable verification criteria for QA execution.

### 5. Validate package and prepare for review

1. Verify package structure with the validator:
   ```bash
   python3 scripts/validate_ux.py docs/ux/<feature-slug> docs/requirements/<feature-slug>.md
   ```
2. If validation reports errors, correct them.
3. When all artifacts are complete and open questions resolved, set `artifact_status: complete` in `README.md`.
4. Hand off to `team-doc-review` for human review.
5. Upon explicit human approval, run:
   ```bash
   python3 scripts/check_upstream_gate.py --stamp-approved docs/ux/<feature-slug>
   ```

**Done:** Package passes `validate_ux.py` with exit code 0 and is ready for human review and downstream consumption.
