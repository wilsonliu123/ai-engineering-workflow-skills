# Execution Assignment Interaction

Use in `team-qa execution-assignment`. The human is choosing how a planned test will be performed. This step records an arrangement; actual test execution and results come later, outside this skill.

## Explain the case before asking

Read the current TC, existing decisions and any applicable human authorization. Reuse valid assignments and resume the next undecided case. Before opening a picker, show a short brief in the human's language:

- **Case:** TC ID and a plain-language title.
- **Action:** what the tester would do, from the planned steps.
- **Expected:** the concrete observations or approved thresholds, separating what should and should not appear when relevant.
- **Type / constraints:** explain the case's class/subtype and any relevant limitation in ordinary words. Name missing tool/environment facts as unknown rather than promising automation is ready.

Keep technical field names and taxonomy codes in the source/session. Mention `Proposed layer` only when it helps explain a real constraint or the human asks about it. If needed, explain `e2e` as checking the complete user flow; both tools and humans can perform that flow. It does not determine the execution assignment.

## Ask one short question

Picker heading: **執行方式**. Question pattern: **TC-003 要用哪種方式檢查？** Use the actual ID and the human's language. Keep the case description in the brief, so the picker question remains readable.

| Label | Plain-language meaning | Matrix value |
|---|---|---|
| 工具自動檢查 | 由工具執行操作並比對預期結果。 | `automated` |
| 人員手動檢查 | 由人操作，逐項確認預期結果。 | `manual` |
| 工具加人工檢查 | 部分檢查交給工具，其餘由人確認；分工需明確記錄。 | `hybrid` |
| 這次先不檢查 | 本輪不執行這則測試；需記錄延後原因。 | `deferred` |

Adapt descriptions to the actual case when supported by the plan. Present choices neutrally under the execution-matrix contract; an AI suggestion, a planning layer, or a picker's default highlight is not a human answer. Wait for the submitted human decision before assigning a value. A request for explanation leaves the case pending and does not advance the queue.

After a choice, record only that choice and applicable human/authorization references. Collect missing details one focused question at a time: intended tool/method for automated or hybrid when known, the tool/human split for hybrid, executing role for manual (QA or UI/UX), and a human-provided reason/reference for deferred. Reuse details already supplied; an unknown tool remains unknown. Selecting a mode does not start tests or prove a result.

## Keep batch rules separate

The normal case question covers only the current TC. Offer a bulk-assignment question only when the human requests it or supplies a standing rule; keep it separate from the case description and picker. Apply a clear authorization within its stated scope and record its source. If the execution type, scope or hybrid split is ambiguous, clarify that specific gap before assigning additional cases. Preserve decisions outside the authorization.

## Example brief

For a planned case that checks card-front visibility:

> **TC-003｜卡片正面的顯示內容**
>
> 操作：開啟單字卡片的正面。
> 應看到：單字、KK 音標、播放控制。
> 不應看到：釋義、例句。
> 類型：畫面功能檢查。
>
> 這一步只記錄檢查方式，還沒有開始測試。

Then show the short question and choices above. Use the actual TC's wording and constraints; add sound playback, visual-quality judgments or other checks only when the plan includes them.
