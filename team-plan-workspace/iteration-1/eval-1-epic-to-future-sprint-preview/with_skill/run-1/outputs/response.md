規劃預覽（尚未寫入 Jira）

**規劃範圍**
- Project：未提供；沿用目前 Jira 專案設定
- Board：42
- Epic：PROJ-EPIC-12（作為跨 Sprint 的產品容器）
- Sprint：2026-11 Sprint 3
- 期間：2026-11-16 至 2026-11-27
- Goal：完成 CSV 匯出

**候選項目**

| Issue | 類型 | 狀態 | Sprint 預覽 |
|---|---|---|---|
| PROJ-101 | Story | 未提供 | included |
| PROJ-102 | Story | 未提供 | included |
| PROJ-103 | Story | 未提供 | included |

Epic 保留為規劃脈絡；Sprint 成員使用 Epic 底下的 Story。沒有執行 Story 狀態、指派、Review、QA 或 Release 變更。

**執行狀態**
- dry_run: true
- applied: false
- 預覽 payload：`name=2026-11 Sprint 3`、`originBoardId=42`、`startDate=2026-11-16`、`endDate=2026-11-27`、`goal=完成 CSV 匯出`
- Jira 尚未建立 Sprint，也尚未移入任何 Issue。

**下一步**
使用者確認後，再以 `team-plan` 的 `commit` 動作套用；完成後各 Story 進入 `team-spec`，再依序進入既有開發、Review、QA 與 Release 流程。

**完成條件**
預覽完成的條件是使用者能看見上述 Sprint payload、3 個候選 Story，以及 `dry_run: true`。
