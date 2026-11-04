# ADR Template (Living System Asset)

Create `docs/shared/adr/ADR-<NNN>-<slug>.md` **only** when a feature introduces a long-lived, cross-module, or hard-to-reverse architectural decision. Never create ADRs for routine feature implementation details.

```markdown
---
artifact_id: ADR-<NNN>
artifact_type: adr
validity: current
risk_level: <low|medium|high|critical>
owner_role: Tech Lead
jira_reference: <JIRA-KEY>
---
# ADR-<NNN>: <Decision Title>

## Context
<Why this architectural decision is needed and what system constraints apply>

## Decision
<The chosen architecture, boundary, or protocol>

## Alternatives Considered
- **Option A:** <Pros / Cons>
- **Option B:** <Pros / Cons>

## Consequences & Trade-offs
<Operational, security, compatibility, and maintenance impact>

## Revisit Triggers
<Conditions under which this decision should be re-evaluated>
```
