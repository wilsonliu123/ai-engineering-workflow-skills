# UX Package Template

Use this layout for `docs/ux/<feature-slug>/README.md`. It acts as the directory package root and carries the authoritative lifecycle governance metadata.

```markdown
---
artifact_id: UX-<feature-slug>
artifact_type: ux-package
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
owner_role: UI/UX
reviewer_roles:
  - Product Manager
  - Frontend Lead
requirements: docs/requirements/<feature-slug>.md
design_mode: <generative|figma-bridge>
figma_url: <url-or-none>
source_commit: <git-sha-or-unknown>
---

# <Feature> — UX Design Package

## 1. Package Overview
### 1.1 Purpose and User Journey
### 1.2 Design Mode
- Mode: `generative` (AI wireframes & specifications) | `figma-bridge` (Figma design of record)
- External reference: `<Figma URL / file key or "None (generative design of record)">`
### 1.3 In-scope Screens
- S01: `<Screen Name>` — `<Brief description>`
- S02: `<Screen Name>` — `<Brief description>`
### 1.4 Explicitly Out of Scope

## 2. Upstream Traceability
Every in-scope screen must map to authoritative requirements and acceptance criteria from `docs/requirements/<feature-slug>.md`.

| Screen ID | Screen Name | REQ IDs | AC IDs | Primary Flow / Notes |
|---|---|---|---|---|
| S01 | `<Screen Name>` | REQ-001 | AC-001, AC-002 | Primary landing state |
| S02 | `<Screen Name>` | REQ-002 | AC-003 | Secondary action / modal |

## 3. Package Manifest
- `README.md` — Package governance, screen inventory, and upstream traceability.
- `wireframes.md` — Screen layouts, visual hierarchy, situation states, or Figma frame mapping.
- `copy-deck.md` — UI labels, error messages, empty-state prompts, and tone guidelines.
- `ui-acceptance.md` — Pre-populated acceptance criteria and situation coverage matrix for QA.

## 4. Design System & Tokens
### 4.1 Typography and Spacing
- Heading scale, body font, spacing units (e.g. 4px/8px grid).
### 4.2 Color Semantics
- Primary, Neutral, Success, Warning, Error backgrounds and text contrast ratios.
### 4.3 Responsive Breakpoints
- Mobile (< 640px), Tablet (640px - 1024px), Desktop (> 1024px).

## 5. Risks and Open Questions
### 5.1 Open Questions
| ID | Question | Impact | Owner | Blocking (Yes/No) |
|---|---|---|---|---|

### 5.2 Assumptions
- Assumes standard component library primitives are available.
```
