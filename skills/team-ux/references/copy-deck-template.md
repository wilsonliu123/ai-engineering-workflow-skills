# Copy Deck Template

Use this layout for `docs/ux/<feature-slug>/copy-deck.md`. It centralizes all user-facing strings, microcopy, validation messages, and system notices.

```markdown
---
artifact_id: UX-COPY-<feature-slug>
artifact_type: copy-deck
feature: <feature-slug>
artifact_status: draft
validity: current
owner_role: UI/UX
---

# <Feature> — Copy Deck

## 1. Tone and Voice Guidelines
- **Clarity:** Direct, concise, action-oriented. Avoid technical jargon or raw error codes in user messages.
- **Supportive:** For errors, explain what happened and offer a direct recovery action.

## 2. Global System Messages
| Key | Default String | Context / Trigger | Variables |
|---|---|---|---|
| `common.network_offline` | You are currently offline. Showing saved data. | Connection drops | None |
| `common.retry_button` | Try Again | Retryable request failure | None |
| `common.cancel_button` | Cancel | Dismiss modal / action | None |

## 3. Screen-Specific Copy

### S01: `<Screen Name>`
| Key | Type (Label/Button/Error/Empty) | Default Copy | Notes / Constraints |
|---|---|---|---|
| `s01.title` | Header | Manage Cards | Max 30 chars |
| `s01.btn.add` | Button | Add New Card | Primary CTA |
| `s01.empty.title` | Empty Title | No cards yet | Visible on zero-state |
| `s01.empty.desc` | Empty Body | Create your first card to start organizing your tasks. | Max 100 chars |
| `s01.error.load_failed` | Banner Error | Unable to load cards. Please check your connection and try again. | Actionable error |
```
