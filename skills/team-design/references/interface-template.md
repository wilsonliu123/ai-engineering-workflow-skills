# Interface design template

Use only when the feature changes a boundary between components, services, clients, events, or external systems. If a canonical OpenAPI/AsyncAPI/JSON Schema/protobuf definition already exists, update/reference that source instead of duplicating it here.

```markdown
---
artifact_type: interface-design
feature: <feature-slug>
artifact_status: draft
review_status: not-started   # reviewed and approved with sad.md
validity: current
owner_role: Tech Lead
reviewer_roles:
  - Tech Lead
source_sad: ./sad.md
canonical_spec: <path-or-none>
---

# <Feature> — Interface Design

## 1. Interface purpose and consumers
## 2. Requirement / AC coverage
## 3. Operations / messages / events
## 4. Request or payload schema
## 5. Response or result schema
## 6. Validation and null/optional semantics
## 7. Authentication, authorization, and data scope
## 8. Error model
## 9. Idempotency, ordering, pagination, timeout, retry (as applicable)
## 10. Compatibility and versioning
## 11. Examples
## 12. Open questions
```
