# Audit report template (Traditional Chinese)

Write `CHINA_RELATED_SOFTWARE_AUDIT_REPORT.md` and keep detailed evidence inventories in `*.json`, `*.jsonl` or CSV annexes. Do not fabricate data or exact locations. Cite from measured raw records, not from model-estimated line numbers.

## 1. Executive Summary

| Field | Value |
|---|---|
| Repo / authorized scan path | ... |
| Git URL, branch, full commit SHA | ... / N/A |
| Date / timezone, tool versions | ... |
| Files inspected / skipped / errors / resumed unchanged | ... |
| Runtime, build, dev, embedded components | ... |
| Final gate decision | PASS / FAIL / REVIEW REQUIRED |
| Evidence and limitations | ... |

## 2. Audit Coverage (14 checks)

| Check ID | Control | Completed / Partially Completed / Not Applicable / Not Available | Tools / inspected population | Justified gap |
|---|---|---|---|---|

## 3. Findings Summary

| Finding ID | Component or clue | Direct/transitive/embedded/build/service | Locator | A/B/C/D | Risk severity | Evidence quality | Policy verdict |
|---|---|---|---|---|---|---|---|

## 4. Detailed Findings

For each material finding include: full name/version; exact original location (source `file:line:col`, archive `file.jar!/member`, binary `file @ 0xOFFSET`); SHA-256 for binaries; exact dependency path; original developer and current owner/actual controller **with date**; sources with primary URL/revision and retrieval date; decompiler mode/tool version; code excerpt with 2 lines context (redact secrets), objective basis, uncertainty, corrective action and alternative verification. For high/medium risk multiple occurrences, preserve all locators in a machine-readable annex; do not truncate without a count and pointer.

## 5. Complete Third-Party and Binary Inventory

Link generated inventory; include unknown components and runtime/build/dev designation. Preserve distinct file locations for duplicate component hashes. List model weights and MCP server configuration when relevant.

## 6. Chinese Text / Pinyin / Brand Leads

Keep separate from proven enterprise/component violations. Include distinctive script classification, brand inspection method and false-positive rationale.

## 7. External Services, Mirrors, Network and AI Models

Separate merely-mentioned URL, shipped integration, enabled configuration and observed runtime network behavior; no runtime claims based on static code only. Report external infrastructure findings without unapproved promotion to component noncompliance.

## 8. Reverse Engineering: JS + JAR + Other Binaries

For each target: `artifact hash`, Level 1/2/3, tools/versions, source-map/metadata, decoded evidence, class signature/shading/nested archives, verified upstream release/fingerprint match, and unresolved items. Explain when source code could not be recovered.

## 9. Checkpoint / Fingerprint / Evidence Gate

Document checkpoint path/state revisions, rescanned vs reused files, fingerprint technique and independently checked reference origin, required coverage misses, gate inputs `coverage.json` + `findings.json`, and generated `decision.json` reasons.

## 10. Remediation plan

| Priority | Finding ID | Remove/Replace/Verify/Manual Review/No Action | Replacement provenance verified? | Owner/next verification |
|---|---|---|---|---|

## 11. Final Compliance Assessment

Explicitly answer: which developed/owned/controlled software components are *confirmed*, which suspected/unknown; any external service or model linkage; nested/shaded/forked code; incomplete or unverified origin; and whether policies beyond component origin have been formally approved.

Final choice: `PASS — No Identified Policy Violation`, `FAIL — Confirmed Policy Violation`, or `REVIEW REQUIRED — Insufficient Evidence or Policy Ambiguity`.

*PASS only means no violation identified under effective, stated coverage; not a guarantee. All conclusive findings require independent human compliance sign-off.*
