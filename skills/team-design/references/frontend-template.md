# Frontend design template

Use when the change affects a user-facing/client application.

```markdown
---
artifact_type: frontend-design
feature: <feature-slug>
artifact_status: draft
owner_role: Frontend Engineer
reviewer_role: Tech Lead
source_sad: ./sad.md
ux_reference: docs/ux/<feature-slug>/   # wireframes S1..Sn; part of the gated UX package
---

# <Feature> — Frontend Design

## 1. Requirement / AC coverage
## 2. User journeys and entry points
## 3. Screen and interaction specification
<!-- Use the requirement's screen IDs (S1..Sn) and quote its copy verbatim; do not re-word UI text. Link each screen to its wireframe in ux_reference. -->
### 3.1 Situation matrix
<!-- Same columns as the requirement's situation coverage, plus Loading. One row per screen ID. Each cell: what the user sees, incl. retry/recovery for network loss (no separate retry section). A cell the requirement marks `N/A — <reason>` stays `N/A` with the same reason; do not design a screen for it. -->
| Screen | Normal | Loading | Invalid input | No permission (403) | Not found / hidden (404) | Empty data | Network loss / timeout |
|---|---|---|---|---|---|---|---|
### 3.2 Per-screen interactions
<!-- Per screen ID: layout regions, primary actions, interaction states that a screenshot cannot show (hover, focus, tooltips) and how they are verified. -->
## 4. Component and page structure
## 5. Client state, caching, and routing
## 6. Interface and data-field mapping
## 7. Validation and error presentation
## 8. Accessibility, localization, browser/device constraints
<!-- Cite the requirement's language/localization decisions and docs/shared/glossary.md (incl. untranslated terms). -->
## 9. Security and privacy considerations
## 10. Frontend-specific testability / observability
## 11. Risks, assumptions, and open questions
```
