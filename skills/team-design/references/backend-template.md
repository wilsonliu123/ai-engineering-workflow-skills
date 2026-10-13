# Backend design template

Use when the change affects services, APIs, integrations, persistence, or server-side business logic.

```markdown
---
document_type: backend-design
feature: <feature-slug>
artifact_status: draft
owner_role: Backend Engineer
reviewer_role: Tech Lead
source_sad: ./sad.md
---

# <Feature> — Backend Design

## 1. Requirement / AC coverage
## 2. Service/module responsibilities
## 3. Domain and business-rule placement
## 4. Data model and persistence
### 4.1 Entities / schema changes
### 4.2 Constraints and indexes
### 4.3 Migration / backfill / retention
## 5. Interface behavior
### 5.1 Input validation
### 5.2 Authentication / authorization / tenant isolation
### 5.3 Errors and compatibility
## 6. Transactions, consistency, and concurrency
## 7. Idempotency, retries, timeout, and degradation
## 8. External dependencies and failure handling
## 9. Configuration, secrets, and security
## 10. Logs, metrics, audit, and operations
## 11. Backend-specific testability
## 12. Risks, assumptions, and open questions
```
