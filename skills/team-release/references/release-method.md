# Release Method (Station 4 — Gates, Scale-Aware HTML Living Manual & Closure)

## 1. The 5 Deterministic Release Gates

Before closing `<JIRA-KEY>` or deleting `.ai-workflow/active/<JIRA-KEY>.json`, `jira_sync.py close-story` enforces:
1. **Spec Gate:** `story.spec_status == "approved"`
2. **Sub-task Gate:** `len(story.tasks) > 0` and every `TASK-###` has `status == "completed"`
3. **Code Review & Commit Gate:** `story.review.status == "approved"` and `story.review.reviewed_commit == <candidate_commit>`
4. **QA Verification & Commit Gate:** `story.qa.status == "passed"`, `story.qa.tested_commit == <candidate_commit>`, 100% Story AC coverage, and zero non-passing test cases
5. **Zero Open Bugs Gate:** Every item in `story.bugs` (including Bugs linked in Jira Web UI) has `status in ("completed", "closed")`

---

## 2. Scale-Aware Living HTML Manual Protocol (`docs/manuals/<manual-name>/`)

Station 4 keeps product and service manuals accurate without creating per-Story document sprawl or running heavyweight full-manual builds on trivial fixes.

### 2.1 Fixed Output Directory & User-Provided Folder Naming

1. **HTML-Only Output Contract:**
   - Every manual lives in its own dedicated folder under `docs/manuals/<manual-name>/`, named after the manual name `<manual-name>` provided by the user (if the user supplies `foo.html`, normalize `<manual-name>` to `foo`):
     ```text
     docs/manuals/<manual-name>/
     ├── <manual-name>.html       # Standalone dark-sidebar HTML5 manual (UTF-8, lang="zh-Hant")
     ├── screenshots/             # Verified UI screenshots (*.png) & raw captures (*.raw.png)
     └── diagrams/                # Architecture & state-machine diagrams (*.png / *.svg / optional *.drawio)
     ```
   - Do **not** generate `.md`, `.pdf`, or `.docx` manual files unless the user explicitly asks for an extra format.
   - Each `<manual-name>/` folder isolates its own `./screenshots/` and `./diagrams/` assets so multiple manuals in one repository (for example, `docs/manuals/user-manual/` and `docs/manuals/admin-manual/`) never collide on asset filenames.

2. **Interactive `<manual-name>` Confirmation Rule:**
   - **Creating a new manual (`0 -> 1` or new audience/module manual):** Always ask the user for `<manual-name>` first (for example: *"請問這份手冊要命名為什麼？（例如 `user-manual` 或 `admin-guide`，將建立於 `docs/manuals/<名稱>/<名稱>.html`）"*).
   - **Major Feature when `docs/manuals/*/` already has manual folder(s):** List the existing `<manual-name>` folder(s) and confirm with the user whether to incrementally update an existing `docs/manuals/<existing>/<existing>.html` or create a new folder `docs/manuals/<new-name>/<new-name>.html`.

3. **Portable Relative Asset Paths (Critical Anti-Breakage Rule):**
   - Inside `docs/manuals/<manual-name>/<manual-name>.html`, every `<img src="...">` **MUST** use a repository-portable relative path (`./screenshots/01-overview.png`, `./diagrams/01-architecture.png`).
   - **Never** leave machine-local `file:///Users/...` URIs in `<manual-name>.html` (note: `user-manual-writing`'s `render_manual.py` emits `file://` URIs for PDF engines — if you invoke it as a helper, rewrite `file://.../docs/manuals/<manual-name>/` back to `./` and delete any temporary `.md` staging file afterward).
   - **Never** inline multi-megabyte Base64 screenshots into `<manual-name>.html`; external `./screenshots/*.png` keeps the HTML file lightweight in Git so future incremental AI edits remain fast and surgical.

---

### 2.2 Four-Scenario Scale Adaptation & Backend-Only Compatibility

Classify `<JIRA-KEY>` before touching `docs/manuals/`:

| Scenario | How to Identify | Required Action on `docs/manuals/<manual-name>/` |
|---|---|---|
| **① `0 -> 1` Greenfield / First Manual** | First functional release of a new product/service, or `docs/manuals/` has no manual covering this surface yet | Ask user for `<manual-name>`. Perform full 3-Layer Source Reconciliation, capture & verify all screenshots (`has_ui: true`) + architecture/lifecycle diagrams, and write the complete `docs/manuals/<manual-name>/<manual-name>.html`. |
| **② Major Feature (`新增大塊需求`)** | Adds new routes/pages, role-gated menu items, major Drawers/Modals/Tabs, or new public API modules | Confirm target `<manual-name>` folder (update existing vs. new folder). Incrementally reconcile **only** added/changed L1/L2/L3 items, capture & audit **only** new/changed screenshots, and surgically update affected `<section>` blocks, `<aside class="sidebar">` TOC links, and Appendix Coverage Matrix. |
| **③ Minor Enhancement (`局部小需求`)** | Tweaks existing fields, filters, button labels, validation rules, or minor UX details | Inspect existing `docs/manuals/<manual-name>/<manual-name>.html`. Surgically update the specific field row, step text, or screenshot **only if** existing manual content became inaccurate or contradictory; otherwise **Skip** (`0` manual edits). |
| **④ Bug Fix / Refactor (`修正問題 / 內部重構`)** | Fixes broken behavior back to intended spec, performance tuning, security hardening, or internal refactoring | **Default `Skip` (`0` manual edits)**. Touch an existing manual only if the fix intentionally changed documented user steps or Troubleshooting guidance. |

- **Backend / API-Only Projects (`change_surface.has_ui == false`):**
  - When a repository or manual covers pure backend/API/CLI functionality with no browser UI, keep the exact same `docs/manuals/<manual-name>/<manual-name>.html` dark-sidebar layout, skip browser UI screenshots cleanly, and replace the UI coverage matrix with an **API Endpoint / Auth / Permission & Error-Code Coverage Matrix** plus `./diagrams/` architecture and state-machine diagrams.

---

### 2.3 The 5 Distilled Pillars of `user-manual-writing`

Whenever creating or updating `docs/manuals/<manual-name>/<manual-name>.html`, enforce all 5 pillars distilled from `user-manual-writing`:

1. **Pillar 1 — 3-Layer Source Code Coverage Reconciliation (100% Zero-Omission & Verbatim Labels):**
   - Reconcile directly from source code before writing prose:
     - **L1 (Routes & Navigation):** Router definitions, sidebar/top-nav configs, and **role-gated conditional branches** (such as `isAdmin ? ... : null` or `super_admin` menus).
     - **L2 (In-Page Secondary Surfaces):** Tabs, Drawers, Modals, Dialogs, and multi-step Wizards inside each route.
     - **L3 (Global Shell & Verbatim UI Copy):** Header switchers, global shortcuts, help drawers, and i18n locale files (`zh-TW` / active UI language).
   - **Verbatim UI Label Rule:** Every page heading, button name, tab label, and field name in the manual must match the real UI / i18n dictionary character-for-character — never invent paraphrased labels.

2. **Pillar 2 — Four Screenshot Quality Gates (`has_ui: true`):**
   - **Gate 1 (Role-Matched & Error-Free):** Log in with an account holding the required role (`admin`, `operator`, etc.). Never capture `401`/`403`, `permission denied`, offline error banners, or unloaded skeleton spinners (except in dedicated Troubleshooting examples).
   - **Gate 2 (Expanded Secondary UI & Realistic Seed Data):** Genuinely click open Tabs, Drawers, and Modals and populate realistic non-secret demo records before capturing.
   - **Gate 3 (Multimodal Inspection & SHA-256 Deduplication):** Inspect every captured `.png` with multimodal file reading, verify file size is healthy (`>= 10 KB`), and confirm no two screenshots share the same SHA-256 hash (which signals an un-opened drawer/tab).
   - **Gate 4 (Visual Callouts & 3-Element `<figcaption>`):** For multi-control screens, annotate numbered highlight boxes (`① ② ③`, keeping `.raw.png` alongside annotated `.png` if using an annotation script) and include all 3 caption elements in `<figcaption>`:
     1. **用途 (Purpose):** What task or state this screen accomplishes.
     2. **視覺焦點 (Visual Focus):** Which region or callout badge (`① ② ③`) to inspect first.
     3. **狀態變化 (State Delta):** What changed compared with the prior state.

3. **Pillar 3 — Structured Architecture & Lifecycle Diagrams:**
   - Include system architecture and core resource lifecycle state-machine visuals under `./diagrams/` (using `drawio-skill` `.drawio` + 2x `.png` if available in the environment, or clean high-contrast inline `<svg>` / `.png`).
   - **Strict CJK Box Ban:** Never draw ASCII/Unicode `+---+` or `│...│` boxes containing CJK characters inside `<pre><code>` (mixed CJK/monospace widths misalign borders).

4. **Pillar 4 — Anti-Stub Section Rule (Zero Image-Only Sections):**
   - Every `<h3>` feature subsection that embeds a screenshot **MUST** also contain either a structured **Field / Control Specification `<table>`** (columns: 欄位/控制項名稱、用途、必填/格式限制、操作影響) or a **Numbered Operation `<ol>`**. Image-only stub sections are forbidden throughout the entire document.

5. **Pillar 5 — Dark-Sidebar CJK-Safe HTML Template:**
   - Use semantic UTF-8 HTML5 (`<!doctype html><html lang="zh-Hant">`) with:
     - Fixed dark left `<aside class="sidebar">` (`width: 280px`, `background: #0f172a`) linking to every `<h2>` / `<h3>` anchor, collapsing cleanly to single-column below `960px`.
     - Light content shell (`background: #f6f8fb`) with white `.doc-header` and structured `<table>` (`table-layout: fixed; overflow-wrap: anywhere`), `.callout` notes, and `<figure class="manual-figure">`.
     - CJK UI names wrapped in `<span class="inline-ui">按鈕名稱</span>` or `<strong>按鈕名稱</strong>` (never CJK inside Markdown/code backticks, and zero emojis).

#### Minimal Canonical HTML Shell (`docs/manuals/<manual-name>/<manual-name>.html`)

```html
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>產品操作手冊</title>
  <style>
    :root {
      --bg: #f6f8fb; --panel: #ffffff; --ink: #16202a; --muted: #5b6775;
      --line: #d7dfe8; --accent: #0f4c81; --accent-soft: #eaf3fb; --code-bg: #f2f5f8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; background: var(--bg); color: var(--ink); line-height: 1.72;
      font-family: "PingFang TC", "Noto Sans CJK TC", "Microsoft JhengHei", system-ui, sans-serif;
    }
    .layout { display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: 100vh; }
    .sidebar {
      position: sticky; top: 0; height: 100vh; overflow-y: auto;
      padding: 28px 20px; background: #0f172a; color: #e2e8f0; border-right: 1px solid #1e293b;
    }
    .sidebar-title { font-size: 13px; letter-spacing: .08em; text-transform: uppercase; color: #94a3b8; margin-bottom: 14px; }
    .toc-link { display: block; color: #e2e8f0; text-decoration: none; padding: 5px 0; font-size: 14px; }
    .toc-link.level-3 { padding-left: 14px; color: #cbd5e1; font-size: 13px; }
    .toc-link:hover { color: #7dd3fc; }
    .content { padding: 40px min(5vw, 56px) 72px; max-width: 1080px; }
    .doc-header, section.card {
      background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
      padding: 26px 28px; margin-bottom: 24px; box-shadow: 0 8px 24px rgba(15, 23, 42, .05);
    }
    h1 { margin: 0 0 8px; font-size: 30px; line-height: 1.28; }
    h2 { margin-top: 0; padding-bottom: 8px; border-bottom: 2px solid var(--line); font-size: 22px; }
    h3 { margin-top: 24px; font-size: 18px; color: #102a43; }
    .inline-ui {
      display: inline-block; padding: 1px 7px; border-radius: 6px;
      background: #eef4fa; border: 1px solid #cfdceb; color: #102a43; font-weight: 600; font-size: .94em;
    }
    code { font-family: "SFMono-Regular", Menlo, monospace; font-size: .9em; background: var(--code-bg); padding: 2px 6px; border-radius: 5px; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; margin: 16px 0 22px; font-size: 14px; }
    th, td { border: 1px solid var(--line); padding: 10px 12px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
    th { background: #eef3f8; font-weight: 700; }
    .manual-figure { margin: 20px 0 24px; padding: 14px; background: var(--panel); border: 1px solid var(--line); border-radius: 14px; }
    .manual-figure img { display: block; width: 100%; height: auto; border-radius: 8px; border: 1px solid #e5ebf2; }
    .manual-figure figcaption { margin-top: 10px; color: var(--muted); font-size: 13px; }
    .callout { margin: 16px 0; padding: 14px 16px; border-left: 4px solid var(--accent); background: var(--accent-soft); border-radius: 10px; }
    @media (max-width: 960px) { .layout { grid-template-columns: 1fr; } .sidebar { position: static; height: auto; } }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-title">目錄導覽</div>
      <nav><!-- <a class="toc-link level-2" href="#sec-1">1. 文件目的與適用角色</a> --></nav>
    </aside>
    <main class="content">
      <header class="doc-header">
        <h1>產品名稱 — 操作手冊</h1>
        <p>版本：v1.0.0 ｜ 最後同步需求：PROJ-101</p>
      </header>
      <!-- Structured <section class="card" id="sec-..."> blocks + Appendix Coverage Matrix -->
    </main>
  </div>
</body>
</html>
```

---

## 3. Living Governance Check & Ephemeral Scaffolding Cleanup

1. **System-Wide Living Assets Check:**
   - Verify whether new repository-wide conventions (`docs/shared/engineering-standards.md`), domain terms (`docs/shared/glossary.md`), architectural decisions (`docs/shared/adr/`), or HTML manuals (`docs/manuals/<manual-name>/<manual-name>.html`) were added or updated.
   - Never create `docs/release/<feature>/` markdown files.
2. **Candidate Commit Anchor & Ephemeral State Removal (`close-story`):**
   - Pass `--commit "<verified_candidate_commit>"` (matching `review.reviewed_commit` and `qa.tested_commit`) when invoking `jira_sync.py close-story` so documentation-only edits in `docs/manuals/` do not invalidate code review or QA commit alignment.
   - `jira_sync.py close-story --key <JIRA-KEY>` posts the permanent release summary to Jira, transitions the Jira Story to `Done`, and automatically deletes `.ai-workflow/active/<JIRA-KEY>.json`.
   - If `close-story` exits non-zero (`blocked: true`), keep `.ai-workflow/active/<JIRA-KEY>.json` intact and route back to the blocking station (`team-dev`, `team-review`, or `team-qa`).
