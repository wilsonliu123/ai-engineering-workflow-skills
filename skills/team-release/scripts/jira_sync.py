#!/usr/bin/env python3
"""
jira_sync.py — Unified Jira & Local Active State CLI for the 4-Station Small-Team Workflow.

Uses ONLY the Python 3 standard library.
Supports both:
  1. Live Jira REST API v3 mode (when JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN are set)
  2. Local / Offline mode (.ai-workflow/active/<JIRA-KEY>.json + formatted console output)

Subcommands:
  init            Initialize .ai-workflow/team-config.json, .gitignore, and extract engineering-standards.md
  status          Inspect active workflow state (or --epic <KEY> rollup) and recommend the exact next station/role
  epic-decompose  Preview (dry_run) or apply platform-neutral Epic-to-Story decomposition with wave ordering
  sync-story      Create/update/backfill Story spec (Scope, AC-*, conditional UI/API design, 抓大放小 decisions, optional risks/Q-###)
  create-subtasks Create development tasks (TASK-001..N) and Jira Sub-tasks
  complete-task   Mark a task or Bug completed with commit SHA, changed files, test evidence, and optional root-cause/solution
  handoff         Record review/station handoff with multi-role awareness (auto-consolidate vs @mention vs gated_by_deploy)
  promote-qa      Explicitly promote a review-approved Story to QA (in_qa) after QA environment deploy in gated_by_deploy mode
  record-qa       Record QA test plan (plan mode) or verification results (verify mode, incl. JUnit/JSON)
  create-bug      Create a linked Jira Bug ticket on test failure and route back to Developer
  triage-bug      Triage a Story or standalone Bug (Not a Bug / Won't Fix / Duplicate / Cannot Reproduce / Fixed) with human decision audit
  close-story     Verify all release gates, post release summary, transition Story to Done, delete active JSON
"""

import argparse
import base64
import datetime
import getpass
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path


STATE_SNAPSHOT_MARKER = "[ai-workflow-state]"
MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024  # 32 MiB per file (matches Qase API v1 & standard Jira limits)
_DENIED_ATTACHMENT_EXACT = {
    ".env",
    "credentials.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
_DENIED_ATTACHMENT_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx")


def _sanitize_multipart_filename(name: str) -> str:
    base = Path(str(name or "attachment.bin")).name
    cleaned = re.sub(r'[\r\n\x00-\x1f"\\/;]', "_", base).strip()
    return cleaned or "attachment.bin"


def _is_denied_secret_attachment(path_obj: Path) -> bool:
    lower_name = path_obj.name.lower()
    if lower_name in _DENIED_ATTACHMENT_EXACT or lower_name.startswith(".env."):
        return True
    if any(lower_name.endswith(sfx) for sfx in _DENIED_ATTACHMENT_SUFFIXES):
        return True
    return False


def validate_attachment_files(file_paths: list, repo_root: Path = None) -> list:
    """
    Validate and deduplicate attachment file paths before any remote Jira/Qase mutation.
    Raises ValueError on missing, directory, empty, oversized (>32 MiB), or secret/credential files.
    """
    validated = []
    seen_keys = set()
    for raw_p in (file_paths or []):
        s = str(raw_p or "").strip()
        if not s:
            continue
        p = Path(s)
        if not p.is_absolute() and repo_root is not None:
            p = (repo_root / p).resolve()
        else:
            p = p.resolve()
        if not p.exists():
            raise ValueError(f"Attachment file does not exist: {s}")
        if not p.is_file():
            raise ValueError(f"Attachment path is not a regular file: {s}")
        if _is_denied_secret_attachment(p):
            raise ValueError(f"Refusing to upload sensitive credential/secret file as attachment: {p.name}")
        size = p.stat().st_size
        if size <= 0:
            raise ValueError(f"Attachment file is empty (0 bytes): {p}")
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"Attachment file exceeds 32 MiB limit ({size} bytes): {p.name}")
        data_bytes = p.read_bytes()
        sha = hashlib.sha256(data_bytes).hexdigest()
        safe_name = _sanitize_multipart_filename(p.name)
        dedup_key = (sha, safe_name)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        guessed_mime, _ = mimetypes.guess_type(safe_name)
        mime_type = guessed_mime or "application/octet-stream"
        validated.append({
            "local_path": str(p),
            "filename": safe_name,
            "size": len(data_bytes),
            "mime_type": mime_type,
            "sha256": sha,
            "bytes": data_bytes,
        })
    return validated


def _build_multipart_form_data(validated_files: list, field_name: str = "file") -> tuple:
    """Encode one or more validated file dicts into an RFC 7578 multipart/form-data payload."""
    boundary = f"----AIWorkflowBoundary{uuid.uuid4().hex}"
    chunks = []
    for vf in validated_files:
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        disp = f'Content-Disposition: form-data; name="{field_name}"; filename="{vf["filename"]}"\r\n'
        chunks.append(disp.encode("utf-8"))
        chunks.append(f'Content-Type: {vf["mime_type"]}\r\n\r\n'.encode("ascii"))
        chunks.append(vf["bytes"])
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return boundary, b"".join(chunks)

VALID_COMMIT_POLICIES = ("confirm_before_commit", "auto_on_test_pass")
VALID_QA_HANDOFF_MODES = ("immediate", "gated_by_deploy")
BUG_TRIAGE_RESOLUTION_ALIASES = {
    "not-a-bug": ("not-a-bug", "Not a Bug"),
    "not a bug": ("not-a-bug", "Not a Bug"),
    "not_a_bug": ("not-a-bug", "Not a Bug"),
    "notabug": ("not-a-bug", "Not a Bug"),
    "wont-fix": ("wont-fix", "Won't Fix"),
    "won't fix": ("wont-fix", "Won't Fix"),
    "wont fix": ("wont-fix", "Won't Fix"),
    "wont_fix": ("wont-fix", "Won't Fix"),
    "won't-fix": ("wont-fix", "Won't Fix"),
    "wontfix": ("wont-fix", "Won't Fix"),
    "duplicate": ("duplicate", "Duplicate"),
    "cannot-reproduce": ("cannot-reproduce", "Cannot Reproduce"),
    "cannot reproduce": ("cannot-reproduce", "Cannot Reproduce"),
    "cannot_reproduce": ("cannot-reproduce", "Cannot Reproduce"),
    "cannotreproduce": ("cannot-reproduce", "Cannot Reproduce"),
    "fixed": ("fixed", "Fixed"),
}
NON_CODE_FIX_RESOLUTIONS = {"not-a-bug", "wont-fix", "duplicate", "cannot-reproduce"}

DEFAULT_TEAM_CONFIG = {
    "version": "1.0",
    "project_key": "PROJ",
    "jira_board_id": "",
    "epic_link_field": "",
    "jira_base_url": "",
    "language": "zh-TW",
    "sync_mode": "auto",
    "notification_channel": "jira",
    "commit_policy": "confirm_before_commit",
    "commit_message_pattern": "",
    "qa_handoff_mode": "immediate",
    "delivery_pipeline": {
        "integration_branch": "",
        "production_branch": "",
        "environments": {},
        "jira_deployment_sync": False,
    },
    "roles": {
        "pm": {"name": "PM Owner", "email": "", "jira_account_id": ""},
        "tech_lead": {"name": "Tech Lead", "email": "", "jira_account_id": ""},
        "developer": {"name": "Developer", "email": "", "jira_account_id": ""},
        "reviewer": {"name": "Reviewer", "email": "", "jira_account_id": ""},
        "qa": {"name": "QA Owner", "email": "", "jira_account_id": ""},
        "release_manager": {"name": "Release Manager", "email": "", "jira_account_id": ""},
    },
    "status_mapping": {
        "todo": "To Do",
        "in_progress": "In Progress",
        "in_review": "In Review",
        "dev_done": "",
        "in_qa": "In QA",
        "ready_for_release": "Ready for Release",
        "done": "Done",
    },
    "issue_type_mapping": {
        "story": "Story",
        "subtask": "Sub-task",
        "bug": "Bug",
    },
    "qa_test_backend": "",
    "qase": {
        "project_code": "",
        "base_url": "https://api.qase.io/v1",
        "app_url": "https://app.qase.io",
        "jira_integration_type": "jira-cloud",
    },
}

DEFAULT_STANDARDS_TEMPLATE = """---
artifact_id: ENG-STANDARDS
artifact_type: engineering-standards
validity: current
owner_role: Tech Lead
---
# Shared Engineering Standards (工程標準規範)

本文件為專案唯一權威的工程規範來源（Single Source of Truth）。`AGENTS.md` 僅保留索引連結，不重複維護以下規則。

## 1. Architecture & Code Conventions (架構與程式規範)
- 遵循專案既有模組邊界與命名慣例，避免引入未經確認的平行抽象層。
- 跨模組或難以逆轉的架構決策須記錄於 `docs/shared/adr/ADR-<NNN>-<slug>.md`。

## 2. Testing & Verification Evidence (測試與驗證要求)
- 每個開發任務（`TASK-*`）與缺陷修復（`BUG-*`）完成前，必須執行對應的自動化測試並記錄真實執行結果。
- 測試案例識別碼（`TC-001..N`）必須對應驗收條件（`AC-001..N`），並區分為三種執行類型：
  - `automated`：本機 AI 可直接執行的單元與整合測試（如 `pytest`、`vitest`、`go test`）。
  - `external`：需依賴外部工具、CI/Staging 環境或第三方沙盒的測試（如 Playwright/Cypress E2E、Postman/Newman、JMeter、實機測試）。
  - `manual`：需人工進行視覺體驗、探索式或跨系統核對的測試。
- 嚴格防假通過（Anti-False-Pass）：`--all-pass` 僅能將 `automated` 類型的測試案例標記為 `pass`；`external` 與 `manual` 測試案例必須匯入外部測試報告（`--junit-xml` / `--results-json`）或顯式回填結果（`--result TC-ID=pass`），未執行或缺少證據不得視為通過。

## 3. Security & Data Hygiene (安全與資料保護)
- 嚴禁在程式碼、設定檔、日誌或版本控制中寫入 API Token、密碼或私鑰。
- 所有外部輸入與跨邊界 API 必須執行格式驗證、權限檢查（AuthN/AuthZ）與輸出跳脫處理。

## 4. Git Commit & PR Standards (Git 提交與 PR 規範)
- 本節為專案 Git Commit 格式、語言、前綴/Emoji 與 Pull Request 慣例的唯一真相來源（Single Source of Truth；若尚未收斂，fallback 參照專案根目錄 `AGENTS.md`）。工作流技能不硬編碼單一格式，請在此明確維護貴團隊的 Commit 模板與 PR 規範。
- **雙重 Jira 票號溯源（Dual Jira Key Traceability）**：無論專案採用何種 Commit 模板，只要該任務已建立 Jira Sub-task（`jira_subtask_key`），Commit 訊息必須同時包含父級 Story Key（`<STORY-KEY>`）與子任務 Key（`<SUBTASK-KEY>`）——若與專案標題格式相容則置於標題，否則置於 Commit 內文或 trailer——以確保 Jira Development Panel 能同時關聯至父 Story 與子任務。
- **提交確認門禁（`commit_policy`）**：預設為 `.ai-workflow/team-config.json` 的 `"commit_policy": "confirm_before_commit"`（完成實作與本地測試後先停下回報並等待使用者確認再執行 `git commit` 與 `complete-task`）；僅在使用者明確授權或團隊顯式設定 `"auto_on_test_pass"` 時才自動提交。可選填 `"commit_message_pattern"`（正規表達式）啟用 `complete-task` 的非阻塞格式檢查提醒。
"""

AGENTS_STANDARDS_POINTER_MARKER = "<!-- ai-workflow:engineering-standards-link -->"
AGENTS_STANDARDS_POINTER_BLOCK = f"""{AGENTS_STANDARDS_POINTER_MARKER}
## Engineering Standards (工程標準規範)

專案所有的編碼、架構、測試、安全與 Git Commit/PR 規範已統一收斂至唯一權威來源：
- 請參閱 [`docs/shared/engineering-standards.md`](docs/shared/engineering-standards.md)
- 請勿在本文件重複維護上述工程細則，以免規範分歧。
{AGENTS_STANDARDS_POINTER_MARKER}
"""


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def load_json_arg(val: str):
    """Load JSON from an @file path, an existing file path, or a raw JSON string."""
    if val is None or not str(val).strip():
        return None
    s = str(val).strip()
    if s.startswith("@"):
        file_path = s[1:].strip()
        if not file_path:
            raise ValueError("Empty JSON file path after '@'.")
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            raise ValueError(f"JSON file not found: {file_path}")
        return json.loads(p.read_text(encoding="utf-8"))
    p = Path(s)
    try:
        is_file = p.exists() and p.is_file()
    except OSError:
        is_file = False
    if is_file:
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(s)


def get_workflow_dir(repo_root: Path) -> Path:
    return repo_root / ".ai-workflow"


def get_active_dir(repo_root: Path) -> Path:
    return get_workflow_dir(repo_root) / "active"


def get_config_path(repo_root: Path) -> Path:
    return get_workflow_dir(repo_root) / "team-config.json"


def get_credentials_path(repo_root: Path) -> Path:
    return get_workflow_dir(repo_root) / "credentials.json"


def get_dotenv_path(repo_root: Path) -> Path:
    return get_workflow_dir(repo_root) / ".env"


def is_zh_tw(config: dict) -> bool:
    """Return True unless the project explicitly configured English ('en') output."""
    lang = str((config or {}).get("language") or "zh-TW").strip().lower()
    return lang not in ("en", "en-us", "en-gb", "english")


def _read_credentials_from_dir(wf_dir: Path) -> dict:
    creds = {"role_credentials": {}, "member_credentials": {}}
    cred_file = wf_dir / "credentials.json"
    if cred_file.exists():
        try:
            raw = json.loads(cred_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in ("jira_base_url", "jira_email", "jira_api_token", "qase_api_token"):
                    if raw.get(k):
                        creds[k] = str(raw[k]).strip()
                raw_rc = raw.get("role_credentials")
                if isinstance(raw_rc, dict):
                    for r_key, r_val in raw_rc.items():
                        if isinstance(r_val, dict):
                            r_em = str(r_val.get("jira_email") or r_val.get("email") or "").strip()
                            r_tk = str(r_val.get("jira_api_token") or r_val.get("api_token") or r_val.get("token") or "").strip()
                            if r_em or r_tk:
                                creds["role_credentials"][str(r_key).strip()] = {
                                    "jira_email": r_em,
                                    "jira_api_token": r_tk,
                                }
                raw_mc = raw.get("member_credentials")
                if isinstance(raw_mc, dict):
                    for m_key, m_val in raw_mc.items():
                        if isinstance(m_val, dict):
                            m_em = str(m_val.get("jira_email") or m_val.get("email") or m_key or "").strip()
                            m_tk = str(m_val.get("jira_api_token") or m_val.get("api_token") or m_val.get("token") or "").strip()
                            if m_em and m_tk:
                                creds["member_credentials"][m_em.lower()] = {
                                    "jira_email": m_em,
                                    "jira_api_token": m_tk,
                                }
        except Exception:
            pass
    env_file = wf_dir / ".env"
    if env_file.exists():
        try:
            env_map = {
                "JIRA_BASE_URL": "jira_base_url",
                "JIRA_EMAIL": "jira_email",
                "JIRA_API_TOKEN": "jira_api_token",
                "QASE_API_TOKEN": "qase_api_token",
            }
            for line in env_file.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[len("export "):].strip()
                v = v.strip().strip("'").strip('"')
                if k in env_map and v and not creds.get(env_map[k]):
                    creds[env_map[k]] = v
        except Exception:
            pass
    return creds


def load_jira_credentials(repo_root: Path = None) -> dict:
    """Load local gitignored Jira credentials (.ai-workflow/credentials.json or .env), with worktree fallback."""
    if repo_root is None:
        return {"role_credentials": {}, "member_credentials": {}}
    creds = _read_credentials_from_dir(get_workflow_dir(repo_root))
    main_root = _resolve_main_worktree_root(repo_root)
    if main_root:
        main_creds = _read_credentials_from_dir(get_workflow_dir(main_root))
        for k in ("jira_base_url", "jira_email", "jira_api_token", "qase_api_token"):
            if main_creds.get(k) and not creds.get(k):
                creds[k] = main_creds[k]
        for r_key, r_val in (main_creds.get("role_credentials") or {}).items():
            if r_key not in creds["role_credentials"]:
                creds["role_credentials"][r_key] = r_val
        for m_key, m_val in (main_creds.get("member_credentials") or {}).items():
            if m_key not in creds["member_credentials"]:
                creds["member_credentials"][m_key] = m_val
    return creds


def save_jira_credentials(
    repo_root: Path,
    jira_url: str = None,
    jira_email: str = None,
    jira_token: str = None,
    role_credentials: dict = None,
    qase_token: str = None,
    member_credentials: dict = None,
) -> Path:
    """Save Jira/Qase credentials (including per-role and per-member tokens) to gitignored .ai-workflow/credentials.json with 0600 permissions."""
    wf_dir = get_workflow_dir(repo_root)
    wf_dir.mkdir(parents=True, exist_ok=True)
    cred_path = get_credentials_path(repo_root)
    existing = _read_credentials_from_dir(wf_dir)
    if jira_url is not None and jira_url.strip():
        existing["jira_base_url"] = jira_url.strip().rstrip("/")
    if jira_email is not None:
        existing["jira_email"] = jira_email.strip()
    if jira_token is not None:
        existing["jira_api_token"] = jira_token.strip()
    if jira_email and jira_token and jira_email.strip() and jira_token.strip():
        existing.setdefault("member_credentials", {})[jira_email.strip().lower()] = {
            "jira_email": jira_email.strip(),
            "jira_api_token": jira_token.strip(),
        }
    if qase_token is not None and qase_token.strip():
        existing["qase_api_token"] = qase_token.strip()
    if isinstance(role_credentials, dict):
        rc_map = existing.setdefault("role_credentials", {})
        mc_map = existing.setdefault("member_credentials", {})
        for r_key, r_val in role_credentials.items():
            norm_role = str(r_key).strip()
            if isinstance(r_val, str) and r_val.strip():
                prev = rc_map.get(norm_role) or {}
                rc_map[norm_role] = {
                    "jira_email": prev.get("jira_email", ""),
                    "jira_api_token": r_val.strip(),
                }
            elif isinstance(r_val, dict):
                prev = rc_map.get(norm_role) or {}
                r_em = str(r_val.get("jira_email") or r_val.get("email") or prev.get("jira_email") or "").strip()
                r_tk = str(r_val.get("jira_api_token") or r_val.get("api_token") or r_val.get("token") or prev.get("jira_api_token") or "").strip()
                if r_em or r_tk:
                    rc_map[norm_role] = {
                        "jira_email": r_em,
                        "jira_api_token": r_tk,
                    }
                if r_em and r_tk:
                    mc_map[r_em.lower()] = {
                        "jira_email": r_em,
                        "jira_api_token": r_tk,
                    }
    if isinstance(member_credentials, dict):
        mc_map = existing.setdefault("member_credentials", {})
        for m_key, m_val in member_credentials.items():
            if isinstance(m_val, dict):
                m_em = str(m_val.get("jira_email") or m_val.get("email") or m_key or "").strip()
                m_tk = str(m_val.get("jira_api_token") or m_val.get("api_token") or m_val.get("token") or "").strip()
                if m_em and m_tk:
                    mc_map[m_em.lower()] = {"jira_email": m_em, "jira_api_token": m_tk}
    existing["updated_at"] = now_iso()
    cred_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        cred_path.chmod(0o600)
    except Exception:
        pass
    return cred_path


def get_effective_commit_policy(config: dict) -> str:
    """Return effective commit_policy, safely defaulting to 'confirm_before_commit'."""
    raw = str((config or {}).get("commit_policy") or "").strip()
    if raw in VALID_COMMIT_POLICIES:
        return raw
    return "confirm_before_commit"


def get_effective_qa_handoff_mode(config: dict) -> str:
    """Return effective qa_handoff_mode, safely falling back to 'immediate' on invalid/non-string values."""
    raw = (config or {}).get("qa_handoff_mode")
    if isinstance(raw, str):
        norm = raw.strip().lower()
        if norm in VALID_QA_HANDOFF_MODES:
            return norm
    return "immediate"


def normalize_triage_resolution(raw: str) -> tuple:
    """Normalize triage resolution aliases case-insensitively into (slug, display_name)."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            "Unknown triage resolution ''. Expected one of: not-a-bug, wont-fix, duplicate, cannot-reproduce, fixed."
        )
    norm = re.sub(r"\s+", " ", raw.strip().replace("\u2019", "'").replace("`", "'")).lower()
    if norm in BUG_TRIAGE_RESOLUTION_ALIASES:
        return BUG_TRIAGE_RESOLUTION_ALIASES[norm]
    raise ValueError(
        f"Unknown triage resolution '{raw}'. Expected one of: not-a-bug, wont-fix, duplicate, cannot-reproduce, fixed."
    )


def _clean_optional_text(val) -> str:
    """Normalize optional text argument safely to '' when None/whitespace (never storing literal 'None')."""
    if val is None:
        return ""
    return str(val).strip()


def _clean_single_line_text(val) -> str:
    """Sanitize optional single-line metadata (deploy_ref, tag, environment) cleanly without control chars or 'None'."""
    if not isinstance(val, str):
        return ""
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", val).strip()
    return cleaned


def _parse_strict_bool(val, default: bool = False) -> bool:
    """Strictly parse boolean values so strings like 'false', '0', 'no', '' never evaluate truthy."""
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    if isinstance(val, str):
        norm = val.strip().lower()
        if norm in ("true", "1", "yes", "y", "on"):
            return True
        if norm in ("false", "0", "no", "n", "off", ""):
            return False
        return default
    return default


def _sanitize_git_ref(val) -> str:
    """Sanitize a git branch/ref name; reject non-string or unsafe/whitespace-containing refs to ''."""
    if not isinstance(val, str):
        return ""
    s = val.strip()
    if not s:
        return ""
    if any(ord(c) < 0x20 or ord(c) == 0x7F or c.isspace() for c in s):
        return ""
    if (
        s.startswith("-")
        or s.startswith("/")
        or s.endswith("/")
        or s.startswith(".")
        or s.endswith(".")
        or ".." in s
        or "//" in s
        or "@{" in s
        or s.endswith(".lock")
        or any(ch in s for ch in ("~", "^", ":", "?", "*", "[", "\\"))
    ):
        return ""
    return s


def _sanitize_tag_prefix(val) -> str:
    """Sanitize a git tag prefix (e.g. 'qa-', 'uat-', 'v'); reject non-string or unsafe refs to ''."""
    if not isinstance(val, str):
        return ""
    s = val.strip()
    if not s:
        return ""
    if any(ord(c) < 0x20 or ord(c) == 0x7F or c.isspace() for c in s):
        return ""
    if (
        s.startswith("-")
        or s.startswith("/")
        or s.startswith(".")
        or ".." in s
        or "//" in s
        or "@{" in s
        or any(ch in s for ch in ("~", "^", ":", "?", "*", "[", "\\"))
    ):
        return ""
    return s


def _sanitize_delivery_pipeline(raw) -> dict:
    """Normalize optional delivery_pipeline config block with strict types and safe empty defaults."""
    default = {
        "integration_branch": "",
        "production_branch": "",
        "environments": {},
        "jira_deployment_sync": False,
    }
    if not isinstance(raw, dict):
        return default
    integration_branch = _sanitize_git_ref(raw.get("integration_branch"))
    production_branch = _sanitize_git_ref(raw.get("production_branch"))
    jira_deployment_sync = _parse_strict_bool(raw.get("jira_deployment_sync"), default=False)
    environments = {}
    raw_envs = raw.get("environments")
    if isinstance(raw_envs, dict):
        for env_k, env_v in raw_envs.items():
            if not isinstance(env_k, str):
                continue
            clean_env_k = env_k.strip().lower()
            if not clean_env_k or not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", clean_env_k):
                continue
            if not isinstance(env_v, dict):
                continue
            raw_trig = env_v.get("trigger")
            trig = raw_trig.strip().lower() if isinstance(raw_trig, str) else ""
            if trig not in ("branch", "tag"):
                trig = ""
            ref = _sanitize_git_ref(env_v.get("ref"))
            prefix = _sanitize_tag_prefix(env_v.get("prefix"))
            raw_tier = env_v.get("deployment_tier")
            tier = raw_tier.strip().lower() if isinstance(raw_tier, str) else ""
            if any(ord(c) < 0x20 or ord(c) == 0x7F for c in tier):
                tier = ""
            environments[clean_env_k] = {
                "trigger": trig,
                "ref": ref,
                "prefix": prefix,
                "deployment_tier": tier,
            }
    return {
        "integration_branch": integration_branch,
        "production_branch": production_branch,
        "environments": environments,
        "jira_deployment_sync": jira_deployment_sync,
    }


def _extract_regex_tag_prefix(raw_pattern: str) -> str:
    """Extract a clean literal tag prefix from a regex like '/^qa-.*/' or glob like 'qa-*'."""
    if not isinstance(raw_pattern, str):
        return ""
    s = raw_pattern.strip().strip("'").strip('"')
    if s.startswith("/"):
        s = s[1:]
    if "/" in s:
        s = s.split("/", 1)[0]
    if s.startswith("^"):
        s = s[1:]
    # Cut at first regex/glob metacharacter (handling '.*' / '.+' / '.?' before stripping)
    m_meta = re.search(r"[*+?(\[{\\|$\]]", s)
    if m_meta:
        cut_idx = m_meta.start()
        if cut_idx > 0 and s[cut_idx - 1] == "." and s[cut_idx] in ("*", "+", "?"):
            cut_idx -= 1
        s = s[:cut_idx]
    return _sanitize_tag_prefix(s)


_CI_CHECK_COMMAND_RE = re.compile(
    r"^(?:bundle\s+exec\s+)?("
    r"swiftlint\b|"
    r"fastlane\s+(?:test|scan|lint|check)\b|"
    r"xcodebuild\s+.*\btest\b|"
    r"(?:python3?|uv\s+run|poetry\s+run)\s+(?:-m\s+)?(?:pytest|unittest|mypy|ruff|flake8|black\b.*--check)|"
    r"pytest\b|ruff\b|flake8\b|mypy\b|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:test|lint|typecheck|check)\b|"
    r"(?:npm|pnpm|yarn|bun)\s+run\s+(?:test[\w:-]*|lint[\w:-]*|typecheck[\w:-]*|check[\w:-]*|build[\w:-]*)|"
    r"(?:npx\s+)?(?:vitest|jest|eslint|tsc|playwright\s+test|cypress\s+run)\b|"
    r"go\s+(?:test|vet)\b|golangci-lint\b|"
    r"cargo\s+(?:test|clippy|check)\b|"
    r"(?:\./gradlew|gradle|mvn)\s+(?:test|check|lint)\b"
    r")",
    re.IGNORECASE,
)


def _extract_ci_check_command(raw_line: str) -> str:
    """Extract a concise local lint/test/build command hint from a CI script/run line, or '' if not a check."""
    if not isinstance(raw_line, str):
        return ""
    s = raw_line.strip()
    if not s or s.startswith("#"):
        return ""
    if s.startswith("- "):
        s = s[2:].strip()
    if s.startswith("run:"):
        s = s[4:].strip()
    if s in ("|", ">", "|-", ">-"):
        return ""
    if (len(s) >= 2) and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1].strip()
    # Strip trailing inline shell comment
    s = re.sub(r"\s+#.*$", "", s).strip()
    if not s or len(s) > 160:
        return ""
    if _CI_CHECK_COMMAND_RE.match(s):
        return s
    return ""


def _extract_yaml_environment_names(lines: list) -> list:
    """Indentation-aware extraction of YAML `environment:` values (inline or nested `name:`)."""
    found = []
    n = len(lines)
    i = 0
    while i < n:
        ln = lines[i]
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        m_env = re.match(r"^environment\s*:\s*(.*)$", stripped)
        if m_env:
            env_indent = len(ln) - len(ln.lstrip(" "))
            rest = re.sub(r"\s+#.*$", "", m_env.group(1)).strip().strip("'").strip('"')
            if rest and not rest.startswith("{"):
                found.append(rest.lower())
            else:
                j = i + 1
                while j < n:
                    sub = lines[j]
                    sub_s = sub.strip()
                    if not sub_s or sub_s.startswith("#"):
                        j += 1
                        continue
                    sub_indent = len(sub) - len(sub.lstrip(" "))
                    if sub_indent <= env_indent:
                        break
                    m_nm = re.match(r"^name\s*:\s*['\"]?([A-Za-z0-9._/-]+)['\"]?", sub_s)
                    if m_nm:
                        found.append(m_nm.group(1).strip().lower())
                        break
                    j += 1
                i = j
                continue
        i += 1
    return found


def _read_ci_text_safely(path: Path, max_bytes: int = 2 * 1024 * 1024):
    """Safely read a UTF-8 text CI file; return None on binary, invalid UTF-8, unreadable, or oversized files."""
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            return None
        raw = path.read_bytes()
        if b"\x00" in raw:
            return None
        return raw.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None


def _detect_ci_delivery_pipeline(repo_root: Path) -> dict:
    """
    Pure-stdlib CI/CD auto-detector for .gitlab-ci.yml and .github/workflows/*.yml|*.yaml.
    Never crashes on binary, invalid UTF-8, unreadable, or malformed files.
    """
    detected_sources = []
    integration_branch = ""
    production_branch = ""
    environments = {}
    jira_deployment_sync = False
    detected_ci_checks = []
    seen_checks = set()

    def _add_check(cmd_str: str) -> None:
        clean_cmd = _extract_ci_check_command(cmd_str)
        if clean_cmd and clean_cmd not in seen_checks:
            seen_checks.add(clean_cmd)
            detected_ci_checks.append(clean_cmd)

    def _record_env(env_key: str, trigger: str, ref: str, prefix: str, tier: str) -> None:
        clean_k = (env_key or "").strip().lower()
        if not clean_k:
            return
        prev = environments.get(clean_k) or {"trigger": "", "ref": "", "prefix": "", "deployment_tier": ""}
        environments[clean_k] = {
            "trigger": trigger or prev["trigger"],
            "ref": ref or prev["ref"],
            "prefix": prefix or prev["prefix"],
            "deployment_tier": tier or prev["deployment_tier"],
        }

    # 1. Inspect .gitlab-ci.yml
    gitlab_path = repo_root / ".gitlab-ci.yml"
    gitlab_text = _read_ci_text_safely(gitlab_path)
    if gitlab_text is not None:
        detected_sources.append(".gitlab-ci.yml")
        lines = gitlab_text.splitlines()
        if re.search(r"(?m)^\s*deployment_tier\s*:", gitlab_text):
            jira_deployment_sync = True

        # Split into top-level blocks
        non_job_keys = {
            "stages", "variables", "default", "workflow", "include",
            "cache", "services", "image", "before_script", "after_script",
        }
        blocks = []
        cur_name = None
        cur_lines = []
        for ln in lines:
            if not ln.strip() or ln.lstrip().startswith("#"):
                if cur_name is not None:
                    cur_lines.append(ln)
                continue
            if not ln[0].isspace():
                m_top = re.match(r"^([A-Za-z0-9_.-]+)\s*:", ln)
                if cur_name is not None:
                    blocks.append((cur_name, cur_lines))
                if m_top:
                    cur_name = m_top.group(1)
                    cur_lines = [ln]
                else:
                    cur_name = None
                    cur_lines = []
            elif cur_name is not None:
                cur_lines.append(ln)
        if cur_name is not None:
            blocks.append((cur_name, cur_lines))

        for job_name, j_lines in blocks:
            if job_name in non_job_keys:
                continue
            j_text = "\n".join(j_lines)

            # Extract script lines for lint/test hints
            in_script = False
            script_indent = 0
            for ln in j_lines[1:]:
                stripped = ln.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                indent = len(ln) - len(ln.lstrip(" "))
                if re.match(r"^(?:script|before_script)\s*:", stripped):
                    in_script = True
                    script_indent = indent
                    after_col = stripped.split(":", 1)[1].strip()
                    if after_col:
                        _add_check(after_col)
                    continue
                if in_script:
                    if indent <= script_indent and not stripped.startswith("-"):
                        in_script = False
                    else:
                        _add_check(stripped)

            if job_name.startswith("."):
                continue

            # Extract deployment_tier
            m_tier = re.search(
                r"(?mi)^\s*deployment_tier\s*:\s*['\"]?(development|testing|staging|production)['\"]?",
                j_text,
            )
            tier = m_tier.group(1).lower() if m_tier else ""

            # Extract environment name (indentation-aware)
            job_env_names = _extract_yaml_environment_names(j_lines[1:])
            env_name = job_env_names[0] if job_env_names else ""

            # Extract branch / tag rules
            branch_ref = ""
            tag_prefix = ""
            m_br = re.search(
                r"\$CI_COMMIT_(?:BRANCH|REF_NAME)\s*==\s*['\"]([A-Za-z0-9._/-]+)['\"]",
                j_text,
            )
            if m_br:
                branch_ref = _sanitize_git_ref(m_br.group(1))

            m_tag = re.search(r"\$CI_COMMIT_TAG\s*=~\s*['\"]?(/\^?[^\s'\"]+)", j_text)
            if m_tag:
                tag_prefix = _extract_regex_tag_prefix(m_tag.group(1))

            if not branch_ref and not tag_prefix:
                # Fallback to legacy `only:` list
                m_only = re.search(r"(?ms)^\s*only\s*:\s*\n((?:\s+-\s+.*\n?)+)", j_text)
                if m_only:
                    for item_ln in m_only.group(1).splitlines():
                        item_val = item_ln.strip().lstrip("-").strip().strip("'").strip('"')
                        if item_val.startswith("/"):
                            cand_pfx = _extract_regex_tag_prefix(item_val)
                            if cand_pfx and not tag_prefix:
                                tag_prefix = cand_pfx
                        elif item_val.lower() in ("release", "develop", "development", "main", "master", "qa", "uat", "staging", "prod", "production"):
                            if not branch_ref:
                                branch_ref = _sanitize_git_ref(item_val)

            # Determine environment key
            jl = job_name.lower()
            env_key = ""
            if tier == "testing" or env_name in ("qa", "testing") or "qa" in jl or tag_prefix.lower().startswith("qa"):
                env_key = "qa"
                tier = tier or "testing"
            elif env_name == "uat" or "uat" in jl or tag_prefix.lower().startswith("uat"):
                env_key = "uat"
                tier = tier or "staging"
            elif tier == "staging" or env_name == "staging":
                env_key = "uat" if ("uat" in jl or "uat" in env_name) else "staging"
                tier = tier or "staging"
            elif tier == "development" or env_name in ("dev", "development"):
                env_key = "dev"
                tier = tier or "development"
            elif tier == "production" or env_name in ("prod", "production"):
                env_key = "prod"
                tier = tier or "production"

            if env_key:
                if tag_prefix:
                    _record_env(env_key, "tag", "", tag_prefix, tier)
                elif branch_ref:
                    _record_env(env_key, "branch", branch_ref, "", tier)
                elif tier:
                    _record_env(env_key, "", "", "", tier)

            if (tier == "development" or env_key == "dev") and branch_ref and not integration_branch:
                integration_branch = branch_ref
            if (tier == "production" or env_key == "prod") and branch_ref and not production_branch:
                production_branch = branch_ref

    # 2. Inspect .github/workflows/*.yml and *.yaml
    gh_wf_dir = repo_root / ".github" / "workflows"
    if gh_wf_dir.is_dir():
        try:
            wf_files = sorted(
                [p for p in gh_wf_dir.iterdir() if p.is_file() and p.suffix.lower() in (".yml", ".yaml")],
                key=lambda p: p.name,
            )
        except OSError:
            wf_files = []
        for wf_path in wf_files:
            wf_text = _read_ci_text_safely(wf_path)
            if wf_text is None:
                continue
            detected_sources.append(f".github/workflows/{wf_path.name}")
            if re.search(r"(?m)^\s*deployment_tier\s*:", wf_text):
                jira_deployment_sync = True

            # Extract run: commands for lint/test checks
            wf_lines = wf_text.splitlines()
            idx_l = 0
            while idx_l < len(wf_lines):
                ln = wf_lines[idx_l]
                stripped = ln.strip()
                m_run = re.match(r"^(?:-\s+)?run\s*:\s*(.*)$", stripped)
                if m_run:
                    rest = m_run.group(1).strip()
                    if rest in ("|", ">", "|-", ">-"):
                        run_indent = len(ln) - len(ln.lstrip(" "))
                        idx_l += 1
                        while idx_l < len(wf_lines):
                            sub_ln = wf_lines[idx_l]
                            if not sub_ln.strip():
                                idx_l += 1
                                continue
                            sub_indent = len(sub_ln) - len(sub_ln.lstrip(" "))
                            if sub_indent <= run_indent:
                                break
                            _add_check(sub_ln)
                            idx_l += 1
                        continue
                    else:
                        _add_check(rest)
                idx_l += 1

            # Extract push branches and tags from workflow header
            push_branches = []
            push_tags = []
            in_push = False
            push_indent = -1
            in_list_kind = ""  # 'branches' or 'tags'
            list_indent = -1
            for ln in wf_lines:
                stripped = ln.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                indent = len(ln) - len(ln.lstrip(" "))
                if indent == 0 and re.match(r"^jobs\s*:", stripped):
                    break
                if re.match(r"^push\s*:", stripped):
                    in_push = True
                    push_indent = indent
                    in_list_kind = ""
                    continue
                if in_push:
                    if indent <= push_indent:
                        in_push = False
                        in_list_kind = ""
                        continue
                    m_br_inline = re.match(r"^branches\s*:\s*\[([^\]]*)\]", stripped)
                    if m_br_inline:
                        for tok in m_br_inline.group(1).split(","):
                            b = _sanitize_git_ref(tok.strip().strip("'").strip('"'))
                            if b:
                                push_branches.append(b)
                        in_list_kind = ""
                        continue
                    m_tg_inline = re.match(r"^tags\s*:\s*\[([^\]]*)\]", stripped)
                    if m_tg_inline:
                        for tok in m_tg_inline.group(1).split(","):
                            t = tok.strip().strip("'").strip('"')
                            if t:
                                push_tags.append(t)
                        in_list_kind = ""
                        continue
                    if re.match(r"^branches\s*:\s*$", stripped):
                        in_list_kind = "branches"
                        list_indent = indent
                        continue
                    if re.match(r"^tags\s*:\s*$", stripped):
                        in_list_kind = "tags"
                        list_indent = indent
                        continue
                    if in_list_kind:
                        if indent <= list_indent and not stripped.startswith("-"):
                            in_list_kind = ""
                        elif stripped.startswith("-"):
                            val = stripped[1:].strip().strip("'").strip('"')
                            if in_list_kind == "branches":
                                b = _sanitize_git_ref(val)
                                if b:
                                    push_branches.append(b)
                            elif in_list_kind == "tags" and val:
                                push_tags.append(val)

            # Extract job environment names (indentation-aware)
            wf_envs = _extract_yaml_environment_names(wf_lines)

            # Map tags to qa/uat environments
            for t_pat in push_tags:
                pfx = _extract_regex_tag_prefix(t_pat)
                if not pfx:
                    continue
                pl = pfx.lower()
                if pl.startswith("qa"):
                    _record_env("qa", "tag", "", pfx, "testing")
                elif pl.startswith("uat"):
                    _record_env("uat", "tag", "", pfx, "staging")

            # Map branch + environment patterns
            for br in push_branches:
                brl = br.lower()
                if brl in ("qa", "testing") or (len(push_branches) == 1 and any(e in ("qa", "testing") for e in wf_envs)):
                    _record_env("qa", "branch", br, "", "testing")
                elif brl == "uat" or (len(push_branches) == 1 and "uat" in wf_envs):
                    _record_env("uat", "branch", br, "", "staging")
                elif brl == "staging" or (len(push_branches) == 1 and "staging" in wf_envs and "uat" not in wf_envs):
                    _record_env("staging", "branch", br, "", "staging")
                elif brl in ("develop", "development", "release") and (
                    any(e in ("dev", "development") for e in wf_envs) or not wf_envs
                ):
                    if not integration_branch and (any(e in ("dev", "development") for e in wf_envs) or brl in ("develop", "release")):
                        integration_branch = br
                    if any(e in ("dev", "development") for e in wf_envs):
                        _record_env("dev", "branch", br, "", "development")
                elif brl in ("main", "master") and any(e in ("prod", "production") for e in wf_envs):
                    if not production_branch:
                        production_branch = br
                    _record_env("prod", "branch", br, "", "production")

    pipeline = _sanitize_delivery_pipeline({
        "integration_branch": integration_branch,
        "production_branch": production_branch,
        "environments": environments,
        "jira_deployment_sync": jira_deployment_sync,
    })

    has_dedicated_qa_trigger = False
    for ek, ev in (pipeline.get("environments") or {}).items():
        if ek == "qa" or ev.get("deployment_tier") == "testing":
            if (ev.get("trigger") == "tag" and ev.get("prefix")) or (ev.get("trigger") == "branch" and ev.get("ref")):
                has_dedicated_qa_trigger = True
                break

    return {
        "detected_sources": detected_sources,
        "delivery_pipeline": pipeline,
        "detected_ci_checks": detected_ci_checks,
        "suggested_qa_handoff_mode": "gated_by_deploy" if has_dedicated_qa_trigger else "immediate",
    }


def _git_ref_exists(repo_root: Path, ref_name: str) -> bool:
    """Return True if ref_name exists in the local git repository."""
    if not ref_name or ref_name.startswith("-"):
        return False
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref_name],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def _is_git_repo(repo_root: Path) -> bool:
    """Return True if repo_root is inside a Git working tree."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def _resolve_base_branch(repo_root: Path, config: dict = None) -> str:
    """
    Resolve preferred base branch for branching/diffing.
    Uses delivery_pipeline.integration_branch when configured (falling back gracefully if the ref does not
    exist locally yet in a Git repo), otherwise preserves main/master fallback behavior.
    """
    dp = _sanitize_delivery_pipeline((config or {}).get("delivery_pipeline"))
    configured = dp.get("integration_branch") or ""
    if not _is_git_repo(repo_root):
        return configured or "main"

    if configured:
        if _git_ref_exists(repo_root, f"refs/heads/{configured}"):
            return configured
        if _git_ref_exists(repo_root, f"refs/remotes/origin/{configured}"):
            return f"origin/{configured}"

    for cand, full_ref in (
        ("main", "refs/heads/main"),
        ("origin/main", "refs/remotes/origin/main"),
        ("master", "refs/heads/master"),
        ("origin/master", "refs/remotes/origin/master"),
    ):
        if _git_ref_exists(repo_root, full_ref):
            return cand

    return configured or "main"


def _build_qa_deploy_hint(config: dict, jira_key: str) -> str:
    """Build natural-language QA deploy guidance when delivery_pipeline configures a QA environment (never auto-tags)."""
    dp = _sanitize_delivery_pipeline((config or {}).get("delivery_pipeline"))
    envs = dp.get("environments") or {}
    qa_env = envs.get("qa")
    if not isinstance(qa_env, dict) or not qa_env.get("trigger"):
        qa_env = None
        for ev in envs.values():
            if isinstance(ev, dict) and ev.get("deployment_tier") == "testing" and ev.get("trigger"):
                qa_env = ev
                break
    if not qa_env:
        return ""

    int_branch = dp.get("integration_branch") or ""
    jira_sync_enabled = bool(dp.get("jira_deployment_sync"))
    trig = qa_env.get("trigger")

    if trig == "tag":
        prefix = qa_env.get("prefix") or "qa-"
        merge_clause = f"merge into `{int_branch}` and " if int_branch else ""
        sync_clause = (
            "wait for Jira `deployment_tier: testing` auto-reconciliation (`status`/`pull`) or "
            if (jira_sync_enabled or qa_env.get("deployment_tier") == "testing")
            else ""
        )
        return (
            f"After human confirmation, {merge_clause}create a `{prefix}*` Git tag to trigger QA deployment "
            f"(AI will never auto-merge or auto-tag), then {sync_clause}"
            f"run `jira_sync.py promote-qa --key {jira_key} --deploy-ref \"<{prefix}...>\"`."
        )
    if trig == "branch":
        ref = qa_env.get("ref") or "qa"
        return (
            f"After human confirmation, promote/merge into the `{ref}` branch to trigger QA deployment "
            f"(AI will never auto-merge), then sync Jira `in_qa` or run "
            f"`jira_sync.py promote-qa --key {jira_key} --deploy-ref \"{ref}\"`."
        )
    return ""


def _build_release_checklist_reminders(config: dict) -> list:
    """
    Build lightweight, strictly non-blocking pre-close release reminders for multi-stage pipelines
    (UAT environment or production_branch distinct from integration_branch).
    Returns [] when neither is configured.
    """
    dp = _sanitize_delivery_pipeline((config or {}).get("delivery_pipeline"))
    envs = dp.get("environments") or {}
    int_branch = dp.get("integration_branch") or ""
    prod_branch = dp.get("production_branch") or ""
    reminders = []

    uat_env = envs.get("uat")
    if not isinstance(uat_env, dict):
        uat_env = None
        for ev in envs.values():
            if isinstance(ev, dict) and ev.get("deployment_tier") == "staging":
                uat_env = ev
                break
    if isinstance(uat_env, dict) and any(uat_env.get(k) for k in ("trigger", "ref", "prefix", "deployment_tier")):
        if uat_env.get("trigger") == "tag" and uat_env.get("prefix"):
            reminders.append(
                f"Reminder (non-blocking): Confirm UAT verification (tag `{uat_env['prefix']}*` / staging) "
                f"has human sign-off before closing the Story (AI will not auto-tag)."
            )
        elif uat_env.get("trigger") == "branch" and uat_env.get("ref"):
            reminders.append(
                f"Reminder (non-blocking): Confirm UAT verification on branch `{uat_env['ref']}` "
                f"has human sign-off before closing the Story."
            )
        else:
            reminders.append(
                "Reminder (non-blocking): Confirm UAT / staging verification has human sign-off before closing the Story."
            )

    if prod_branch and prod_branch != int_branch:
        if int_branch:
            reminders.append(
                f"Reminder (non-blocking): Confirm `{int_branch}` has been merged into production branch "
                f"`{prod_branch}` with human approval before/alongside closing the Story (AI will not auto-merge)."
            )
        else:
            reminders.append(
                f"Reminder (non-blocking): Confirm changes are merged into production branch `{prod_branch}` "
                f"with human approval before/alongside closing the Story (AI will not auto-merge)."
            )
    return reminders


def _extract_jira_resolution(fields: dict) -> str:
    """Safely extract remote Jira fields.resolution.name handling None or non-dict values."""
    if not isinstance(fields, dict):
        return ""
    res_obj = fields.get("resolution")
    if isinstance(res_obj, dict):
        return str(res_obj.get("name") or "").strip()
    if isinstance(res_obj, str):
        return res_obj.strip()
    return ""


def _is_jira_status_in_qa(status_name: str, config: dict = None) -> bool:
    s = (status_name or "").strip().lower()
    if not s:
        return False
    in_qa_mapped = str(((config or {}).get("status_mapping") or {}).get("in_qa") or "In QA").strip().lower()
    return bool(in_qa_mapped and s == in_qa_mapped)


def _is_awaiting_qa_deploy(story: dict, config: dict) -> bool:
    """Return True when Story is in gated_by_deploy mode, review is approved, and QA deploy has not yet unblocked."""
    if get_effective_qa_handoff_mode(config) != "gated_by_deploy":
        return False
    review = story.get("review") or {}
    if review.get("status") != "approved":
        return False
    candidate_commit = story.get("candidate_commit") or review.get("reviewed_commit") or ""
    gate = story.get("qa_deploy_gate") or {}
    gate_is_awaiting_current = (
        isinstance(gate, dict)
        and gate.get("status") == "awaiting_qa_deploy"
        and (not gate.get("commit") or not candidate_commit or gate.get("commit") == candidate_commit)
    )
    qa = story.get("qa") or {}
    if (
        qa.get("status") == "passed"
        and (not qa.get("tested_commit") or not candidate_commit or qa.get("tested_commit") == candidate_commit)
        and not gate_is_awaiting_current
    ):
        return False
    if (
        isinstance(gate, dict)
        and gate.get("status") == "deployed"
        and (not gate.get("commit") or not candidate_commit or gate.get("commit") == candidate_commit)
    ):
        return False
    if (
        _is_jira_status_in_qa(story.get("jira_status", ""), config)
        and not (
            isinstance(gate, dict)
            and gate.get("status") == "awaiting_qa_deploy"
            and gate.get("awaiting_in_qa_cycle")
        )
    ):
        return False
    return True


def _is_tc_waiver_valid(tc: dict, story: dict) -> bool:
    """Verify that a 'waived' or 'not-applicable' TC carries complete audit metadata referencing a closed/triaged bug."""
    if not isinstance(tc, dict):
        return False
    res = str(tc.get("result") or "").strip().lower()
    if res not in ("waived", "not-applicable"):
        return False
    triage_res = str(tc.get("triage_resolution") or "").strip().lower()
    waived_by = str(tc.get("waived_by") or "").strip()
    waived_reason = str(tc.get("waived_reason") or "").strip()
    waived_bug_id = str(tc.get("waived_bug_id") or "").strip().upper()
    if triage_res not in NON_CODE_FIX_RESOLUTIONS:
        return False
    if not waived_by or not waived_reason or not waived_bug_id:
        return False
    if story.get("issue_kind") == "standalone_bug":
        if waived_bug_id == str(story.get("jira_key") or "").strip().upper() and story.get("triage_closed"):
            st_triage = story.get("triage")
            if isinstance(st_triage, dict):
                st_triage_res = str(st_triage.get("resolution") or "").strip().lower()
                if st_triage_res in NON_CODE_FIX_RESOLUTIONS and triage_res == st_triage_res:
                    return True
    for b in (story.get("bugs") or []):
        if not isinstance(b, dict):
            continue
        b_keys = {
            str(b.get("bug_key") or "").strip().upper(),
            str(b.get("id") or "").strip().upper(),
        }
        if waived_bug_id in b_keys:
            if b.get("status") not in ("completed", "closed"):
                return False
            b_triage = b.get("triage")
            if isinstance(b_triage, dict):
                b_triage_res = str(b_triage.get("resolution") or "").strip().lower()
                if b_triage_res in NON_CODE_FIX_RESOLUTIONS and triage_res == b_triage_res:
                    return True
            return False
    return False


def _is_tc_satisfied(tc: dict, story: dict) -> bool:
    """Return True if a TC is 'pass' or a validly audited 'waived'/'not-applicable' disposition."""
    if not isinstance(tc, dict):
        return False
    if tc.get("result") == "pass":
        return True
    return _is_tc_waiver_valid(tc, story)


def load_team_config(repo_root: Path) -> dict:
    cfg_path = get_config_path(repo_root)
    if not cfg_path.exists():
        return json.loads(json.dumps(DEFAULT_TEAM_CONFIG))
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        merged = json.loads(json.dumps(DEFAULT_TEAM_CONFIG))
        merged.update(data)
        merged["commit_policy"] = get_effective_commit_policy(merged)
        merged["qa_handoff_mode"] = get_effective_qa_handoff_mode(merged)
        merged["delivery_pipeline"] = _sanitize_delivery_pipeline(data.get("delivery_pipeline"))
        raw_pat = merged.get("commit_message_pattern")
        merged["commit_message_pattern"] = "" if raw_pat is None else raw_pat
        raw_elf = merged.get("epic_link_field")
        merged["epic_link_field"] = raw_elf.strip() if isinstance(raw_elf, str) else ""
        merged["roles"] = {**DEFAULT_TEAM_CONFIG["roles"], **data.get("roles", {})}
        raw_sm = data.get("status_mapping") if isinstance(data.get("status_mapping"), dict) else {}
        sanitized_sm = {}
        for sm_key, sm_default in DEFAULT_TEAM_CONFIG["status_mapping"].items():
            raw_v = raw_sm.get(sm_key)
            if isinstance(raw_v, str):
                cleaned_v = raw_v.strip()
                sanitized_sm[sm_key] = cleaned_v if (sm_key == "dev_done" or cleaned_v) else sm_default
            else:
                sanitized_sm[sm_key] = sm_default
        for extra_k, extra_v in raw_sm.items():
            if extra_k not in sanitized_sm and isinstance(extra_v, str):
                sanitized_sm[extra_k] = extra_v.strip()
        merged["status_mapping"] = sanitized_sm
        merged["issue_type_mapping"] = {
            **DEFAULT_TEAM_CONFIG["issue_type_mapping"],
            **data.get("issue_type_mapping", {}),
        }
        merged["qase"] = {
            **DEFAULT_TEAM_CONFIG["qase"],
            **(data.get("qase") or {}),
        }
        return merged
    except Exception as exc:
        raise ValueError(f"Invalid team-config.json at {cfg_path}: {exc}") from exc


def _read_git_commit_message(repo_root: Path, commit_sha: str) -> str:
    """Safely read full commit message for commit_sha via git log; returns '' on error/unavailable."""
    sha = str(commit_sha or "").strip()
    if not sha or sha == "unknown" or sha.startswith("-"):
        return ""
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%B", sha, "--"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def _contains_jira_key(text: str, key: str) -> bool:
    """Return True if text contains exact Jira issue key bounded against alphanumeric/hyphen neighbors."""
    if not text or not key:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9-]){re.escape(key)}(?![A-Za-z0-9-])", text, re.IGNORECASE))


def _validate_commit_message(commit_msg: str, story_key: str, subtask_keys: list, config: dict) -> list:
    """
    Perform strictly non-blocking commit message validation against optional team-config.json
    `commit_message_pattern` regex and Jira Story / Sub-task key traceability.
    Always returns a list of warning strings (empty list when clean or unavailable).
    """
    warnings = []
    raw_pattern = (config or {}).get("commit_message_pattern")
    pattern = ""
    compiled = None
    if raw_pattern is not None and not isinstance(raw_pattern, str):
        warnings.append(
            f"Invalid commit_message_pattern in team-config.json: expected string regex, got {type(raw_pattern).__name__}"
        )
    elif isinstance(raw_pattern, str):
        pattern = raw_pattern.strip()
        if pattern:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                warnings.append(
                    f"Invalid commit_message_pattern regex in team-config.json ({pattern!r}): {exc}"
                )

    msg = str(commit_msg or "").strip()
    if not msg:
        return warnings

    msg_norm = msg.replace("\r\n", "\n").replace("\r", "\n")
    first_line = msg_norm.splitlines()[0] if msg_norm.splitlines() else ""

    if compiled is not None:
        expects_multiline = (
            "\\n" in pattern
            or "\n" in pattern
            or "(?s)" in pattern
            or "(?m)" in pattern
            or bool(compiled.flags & (re.MULTILINE | re.DOTALL))
            or bool(re.search(r"\(\?[aiLmsux]*[ms]", pattern))
        )
        if expects_multiline:
            matched = bool(compiled.search(msg_norm))
        else:
            matched = bool(compiled.search(first_line))
            if not matched and not pattern.lstrip().startswith(("^", r"\A")):
                matched = bool(compiled.search(msg_norm))
        if not matched:
            warnings.append(
                f"Commit message '{first_line}' does not match configured commit_message_pattern "
                f"({pattern!r}). Check docs/shared/engineering-standards.md §4 (or AGENTS.md)."
            )

    norm_story = str(story_key or "").strip()
    if norm_story and not _contains_jira_key(msg_norm, norm_story):
        warnings.append(
            f"Commit message is missing parent Jira Story key '{norm_story}'. "
            f"Include '{norm_story}' for Jira Development Panel traceability."
        )

    seen_sub = set()
    for raw_sub in subtask_keys or []:
        sub_key = str(raw_sub or "").strip()
        if not sub_key or sub_key.lower() in seen_sub:
            continue
        seen_sub.add(sub_key.lower())
        if not _contains_jira_key(msg_norm, sub_key):
            warnings.append(
                f"Commit message is missing Jira Sub-task key '{sub_key}'. "
                f"Include both '{norm_story or '<STORY-KEY>'}' and '{sub_key}' so Jira Development Panel links commits to this Sub-task."
            )

    return warnings


def _detect_git_head_commit(repo_root: Path) -> str:
    """Auto-detect current short Git HEAD commit SHA in repo_root."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def _detect_git_changed_files(repo_root: Path, commit_sha: str = "", config: dict = None) -> list:
    """Auto-detect changed files from the given commit SHA, working tree diff, or base-branch range."""
    if commit_sha and commit_sha not in ("HEAD", "unknown"):
        try:
            res = subprocess.run(
                ["git", "show", "--name-only", "--pretty=format:", commit_sha],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                files = [f.strip() for f in res.stdout.splitlines() if f.strip()]
                if files:
                    return files
        except Exception:
            pass
    base_branch = _resolve_base_branch(repo_root, config)
    cmds = [
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "diff", "--name-only", "--cached"],
    ]
    if base_branch and _git_ref_exists(repo_root, base_branch):
        cmds.append(["git", "diff", "--name-only", f"{base_branch}...HEAD"])
    for cmd in cmds:
        try:
            res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                files = [f.strip() for f in res.stdout.splitlines() if f.strip()]
                if files:
                    return files
        except Exception:
            pass
    return []


def _resolve_main_worktree_root(repo_root: Path):
    """If repo_root is a secondary Git worktree, return the main repository root Path."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            common_dir = Path(res.stdout.strip())
            if not common_dir.is_absolute():
                common_dir = (repo_root / common_dir).resolve()
            else:
                common_dir = common_dir.resolve()
            if common_dir.name == ".git":
                main_root = common_dir.parent.resolve()
                if main_root != repo_root.resolve() and main_root.exists():
                    return main_root
    except Exception:
        pass
    return None


def _normalize_member_dict(role: str, entry) -> dict:
    if isinstance(entry, str):
        return {
            "role": role,
            "name": entry.strip() or role,
            "email": "",
            "jira_account_id": "",
            "specialty": "",
        }
    if isinstance(entry, dict):
        return {
            "role": role,
            "name": str(entry.get("name") or role).strip(),
            "email": str(entry.get("email") or "").strip(),
            "jira_account_id": str(entry.get("jira_account_id") or "").strip(),
            "specialty": str(entry.get("specialty") or "").strip(),
        }
    return {"role": role, "name": role, "email": "", "jira_account_id": "", "specialty": ""}


def _is_member_claimed(role_key: str, member: dict) -> bool:
    if not isinstance(member, dict):
        return False
    def_name = (DEFAULT_TEAM_CONFIG["roles"].get(role_key) or {}).get("name", role_key)
    placeholders = {"", def_name.lower(), role_key.lower(), "unclaimed", "待認領", "tbd"}
    email = (member.get("email") or "").strip()
    acct = (member.get("jira_account_id") or "").strip()
    name = (member.get("name") or "").strip()
    if email or acct:
        return True
    return name.lower() not in placeholders


def resolve_role_members(config: dict, role: str) -> list:
    """Return a non-empty list of normalized member dicts for the given role (supports 1-to-N pools)."""
    roles = (config or {}).get("roles", {})
    entry = roles.get(role)
    if isinstance(entry, list):
        items = [_normalize_member_dict(role, m) for m in entry if isinstance(m, (dict, str))]
        return items or [_normalize_member_dict(role, {})]
    if isinstance(entry, dict):
        raw_members = entry.get("members")
        if isinstance(raw_members, list) and raw_members:
            items = [_normalize_member_dict(role, m) for m in raw_members if isinstance(m, (dict, str))]
            if items:
                return items
        return [_normalize_member_dict(role, entry)]
    if isinstance(entry, str):
        return [_normalize_member_dict(role, entry)]
    return [_normalize_member_dict(role, {})]


def resolve_role_person(config: dict, role: str) -> dict:
    return dict(resolve_role_members(config, role)[0])


def find_member_in_role(config: dict, role: str, selector: str):
    """Find a member inside a role pool by accountId, email, name, or specialty."""
    sel = (selector or "").strip()
    if not sel:
        return None
    sel_lower = sel.lower()
    for m in resolve_role_members(config, role):
        if not _is_member_claimed(role, m):
            continue
        if m.get("jira_account_id") and m["jira_account_id"] == sel:
            return dict(m)
        if m.get("email") and m["email"].lower() == sel_lower:
            return dict(m)
        if m.get("name") and m["name"].lower() == sel_lower:
            return dict(m)
        if m.get("specialty") and m["specialty"].lower() == sel_lower:
            return dict(m)
    return None


def find_member_across_roles(config: dict, account_id: str = "", email: str = "", preferred_role: str = ""):
    """Find (role_key, member_dict) across configured roles matching account_id or email."""
    acct = (account_id or "").strip()
    em = (email or "").strip().lower()
    if not acct and not em:
        return None, None
    role_keys = list((config.get("roles") or {}).keys())
    if preferred_role and preferred_role in role_keys:
        role_keys = [preferred_role] + [r for r in role_keys if r != preferred_role]
    for rk in role_keys:
        for m in resolve_role_members(config, rk):
            if not _is_member_claimed(rk, m):
                continue
            if acct and m.get("jira_account_id") == acct:
                return rk, dict(m)
            if em and (m.get("email") or "").lower() == em:
                return rk, dict(m)
    return None, None


def resolve_story_role_person(config: dict, story: dict = None, role: str = "", selector: str = None) -> dict:
    """Resolve the effective person for `role` on a specific `story` (honoring --to-assignee & sticky role_owners)."""
    if selector:
        found = find_member_in_role(config, role, selector)
        if found:
            return found
    if isinstance(story, dict):
        ro = (story.get("role_owners") or {}).get(role)
        if isinstance(ro, dict) and _is_member_claimed(role, ro):
            refreshed = find_member_in_role(
                config,
                role,
                ro.get("jira_account_id") or ro.get("email") or ro.get("name") or "",
            )
            return refreshed or _normalize_member_dict(role, ro)
        if role == "qa":
            qa_em = ((story.get("qa") or {}).get("owner_email") or "").strip()
            if qa_em:
                found_qa = find_member_in_role(config, "qa", qa_em)
                if found_qa:
                    return found_qa
    return resolve_role_person(config, role)


def serialize_member_for_config(member: dict, default_role: str = "") -> dict:
    out = {
        "name": str(member.get("name") or default_role).strip(),
        "email": str(member.get("email") or "").strip(),
        "jira_account_id": str(member.get("jira_account_id") or "").strip(),
    }
    spec = str(member.get("specialty") or "").strip()
    if spec:
        out["specialty"] = spec
    return out


def serialize_role_entry(role: str, members: list) -> dict:
    cleaned = [serialize_member_for_config(m, role) for m in (members or [])]
    if not cleaned:
        def_name = (DEFAULT_TEAM_CONFIG["roles"].get(role) or {}).get("name", role)
        return {"name": def_name, "email": "", "jira_account_id": ""}
    if len(cleaned) == 1:
        return cleaned[0]
    primary = dict(cleaned[0])
    primary["members"] = cleaned
    return primary


def format_person_badge(role: str, person: dict) -> str:
    name = (person or {}).get("name") or role
    email = (person or {}).get("email") or ""
    spec = (person or {}).get("specialty") or ""
    role_tag = f"`{role}:{spec}`" if spec else f"`{role}`"
    if email:
        return f"{role_tag} — **{name}** ({email})"
    return f"{role_tag} — **{name}**"


def format_role_badge(config: dict, role: str, story: dict = None, person: dict = None) -> str:
    """Format a clear role/owner attribution string for Jira comments."""
    p = person or resolve_story_role_person(config, story, role)
    return format_person_badge(role, p)


def validate_jira_key(raw_key: str) -> str:
    """Normalize and validate Jira Story key, preventing path traversal."""
    key = (raw_key or "").strip().upper()
    if not key or ".." in key or "/" in key or "\\" in key or not re.match(r"^[A-Z][A-Z0-9_]*-[A-Z0-9_-]+$", key):
        raise ValueError(f"Invalid Jira Story key '{raw_key}'. Expected format like 'PROJ-101'.")
    return key


def is_same_person(person_a: dict, person_b: dict) -> bool:
    """Return True if two role entries refer to the same human."""
    if not person_a or not person_b:
        return False
    acct_a = person_a.get("jira_account_id", "").strip()
    acct_b = person_b.get("jira_account_id", "").strip()
    if acct_a and acct_b:
        return acct_a == acct_b
    email_a = person_a.get("email", "").strip().lower()
    email_b = person_b.get("email", "").strip().lower()
    if email_a and email_b:
        return email_a == email_b
    name_a = person_a.get("name", "").strip().lower()
    name_b = person_b.get("name", "").strip().lower()
    default_names = {v["name"].lower() for v in DEFAULT_TEAM_CONFIG["roles"].values()}
    if name_a and name_b and name_a not in default_names and name_b not in default_names:
        return name_a == name_b
    return False


def resolve_effective_reviewer(config: dict, story: dict = None) -> tuple:
    """
    Resolve the effective reviewer role and person.
    - When `reviewer` has multiple claimed members, prefer a peer reviewer who did NOT author the Story's tasks.
    - Otherwise if developer and reviewer map to the same person and `independent_reviewer` is configured
      as a distinct person, route to `independent_reviewer` per governance §3.1.
    Returns (effective_role, reviewer_person_dict, self_reviewed_with_ai).
    """
    story_devs = []
    if isinstance(story, dict):
        for t in story.get("tasks") or []:
            t_ass = t.get("assignee")
            if isinstance(t_ass, dict) and _is_member_claimed("developer", t_ass):
                story_devs.append(t_ass)
        if not story_devs:
            ro_dev = (story.get("role_owners") or {}).get("developer")
            if isinstance(ro_dev, dict) and _is_member_claimed("developer", ro_dev):
                story_devs.append(ro_dev)
    if not story_devs:
        story_devs = [resolve_role_person(config, "developer")]

    claimed_reviewers = [m for m in resolve_role_members(config, "reviewer") if _is_member_claimed("reviewer", m)]
    for cand in claimed_reviewers:
        if not any(is_same_person(d, cand) for d in story_devs):
            return ("reviewer", cand, False)

    rev_person = claimed_reviewers[0] if claimed_reviewers else resolve_role_person(config, "reviewer")
    if any(is_same_person(d, rev_person) for d in story_devs):
        ind_entry = (config.get("roles") or {}).get("independent_reviewer")
        if ind_entry:
            ind_person = resolve_role_person(config, "independent_reviewer")
            has_identity = bool(
                ind_person.get("jira_account_id")
                or ind_person.get("email")
                or (ind_person.get("name") and ind_person["name"] != "independent_reviewer")
            )
            if has_identity and not any(is_same_person(d, ind_person) for d in story_devs):
                return ("independent_reviewer", ind_person, False)
        return ("reviewer", rev_person, True)
    return ("reviewer", rev_person, False)


def resolve_acting_person(config: dict, role: str, client=None, story: dict = None) -> dict:
    """Resolve which specific member of `role` is currently executing on this machine."""
    if client is not None and getattr(client, "email", ""):
        found = find_member_in_role(config, role, client.email)
        if found:
            return found
    return resolve_story_role_person(config, story, role)


class JiraClient:
    """Dependency-free Jira REST API v3 client with per-role & per-member credential resolution and local/offline fallback."""

    def __init__(
        self,
        config: dict,
        offline: bool = False,
        repo_root: Path = None,
        role: str = None,
        member_email: str = None,
    ):
        self.config = config
        self.role = (role or "").strip()
        local_creds = load_jira_credentials(repo_root) if repo_root else {"role_credentials": {}, "member_credentials": {}}
        self.base_url = (
            os.environ.get("JIRA_BASE_URL")
            or config.get("jira_base_url")
            or local_creds.get("jira_base_url")
            or ""
        ).rstrip("/")
        if self.base_url:
            scheme = urllib.parse.urlparse(self.base_url).scheme.lower()
            if scheme not in ("http", "https"):
                raise ValueError(f"Invalid jira_base_url scheme '{scheme}'. Only http and https are allowed.")

        role_creds_map = local_creds.get("role_credentials") or {}
        member_creds_map = local_creds.get("member_credentials") or {}
        role_cred = role_creds_map.get(self.role) if self.role else None
        role_person = resolve_role_person(config, self.role) if self.role else {}

        env_role_email = os.environ.get(f"JIRA_EMAIL_{self.role.upper()}", "").strip() if self.role else ""
        env_role_token = os.environ.get(f"JIRA_API_TOKEN_{self.role.upper()}", "").strip() if self.role else ""

        default_email = (os.environ.get("JIRA_EMAIL") or local_creds.get("jira_email") or "").strip()
        default_token = (os.environ.get("JIRA_API_TOKEN") or local_creds.get("jira_api_token") or "").strip()

        resolved_email = ""
        resolved_token = ""
        explicit_member_em = (member_email or "").strip().lower()
        if explicit_member_em and explicit_member_em in member_creds_map:
            mc = member_creds_map[explicit_member_em]
            resolved_email = mc["jira_email"]
            resolved_token = mc["jira_api_token"]
        elif env_role_token:
            resolved_token = env_role_token
            resolved_email = env_role_email or (role_cred or {}).get("jira_email") or role_person.get("email") or default_email
        elif (
            self.role
            and default_email
            and default_token
            and find_member_in_role(config, self.role, default_email) is not None
            and not (isinstance(role_cred, dict) and role_cred.get("jira_api_token") and (role_cred.get("jira_email") or "").lower() != default_email.lower())
        ):
            # Local collaborator belongs to this multi-member role pool -> act under their own identity
            resolved_email = default_email
            resolved_token = default_token
        elif isinstance(role_cred, dict) and role_cred.get("jira_api_token"):
            resolved_token = role_cred["jira_api_token"].strip()
            resolved_email = (role_cred.get("jira_email") or role_person.get("email") or default_email).strip()
        else:
            # Check member_credentials or other role_credentials matching any claimed member of self.role
            for m in (resolve_role_members(config, self.role) if self.role else []):
                t_em = (m.get("email") or "").strip().lower()
                if not t_em:
                    continue
                if t_em in member_creds_map:
                    resolved_email = member_creds_map[t_em]["jira_email"]
                    resolved_token = member_creds_map[t_em]["jira_api_token"]
                    break
                for other_role, other_cred in role_creds_map.items():
                    if isinstance(other_cred, dict) and other_cred.get("jira_api_token"):
                        other_person = resolve_role_person(config, other_role)
                        other_em = (other_cred.get("jira_email") or other_person.get("email") or "").strip().lower()
                        if other_em == t_em:
                            resolved_token = other_cred["jira_api_token"].strip()
                            resolved_email = (other_cred.get("jira_email") or m.get("email")).strip()
                            break
                if resolved_token:
                    break

        self.email = resolved_email or default_email
        self.api_token = resolved_token or default_token
        self.using_role_token = bool(resolved_token)
        self._explicit_offline = bool(offline)
        self.offline = offline or not (self.base_url and self.email and self.api_token)

    @property
    def is_live(self) -> bool:
        return not self.offline

    def _headers(self) -> dict:
        token = base64.b64encode(f"{self.email}:{self.api_token}".encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict = None):
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Jira API {method} {path} failed with HTTP {exc.code}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Jira API {method} {path} connection error: {exc.reason}") from exc

    def verify_connection(self) -> dict:
        """Verify Jira REST API v3 credentials via GET /rest/api/3/myself."""
        if not self.is_live:
            return {"mode": "offline", "verified": False}
        res = self._request("GET", "/rest/api/3/myself")
        if isinstance(res, dict):
            return {
                "mode": "live",
                "verified": True,
                "account_id": res.get("accountId", ""),
                "display_name": res.get("displayName", ""),
                "email": res.get("emailAddress", self.email),
            }
        return {"mode": "live", "verified": True}

    def resolve_account_id_by_email(self, email: str) -> str:
        """Query Jira REST API v3 user search to resolve accountId from email."""
        if not self.is_live or not email or not email.strip():
            return ""
        q = urllib.parse.quote(email.strip())
        res = self._request("GET", f"/rest/api/3/user/search?query={q}")
        if isinstance(res, list) and res:
            target = email.strip().lower()
            for item in res:
                if isinstance(item, dict) and (item.get("emailAddress") or "").strip().lower() == target:
                    return str(item.get("accountId") or "").strip()
            first = res[0]
            if isinstance(first, dict):
                return str(first.get("accountId") or "").strip()
        return ""

    def search_active_stories(self, project_key: str, bug_type_name: str = "Bug", max_results: int = 20) -> list:
        """Discover non-Done parent Stories and unlinked standalone Bugs in Jira via POST /rest/api/3/search/jql."""
        if not self.is_live or not (project_key or "").strip():
            return []
        proj = project_key.strip()
        jql = f'project = "{proj}" AND statusCategory != Done AND issuetype not in subTaskIssueTypes() ORDER BY updated DESC'
        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "issuetype", "parent", "assignee", "issuelinks"],
        }
        try:
            res = self._request("POST", "/rest/api/3/search/jql", payload)
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                res = self._request("POST", "/rest/api/3/search", payload)
            else:
                raise
        issues = (res or {}).get("issues") if isinstance(res, dict) else []
        if not isinstance(issues, list):
            return []
        sub_or_epic_types = {"sub-task", "subtask", "子任務", "epic"}
        bug_types = {"bug", "缺陷", (bug_type_name or "bug").strip().lower()}

        # Collect any Bug keys linked to non-Bug parent Stories/Tasks so Story-owned child Bugs are not duplicated
        linked_child_bug_keys = set()
        for iss in issues:
            if not isinstance(iss, dict):
                continue
            key = (iss.get("key") or "").strip().upper()
            fields = iss.get("fields") or {}
            itype = fields.get("issuetype") or {}
            if itype.get("subtask") is True:
                continue
            itype_name = (itype.get("name") or "").strip().lower()
            if itype_name in sub_or_epic_types:
                continue
            if itype_name not in bug_types:
                for link in fields.get("issuelinks") or []:
                    if not isinstance(link, dict):
                        continue
                    for side in ("inwardIssue", "outwardIssue"):
                        linked = link.get(side) or {}
                        lk = str(linked.get("key") or "").strip().upper()
                        if lk:
                            linked_child_bug_keys.add(lk)
            else:
                parent = fields.get("parent")
                if isinstance(parent, dict) and parent.get("key"):
                    p_type = str((((parent.get("fields") or {}).get("issuetype") or {}).get("name")) or "").strip().lower()
                    if p_type != "epic":
                        linked_child_bug_keys.add(key)
                for link in fields.get("issuelinks") or []:
                    if not isinstance(link, dict):
                        continue
                    for side in ("inwardIssue", "outwardIssue"):
                        linked = link.get(side) or {}
                        l_fields = linked.get("fields") or {}
                        l_type = str(((l_fields.get("issuetype") or {}).get("name")) or "").strip().lower()
                        if linked.get("key") and l_type not in bug_types and l_type not in sub_or_epic_types:
                            linked_child_bug_keys.add(key)

        found_keys = []
        for iss in issues:
            if not isinstance(iss, dict):
                continue
            key = (iss.get("key") or "").strip().upper()
            if not key:
                continue
            fields = iss.get("fields") or {}
            itype = fields.get("issuetype") or {}
            if itype.get("subtask") is True:
                continue
            itype_name = (itype.get("name") or "").strip().lower()
            if itype_name in sub_or_epic_types:
                continue
            if itype_name in bug_types and key in linked_child_bug_keys:
                continue
            if key not in found_keys:
                found_keys.append(key)
        return found_keys

    def list_boards(self, project_key: str, board_type: str = "Scrum", max_results: int = 50) -> list:
        if not self.is_live:
            return []
        query = urllib.parse.urlencode({
            "projectKeyOrId": project_key,
            "type": board_type,
            "maxResults": max_results,
        })
        res = self._request("GET", f"/rest/agile/1.0/board?{query}")
        return (res or {}).get("values", []) if isinstance(res, dict) else []

    def list_sprints(self, board_id: str, state: str = "", max_results: int = 50) -> list:
        if not self.is_live:
            return []
        params = {"maxResults": max_results}
        if state:
            params["state"] = state
        query = urllib.parse.urlencode(params)
        res = self._request("GET", f"/rest/agile/1.0/board/{urllib.parse.quote(str(board_id))}/sprint?{query}")
        return (res or {}).get("values", []) if isinstance(res, dict) else []

    def get_sprint(self, sprint_id: str) -> dict:
        if not self.is_live:
            return {"id": int(sprint_id) if str(sprint_id).isdigit() else sprint_id}
        return self._request("GET", f"/rest/agile/1.0/sprint/{urllib.parse.quote(str(sprint_id))}")

    def create_sprint(self, payload: dict) -> dict:
        if not self.is_live:
            return {"id": "offline-sprint", **payload}
        return self._request("POST", "/rest/agile/1.0/sprint", payload)

    def update_sprint(self, sprint_id: str, payload: dict) -> dict:
        if not self.is_live:
            return {"id": sprint_id, **payload}
        return self._request("PUT", f"/rest/agile/1.0/sprint/{urllib.parse.quote(str(sprint_id))}", payload)

    def get_sprint_issues(self, sprint_id: str, max_results: int = 50) -> list:
        if not self.is_live:
            return []
        query = urllib.parse.urlencode({"maxResults": max_results})
        res = self._request("GET", f"/rest/agile/1.0/sprint/{urllib.parse.quote(str(sprint_id))}/issue?{query}")
        return (res or {}).get("issues", []) if isinstance(res, dict) else []

    def move_issues_to_sprint(self, sprint_id: str, issue_keys: list) -> None:
        if not self.is_live:
            return
        self._request(
            "POST",
            f"/rest/agile/1.0/sprint/{urllib.parse.quote(str(sprint_id))}/issue",
            {"issues": issue_keys},
        )

    @staticmethod
    def _parse_inline_markdown(text: str) -> list:
        """Parse inline Markdown (`code` and **bold**) into valid Jira ADF text nodes.

        Guarantees:
        - Inline code (`...`) takes precedence over bold (**...**): asterisks inside
          backticks remain literal inside the `code` mark.
        - ADF `code` mark is NEVER combined with `strong` (or other formatting marks).
        - Empty text nodes ({"type": "text", "text": ""}) are NEVER emitted.
        - Unclosed delimiters (**abc, `abc) and degenerate markers (****, ``) fall
          back safely to literal text without dropping content.
        """
        if not text:
            return []

        nodes = []

        def _append_node(seg: str, mark_type: str = None) -> None:
            if not seg:
                return
            if mark_type is None:
                if nodes and "marks" not in nodes[-1]:
                    nodes[-1]["text"] += seg
                else:
                    nodes.append({"type": "text", "text": seg})
            else:
                nodes.append({"type": "text", "text": seg, "marks": [{"type": mark_type}]})

        def _find_closing_bold(src: str, start: int) -> int:
            pos = start
            length = len(src)
            while pos < length:
                if src[pos] == "`":
                    close_tick = src.find("`", pos + 1)
                    if close_tick != -1:
                        pos = close_tick + 1
                        continue
                    pos += 1
                    continue
                if src[pos:pos + 2] == "**":
                    return pos
                pos += 1
            return -1

        def _emit_bold_inner(inner: str) -> None:
            pos = 0
            length = len(inner)
            while pos < length:
                if inner[pos] == "`":
                    close_tick = inner.find("`", pos + 1)
                    if close_tick != -1:
                        code_text = inner[pos + 1:close_tick]
                        if code_text:
                            _append_node(code_text, "code")
                        else:
                            _append_node("``", "strong")
                        pos = close_tick + 1
                        continue
                    _append_node("`", "strong")
                    pos += 1
                    continue
                next_tick = inner.find("`", pos)
                if next_tick == -1:
                    _append_node(inner[pos:], "strong")
                    break
                _append_node(inner[pos:next_tick], "strong")
                pos = next_tick

        i = 0
        n = len(text)
        while i < n:
            if text[i] == "`":
                close_tick = text.find("`", i + 1)
                if close_tick != -1:
                    code_text = text[i + 1:close_tick]
                    if code_text:
                        _append_node(code_text, "code")
                    else:
                        _append_node("``")
                    i = close_tick + 1
                    continue
                _append_node("`")
                i += 1
                continue

            if text[i:i + 2] == "**":
                close_bold = _find_closing_bold(text, i + 2)
                if close_bold != -1:
                    inner = text[i + 2:close_bold]
                    if inner:
                        _emit_bold_inner(inner)
                    else:
                        _append_node("****")
                    i = close_bold + 2
                    continue
                _append_node("**")
                i += 2
                continue

            next_pos = i + 1
            while next_pos < n and text[next_pos] != "`" and text[next_pos:next_pos + 2] != "**":
                next_pos += 1
            _append_node(text[i:next_pos])
            i = next_pos

        return nodes

    @staticmethod
    def text_to_adf(
        text: str,
        mention_account_id: str = "",
        mention_name: str = "",
        lang_zh: bool = True,
    ) -> dict:
        """Convert Markdown/plain text into Atlassian Document Format (ADF) v1 with heading, bulletList, table, and inline bold/code support."""
        content_nodes = []
        handoff_prompt = " 請接手下一階段工作：" if lang_zh else " — ready for next station:"
        if mention_account_id:
            content_nodes.append({
                "type": "paragraph",
                "content": [
                    {
                        "type": "mention",
                        "attrs": {
                            "id": mention_account_id,
                            "text": f"@{mention_name or mention_account_id}",
                        },
                    },
                    {"type": "text", "text": handoff_prompt},
                ],
            })
        elif mention_name:
            content_nodes.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": f"@{mention_name}{handoff_prompt}"}],
            })

        lines = (text or "").strip().splitlines()
        i = 0
        n = len(lines)
        heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
        bullet_re = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
        table_sep_re = re.compile(r"^\|\s*[-:]+[-|\s:]*\|$")

        def _is_table_line(ln: str) -> bool:
            s = ln.strip()
            return len(s) >= 2 and s.startswith("|") and s.endswith("|")

        def _inline_or_fallback(raw: str, fallback: str = " ") -> list:
            parsed = JiraClient._parse_inline_markdown(raw)
            return parsed if parsed else [{"type": "text", "text": fallback}]

        while i < n:
            raw_line = lines[i]
            stripped = raw_line.strip()
            if not stripped:
                i += 1
                continue

            # 1. Markdown Heading (# .. ######)
            hm = heading_re.match(stripped)
            if hm:
                level = min(max(len(hm.group(1)), 1), 6)
                heading_text = hm.group(2).strip()
                content_nodes.append({
                    "type": "heading",
                    "attrs": {"level": level},
                    "content": _inline_or_fallback(heading_text, heading_text),
                })
                i += 1
                continue

            # 2. Markdown Bullet List (- item / * item)
            bm = bullet_re.match(raw_line)
            if bm:
                items = []
                while i < n:
                    m_item = bullet_re.match(lines[i])
                    if not m_item:
                        break
                    items.append(m_item.group(1).strip())
                    i += 1
                content_nodes.append({
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": _inline_or_fallback(item_text, item_text),
                                }
                            ],
                        }
                        for item_text in items
                    ],
                })
                continue

            # 3. Markdown Table (| col1 | col2 |)
            if _is_table_line(stripped):
                raw_table_lines = []
                while i < n and _is_table_line(lines[i]):
                    raw_table_lines.append(lines[i].strip())
                    i += 1
                rows = []
                for t_line in raw_table_lines:
                    if table_sep_re.match(t_line):
                        continue
                    cells = [c.strip() for c in t_line[1:-1].split("|")]
                    rows.append(cells)
                if rows:
                    table_rows = []
                    for r_idx, r_cells in enumerate(rows):
                        cell_type = "tableHeader" if r_idx == 0 else "tableCell"
                        table_rows.append({
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": cell_type,
                                    "attrs": {},
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": _inline_or_fallback(c_text, " "),
                                        }
                                    ],
                                }
                                for c_text in r_cells
                            ],
                        })
                    content_nodes.append({
                        "type": "table",
                        "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
                        "content": table_rows,
                    })
                continue

            # 4. Regular paragraph (collect contiguous non-special lines)
            para_lines = []
            while i < n:
                cur = lines[i]
                cur_s = cur.strip()
                if not cur_s or heading_re.match(cur_s) or bullet_re.match(cur) or _is_table_line(cur_s):
                    break
                para_lines.append(cur_s)
                i += 1
            if para_lines:
                para_text = "\n".join(para_lines)
                content_nodes.append({
                    "type": "paragraph",
                    "content": _inline_or_fallback(para_text, para_text),
                })

        if not content_nodes:
            content_nodes.append({"type": "paragraph", "content": [{"type": "text", "text": "Updated by ai-engineering-workflow"}]})
        return {"type": "doc", "version": 1, "content": content_nodes}

    def update_or_create_story(
        self,
        jira_key: str,
        title: str,
        description_md: str,
        create: bool = False,
        allow_create: bool = True,
    ) -> dict:
        adf = self.text_to_adf(description_md)
        if not self.is_live:
            return {"mode": "offline", "jira_key": jira_key, "action": "sync-story", "summary": title}
        story_type = (self.config.get("issue_type_mapping") or {}).get("story") or "Story"
        proj_key = self.config.get("project_key") or jira_key.split("-")[0]
        create_payload = {
            "fields": {
                "project": {"key": proj_key},
                "summary": title,
                "description": adf,
                "issuetype": {"name": story_type},
            }
        }
        if create:
            res = self._request("POST", "/rest/api/3/issue", create_payload)
            return {"mode": "live", "jira_key": res.get("key", jira_key), "action": "create-story"}
        payload = {"fields": {"summary": title, "description": adf}}
        try:
            self._request("PUT", f"/rest/api/3/issue/{urllib.parse.quote(jira_key)}", payload)
            return {"mode": "live", "jira_key": jira_key, "action": "update-story"}
        except RuntimeError as exc:
            if "HTTP 404" in str(exc) and allow_create:
                res = self._request("POST", "/rest/api/3/issue", create_payload)
                return {"mode": "live", "jira_key": res.get("key", jira_key), "action": "create-story"}
            raise

    def create_subtask(self, parent_key: str, summary: str, description_md: str, index: int) -> str:
        if not self.is_live:
            return f"{parent_key}-SUB-{index}"
        proj_key = self.config.get("project_key") or parent_key.split("-")[0]
        configured_type = (self.config.get("issue_type_mapping") or {}).get("subtask") or "Sub-task"
        candidates = []
        for itype in (configured_type, "Sub-task", "Subtask", "子任務"):
            if itype and itype not in candidates:
                candidates.append(itype)

        adf_desc = self.text_to_adf(description_md)
        last_exc = None
        for idx_c, itype in enumerate(candidates):
            payload = {
                "fields": {
                    "project": {"key": proj_key},
                    "parent": {"key": parent_key},
                    "summary": summary,
                    "description": adf_desc,
                    "issuetype": {"name": itype},
                }
            }
            try:
                res = self._request("POST", "/rest/api/3/issue", payload)
                return res.get("key") or f"{parent_key}-SUB-{index}"
            except RuntimeError as exc:
                last_exc = exc
                if "HTTP 400" in str(exc) and idx_c + 1 < len(candidates):
                    continue
                raise
        if last_exc:
            raise last_exc
        return f"{parent_key}-SUB-{index}"

    def create_bug(self, parent_key: str, summary: str, description_md: str, index: int) -> str:
        if not self.is_live:
            return f"{parent_key}-BUG-{index}"
        proj_key = self.config.get("project_key") or parent_key.split("-")[0]
        bug_type = (self.config.get("issue_type_mapping") or {}).get("bug") or "Bug"
        payload = {
            "fields": {
                "project": {"key": proj_key},
                "summary": summary,
                "description": self.text_to_adf(description_md),
                "issuetype": {"name": bug_type},
            }
        }
        res = self._request("POST", "/rest/api/3/issue", payload)
        bug_key = res.get("key") or f"{parent_key}-BUG-{index}"
        # Link Bug to parent Story
        link_payload = {
            "type": {"name": "Blocks"},
            "inwardIssue": {"key": bug_key},
            "outwardIssue": {"key": parent_key},
        }
        try:
            self._request("POST", "/rest/api/3/issueLink", link_payload)
        except Exception:
            pass
        return bug_key

    def create_standalone_bug(self, summary: str, description_md: str, fallback_key: str = "") -> str:
        proj_key = self.config.get("project_key") or "PROJ"
        if not self.is_live:
            return fallback_key or f"{proj_key}-BUG-1"
        bug_type = (self.config.get("issue_type_mapping") or {}).get("bug") or "Bug"
        payload = {
            "fields": {
                "project": {"key": proj_key},
                "summary": summary,
                "description": self.text_to_adf(description_md),
                "issuetype": {"name": bug_type},
            }
        }
        res = self._request("POST", "/rest/api/3/issue", payload)
        return res.get("key") or fallback_key or f"{proj_key}-BUG-1"

    def create_epic_story(
        self,
        epic_key: str,
        title: str,
        description_md: str,
        issue_type: str = "Story",
        priority: str = "",
    ) -> dict:
        """Create a child Story under a Jira Epic with parent -> epic_link_field -> unlinked HTTP 400 fallback."""
        proj_key = self.config.get("project_key") or epic_key.split("-")[0]
        eff_type = _clean_optional_text(issue_type) or (self.config.get("issue_type_mapping") or {}).get("story") or "Story"
        if not self.is_live:
            return {"mode": "offline", "jira_key": "", "epic_link_mode": "offline", "warning": ""}

        adf = self.text_to_adf(description_md)
        base_fields = {
            "project": {"key": proj_key},
            "summary": title,
            "description": adf,
            "issuetype": {"name": eff_type},
        }
        clean_prio = _clean_optional_text(priority)
        if clean_prio:
            base_fields["priority"] = {"name": clean_prio}

        try:
            res = self._request("POST", "/rest/api/3/issue", {"fields": {**base_fields, "parent": {"key": epic_key}}})
            new_key = _clean_optional_text((res or {}).get("key")).upper()
            if not new_key:
                raise RuntimeError("Jira API did not return a Story key.")
            return {"mode": "live", "jira_key": new_key, "epic_link_mode": "parent", "warning": ""}
        except RuntimeError as exc:
            if "HTTP 400" not in str(exc):
                raise

        epic_link_field = _clean_optional_text(self.config.get("epic_link_field"))
        epic_link_field_rejected = False
        if epic_link_field:
            try:
                res2 = self._request("POST", "/rest/api/3/issue", {"fields": {**base_fields, epic_link_field: epic_key}})
                new_key = _clean_optional_text((res2 or {}).get("key")).upper()
                if not new_key:
                    raise RuntimeError("Jira API did not return a Story key.")
                return {
                    "mode": "live",
                    "jira_key": new_key,
                    "epic_link_mode": "epic_link_field",
                    "epic_link_field": epic_link_field,
                    "warning": (
                        f"Jira rejected fields.parent (HTTP 400); linked Story {new_key} to Epic {epic_key} "
                        f"via configured epic_link_field '{epic_link_field}'."
                    ),
                }
            except RuntimeError as exc2:
                if "HTTP 400" not in str(exc2):
                    raise
                epic_link_field_rejected = True

        res3 = self._request("POST", "/rest/api/3/issue", {"fields": base_fields})
        new_key = _clean_optional_text((res3 or {}).get("key")).upper()
        if not new_key:
            raise RuntimeError("Jira API did not return a Story key.")
        if epic_link_field_rejected:
            unlinked_warning = (
                f"Jira rejected both fields.parent and configured epic_link_field '{epic_link_field}' "
                f"for Epic {epic_key} (HTTP 400); created Story {new_key} without parent Epic link. "
                f"Verify 'epic_link_field' in .ai-workflow/team-config.json or link manually in Jira."
            )
        else:
            unlinked_warning = (
                f"Jira rejected fields.parent for Epic {epic_key} (HTTP 400); created Story {new_key} "
                f"without parent Epic link. Configure 'epic_link_field' in .ai-workflow/team-config.json or link manually in Jira."
            )
        return {
            "mode": "live",
            "jira_key": new_key,
            "epic_link_mode": "unlinked",
            "warning": unlinked_warning,
        }

    def add_comment(self, issue_key: str, body_md: str, mention_account_id: str = "", mention_name: str = "") -> dict:
        adf = self.text_to_adf(
            body_md,
            mention_account_id=mention_account_id,
            mention_name=mention_name,
            lang_zh=is_zh_tw(self.config),
        )
        if not self.is_live:
            return {"mode": "offline", "issue_key": issue_key, "comment_posted": False}
        self._request("POST", f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/comment", {"body": adf})
        return {"mode": "live", "issue_key": issue_key, "comment_posted": True}

    def transition_issue(self, issue_key: str, target_status_name: str, resolution_name: str = "") -> dict:
        res_name = _clean_optional_text(resolution_name)
        if not self.is_live or not target_status_name:
            out = {"mode": "offline" if not self.is_live else "live", "transitioned_to": target_status_name}
            if res_name:
                out["resolution"] = res_name
            return out
        trans_resp = self._request("GET", f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/transitions")
        transitions = trans_resp.get("transitions") or []
        matched_id = None
        for t in transitions:
            t_name = (t.get("name") or "").strip().lower()
            to_name = ((t.get("to") or {}).get("name") or "").strip().lower()
            if target_status_name.strip().lower() in (t_name, to_name):
                matched_id = t.get("id")
                break
        if matched_id:
            trans_path = f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/transitions"
            if res_name:
                payload_with_res = {
                    "transition": {"id": str(matched_id)},
                    "fields": {"resolution": {"name": res_name}},
                }
                try:
                    self._request("POST", trans_path, payload_with_res)
                    return {
                        "mode": "live",
                        "transitioned_to": target_status_name,
                        "transition_id": str(matched_id),
                        "resolution": res_name,
                        "resolution_applied": True,
                        "resolution_fallback": False,
                    }
                except RuntimeError as exc:
                    # Gracefully retry transition without fields.resolution if Jira rejects resolution on that screen
                    self._request("POST", trans_path, {"transition": {"id": str(matched_id)}})
                    return {
                        "mode": "live",
                        "transitioned_to": target_status_name,
                        "transition_id": str(matched_id),
                        "resolution": res_name,
                        "resolution_applied": False,
                        "resolution_fallback": True,
                        "warning": f"Jira rejected resolution '{res_name}' on transition screen ({exc}); retried without resolution field.",
                    }
            self._request(
                "POST",
                trans_path,
                {"transition": {"id": str(matched_id)}},
            )
            return {"mode": "live", "transitioned_to": target_status_name, "transition_id": str(matched_id)}
        return {"mode": "live", "transitioned_to": None, "warning": f"Transition '{target_status_name}' not found"}

    def assign_issue(self, issue_key: str, account_id: str) -> dict:
        if not self.is_live or not account_id:
            return {"mode": "offline" if not self.is_live else "live", "assigned_to": account_id}
        self._request(
            "PUT",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/assignee",
            {"accountId": account_id},
        )
        return {"mode": "live", "assigned_to": account_id}

    def add_attachments(self, issue_key: str, validated_files: list) -> list:
        """Upload one or more validated files to a Jira issue via POST /rest/api/3/issue/{key}/attachments."""
        if not validated_files:
            return []
        if not self.is_live:
            return [
                {
                    "mode": "offline",
                    "id": f"offline-att-{idx}",
                    "filename": vf["filename"],
                    "size": vf["size"],
                    "mime_type": vf["mime_type"],
                    "sha256": vf["sha256"],
                    "local_path": vf["local_path"],
                }
                for idx, vf in enumerate(validated_files, start=1)
            ]
        boundary, body = _build_multipart_form_data(validated_files, field_name="file")
        path = f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/attachments"
        url = f"{self.base_url}{path}"
        token = base64.b64encode(f"{self.email}:{self.api_token}".encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw) if raw.strip() else []
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Jira API POST {path} failed with HTTP {exc.code}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Jira API POST {path} connection error: {exc.reason}") from exc

        items = parsed if isinstance(parsed, list) else []
        out = []
        for idx, vf in enumerate(validated_files):
            remote = items[idx] if idx < len(items) and isinstance(items[idx], dict) else {}
            out.append({
                "mode": "live",
                "id": str(remote.get("id") or ""),
                "filename": remote.get("filename") or vf["filename"],
                "size": int(remote.get("size") or vf["size"]),
                "mime_type": remote.get("mimeType") or vf["mime_type"],
                "url": remote.get("content") or "",
                "sha256": vf["sha256"],
                "local_path": vf["local_path"],
            })
        return out

    def get_issue(self, issue_key: str) -> dict:
        if not self.is_live:
            return {}
        return self._request("GET", f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}")

    def sync_state_snapshot(self, jira_key: str, story: dict) -> None:
        if not self.is_live:
            return
        try:
            prop_path = f"/rest/api/3/issue/{urllib.parse.quote(jira_key)}/properties/ai_workflow_state"
            self._request("PUT", prop_path, story)
        except Exception:
            pass

    def get_state_snapshot(self, jira_key: str):
        if not self.is_live:
            return None
        try:
            prop_path = f"/rest/api/3/issue/{urllib.parse.quote(jira_key)}/properties/ai_workflow_state"
            res = self._request("GET", prop_path)
            if isinstance(res, dict) and isinstance(res.get("value"), dict):
                return res["value"]
        except Exception:
            pass
        return None


class QaseClient:
    """Dependency-free Qase TestOps API v1 client for Suite, Case, External Jira Link, and Test Run sync."""

    def __init__(self, config: dict, offline: bool = False, repo_root: Path = None):
        self.config = config
        qase_cfg = config.get("qase") if isinstance(config.get("qase"), dict) else {}
        self.project_code = (
            os.environ.get("QASE_PROJECT_CODE")
            or qase_cfg.get("project_code")
            or ""
        ).strip().upper()
        self.base_url = (
            os.environ.get("QASE_BASE_URL")
            or qase_cfg.get("base_url")
            or "https://api.qase.io/v1"
        ).strip().rstrip("/")
        self.app_url = (
            os.environ.get("QASE_APP_URL")
            or qase_cfg.get("app_url")
            or "https://app.qase.io"
        ).strip().rstrip("/")
        self.jira_integration_type = (
            os.environ.get("QASE_JIRA_INTEGRATION_TYPE")
            or qase_cfg.get("jira_integration_type")
            or "jira-cloud"
        ).strip()
        for label, url_val in (("qase.base_url", self.base_url), ("qase.app_url", self.app_url)):
            if url_val:
                scheme = urllib.parse.urlparse(url_val).scheme.lower()
                if scheme not in ("http", "https"):
                    raise ValueError(f"Invalid {label} scheme '{scheme}'. Only http and https are allowed.")
        local_creds = load_jira_credentials(repo_root) if repo_root else {}
        self.api_token = (
            os.environ.get("QASE_API_TOKEN")
            or local_creds.get("qase_api_token")
            or ""
        ).strip()
        self.offline = bool(offline)
        self.has_credentials = bool(self.project_code and self.api_token)

    def _headers(self) -> dict:
        return {
            "Token": self.api_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw) if raw.strip() else {}
                if isinstance(parsed, dict) and parsed.get("status") is False:
                    raise RuntimeError(
                        f"Qase API {method} {path} returned status=false: {parsed.get('errorMessage') or parsed}"
                    )
                return parsed
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qase API {method} {path} failed with HTTP {exc.code}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Qase API {method} {path} connection error: {exc.reason}") from exc

    @staticmethod
    def _automation_code(exec_type: str) -> int:
        return {"automated": 2, "external": 1, "manual": 0}.get((exec_type or "").lower(), 2)

    def sync_test_plan(self, jira_key: str, story_title: str, qa: dict, normalized_cases: list) -> dict:
        if self.offline:
            suite_id = int(qa.get("qase_suite_id") or 1)
            for idx, c in enumerate(normalized_cases, start=1):
                c["qase_case_id"] = int(c.get("qase_case_id") or idx)
            code = self.project_code or "OFFLINE"
            suite_url = f"{self.app_url}/project/{code}?suite={suite_id}"
            qa["qase_suite_id"] = suite_id
            qa["qase_project_code"] = code
            qa["qase_suite_url"] = suite_url
            return {
                "mode": "offline",
                "project_code": code,
                "suite_id": suite_id,
                "suite_url": suite_url,
                "case_ids": [c["qase_case_id"] for c in normalized_cases],
            }

        if not self.has_credentials:
            raise RuntimeError(
                "qa_test_backend is set to 'qase', but Qase project_code or Qase API token is missing. "
                "Provide --qase-project-code <CODE> and --qase-token <TOKEN> (or set QASE_API_TOKEN)."
            )

        code_q = urllib.parse.quote(self.project_code)
        suite_payload = {
            "title": f"[{jira_key}] {story_title or jira_key}"[:255],
            "description": f"Jira Story: {jira_key}",
        }
        suite_id = qa.get("qase_suite_id")
        if suite_id:
            try:
                self._request("PATCH", f"/suite/{code_q}/{int(suite_id)}", suite_payload)
                suite_id = int(suite_id)
            except RuntimeError as exc:
                if "HTTP 404" in str(exc):
                    suite_id = None
                else:
                    raise
        if not suite_id:
            s_res = self._request("POST", f"/suite/{code_q}", suite_payload)
            suite_id = int((s_res.get("result") or {}).get("id"))

        to_create = []
        for c in normalized_cases:
            case_payload = {
                "title": f"[{c['id']}][{c['ac_id']}] {c['title']}"[:255],
                "description": (
                    f"Jira Story: {jira_key}\n"
                    f"Acceptance Criterion: {c['ac_id']}\n"
                    f"Execution Tier: {c['execution_type']}"
                ),
                "suite_id": suite_id,
                "automation": self._automation_code(c.get("execution_type")),
            }
            existing_cid = c.get("qase_case_id")
            if existing_cid:
                try:
                    self._request("PATCH", f"/case/{code_q}/{int(existing_cid)}", case_payload)
                    c["qase_case_id"] = int(existing_cid)
                    continue
                except RuntimeError as exc:
                    if "HTTP 404" not in str(exc):
                        raise
            to_create.append((c, case_payload))

        if to_create:
            b_res = self._request("POST", f"/case/{code_q}/bulk", {"cases": [p for _, p in to_create]})
            created_ids = (b_res.get("result") or {}).get("ids") or []
            if len(created_ids) == len(to_create):
                for (c_obj, _), cid in zip(to_create, created_ids):
                    c_obj["qase_case_id"] = int(cid)
            else:
                for c_obj, c_payload in to_create:
                    single_res = self._request("POST", f"/case/{code_q}", c_payload)
                    c_obj["qase_case_id"] = int((single_res.get("result") or {}).get("id"))

        external_link_warning = None
        links = [
            {"case_id": int(c["qase_case_id"]), "external_issue_id": jira_key}
            for c in normalized_cases
            if c.get("qase_case_id")
        ]
        if links:
            try:
                self._request(
                    "POST",
                    f"/case/{code_q}/external-issue/attach",
                    {"type": self.jira_integration_type, "links": links},
                )
            except RuntimeError as exc:
                external_link_warning = str(exc)

        suite_url = f"{self.app_url}/project/{self.project_code}?suite={suite_id}"
        qa["qase_suite_id"] = suite_id
        qa["qase_project_code"] = self.project_code
        qa["qase_suite_url"] = suite_url
        out = {
            "mode": "live",
            "project_code": self.project_code,
            "suite_id": suite_id,
            "suite_url": suite_url,
            "case_ids": [int(c["qase_case_id"]) for c in normalized_cases],
        }
        if external_link_warning:
            out["external_link_warning"] = external_link_warning
        return out

    def upload_attachments(self, validated_files: list, existing_cache: dict = None) -> tuple:
        """
        Upload validated files to Qase via POST /attachment/{code}, reusing cached hashes by SHA-256
        so identical screenshots/videos are never uploaded twice across record-qa and create-bug.
        Returns (ordered_hashes, attachment_records, updated_cache).
        """
        cache = dict(existing_cache or {})
        if not validated_files:
            return [], [], cache

        if self.offline:
            ordered_hashes = []
            records = []
            for vf in validated_files:
                h = cache.get(vf["sha256"]) or f"offline-qase-{vf['sha256'][:16]}"
                cache[vf["sha256"]] = h
                ordered_hashes.append(h)
                records.append({
                    "mode": "offline",
                    "filename": vf["filename"],
                    "hash": h,
                    "size": vf["size"],
                    "mime_type": vf["mime_type"],
                    "sha256": vf["sha256"],
                })
            return ordered_hashes, records, cache

        if not self.has_credentials:
            raise RuntimeError(
                "qa_test_backend is set to 'qase', but Qase project_code or Qase API token is missing."
            )

        uncached = [vf for vf in validated_files if vf["sha256"] not in cache]
        url_by_sha = {}
        if uncached:
            code_q = urllib.parse.quote(self.project_code)
            boundary, body = _build_multipart_form_data(uncached, field_name="file")
            path = f"/attachment/{code_q}"
            url = f"{self.base_url}{path}"
            headers = {
                "Token": self.api_token,
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            }
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                    parsed = json.loads(raw) if raw.strip() else {}
                    if isinstance(parsed, dict) and parsed.get("status") is False:
                        raise RuntimeError(
                            f"Qase API POST {path} returned status=false: {parsed.get('errorMessage') or parsed}"
                        )
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Qase API POST {path} failed with HTTP {exc.code}: {err_body}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Qase API POST {path} connection error: {exc.reason}") from exc

            res_items = (parsed.get("result") or []) if isinstance(parsed, dict) else []
            for idx, vf in enumerate(uncached):
                remote = res_items[idx] if idx < len(res_items) and isinstance(res_items[idx], dict) else {}
                h = str(remote.get("hash") or "").strip()
                if not h:
                    raise RuntimeError(f"Qase attachment upload did not return a hash for {vf['filename']}")
                cache[vf["sha256"]] = h
                url_by_sha[vf["sha256"]] = str(remote.get("url") or "")

        ordered_hashes = []
        records = []
        for vf in validated_files:
            h = cache[vf["sha256"]]
            ordered_hashes.append(h)
            records.append({
                "mode": "live",
                "filename": vf["filename"],
                "hash": h,
                "url": url_by_sha.get(vf["sha256"], ""),
                "size": vf["size"],
                "mime_type": vf["mime_type"],
                "sha256": vf["sha256"],
            })
        return ordered_hashes, records, cache

    def create_defect(
        self,
        title: str,
        actual_result: str,
        severity: str = "major",
        attachment_hashes: list = None,
    ) -> dict:
        sev_map = {"blocker": 1, "critical": 2, "major": 3, "minor": 5}
        sev_int = sev_map.get((severity or "major").strip().lower(), 3)
        hashes = [str(h) for h in (attachment_hashes or []) if h]
        if self.offline:
            code = self.project_code or "OFFLINE"
            return {
                "mode": "offline",
                "project_code": code,
                "defect_id": 1,
                "attachments": hashes,
            }
        if not self.has_credentials:
            raise RuntimeError(
                "qa_test_backend is set to 'qase', but Qase project_code or Qase API token is missing."
            )
        code_q = urllib.parse.quote(self.project_code)
        payload = {
            "title": (title or "QA Defect")[:255],
            "actual_result": actual_result or title,
            "severity": sev_int,
        }
        if hashes:
            payload["attachments"] = hashes
        res = self._request("POST", f"/defect/{code_q}", payload)
        defect_id = int((res.get("result") or {}).get("id") or 0)
        return {
            "mode": "live",
            "project_code": self.project_code,
            "defect_id": defect_id,
            "defect_url": f"{self.app_url}/defect/{self.project_code}/view/{defect_id}" if defect_id else "",
            "attachments": hashes,
        }

    def update_run_case_attachments(
        self,
        run_id: int,
        case_id: int,
        status_str: str,
        comment: str,
        attachment_hashes: list,
    ) -> dict:
        hashes = [str(h) for h in (attachment_hashes or []) if h]
        if self.offline or not run_id or not case_id or not hashes:
            return {"mode": "offline" if self.offline else "live", "updated": False}
        code_q = urllib.parse.quote(self.project_code)
        payload = {
            "case_id": int(case_id),
            "status": status_str,
            "attachments": hashes,
        }
        if comment:
            payload["comment"] = comment
        self._request("POST", f"/result/{code_q}/{int(run_id)}", payload)
        return {"mode": "live", "updated": True, "run_id": int(run_id), "case_id": int(case_id), "attachments": hashes}

    def resolve_defect(self, defect_id: int) -> dict:
        if not defect_id:
            return {"resolved": False}
        if self.offline:
            return {"mode": "offline", "defect_id": int(defect_id), "resolved": True}
        if not self.has_credentials:
            return {"mode": "live", "defect_id": int(defect_id), "resolved": False}
        code_q = urllib.parse.quote(self.project_code)
        self._request("PATCH", f"/defect/{code_q}/{int(defect_id)}/resolve", {})
        return {"mode": "live", "defect_id": int(defect_id), "resolved": True}

    def sync_verification_run(
        self,
        jira_key: str,
        story_title: str,
        qa: dict,
        test_cases: list,
        tested_commit: str,
        qa_status: str,
        tc_validated_attachments: dict = None,
    ) -> dict:
        if any(not c.get("qase_case_id") for c in test_cases):
            self.sync_test_plan(jira_key, story_title, qa, test_cases)

        att_cache = dict(qa.get("qase_attachment_cache") or {})
        tc_att_map = tc_validated_attachments or {}
        for c in test_cases:
            cid_key = c.get("id")
            vfiles = tc_att_map.get(cid_key) or []
            if vfiles:
                hashes, _, att_cache = self.upload_attachments(vfiles, existing_cache=att_cache)
                existing_hashes = [h for h in (c.get("qase_attachment_hashes") or []) if h]
                merged_hashes = list(dict.fromkeys(existing_hashes + hashes))
                c["qase_attachment_hashes"] = merged_hashes
        if att_cache:
            qa["qase_attachment_cache"] = att_cache

        if self.offline:
            code = self.project_code or "OFFLINE"
            run_id = 1
            run_url = f"{self.app_url}/run/{code}/dashboard/{run_id}"
            qa["qase_last_run_id"] = run_id
            qa["qase_last_run_url"] = run_url
            return {"mode": "offline", "project_code": code, "run_id": run_id, "run_url": run_url}

        if not self.has_credentials:
            raise RuntimeError(
                "qa_test_backend is set to 'qase', but Qase project_code or Qase API token is missing."
            )

        code_q = urllib.parse.quote(self.project_code)
        case_ids = [int(c["qase_case_id"]) for c in test_cases if c.get("qase_case_id")]
        run_payload = {
            "title": f"[{jira_key}] QA Verification ({tested_commit})"[:255],
            "description": f"Jira Story: {jira_key} | Commit: {tested_commit} | Status: {qa_status}",
            "cases": case_ids,
        }
        r_res = self._request("POST", f"/run/{code_q}", run_payload)
        run_id = int((r_res.get("result") or {}).get("id"))

        external_link_warning = None
        try:
            self._request(
                "POST",
                f"/run/{code_q}/external-issue",
                {
                    "type": self.jira_integration_type,
                    "runs": [{"run_id": run_id, "external_issue": jira_key}],
                },
            )
        except RuntimeError as exc:
            external_link_warning = str(exc)

        status_map = {
            "pass": "passed",
            "fail": "failed",
            "blocked": "blocked",
            "not-executed": "skipped",
            "waived": "skipped",
            "not-applicable": "skipped",
        }
        results_payload = []
        for c in test_cases:
            if not c.get("qase_case_id"):
                continue
            item = {
                "case_id": int(c["qase_case_id"]),
                "status": status_map.get(c.get("result", "not-executed"), "skipped"),
            }
            if c.get("notes"):
                item["comment"] = str(c["notes"])
            if c.get("qase_attachment_hashes"):
                item["attachments"] = list(c["qase_attachment_hashes"])
            results_payload.append(item)

        if results_payload:
            self._request("POST", f"/result/{code_q}/{run_id}/bulk", {"results": results_payload})

        completed = False
        if qa_status == "passed":
            self._request("POST", f"/run/{code_q}/{run_id}/complete", {})
            completed = True

        run_url = f"{self.app_url}/run/{self.project_code}/dashboard/{run_id}"
        qa["qase_last_run_id"] = run_id
        qa["qase_last_run_url"] = run_url
        out = {
            "mode": "live",
            "project_code": self.project_code,
            "run_id": run_id,
            "run_url": run_url,
            "completed": completed,
        }
        if external_link_warning:
            out["external_link_warning"] = external_link_warning
        return out


def _adf_to_plain_text(node) -> str:
    """Recursively extract plain text from an ADF JSON object or string."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(filter(None, (_adf_to_plain_text(item) for item in node)))
    if isinstance(node, dict):
        ntype = node.get("type")
        if ntype == "text":
            return str(node.get("text") or "")
        if ntype == "hardBreak":
            return "\n"
        if ntype == "mention":
            return str((node.get("attrs") or {}).get("text") or "")
        content = node.get("content") or []
        if ntype in ("paragraph", "heading"):
            return "".join(_adf_to_plain_text(c) for c in content)
        return "\n".join(filter(None, (_adf_to_plain_text(c) for c in content)))
    return str(node)


def _extract_state_snapshot_from_text(text: str):
    """Extract the most recent [ai-workflow-state] JSON snapshot embedded in text."""
    if not text or STATE_SNAPSHOT_MARKER not in text:
        return None
    matches = []
    for line in text.splitlines():
        idx = line.find(STATE_SNAPSHOT_MARKER)
        if idx != -1:
            raw_json = line[idx + len(STATE_SNAPSHOT_MARKER):].strip()
            if raw_json.startswith("{") and raw_json.endswith("}"):
                matches.append(raw_json)
    for candidate in reversed(matches):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and parsed.get("jira_key"):
                return parsed
        except Exception:
            continue
    return None


def _hydrate_story_from_worktree(repo_root: Path, clean_key: str):
    """If running in a secondary Git worktree, copy active <KEY>.json from the main worktree if present."""
    main_root = _resolve_main_worktree_root(repo_root)
    if not main_root:
        return None
    src_file = get_active_dir(main_root) / f"{clean_key}.json"
    if not src_file.exists():
        return None
    try:
        data = json.loads(src_file.read_text(encoding="utf-8"))
        dst_dir = get_active_dir(repo_root)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_file = dst_dir / f"{clean_key}.json"
        dst_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return data
    except Exception:
        return None


def safe_assign_issue(client: JiraClient, issue_key: str, account_id: str) -> dict:
    """Assign a Jira issue via PUT /assignee, degrading gracefully to a warning if Jira permissions disallow assignment."""
    acct = (account_id or "").strip()
    if not client or not acct:
        return {"mode": "offline" if (not client or not client.is_live) else "live", "assigned_to": None}
    try:
        return client.assign_issue(issue_key, acct)
    except Exception as exc:
        return {"mode": "live", "assigned_to": None, "warning": str(exc)}


def safe_add_jira_attachments(client: JiraClient, issue_key: str, validated_files: list) -> dict:
    """Upload validated attachments to a Jira issue, degrading gracefully to a warning if project permissions reject upload."""
    if not validated_files:
        return {"uploaded": [], "warning": None}
    try:
        uploaded = client.add_attachments(issue_key, validated_files)
        return {"uploaded": uploaded, "warning": None}
    except Exception as exc:
        fallback = [
            {
                "mode": "warning-local-only",
                "filename": vf["filename"],
                "size": vf["size"],
                "mime_type": vf["mime_type"],
                "sha256": vf["sha256"],
                "local_path": vf["local_path"],
            }
            for vf in validated_files
        ]
        return {"uploaded": fallback, "warning": str(exc)}


def _compute_description_hash(plain_text: str) -> str:
    norm = "\n".join(ln.strip() for ln in (plain_text or "").strip().splitlines() if ln.strip())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _is_jira_status_done(status_name: str, config: dict = None) -> bool:
    s = (status_name or "").strip().lower()
    if not s:
        return False
    done_mapped = (((config or {}).get("status_mapping") or {}).get("done") or "done").strip().lower()
    return s in {"done", "closed", "resolved", "completed", "完成", "已完成", done_mapped}


def reconcile_story_with_jira(
    client: JiraClient,
    config: dict,
    repo_root: Path,
    jira_key: str,
    story: dict = None,
    issue: dict = None,
    snapshot: dict = None,
    save: bool = True,
) -> tuple:
    """
    Bidirectional reconciliation between local `.ai-workflow/active/<KEY>.json` and live Jira Web UI edits.
    Merges newer cross-machine snapshots, manually added/completed/reopened/reassigned Sub-tasks,
    manually linked/closed Bugs, Story assignee/status/title updates, and Story Description edits.
    """
    clean_key = validate_jira_key(jira_key)
    delta = {
        "snapshot_adopted": False,
        "subtasks_added": [],
        "subtasks_completed": [],
        "subtasks_reopened": [],
        "bugs_added": [],
        "bugs_closed": [],
        "bugs_reopened": [],
        "acs_imported": [],
        "description_modified": False,
        "assignee_updated": False,
    }
    if story is None:
        p = get_active_dir(repo_root) / f"{clean_key}.json"
        story = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"jira_key": clean_key}
    if not client or not client.is_live:
        return story, delta

    changed = False
    if issue is None:
        try:
            issue = client.get_issue(clean_key)
        except Exception:
            return story, delta
    if not isinstance(issue, dict):
        return story, delta

    # 1. Cross-machine hidden snapshot freshness check
    remote_snap = snapshot if isinstance(snapshot, dict) else client.get_state_snapshot(clean_key)
    if isinstance(remote_snap, dict) and remote_snap.get("jira_key"):
        rem_ts = str(remote_snap.get("updated_at") or "")
        loc_ts = str(story.get("updated_at") or "")
        if rem_ts and (not loc_ts or rem_ts > loc_ts):
            merged_story = dict(remote_snap)
            merged_story["jira_key"] = clean_key
            story = merged_story
            delta["snapshot_adopted"] = True
            changed = True

    fields = issue.get("fields") or {}
    if not fields:
        return story, delta

    # 2. Story summary, status, and manual assignee sync
    remote_summary = (fields.get("summary") or "").strip()
    if remote_summary and remote_summary != clean_key and story.get("title") != remote_summary:
        if story.get("title"):
            story["title"] = remote_summary
            changed = True
        else:
            story["title"] = remote_summary
            changed = True

    remote_status = ((fields.get("status") or {}).get("name") or "").strip()
    if remote_status and story.get("jira_status") != remote_status:
        story["jira_status"] = remote_status
        changed = True

    remote_resolution = _extract_jira_resolution(fields)
    if "resolution" in fields and story.get("jira_resolution", "") != remote_resolution:
        story["jira_resolution"] = remote_resolution
        changed = True
    elif remote_resolution and story.get("jira_resolution") != remote_resolution:
        story["jira_resolution"] = remote_resolution
        changed = True

    # Scenario A (#4): Auto-unblock QA deploy gate when CI/CD transitions Jira Story to status_mapping.in_qa
    if get_effective_qa_handoff_mode(config) == "gated_by_deploy":
        deploy_gate = story.get("qa_deploy_gate")
        if isinstance(deploy_gate, dict) and deploy_gate.get("status") == "awaiting_qa_deploy":
            if deploy_gate.get("awaiting_in_qa_cycle"):
                if remote_status and not _is_jira_status_in_qa(remote_status, config):
                    deploy_gate["awaiting_in_qa_cycle"] = False
                    changed = True
            elif _is_jira_status_in_qa(remote_status, config):
                deploy_gate["status"] = "deployed"
                deploy_gate["commit"] = (
                    deploy_gate.get("commit")
                    or story.get("candidate_commit")
                    or (story.get("review") or {}).get("reviewed_commit")
                    or ""
                )
                deploy_gate["awaiting_in_qa_cycle"] = False
                deploy_gate["promoted_at"] = now_iso()
                deploy_gate["source"] = "jira_status_sync"
                delta["qa_deploy_unblocked"] = True
                changed = True

    ass_obj = fields.get("assignee")
    if isinstance(ass_obj, dict) and ass_obj.get("accountId"):
        acct_id = str(ass_obj.get("accountId") or "").strip()
        em_addr = str(ass_obj.get("emailAddress") or "").strip()
        pref_role = story.get("current_owner_role") or ""
        matched_role, matched_member = find_member_across_roles(
            config,
            account_id=acct_id,
            email=em_addr,
            preferred_role=pref_role,
        )
        if matched_role and matched_member:
            ro_map = story.setdefault("role_owners", {})
            serialized_m = serialize_member_for_config(matched_member, matched_role)
            if ro_map.get(matched_role) != serialized_m:
                ro_map[matched_role] = serialized_m
                delta["assignee_updated"] = True
                changed = True

    # 3. Reconcile Sub-tasks (`fields.subtasks`)
    tasks = story.setdefault("tasks", [])
    by_sub_key = {
        str(t.get("jira_subtask_key") or "").strip().upper(): t
        for t in tasks
        if isinstance(t, dict) and t.get("jira_subtask_key")
    }
    used_task_ids = {str(t.get("id") or "").strip().upper() for t in tasks if isinstance(t, dict) and t.get("id")}

    for sub in fields.get("subtasks") or []:
        if not isinstance(sub, dict):
            continue
        sub_key = str(sub.get("key") or "").strip().upper()
        if not sub_key:
            continue
        sub_fields = sub.get("fields") or {}
        if "assignee" not in sub_fields and sub_key not in by_sub_key:
            try:
                full_sub = client.get_issue(sub_key)
                if isinstance(full_sub, dict) and isinstance(full_sub.get("fields"), dict):
                    sub_fields = {**full_sub["fields"], **sub_fields}
            except Exception:
                pass
        sub_sum = str(sub_fields.get("summary") or sub_key).strip()
        sub_st_raw = str(((sub_fields.get("status") or {}).get("name")) or "").strip()
        sub_is_done = _is_jira_status_done(sub_st_raw, config)

        sub_assignee = None
        sub_ass_obj = sub_fields.get("assignee")
        if isinstance(sub_ass_obj, dict) and sub_ass_obj.get("accountId"):
            s_acct = str(sub_ass_obj.get("accountId") or "").strip()
            s_em = str(sub_ass_obj.get("emailAddress") or "").strip()
            _, found_dev = find_member_across_roles(config, account_id=s_acct, email=s_em, preferred_role="developer")
            if found_dev:
                sub_assignee = serialize_member_for_config(found_dev, "developer")
            else:
                sub_assignee = {
                    "name": str(sub_ass_obj.get("displayName") or s_acct).strip(),
                    "email": s_em,
                    "jira_account_id": s_acct,
                }

        clean_sub_title = re.sub(r"^\[?TASK-\d+\]?\s*:?\s*", "", sub_sum, flags=re.IGNORECASE).strip() or sub_sum
        if sub_key in by_sub_key:
            t_obj = by_sub_key[sub_key]
            if clean_sub_title and clean_sub_title != sub_key and t_obj.get("title") != clean_sub_title:
                t_obj["title"] = clean_sub_title
                changed = True
            if sub_assignee and t_obj.get("assignee") != sub_assignee:
                t_obj["assignee"] = sub_assignee
                changed = True
            if sub_is_done and t_obj.get("status") != "completed":
                t_obj["status"] = "completed"
                t_obj["commit"] = t_obj.get("commit") or story.get("candidate_commit") or _detect_git_head_commit(repo_root) or "jira-ui"
                t_obj["test_result"] = t_obj.get("test_result") or "Marked Done in Jira Web UI"
                t_obj["completed_at"] = t_obj.get("completed_at") or now_iso()
                t_obj["last_synced_jira_status"] = sub_st_raw or "Done"
                delta["subtasks_completed"].append(sub_key)
                changed = True
            elif (
                (not sub_is_done)
                and sub_st_raw
                and t_obj.get("status") == "completed"
                and _is_jira_status_done(t_obj.get("last_synced_jira_status", ""), config)
            ):
                t_obj["status"] = "pending"
                t_obj["last_synced_jira_status"] = sub_st_raw
                delta["subtasks_reopened"].append(sub_key)
                changed = True
            elif sub_st_raw and t_obj.get("last_synced_jira_status") != sub_st_raw:
                t_obj["last_synced_jira_status"] = sub_st_raw
                changed = True
        else:
            tm = re.search(r"(TASK-\d+)", sub_sum, re.IGNORECASE)
            cand_id = tm.group(1).upper() if tm else ""
            if not cand_id or cand_id in used_task_ids:
                next_idx = len(tasks) + 1
                while f"TASK-{next_idx:03d}" in used_task_ids:
                    next_idx += 1
                cand_id = f"TASK-{next_idx:03d}"
            used_task_ids.add(cand_id)
            acs_cov = [a["id"] for a in (story.get("acceptance_criteria") or []) if a.get("id")] or ["AC-001"]
            new_task = {
                "id": cand_id,
                "jira_subtask_key": sub_key,
                "title": clean_sub_title,
                "ac_coverage": acs_cov,
                "depends_on": [],
                "files": [],
                "verification": "Run automated tests",
                "status": "completed" if sub_is_done else "pending",
                "source": "jira-ui",
                "last_synced_jira_status": sub_st_raw or ("Done" if sub_is_done else "To Do"),
                "commit": (story.get("candidate_commit") or "jira-ui") if sub_is_done else None,
                "changed_files": [],
                "test_result": "Marked Done in Jira Web UI" if sub_is_done else None,
                "completed_at": now_iso() if sub_is_done else None,
            }
            if sub_assignee:
                new_task["assignee"] = sub_assignee
            tasks.append(new_task)
            by_sub_key[sub_key] = new_task
            delta["subtasks_added"].append(sub_key)
            changed = True

    # 4. Reconcile Linked Bugs (`fields.issuelinks`) — only for parent Stories, not standalone bugs
    bugs = story.setdefault("bugs", [])
    by_bug_key = {
        str(b.get("bug_key") or "").strip().upper(): b
        for b in bugs
        if isinstance(b, dict) and b.get("bug_key")
    }
    configured_bug_type = ((config.get("issue_type_mapping") or {}).get("bug") or "bug").strip().lower()
    iss_links = (fields.get("issuelinks") or []) if story.get("issue_kind") != "standalone_bug" else []
    for link in iss_links:
        if not isinstance(link, dict):
            continue
        linked = link.get("inwardIssue") or link.get("outwardIssue") or {}
        if not isinstance(linked, dict):
            continue
        b_key = str(linked.get("key") or "").strip().upper()
        if not b_key:
            continue
        l_fields = linked.get("fields") or {}
        l_type = str(((l_fields.get("issuetype") or {}).get("name")) or "").strip().lower()
        if l_type not in ("bug", "缺陷", configured_bug_type) and "BUG" not in b_key:
            continue
        b_st_raw = str(((l_fields.get("status") or {}).get("name")) or "").strip()
        b_is_done = _is_jira_status_done(b_st_raw, config)
        b_has_res_field = "resolution" in l_fields
        b_res_raw = _extract_jira_resolution(l_fields)
        if b_is_done and not b_res_raw and not b_has_res_field:
            try:
                full_bug = client.get_issue(b_key)
                if isinstance(full_bug, dict) and isinstance(full_bug.get("fields"), dict):
                    b_res_raw = _extract_jira_resolution(full_bug["fields"])
                    b_has_res_field = "resolution" in full_bug["fields"]
            except Exception:
                pass
        if b_key in by_bug_key:
            b_obj = by_bug_key[b_key]
            if b_st_raw and b_obj.get("jira_status") != b_st_raw:
                b_obj["jira_status"] = b_st_raw
                changed = True
            if b_has_res_field and b_obj.get("jira_resolution", "") != b_res_raw:
                b_obj["jira_resolution"] = b_res_raw
                changed = True
            elif b_res_raw and b_obj.get("jira_resolution") != b_res_raw:
                b_obj["jira_resolution"] = b_res_raw
                changed = True
            if b_is_done and b_obj.get("status") not in ("completed", "closed"):
                b_obj["status"] = "completed"
                b_obj["last_synced_jira_status"] = b_st_raw or "Done"
                delta["bugs_closed"].append(b_key)
                changed = True
            elif (
                (not b_is_done)
                and b_st_raw
                and b_obj.get("status") in ("completed", "closed")
                and _is_jira_status_done(b_obj.get("last_synced_jira_status", ""), config)
            ):
                b_obj["status"] = "open"
                b_obj["last_synced_jira_status"] = b_st_raw
                delta["bugs_reopened"].append(b_key)
                changed = True
            elif b_st_raw and b_obj.get("last_synced_jira_status") != b_st_raw:
                b_obj["last_synced_jira_status"] = b_st_raw
                changed = True
        else:
            new_bug = {
                "bug_key": b_key,
                "tc_id": "JIRA-UI",
                "ac_id": "",
                "title": str(l_fields.get("summary") or b_key).strip(),
                "severity": "major",
                "steps": "Created/linked in Jira Web UI",
                "expected": "See Jira issue",
                "actual": "See Jira issue",
                "reported_commit": story.get("candidate_commit") or "HEAD",
                "status": "completed" if b_is_done else "open",
                "source": "jira-ui",
                "jira_status": b_st_raw or ("Done" if b_is_done else "To Do"),
                "jira_resolution": b_res_raw,
                "last_synced_jira_status": b_st_raw or ("Done" if b_is_done else "To Do"),
                "created_at": now_iso(),
            }
            bugs.append(new_bug)
            by_bug_key[b_key] = new_bug
            delta["bugs_added"].append(b_key)
            changed = True

    # 5. Story Description Drift Guard & Manual AC Import (skipped when preserving original bug report)
    raw_desc = fields.get("description")
    if (
        raw_desc is not None
        and story.get("last_synced_description_hash")
        and not story.get("preserve_remote_description")
        and story.get("issue_kind") != "standalone_bug"
    ):
        remote_plain = _adf_to_plain_text(raw_desc).strip()
        if remote_plain:
            rem_hash = _compute_description_hash(remote_plain)
            if rem_hash != story.get("last_synced_description_hash"):
                story["remote_description_modified"] = True
                story["remote_description_text"] = remote_plain
                delta["description_modified"] = True
                existing_acs = story.setdefault("acceptance_criteria", [])
                known_ac_ids = {str(a.get("id") or "").strip().upper() for a in existing_acs if isinstance(a, dict)}
                for m_ac in re.finditer(r"(AC-\d+)\s*\*{0,2}\s*[:：]\s*(.+)", remote_plain):
                    ac_id = m_ac.group(1).strip().upper()
                    ac_desc = m_ac.group(2).strip()
                    if ac_desc and ac_id not in known_ac_ids:
                        existing_acs.append({"id": ac_id, "description": ac_desc})
                        known_ac_ids.add(ac_id)
                        delta["acs_imported"].append(ac_id)
                changed = True

    if changed and save:
        save_active_story(repo_root, clean_key, story, client=client, sync_remote=True)
    return story, delta


def _search_jql_issues(client: JiraClient, jql: str, fields: list, max_results: int = 100) -> list:
    """Query Jira issues via POST /rest/api/3/search/jql with /search HTTP 404 fallback."""
    if not client or not client.is_live or not (jql or "").strip():
        return []
    payload = {
        "jql": jql,
        "maxResults": max_results,
        "fields": list(fields or ["summary", "status", "issuetype", "parent"]),
    }
    try:
        res = client._request("POST", "/rest/api/3/search/jql", payload)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        res = client._request("POST", "/rest/api/3/search", payload)
    issues = (res or {}).get("issues") if isinstance(res, dict) else []
    return issues if isinstance(issues, list) else []


def _fetch_epic_children_live(client: JiraClient, config: dict, epic_key: str) -> tuple:
    """
    Fetch child issues under a Jira Epic using `parent = "<EPIC>"` with `"Epic Link" = "<EPIC>"` fallback.
    Returns (filtered_issues, query_mode, warnings).
    """
    clean_epic = validate_jira_key(epic_key)
    if not client or not client.is_live:
        return ([], "offline", [])

    fields = ["summary", "status", "issuetype", "parent", "assignee", "subtasks", "issuelinks", "resolution"]
    cfg_elf = _clean_optional_text((config or {}).get("epic_link_field"))
    if cfg_elf and cfg_elf not in fields:
        fields.append(cfg_elf)

    warnings = []
    issues_raw = []
    query_mode = "parent"
    parent_failed_err = ""

    try:
        issues_raw = _search_jql_issues(client, f'parent = "{clean_epic}"', fields)
    except RuntimeError as exc:
        if "HTTP 400" in str(exc) or "HTTP 404" in str(exc):
            parent_failed_err = str(exc)
            issues_raw = []
        else:
            warnings.append(f"Failed to query Epic {clean_epic} children via parent JQL: {exc}")
            return ([], "error", warnings)

    if parent_failed_err or not issues_raw:
        try:
            issues_fallback = _search_jql_issues(client, f'"Epic Link" = "{clean_epic}"', fields)
            if parent_failed_err or issues_fallback:
                issues_raw = issues_fallback
                query_mode = "epic_link"
        except RuntimeError as exc2:
            if parent_failed_err:
                warnings.append(
                    f"Failed to query Epic {clean_epic} children (parent query: {parent_failed_err}; Epic Link query: {exc2})."
                )
                return ([], "error", warnings)

    filtered = []
    seen_keys = set()
    skip_types = {"epic", "sub-task", "subtask", "子任務"}
    for iss in issues_raw:
        if not isinstance(iss, dict):
            continue
        k = _clean_optional_text(iss.get("key")).upper()
        if not k or k == clean_epic or k in seen_keys:
            continue
        flds = iss.get("fields") or {}
        itype = flds.get("issuetype") or {}
        if itype.get("subtask") is True:
            continue
        itype_name = _clean_optional_text(itype.get("name")).lower()
        if itype_name in skip_types:
            continue
        seen_keys.add(k)
        filtered.append(iss)
    return (filtered, query_mode, warnings)


def hydrate_story_from_remote(client: JiraClient, repo_root: Path, jira_key: str, persist: bool = True):
    """
    Auto-hydrate .ai-workflow/active/<KEY>.json from remote Jira issue when local file is missing.
    Supports hidden Jira Issue Property (ai_workflow_state), legacy [ai-workflow-state] snapshot
    extraction, structural reconstruction from standard Jira fields, and live Jira UI reconciliation.
    """
    if not client or not client.is_live:
        return None
    clean_key = validate_jira_key(jira_key)
    try:
        issue = client.get_issue(clean_key)
    except Exception:
        return None
    if not issue or not isinstance(issue, dict) or ("fields" not in issue and "key" not in issue):
        return None

    fields = issue.get("fields") or {}
    if not fields and issue.get("key") != clean_key:
        return None

    # 1. Check hidden Jira Issue Property (ai_workflow_state) first, then legacy comments/description
    snapshot = client.get_state_snapshot(clean_key)
    comments_list = ((fields.get("comment") or {}).get("comments")) or []
    if not snapshot:
        for c_item in reversed(comments_list):
            c_text = _adf_to_plain_text(c_item.get("body"))
            snapshot = _extract_state_snapshot_from_text(c_text)
            if snapshot:
                break
    desc_text = _adf_to_plain_text(fields.get("description"))
    if not snapshot:
        snapshot = _extract_state_snapshot_from_text(desc_text)

    if snapshot:
        story = dict(snapshot)
        story["jira_key"] = clean_key
    else:
        # 2. Structural reconstruction from Jira fields
        summary = (fields.get("summary") or clean_key).strip()
        status_name = ((fields.get("status") or {}).get("name") or "").strip()
        itype_name = (((fields.get("issuetype") or {}).get("name")) or "").strip().lower()
        cfg_bug_type = (((client.config or {}).get("issue_type_mapping") or {}).get("bug") or "bug").strip().lower()
        if itype_name in ("bug", "缺陷", cfg_bug_type):
            is_done_already = _is_jira_status_done(status_name, client.config)
            story = {
                "jira_key": clean_key,
                "issue_kind": "standalone_bug",
                "preserve_remote_description": True,
                "title": summary,
                "scope_in": summary,
                "scope_out": "N/A",
                "change_surface": {"has_ui": False, "has_api": False, "has_data": False},
                "risk_level": "medium",
                "acceptance_criteria": [{"id": "AC-001", "description": f"修復報修問題：{summary}"}],
                "design": {"summary": "Standalone bug report hydrated from Jira (original description preserved)."},
                "bug_report": {
                    "severity": "major",
                    "steps": (desc_text or summary).strip(),
                    "expected": f"修復報修問題：{summary}",
                    "actual": summary,
                },
                "spec_status": "approved",
                "jira_status": status_name or "To Do",
                "tasks": [
                    {
                        "id": "TASK-001",
                        "jira_subtask_key": None,
                        "title": f"修復報修問題：{summary}",
                        "ac_coverage": ["AC-001"],
                        "depends_on": [],
                        "files": [],
                        "verification": "Run regression tests & verify bug steps",
                        "status": "completed" if is_done_already else "pending",
                        "last_synced_jira_status": status_name or "To Do",
                        "commit": "HEAD" if is_done_already else None,
                        "changed_files": [],
                        "test_result": "Hydrated from Jira" if is_done_already else None,
                        "completed_at": now_iso() if is_done_already else None,
                    }
                ],
                "bugs": [],
                "qa": {
                    "status": "passed" if is_done_already else "planned",
                    "test_cases": [
                        {
                            "id": "TC-001",
                            "ac_id": "AC-001",
                            "title": f"回歸驗證：{summary}",
                            "execution_type": "manual",
                            "recommended_by_ai": True,
                            "result": "pass" if is_done_already else "not-executed",
                        }
                    ],
                },
            }
            if status_name.lower() in ("in qa", "ready for release") or is_done_already:
                story["review"] = {"status": "approved", "reviewed_commit": "HEAD", "findings": []}
            if status_name.lower() == "ready for release" or is_done_already:
                story["qa"]["status"] = "passed"
                story["qa"]["tested_commit"] = "HEAD"
                story["qa"]["test_cases"][0]["result"] = "pass"
        else:
            acs = []
            for m in re.finditer(r"(AC-\d+)\s*\*{0,2}\s*[:：]\s*(.+)", desc_text or ""):
                ac_id = m.group(1).strip().upper()
                ac_desc = m.group(2).strip()
                if ac_desc and not any(a["id"] == ac_id for a in acs):
                    acs.append({"id": ac_id, "description": ac_desc})
            if not acs and status_name.lower() not in ("", "to do", "todo"):
                acs = [{"id": "AC-001", "description": summary}]

            spec_status = "approved" if (acs or status_name.lower() not in ("", "to do", "todo")) else "draft"
            tasks = []
            for idx, sub in enumerate(fields.get("subtasks") or [], start=1):
                sub_key = sub.get("key") or f"{clean_key}-SUB-{idx}"
                sub_fields = sub.get("fields") or {}
                sub_sum = (sub_fields.get("summary") or f"TASK-{idx:03d}").strip()
                sub_st_raw = ((sub_fields.get("status") or {}).get("name") or "").strip()
                tm = re.search(r"(TASK-\d+)", sub_sum, re.IGNORECASE)
                t_id = tm.group(1).upper() if tm else f"TASK-{idx:03d}"
                clean_title = re.sub(r"^\[?TASK-\d+\]?\s*:?\s*", "", sub_sum, flags=re.IGNORECASE).strip() or sub_sum
                is_done = _is_jira_status_done(sub_st_raw, client.config)
                tasks.append({
                    "id": t_id,
                    "jira_subtask_key": sub_key,
                    "title": clean_title,
                    "ac_coverage": [a["id"] for a in acs] if acs else ["AC-001"],
                    "depends_on": [],
                    "files": [],
                    "verification": "Run automated tests",
                    "status": "completed" if is_done else "pending",
                    "last_synced_jira_status": sub_st_raw or ("Done" if is_done else "To Do"),
                    "commit": "HEAD" if is_done else None,
                    "changed_files": [],
                    "test_result": "Hydrated from Jira" if is_done else None,
                    "completed_at": now_iso() if is_done else None,
                })

            bugs = []
            for idx, link in enumerate(fields.get("issuelinks") or [], start=1):
                linked = link.get("inwardIssue") or link.get("outwardIssue") or {}
                l_fields = linked.get("fields") or {}
                l_type = ((l_fields.get("issuetype") or {}).get("name") or "").lower()
                if l_type in ("bug", "缺陷") or "BUG" in (linked.get("key") or ""):
                    b_key = linked.get("key") or f"{clean_key}-BUG-{idx}"
                    b_st_raw = ((l_fields.get("status") or {}).get("name") or "").strip()
                    b_res_raw = _extract_jira_resolution(l_fields)
                    b_done = _is_jira_status_done(b_st_raw, client.config)
                    bugs.append({
                        "bug_key": b_key,
                        "tc_id": "TC-001",
                        "ac_id": "AC-001",
                        "title": l_fields.get("summary") or b_key,
                        "severity": "major",
                        "steps": "Hydrated from Jira issueLink",
                        "expected": "Pass",
                        "actual": "Fail",
                        "reported_commit": "HEAD",
                        "status": "completed" if b_done else "open",
                        "jira_status": b_st_raw or ("Done" if b_done else "To Do"),
                        "jira_resolution": b_res_raw,
                        "last_synced_jira_status": b_st_raw or ("Done" if b_done else "To Do"),
                        "created_at": now_iso(),
                    })

            story = {
                "jira_key": clean_key,
                "title": summary,
                "scope_in": summary,
                "scope_out": "N/A",
                "change_surface": {"has_ui": False, "has_api": False, "has_data": False},
                "risk_level": "medium",
                "acceptance_criteria": acs,
                "design": {"summary": "Hydrated from remote Jira issue."},
                "spec_status": spec_status,
                "jira_status": status_name or "In Progress",
                "tasks": tasks,
                "bugs": bugs,
            }
            if status_name.lower() in ("in qa", "ready for release", "done"):
                story["review"] = {"status": "approved", "reviewed_commit": "HEAD", "findings": []}
            if status_name.lower() in ("ready for release", "done"):
                story["qa"] = {
                    "status": "passed",
                    "tested_commit": "HEAD",
                    "test_cases": [
                        {
                            "id": f"TC-{idx:03d}",
                            "ac_id": ac["id"],
                            "title": ac["description"],
                            "execution_type": "automated",
                            "recommended_by_ai": True,
                            "result": "pass",
                        }
                        for idx, ac in enumerate(acs, start=1)
                    ],
                }

    parent_obj = fields.get("parent") if isinstance(fields.get("parent"), dict) else {}
    parent_key = _clean_optional_text(parent_obj.get("key")).upper()
    parent_type = _clean_optional_text(((parent_obj.get("fields") or {}).get("issuetype") or {}).get("name")).lower()
    cfg_elf = _clean_optional_text(((client.config or {}).get("epic_link_field")))
    elf_val = _clean_optional_text(fields.get(cfg_elf)).upper() if cfg_elf else ""
    if not story.get("epic_key"):
        if parent_key and (not parent_type or parent_type == "epic"):
            story["epic_key"] = parent_key
        elif elf_val:
            story["epic_key"] = elf_val

    story, _ = reconcile_story_with_jira(
        client=client,
        config=client.config or load_team_config(repo_root),
        repo_root=repo_root,
        jira_key=clean_key,
        story=story,
        issue=issue,
        snapshot=snapshot,
        save=False,
    )
    if persist:
        save_active_story(repo_root, clean_key, story, client=None, sync_remote=False)
    return story


def load_active_story(
    repo_root: Path,
    jira_key: str,
    client: JiraClient = None,
    reconcile: bool = True,
) -> dict:
    clean_key = validate_jira_key(jira_key)
    p = get_active_dir(repo_root) / f"{clean_key}.json"
    if p.exists():
        story = json.loads(p.read_text(encoding="utf-8"))
        if reconcile and client is not None and client.is_live:
            cfg = client.config or load_team_config(repo_root)
            story, _ = reconcile_story_with_jira(client, cfg, repo_root, clean_key, story=story, save=True)
        return story

    # 1. Try hydrating from main Git worktree if in a secondary worktree
    wt_story = _hydrate_story_from_worktree(repo_root, clean_key)
    if wt_story is not None:
        if reconcile and client is not None and client.is_live:
            cfg = client.config or load_team_config(repo_root)
            wt_story, _ = reconcile_story_with_jira(client, cfg, repo_root, clean_key, story=wt_story, save=True)
        return wt_story

    # 2. Try auto-hydrating from remote Jira when client is in live mode
    eff_client = client
    if eff_client is None:
        try:
            eff_client = JiraClient(load_team_config(repo_root), repo_root=repo_root)
        except Exception:
            eff_client = None
    if eff_client is not None and eff_client.is_live:
        rem_story = hydrate_story_from_remote(eff_client, repo_root, clean_key)
        if rem_story is not None:
            return rem_story

    raise FileNotFoundError(f"Active story state not found: {p}")


def save_active_story(
    repo_root: Path,
    jira_key: str,
    data: dict,
    client: JiraClient = None,
    sync_remote: bool = True,
) -> Path:
    clean_key = validate_jira_key(jira_key)
    active_dir = get_active_dir(repo_root)
    active_dir.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_iso()
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    p = active_dir / f"{clean_key}.json"
    p.write_text(serialized, encoding="utf-8")

    # Mirror to main worktree if inside a secondary Git worktree
    main_root = _resolve_main_worktree_root(repo_root)
    if main_root:
        try:
            main_active = get_active_dir(main_root)
            main_active.mkdir(parents=True, exist_ok=True)
            (main_active / f"{clean_key}.json").write_text(serialized, encoding="utf-8")
        except Exception:
            pass

    if sync_remote and client is not None and client.is_live:
        client.sync_state_snapshot(clean_key, data)
    return p


def _handoff_governance_fields(handoff_record: dict) -> dict:
    """Build explicit SoD & Human-in-the-Loop governance flags for handoff JSON outputs."""
    same_person = bool((handoff_record or {}).get("same_person"))
    return {
        "same_person": same_person,
        "can_auto_chain_next_station": False,
        "requires_user_confirmation_for_next_station": True,
        "cross_person_clean_stop": not same_person,
        "next_station_prompt_allowed": same_person,
        "next_station_owner": str((handoff_record or {}).get("to_person") or ""),
    }


def _resolve_local_actor_identity(repo_root: Path, config: dict):
    """Resolve the current local machine actor identity from env / local credentials matched against team-config.json."""
    local_creds = load_jira_credentials(repo_root)
    actor_email = (os.environ.get("JIRA_EMAIL") or local_creds.get("jira_email") or "").strip()
    if not actor_email:
        mc = local_creds.get("member_credentials") or {}
        if isinstance(mc, dict) and len(mc) == 1:
            only_entry = next(iter(mc.values()))
            actor_email = str((only_entry or {}).get("jira_email") or "").strip()
    if not actor_email:
        rc = local_creds.get("role_credentials") or {}
        if isinstance(rc, dict):
            rc_emails = {
                str((v or {}).get("jira_email") or "").strip().lower()
                for v in rc.values()
                if isinstance(v, dict) and str((v or {}).get("jira_email") or "").strip()
            }
            if len(rc_emails) == 1:
                actor_email = next(iter(rc_emails))
    if not actor_email:
        return None

    matched_roles = []
    matched_name = ""
    matched_acct = ""
    em_lower = actor_email.lower()
    for rk in (config.get("roles") or {}).keys():
        for m in resolve_role_members(config, rk):
            if not _is_member_claimed(rk, m):
                continue
            if (m.get("email") or "").strip().lower() == em_lower:
                if rk not in matched_roles:
                    matched_roles.append(rk)
                if not matched_name and m.get("name"):
                    matched_name = str(m["name"]).strip()
                if not matched_acct and m.get("jira_account_id"):
                    matched_acct = str(m["jira_account_id"]).strip()
    return {
        "name": matched_name or actor_email,
        "email": actor_email,
        "jira_account_id": matched_acct,
        "roles": matched_roles,
    }


def _build_qa_identity_guardrail(repo_root: Path, config: dict, story: dict = None) -> dict:
    """Compare current local actor against explicitly configured roles.qa for Station 3b SoD Entry Identity Guardrail."""
    actor = _resolve_local_actor_identity(repo_root, config)
    claimed_qa = [m for m in resolve_role_members(config, "qa") if _is_member_claimed("qa", m)]
    if not claimed_qa and isinstance(story, dict):
        ro_qa = (story.get("role_owners") or {}).get("qa")
        if isinstance(ro_qa, dict) and _is_member_claimed("qa", ro_qa):
            claimed_qa.append(_normalize_member_dict("qa", ro_qa))

    qa_role_claimed = len(claimed_qa) > 0
    identifiers_comparable = True
    if not qa_role_claimed:
        actor_matches_qa = False if actor is not None else None
    elif actor is None:
        actor_matches_qa = None
    else:
        identifiers_comparable = (
            bool(actor.get("roles"))
            or bool(actor.get("jira_account_id"))
            or any(bool(qm.get("email")) for qm in claimed_qa)
        )
        if identifiers_comparable:
            actor_matches_qa = any(is_same_person(actor, qm) for qm in claimed_qa)
        else:
            actor_matches_qa = None

    story_devs = []
    if isinstance(story, dict):
        for t in story.get("tasks") or []:
            t_ass = t.get("assignee")
            if isinstance(t_ass, dict) and _is_member_claimed("developer", t_ass):
                story_devs.append(t_ass)
        if not story_devs:
            ro_dev = (story.get("role_owners") or {}).get("developer")
            if isinstance(ro_dev, dict) and _is_member_claimed("developer", ro_dev):
                story_devs.append(ro_dev)
    if not story_devs:
        story_devs = [m for m in resolve_role_members(config, "developer") if _is_member_claimed("developer", m)]
    actor_is_story_developer = bool(actor is not None and any(is_same_person(actor, d) for d in story_devs))

    identity_mismatch = bool(qa_role_claimed and actor is not None and actor_matches_qa is False)
    if qa_role_claimed and actor is not None and not identifiers_comparable:
        warning = None
    else:
        warning = ""
    if identity_mismatch:
        qa_labels = ", ".join(
            f"{q.get('name') or 'qa'} ({q.get('email')})" if q.get("email") else str(q.get("name") or "qa")
            for q in claimed_qa
        )
        actor_label = f"{actor.get('name')} ({actor.get('email')})" if actor.get("email") else str(actor.get("name") or "")
        dev_note = " (who is also the Story developer)" if actor_is_story_developer else ""
        warning = (
            f"SoD Identity Mismatch: current local actor {actor_label}{dev_note} does not match configured "
            f"roles.qa ({qa_labels}). Warn the user clearly and require explicit user confirmation before executing QA verification."
        )

    return {
        "qa_role_claimed": qa_role_claimed,
        "current_actor": actor,
        "configured_qa_owners": [serialize_member_for_config(q, "qa") for q in claimed_qa],
        "actor_matches_qa": actor_matches_qa,
        "actor_is_story_developer": actor_is_story_developer,
        "identity_mismatch": identity_mismatch,
        "requires_explicit_confirmation": identity_mismatch,
        "warning": warning,
    }


def execute_role_handoff(
    client: JiraClient,
    config: dict,
    story: dict,
    from_role: str,
    to_role: str,
    status_key: str,
    comment_md: str,
    from_person_override: dict = None,
    to_assignee: str = None,
) -> dict:
    """Execute role handoff with multi-role/multi-member awareness, sticky role_owners, and Jira assignee update."""
    zh = is_zh_tw(config)
    from_person = from_person_override or resolve_acting_person(config, from_role, client=client, story=story)
    to_person = resolve_story_role_person(config, story, to_role, selector=to_assignee)
    if _is_member_claimed(to_role, to_person):
        story.setdefault("role_owners", {})[to_role] = serialize_member_for_config(to_person, to_role)
    target_jira_status = config.get("status_mapping", {}).get(status_key, status_key)
    jira_key = story["jira_key"]

    from_badge = format_person_badge(from_role, from_person)
    to_badge = format_person_badge(to_role, to_person)
    attr_label = "站點角色與負責人" if zh else "Station Role & Owner"
    attribution_line = f"- **{attr_label}**：{from_badge} → {to_badge}" if zh else f"- **{attr_label}**: {from_badge} → {to_badge}"
    full_comment_md = f"{comment_md}\n{attribution_line}" if attribution_line not in comment_md else comment_md

    if is_same_person(from_person, to_person):
        # Same person holds both roles: skip @mention notification noise, but ensure issue is assigned to them
        if to_person.get("jira_account_id"):
            safe_assign_issue(client, jira_key, to_person["jira_account_id"])
        client.add_comment(jira_key, full_comment_md)
        client.transition_issue(jira_key, target_jira_status)
        handoff_record = {
            "timestamp": now_iso(),
            "from_role": from_role,
            "to_role": to_role,
            "from_person": from_person["name"],
            "to_person": to_person["name"],
            "next_station_owner": to_person["name"],
            "same_person": True,
            "can_auto_chain_next_station": False,
            "requires_user_confirmation_for_next_station": True,
            "cross_person_clean_stop": False,
            "next_station_prompt_allowed": True,
            "handoff_mode": "auto-consolidated",
            "target_jira_status": target_jira_status,
            "assigned_account_id": to_person.get("jira_account_id", ""),
            "notified": False,
        }
    else:
        # Different people (or target role not yet claimed by a collaborator): transition status, reassign if known, and comment
        role_claimed = _is_role_claimed(to_role, (config.get("roles") or {}).get(to_role))
        client.transition_issue(jira_key, target_jira_status)
        if to_person.get("jira_account_id"):
            safe_assign_issue(client, jira_key, to_person["jira_account_id"])
        if role_claimed:
            client.add_comment(
                jira_key,
                full_comment_md,
                mention_account_id=to_person.get("jira_account_id", ""),
                mention_name=to_person.get("name", to_role),
            )
            mention_val = f"@{to_person.get('name') or to_role}"
            notified_val = True
        else:
            pending_note = (
                f"- **下一站待認領角色**：`{to_role}` 尚待協作成員執行 `$team-next-step` / `auth` 認領"
                if zh
                else f"- **Next Role Pending Claim**: `{to_role}` awaiting collaborator claim via `$team-next-step` / `auth`"
            )
            unclaimed_note = f"{full_comment_md}\n{pending_note}"
            client.add_comment(jira_key, unclaimed_note)
            mention_val = ""
            notified_val = False
        handoff_record = {
            "timestamp": now_iso(),
            "from_role": from_role,
            "to_role": to_role,
            "from_person": from_person["name"],
            "to_person": to_person["name"],
            "next_station_owner": to_person["name"],
            "same_person": False,
            "can_auto_chain_next_station": False,
            "requires_user_confirmation_for_next_station": True,
            "cross_person_clean_stop": True,
            "next_station_prompt_allowed": False,
            "role_claimed": role_claimed,
            "handoff_mode": "jira-notification" if role_claimed else "awaiting-role-claim",
            "target_jira_status": target_jira_status,
            "assigned_account_id": to_person.get("jira_account_id", ""),
            "mention": mention_val,
            "notified": notified_val,
        }

    story.setdefault("handoffs", []).append(handoff_record)
    story["current_owner_role"] = to_role
    story["jira_status"] = target_jira_status
    return handoff_record


# ---------------------------------------------------------------------------
# Subcommand 1: init
# ---------------------------------------------------------------------------

def _find_markdown_headings_outside_fences(text: str) -> list:
    """
    Return list of (start_offset, level, title) for Markdown headings (#..###)
    ignoring any lines inside fenced code blocks (``` or ~~~).
    """
    headings = []
    in_fence = False
    fence_marker = ""
    offset = 0
    fence_re = re.compile(r"^\s*(`{3,}|~{3,})")
    heading_re = re.compile(r"^(#{1,3})\s+(.+?)\s*$")

    for line in text.splitlines(keepends=True):
        fm = fence_re.match(line)
        if fm:
            marker_char = fm.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker_char
            elif marker_char == fence_marker:
                in_fence = False
                fence_marker = ""
        elif not in_fence:
            hm = heading_re.match(line.rstrip("\r\n"))
            if hm:
                headings.append((offset, len(hm.group(1)), hm.group(2).strip()))
        offset += len(line)
    return headings


def extract_and_trim_agents_md(agents_path: Path, standards_path: Path) -> dict:
    """
    Extract engineering standards from AGENTS.md into docs/shared/engineering-standards.md
    and replace duplicated standards sections in AGENTS.md with a reference link.
    """
    standards_created = False
    standards_updated = False
    agents_trimmed = False

    standards_path.parent.mkdir(parents=True, exist_ok=True)

    if not agents_path.exists():
        if not standards_path.exists():
            standards_path.write_text(DEFAULT_STANDARDS_TEMPLATE, encoding="utf-8")
            standards_created = True
        return {
            "standards_path": str(standards_path),
            "standards_created": standards_created,
            "standards_updated": standards_updated,
            "agents_md_exists": False,
            "agents_trimmed": False,
        }

    agents_text = agents_path.read_text(encoding="utf-8")

    standards_keywords = (
        "engineering standard",
        "coding standard",
        "code style",
        "security",
        "testing standard",
        "testing rule",
        "architecture rule",
        "git commit",
        "commit message",
        "conventional commit",
        "pull request",
        "pr and commit",
        "git workflow",
        "git convention",
        "branching",
        "規範",
        "安全",
        "測試標準",
        "編碼規範",
        "工程標準",
        "提交",
        "分支",
    )
    standards_word_patterns = (
        re.compile(r"(?<![a-z0-9])pr(?![a-z0-9])", re.IGNORECASE),
        re.compile(r"(?<![a-z0-9])commits?(?![a-z0-9])", re.IGNORECASE),
    )

    matches = _find_markdown_headings_outside_fences(agents_text)
    extracted_blocks = []
    keep_ranges = []
    last_idx = 0

    idx = 0
    while idx < len(matches):
        start, level, title = matches[idx]

        # Find where this section ends (next heading of same or higher level outside code fences)
        end = len(agents_text)
        next_idx = idx + 1
        while next_idx < len(matches):
            if matches[next_idx][1] <= level:
                end = matches[next_idx][0]
                break
            next_idx += 1

        section_body = agents_text[start:end]
        is_pointer_section = AGENTS_STANDARDS_POINTER_MARKER in section_body
        title_lower = title.lower()
        is_standards_heading = any(k in title_lower for k in standards_keywords) or any(
            p.search(title) for p in standards_word_patterns
        )
        if not is_pointer_section and is_standards_heading:
            keep_ranges.append((last_idx, start))
            extracted_blocks.append(section_body.strip())
            last_idx = end
            idx = next_idx
        else:
            idx += 1

    keep_ranges.append((last_idx, len(agents_text)))

    if not standards_path.exists():
        if extracted_blocks:
            content = (
                "---\n"
                "artifact_id: ENG-STANDARDS\n"
                "artifact_type: engineering-standards\n"
                "validity: current\n"
                "owner_role: Tech Lead\n"
                "---\n"
                "# Shared Engineering Standards (工程標準規範)\n\n"
                "本文件由 `team-init` 自 `AGENTS.md` 萃取並收斂，作為專案唯一權威的工程標準來源。\n\n"
                + "\n\n".join(extracted_blocks)
                + "\n"
            )
            standards_path.write_text(content, encoding="utf-8")
        else:
            standards_path.write_text(DEFAULT_STANDARDS_TEMPLATE, encoding="utf-8")
        standards_created = True
    elif extracted_blocks:
        existing_std = standards_path.read_text(encoding="utf-8")
        missing_blocks = [b for b in extracted_blocks if b not in existing_std]
        if missing_blocks:
            merged_std = existing_std.rstrip() + "\n\n" + "\n\n".join(missing_blocks) + "\n"
            standards_path.write_text(merged_std, encoding="utf-8")
            standards_updated = True

    if extracted_blocks:
        rebuilt = "".join(agents_text[s:e] for s, e in keep_ranges).strip()
        if AGENTS_STANDARDS_POINTER_MARKER not in rebuilt:
            rebuilt = rebuilt + "\n\n" + AGENTS_STANDARDS_POINTER_BLOCK
        else:
            rebuilt = rebuilt + "\n"
        agents_path.write_text(rebuilt, encoding="utf-8")
        agents_trimmed = True
    elif AGENTS_STANDARDS_POINTER_MARKER not in agents_text:
        updated = agents_text.rstrip() + "\n\n" + AGENTS_STANDARDS_POINTER_BLOCK
        agents_path.write_text(updated, encoding="utf-8")
        agents_trimmed = True

    return {
        "standards_path": str(standards_path),
        "standards_created": standards_created,
        "standards_updated": standards_updated,
        "extracted_sections_count": len(extracted_blocks),
        "agents_md_exists": True,
        "agents_trimmed": agents_trimmed,
    }


def _is_role_claimed(role_key: str, entry) -> bool:
    """Return True if a role in team-config.json has at least one claimed member (not left at default placeholder)."""
    members = resolve_role_members({"roles": {role_key: entry}}, role_key)
    return any(_is_member_claimed(role_key, m) for m in members)


def _partition_claimed_roles(config: dict) -> tuple:
    """Return (claimed_roles_dict, unclaimed_roles_list) across the 6 standard workflow roles."""
    claimed = {}
    unclaimed = []
    for r_key in DEFAULT_TEAM_CONFIG["roles"]:
        members = [m for m in resolve_role_members(config, r_key) if _is_member_claimed(r_key, m)]
        if members:
            claimed[r_key] = serialize_role_entry(r_key, members)
        else:
            unclaimed.append(r_key)
    return claimed, unclaimed


def upsert_role_member(
    existing_entry,
    role_key: str,
    new_member: dict,
    explicit_claim: bool = True,
    replace: bool = False,
    explicit_name: bool = False,
    explicit_specialty: bool = False,
) -> dict:
    """
    Non-destructively append or update a member in a role pool.
    - Single claimed member serializes cleanly as {"name", "email", "jira_account_id"[, "specialty"]}.
    - Multiple claimed members serialize with `"members": [...]` while preserving primary fields on top-level.
    """
    existing_members = [
        m for m in resolve_role_members({"roles": {role_key: existing_entry}}, role_key)
        if _is_member_claimed(role_key, m)
    ]
    if replace or not existing_members:
        return serialize_role_entry(role_key, [new_member])

    new_em = (new_member.get("email") or "").strip().lower()
    matched_idx = None
    for idx, old_m in enumerate(existing_members):
        if is_same_person(old_m, new_member) or (new_em and (old_m.get("email") or "").strip().lower() == new_em):
            matched_idx = idx
            break

    if matched_idx is not None:
        old_m = existing_members[matched_idx]
        if explicit_claim:
            merged_m = {
                "name": new_member.get("name") if explicit_name else (new_member.get("name") or old_m.get("name") or role_key),
                "email": new_member.get("email") or old_m.get("email") or "",
                "jira_account_id": new_member.get("jira_account_id") or old_m.get("jira_account_id") or "",
                "specialty": new_member.get("specialty") if explicit_specialty else (new_member.get("specialty") or old_m.get("specialty") or ""),
            }
        else:
            merged_m = {
                "name": new_member.get("name") if explicit_name else (old_m.get("name") or new_member.get("name") or role_key),
                "email": old_m.get("email") or new_member.get("email") or "",
                "jira_account_id": old_m.get("jira_account_id") or new_member.get("jira_account_id") or "",
                "specialty": new_member.get("specialty") if explicit_specialty else (old_m.get("specialty") or ""),
            }
        existing_members[matched_idx] = merged_m
    elif explicit_claim:
        existing_members.append(new_member)

    return serialize_role_entry(role_key, existing_members)


def _parse_role_list(raw_role: str) -> list:
    """Parse comma-separated role names (e.g. 'pm,developer,qa') and validate against known roles."""
    if not raw_role or not raw_role.strip():
        return []
    valid_roles = set(DEFAULT_TEAM_CONFIG["roles"].keys()) | {"independent_reviewer"}
    parsed = []
    for part in raw_role.split(","):
        rk = part.strip().lower()
        if not rk:
            continue
        if rk not in valid_roles:
            raise ValueError(f"Unknown role '{rk}'. Expected one of: {', '.join(sorted(valid_roles))}.")
        if rk not in parsed:
            parsed.append(rk)
    return parsed


def _are_roles_unconfigured(roles: dict) -> bool:
    return not any(_is_role_claimed(rk, (roles or {}).get(rk)) for rk in DEFAULT_TEAM_CONFIG["roles"])


def _inspect_init_state(repo_root: Path) -> dict:
    cfg_path = get_config_path(repo_root)
    cred_path = get_credentials_path(repo_root)
    dotenv_path = get_dotenv_path(repo_root)
    cfg = load_team_config(repo_root)
    local_creds = load_jira_credentials(repo_root)

    eff_url = (
        os.environ.get("JIRA_BASE_URL")
        or cfg.get("jira_base_url")
        or local_creds.get("jira_base_url")
        or ""
    ).strip()
    eff_email = (os.environ.get("JIRA_EMAIL") or local_creds.get("jira_email") or "").strip()
    eff_token = (os.environ.get("JIRA_API_TOKEN") or local_creds.get("jira_api_token") or "").strip()
    role_creds = local_creds.get("role_credentials") or {}
    has_any_role_token = any(isinstance(v, dict) and v.get("jira_api_token") for v in role_creds.values())
    has_any_creds = bool((eff_email and eff_token) or has_any_role_token)

    missing = []
    if not eff_url:
        missing.append("project_url")
    if not cfg_path.exists():
        missing.append("project_key")
    if not has_any_creds:
        missing.append("jira_credentials")
    if _are_roles_unconfigured(cfg.get("roles", {})):
        missing.append("roles")

    role_token_status = {
        r_key: bool((role_creds.get(r_key) or {}).get("jira_api_token"))
        for r_key in DEFAULT_TEAM_CONFIG["roles"]
    }

    questions_to_ask = [
        {
            "id": "project_url_and_key",
            "title": "1. 專案 Jira URL 與 Project Key",
            "prompt": "請提供 Jira 專案網址（例如 https://your-org.atlassian.net）與 Project Key（例如 PROJ / TEAM）。若僅使用本地離線模式請明確告知。",
            "configured": bool(eff_url and cfg_path.exists()),
            "current_value": {"jira_base_url": eff_url, "project_key": cfg.get("project_key", "PROJ")},
        },
        {
            "id": "role_definitions_and_tokens",
            "title": "2. 角色認領與個人 Jira API Token（支援僅先填寫自己的角色，其餘交由協作者後續認領）",
            "prompt": (
                "請問您自己負責哪幾個角色（pm / tech_lead / developer / reviewer / qa / release_manager），並請提供您的姓名、Jira Email 與 API Token？"
                "（若您已知其他成員資訊亦可一併提供；若尚未確定，其他角色可留空為待認領狀態，待其他成員 git clone 並執行 $team-next-step 時再各自認領並綁定個人 Token。）"
                "所有 Token 僅寫入受 .gitignore 保護的 .ai-workflow/credentials.json（權限 0600），絕不進版控。"
            ),
            "configured": (not _are_roles_unconfigured(cfg.get("roles", {}))) and has_any_creds,
            "current_value": {
                "default_jira_email": eff_email,
                "has_default_api_token": bool(eff_token),
                "roles": cfg.get("roles", {}),
                "role_has_api_token": role_token_status,
            },
        },
        {
            "id": "status_and_issue_types",
            "title": "3. Jira 狀態與單據類型映射（選填確認）",
            "prompt": "預設狀態為 To Do / In Progress / In Review / In QA / Ready for Release / Done，單據類型為 Story / Sub-task / Bug，是否直接沿用或需自訂？",
            "configured": True,
            "current_value": {
                "status_mapping": cfg.get("status_mapping", {}),
                "issue_type_mapping": cfg.get("issue_type_mapping", {}),
            },
        },
    ]

    ci_detect = _detect_ci_delivery_pipeline(repo_root)
    return {
        "ok": True,
        "action": "init-inspect",
        "config_exists": cfg_path.exists(),
        "credentials_file_exists": cred_path.exists() or dotenv_path.exists(),
        "credentials_configured": has_any_creds,
        "role_has_api_token": role_token_status,
        "ready_for_live_jira": bool(eff_url and has_any_creds),
        "missing_confirmations": missing,
        "requires_user_inquiry": len(missing) > 0,
        "detected_delivery_pipeline": ci_detect["delivery_pipeline"],
        "detected_ci_checks": ci_detect["detected_ci_checks"],
        "suggested_qa_handoff_mode": ci_detect["suggested_qa_handoff_mode"],
        "agent_instruction": (
            "STOP and ask the user to confirm the items in `questions_to_ask` (especially project URL, Project Key, "
            "each role's person/email, and their Jira API token(s)) BEFORE executing `jira_sync.py init`."
            if missing
            else "All core settings are present. Confirm with the user if any updates are needed before running `init`."
        ),
        "questions_to_ask": questions_to_ask,
    }


def _prompt_interactive_init(cfg: dict, local_creds: dict) -> tuple:
    """Interactive terminal prompt for project URL, key, default Jira account/token, and per-role person/email/token."""
    def _ask(prompt_text: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        sys.stderr.write(f"{prompt_text}{suffix}: ")
        sys.stderr.flush()
        line = sys.stdin.readline()
        if not line:
            return default
        val = line.strip()
        return val if val else default

    sys.stderr.write("=== Team Init Interactive Setup (專案與角色互動式設定) ===\n")
    cur_url = cfg.get("jira_base_url") or local_creds.get("jira_base_url") or ""
    jira_url = _ask("1. Jira Project Base URL (e.g. https://your-org.atlassian.net)", cur_url)
    cfg["jira_base_url"] = jira_url.rstrip("/")

    cur_key = cfg.get("project_key") or "PROJ"
    proj_key = _ask("2. Jira Project Key (e.g. PROJ, TEAM)", cur_key)
    if proj_key:
        cfg["project_key"] = proj_key.upper()

    cur_email = local_creds.get("jira_email") or os.environ.get("JIRA_EMAIL") or ""
    jira_email = _ask("3. Default Jira Account Email (JIRA_EMAIL)", cur_email)

    cur_token_hint = "***configured***" if (local_creds.get("jira_api_token") or os.environ.get("JIRA_API_TOKEN")) else ""
    raw_token = _ask("4. Default Jira API Token (JIRA_API_TOKEN, saved to gitignored .ai-workflow/credentials.json)", cur_token_hint)
    jira_token = None
    if raw_token and raw_token != "***configured***":
        jira_token = raw_token

    sys.stderr.write("\n--- 5. Role-to-Person & Per-Role API Token Mapping (支援一人兼任多職與各角色獨立 Token) ---\n")
    role_order = ("pm", "tech_lead", "developer", "reviewer", "qa", "release_manager")
    role_creds_existing = local_creds.get("role_credentials") or {}
    interactive_role_creds = {}
    for r_key in role_order:
        prev = cfg["roles"].get(r_key) if isinstance(cfg["roles"].get(r_key), dict) else {}
        r_name = _ask(f"  Role [{r_key}] Name", prev.get("name", r_key))
        r_email = _ask(f"  Role [{r_key}] Email", prev.get("email", ""))
        prev_rc = role_creds_existing.get(r_key) or {}
        rc_hint = "***configured***" if prev_rc.get("jira_api_token") else ""
        r_tok = _ask(f"  Role [{r_key}] Jira API Token (optional, leave blank to reuse default/same-email token)", rc_hint)
        r_acct = _ask(f"  Role [{r_key}] Jira Account ID (optional, auto-resolved from token/email if blank)", prev.get("jira_account_id", ""))
        cfg["roles"][r_key] = {
            "name": r_name,
            "email": r_email,
            "jira_account_id": r_acct,
        }
        if r_tok and r_tok != "***configured***":
            interactive_role_creds[r_key] = {"jira_email": r_email or jira_email, "jira_api_token": r_tok}

    return jira_email, jira_token, interactive_role_creds


def ensure_live_or_explicit_offline(client: JiraClient, config: dict, offline_flag: bool) -> bool:
    """Block silent offline fallback when team-config.json has jira_base_url but local credentials are missing."""
    base_url = (config.get("jira_base_url") or "").strip()
    if base_url and not offline_flag and not client.is_live:
        print(
            f"ERROR: Project is configured for live Jira ({base_url}), but local Jira credentials are missing "
            f"(.ai-workflow/credentials.json is gitignored per collaborator machine). "
            f"Run 'jira_sync.py auth --jira-email <EMAIL> --jira-token <TOKEN>' to bind your personal credentials, "
            f"or pass --offline explicitly.",
            file=sys.stderr,
        )
        return False
    return True


def _resolve_planning_board_id(client: JiraClient, config: dict, explicit_board_id: str = None) -> str:
    board_id = str(explicit_board_id or config.get("jira_board_id") or "").strip()
    if board_id:
        return board_id
    if not client.is_live:
        raise ValueError("A Jira board id is required in offline mode; pass --board-id or set jira_board_id.")
    boards = client.list_boards(config.get("project_key", ""))
    if len(boards) == 1 and boards[0].get("id") is not None:
        return str(boards[0]["id"])
    if not boards:
        raise ValueError("No Scrum board was found for the configured Jira project.")
    ids = ", ".join(str(b.get("id")) for b in boards if b.get("id") is not None)
    raise ValueError(f"Multiple Scrum boards found ({ids}); pass --board-id explicitly.")


def _planning_issue_keys(client: JiraClient, config: dict, issue_keys: list, jql: str = "", epic_key: str = "") -> list:
    explicit = []
    for key in issue_keys or []:
        clean = validate_jira_key(key)
        if clean not in explicit:
            explicit.append(clean)
    if explicit or not client.is_live:
        return explicit
    query = (jql or "").strip()
    if not query and epic_key:
        query = f'parent = "{validate_jira_key(epic_key)}"'
    if not query:
        return []
    payload = {
        "jql": query,
        "maxResults": 100,
        "fields": ["summary", "status", "issuetype", "parent"],
    }
    try:
        res = client._request("POST", "/rest/api/3/search/jql", payload)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        res = client._request("POST", "/rest/api/3/search", payload)
    found = []
    for issue in (res or {}).get("issues", []) if isinstance(res, dict) else []:
        if isinstance(issue, dict) and issue.get("key"):
            key = str(issue["key"]).strip().upper()
            if key not in found:
                found.append(key)
    return found


def _planning_preview(client: JiraClient, issue_keys: list) -> list:
    rows = []
    for key in issue_keys:
        issue = client.get_issue(key) if client.is_live else {"key": key, "fields": {}}
        fields = issue.get("fields") or {}
        issue_type = str(((fields.get("issuetype") or {}).get("name")) or "").strip().lower()
        status = str(((fields.get("status") or {}).get("name")) or "").strip()
        reason = ""
        if issue_type in {"epic", "sub-task", "subtask", "子任務"}:
            reason = "Epic and Sub-task are not sprint members by default"
        elif status.lower() in {"done", "closed", "resolved"}:
            reason = "Issue is already completed"
        rows.append({
            "key": key,
            "summary": fields.get("summary") or key,
            "issue_type": issue_type or "unknown",
            "status": status,
            "included": not bool(reason),
            "reason": reason,
        })
    return rows


def cmd_sprint_plan(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="pm")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 2
    board_id = _resolve_planning_board_id(client, config, args.board_id)
    issue_keys = _planning_issue_keys(client, config, args.issue_key or [], args.jql, args.epic_key)
    rows = _planning_preview(client, issue_keys)
    selected = [row["key"] for row in rows if row["included"]]
    payload = {
        "name": (args.name or "").strip(),
        "originBoardId": int(board_id) if str(board_id).isdigit() else board_id,
    }
    if not payload["name"] and not args.sprint_id:
        raise ValueError("--name is required when creating a new Sprint.")
    for field in ("start_date", "end_date", "goal"):
        value = getattr(args, field, None)
        if value:
            payload[{"start_date": "startDate", "end_date": "endDate", "goal": "goal"}[field]] = value
    out = {
        "mode": "live" if client.is_live else "offline",
        "action": "sprint-plan",
        "board_id": board_id,
        "sprint_id": str(args.sprint_id) if args.sprint_id else None,
        "sprint_payload": payload,
        "issues": rows,
        "selected_issue_keys": selected,
        "dry_run": not args.apply,
        "applied": False,
    }
    if args.sprint_id:
        current_sprint = client.get_sprint(args.sprint_id)
        update_payload = {
            "name": (args.name or current_sprint.get("name") or "").strip(),
            "originBoardId": current_sprint.get("originBoardId", payload["originBoardId"]),
            "state": current_sprint.get("state") or "future",
        }
        for arg_name, api_name in (("start_date", "startDate"), ("end_date", "endDate"), ("goal", "goal")):
            value = getattr(args, arg_name, None)
            if value is not None:
                update_payload[api_name] = value
            elif current_sprint.get(api_name) is not None:
                update_payload[api_name] = current_sprint.get(api_name)
        out["sprint_update_payload"] = update_payload
    if not args.apply:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.sprint_id:
        sprint = current_sprint
        if any(getattr(args, key, None) is not None for key in ("name", "start_date", "end_date", "goal")):
            sprint = client.update_sprint(args.sprint_id, out["sprint_update_payload"])
    else:
        sprint = client.create_sprint(payload)
    sprint_id = str(sprint.get("id") or args.sprint_id or "")
    if not sprint_id:
        raise RuntimeError("Jira did not return a Sprint id.")
    moved = []
    for idx in range(0, len(selected), 50):
        chunk = selected[idx:idx + 50]
        if chunk:
            client.move_issues_to_sprint(sprint_id, chunk)
            moved.extend(chunk)
    out.update({"sprint_id": sprint_id, "sprint": sprint, "moved_issue_keys": moved, "dry_run": False, "applied": True})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_sprint_status(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="pm")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 2
    if args.sprint_id:
        sprint = client.get_sprint(args.sprint_id)
        issues = client.get_sprint_issues(args.sprint_id)
        out = {"mode": "live" if client.is_live else "offline", "sprint": sprint, "issues": issues}
    else:
        board_id = _resolve_planning_board_id(client, config, args.board_id)
        sprints = client.list_sprints(board_id, args.state or "")
        out = {"mode": "live" if client.is_live else "offline", "board_id": board_id, "sprints": sprints}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _validate_and_plan_epic_stories(raw_stories_arg) -> tuple:
    """
    Parse, validate, and topologically order Epic child Story specs into execution waves.
    Raises ValueError on malformed JSON, empty/non-list input, missing/blank title,
    duplicate local IDs, self-dependency, unknown depends_on, or dependency cycles.
    """
    if raw_stories_arg is None or not str(raw_stories_arg).strip():
        raise ValueError("--stories-json must be a non-empty JSON array.")
    try:
        parsed = load_json_arg(raw_stories_arg)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        raise ValueError(f"Invalid --stories-json: {exc}") from exc

    if not isinstance(parsed, list) or not parsed:
        raise ValueError("--stories-json must be a non-empty JSON array of Story objects.")

    explicit_ids_lower = set()
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(f"Story spec at index {idx} must be a JSON object.")
        if "id" in item and item["id"] is not None:
            if not isinstance(item["id"], str) or not item["id"].strip():
                raise ValueError(f"Story spec at index {idx} has an empty or non-string 'id'.")
            raw_id = item["id"].strip()
            if raw_id.lower() in explicit_ids_lower:
                raise ValueError(f"Duplicate local story id '{raw_id}' in --stories-json.")
            explicit_ids_lower.add(raw_id.lower())

    used_ids_lower = set(explicit_ids_lower)
    next_seq = 1
    normalized = []
    by_id = {}

    for idx, item in enumerate(parsed):
        raw_title = item.get("title")
        if not isinstance(raw_title, str) or not raw_title.strip():
            raise ValueError(f"Story spec at index {idx} is missing a non-empty string 'title'.")
        title = raw_title.strip()

        if "id" in item and item["id"] is not None:
            sid = item["id"].strip()
        else:
            while f"s-{next_seq}" in used_ids_lower:
                next_seq += 1
            sid = f"S-{next_seq}"
            next_seq += 1
            used_ids_lower.add(sid.lower())

        summary = _clean_optional_text(item.get("summary"))
        scope = _clean_optional_text(item.get("scope"))
        scope_in = _clean_optional_text(item.get("scope_in")) or scope or summary or title
        scope_out = _clean_optional_text(item.get("scope_out")) or "N/A"

        raw_acs = item.get("acceptance_criteria")
        acs = []
        if raw_acs is not None:
            if not isinstance(raw_acs, list):
                raise ValueError(f"Story '{sid}' field 'acceptance_criteria' must be a list.")
            for a_idx, raw_ac in enumerate(raw_acs, start=1):
                if isinstance(raw_ac, str):
                    s = raw_ac.strip()
                    if not s:
                        continue
                    if ":" in s and re.match(r"^AC-\d+$", s.split(":", 1)[0].strip(), re.IGNORECASE):
                        ac_id, ac_desc = s.split(":", 1)
                        ac_id = ac_id.strip().upper()
                        ac_desc = ac_desc.strip()
                    else:
                        ac_id = f"AC-{a_idx:03d}"
                        ac_desc = s
                    if ac_desc:
                        acs.append({"id": ac_id, "description": ac_desc})
                elif isinstance(raw_ac, dict):
                    ac_id = _clean_optional_text(raw_ac.get("id")).upper() or f"AC-{a_idx:03d}"
                    ac_desc = _clean_optional_text(raw_ac.get("description") or raw_ac.get("text"))
                    if ac_desc:
                        acs.append({"id": ac_id, "description": ac_desc})
                else:
                    raise ValueError(f"Story '{sid}' contains an invalid acceptance criterion entry.")

        raw_deps = item.get("depends_on")
        deps = []
        if raw_deps is not None:
            if not isinstance(raw_deps, list):
                raise ValueError(f"Story '{sid}' field 'depends_on' must be a list of story IDs.")
            for d in raw_deps:
                if not isinstance(d, str) or not d.strip():
                    raise ValueError(f"Story '{sid}' contains an empty or non-string entry in 'depends_on'.")
                clean_d = d.strip()
                if clean_d not in deps:
                    deps.append(clean_d)

        raw_sc = item.get("shared_contracts")
        shared_contracts = []
        if raw_sc is not None:
            if isinstance(raw_sc, str):
                sc_clean = _clean_optional_text(raw_sc)
                if sc_clean:
                    shared_contracts.append(sc_clean)
            elif isinstance(raw_sc, list):
                for x in raw_sc:
                    sc_clean = _clean_optional_text(x)
                    if sc_clean:
                        shared_contracts.append(sc_clean)
            else:
                raise ValueError(f"Story '{sid}' field 'shared_contracts' must be a list or string.")

        priority = _clean_optional_text(item.get("priority"))
        raw_sp = item.get("story_points")
        story_points = None
        if raw_sp is not None and not (isinstance(raw_sp, str) and not raw_sp.strip()):
            if isinstance(raw_sp, bool):
                raise ValueError(f"Story '{sid}' field 'story_points' must be a finite non-negative number.")
            if isinstance(raw_sp, (int, float)):
                num = float(raw_sp)
                if not math.isfinite(num) or num < 0:
                    raise ValueError(f"Story '{sid}' field 'story_points' must be a finite non-negative number.")
                story_points = int(num) if (isinstance(raw_sp, int) or num.is_integer()) else num
            elif isinstance(raw_sp, str):
                try:
                    num = float(raw_sp.strip())
                except ValueError as exc:
                    raise ValueError(f"Story '{sid}' field 'story_points' must be a finite non-negative number.") from exc
                if not math.isfinite(num) or num < 0:
                    raise ValueError(f"Story '{sid}' field 'story_points' must be a finite non-negative number.")
                story_points = int(num) if num.is_integer() else num
            else:
                raise ValueError(f"Story '{sid}' field 'story_points' must be a finite non-negative number.")

        rec = {
            "id": sid,
            "title": title,
            "summary": summary or scope_in,
            "scope_in": scope_in,
            "scope_out": scope_out,
            "acceptance_criteria": acs,
            "depends_on": deps,
            "shared_contracts": shared_contracts,
            "priority": priority,
            "story_points": story_points,
        }
        normalized.append(rec)
        by_id[sid] = rec

    for rec in normalized:
        sid = rec["id"]
        for dep in rec["depends_on"]:
            if dep == sid:
                raise ValueError(f"Self-dependency detected: story '{sid}' cannot depend on itself.")
            if dep not in by_id:
                raise ValueError(f"Story '{sid}' depends on unknown story id '{dep}'.")

    # Topological wave computation (Kahn's algorithm preserving input order within each wave)
    in_degree = {rec["id"]: len(rec["depends_on"]) for rec in normalized}
    dependents = {rec["id"]: [] for rec in normalized}
    for rec in normalized:
        for dep in rec["depends_on"]:
            dependents[dep].append(rec["id"])

    ready = [rec["id"] for rec in normalized if in_degree[rec["id"]] == 0]
    processed = set()
    execution_waves = []
    wave_num = 0
    while ready:
        wave_num += 1
        execution_waves.append({
            "wave": wave_num,
            "label": f"Wave {wave_num}",
            "story_ids": list(ready),
        })
        for sid in ready:
            by_id[sid]["wave"] = wave_num
            processed.add(sid)
        newly_unlocked = set()
        for sid in ready:
            for dep_child in dependents[sid]:
                in_degree[dep_child] -= 1
                if in_degree[dep_child] == 0:
                    newly_unlocked.add(dep_child)
        ready = [rec["id"] for rec in normalized if rec["id"] in newly_unlocked]

    if len(processed) < len(normalized):
        remaining = [rec["id"] for rec in normalized if rec["id"] not in processed]
        state = {}
        stack = []
        cycle_path = None

        def _find_cycle(node: str) -> bool:
            nonlocal cycle_path
            state[node] = 1
            stack.append(node)
            for dep in by_id[node]["depends_on"]:
                if dep in processed:
                    continue
                if state.get(dep) == 1:
                    idx_start = stack.index(dep)
                    cycle_path = stack[idx_start:] + [dep]
                    return True
                if state.get(dep, 0) == 0 and _find_cycle(dep):
                    return True
            stack.pop()
            state[node] = 2
            return False

        for rem_id in remaining:
            if state.get(rem_id, 0) == 0 and _find_cycle(rem_id):
                break
        cycle_str = " -> ".join(cycle_path) if cycle_path else ", ".join(remaining)
        raise ValueError(f"Circular dependency cycle detected: {cycle_str}")

    shared_contracts_summary = []
    seen_sc = set()
    for rec in normalized:
        for sc in rec["shared_contracts"]:
            if sc not in seen_sc:
                seen_sc.add(sc)
                shared_contracts_summary.append(sc)

    return normalized, execution_waves, shared_contracts_summary


def cmd_epic_decompose(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    epic_key = validate_jira_key(args.epic)
    stories, execution_waves, shared_contracts_summary = _validate_and_plan_epic_stories(args.stories_json)

    raw_issue_type = getattr(args, "issue_type", None)
    if raw_issue_type is not None and not _clean_optional_text(raw_issue_type):
        raise ValueError("--issue-type cannot be empty.")

    config = load_team_config(repo_root)
    eff_issue_type = (
        _clean_optional_text(raw_issue_type)
        or (config.get("issue_type_mapping") or {}).get("story")
        or "Story"
    )
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="pm")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1

    active_dir = get_active_dir(repo_root)
    existing_by_key = {}
    if active_dir.exists():
        for af in sorted(active_dir.glob("*.json")):
            try:
                st = json.loads(af.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(st, dict) and _clean_optional_text(st.get("epic_key")).upper() == epic_key:
                k = _clean_optional_text(st.get("jira_key") or af.stem).upper()
                existing_by_key[k] = {
                    "key": k,
                    "title": _clean_optional_text(st.get("title")) or k,
                    "jira_status": _clean_optional_text(st.get("jira_status")) or "To Do",
                    "epic_decompose_id": _clean_optional_text(st.get("epic_decompose_id")),
                    "source": "local",
                }

    epic_summary = epic_key
    warnings = []
    if client.is_live:
        try:
            epic_issue = client.get_issue(epic_key)
            if isinstance(epic_issue, dict):
                e_sum = _clean_optional_text(((epic_issue.get("fields") or {}).get("summary")))
                if e_sum:
                    epic_summary = e_sum
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                if getattr(args, "apply", False):
                    print(
                        f"ERROR: Epic {epic_key} was not found in Jira (HTTP 404). Refusing to create Stories under a non-existent Epic.",
                        file=sys.stderr,
                    )
                    return 1
                warnings.append(f"Epic {epic_key} was not found in Jira (HTTP 404).")
        except Exception:
            pass
        remote_children, _, fetch_warnings = _fetch_epic_children_live(client, config, epic_key)
        warnings.extend(fetch_warnings)
        for iss in remote_children:
            rk = _clean_optional_text(iss.get("key")).upper()
            flds = iss.get("fields") or {}
            r_sum = _clean_optional_text(flds.get("summary")) or rk
            r_st = _clean_optional_text(((flds.get("status") or {}).get("name"))) or "To Do"
            if rk in existing_by_key:
                existing_by_key[rk]["title"] = r_sum or existing_by_key[rk]["title"]
                existing_by_key[rk]["jira_status"] = r_st or existing_by_key[rk]["jira_status"]
                existing_by_key[rk]["source"] = "local+jira"
                if not existing_by_key[rk].get("epic_decompose_id"):
                    rem_snap = client.get_state_snapshot(rk)
                    if isinstance(rem_snap, dict):
                        existing_by_key[rk]["epic_decompose_id"] = _clean_optional_text(rem_snap.get("epic_decompose_id"))
            else:
                rem_snap = client.get_state_snapshot(rk)
                snap_dec_id = _clean_optional_text(rem_snap.get("epic_decompose_id")) if isinstance(rem_snap, dict) else ""
                existing_by_key[rk] = {
                    "key": rk,
                    "title": r_sum,
                    "jira_status": r_st,
                    "epic_decompose_id": snap_dec_id,
                    "source": "jira",
                }

    existing_stories = [existing_by_key[k] for k in sorted(existing_by_key.keys())]
    existing_by_decompose_id = {}
    existing_by_norm_title = {}
    for row in existing_stories:
        if row.get("epic_decompose_id"):
            existing_by_decompose_id[row["epic_decompose_id"].lower()] = row["key"]
        norm_t = re.sub(r"\s+", " ", (row.get("title") or "").strip().lower())
        if norm_t and norm_t not in existing_by_norm_title:
            existing_by_norm_title[norm_t] = row["key"]

    duplicate_warnings = []
    stories_to_create = []
    skipped_existing = []
    for spec in stories:
        sid = spec["id"]
        norm_t = re.sub(r"\s+", " ", spec["title"].strip().lower())
        if sid.lower() in existing_by_decompose_id:
            ex_key = existing_by_decompose_id[sid.lower()]
            duplicate_warnings.append({
                "id": sid,
                "title": spec["title"],
                "existing_key": ex_key,
                "match_type": "epic_decompose_id",
                "message": f"Story '{sid}' was already created as {ex_key} under Epic {epic_key}; skipping duplicate creation.",
            })
            skipped_existing.append({"id": sid, "title": spec["title"], "existing_key": ex_key})
            continue
        if norm_t in existing_by_norm_title:
            ex_key = existing_by_norm_title[norm_t]
            duplicate_warnings.append({
                "id": sid,
                "title": spec["title"],
                "existing_key": ex_key,
                "match_type": "title",
                "message": f"Potential duplicate under Epic {epic_key}: existing Story {ex_key} has a matching title '{spec['title']}'.",
            })
        stories_to_create.append(spec)

    if not getattr(args, "apply", False):
        out = {
            "ok": True,
            "action": "epic-decompose",
            "mode": "preview",
            "dry_run": True,
            "applied": False,
            "execution_mode": "live" if client.is_live else "offline",
            "epic_key": epic_key,
            "epic_summary": epic_summary,
            "issue_type": eff_issue_type,
            "existing_stories": existing_stories,
            "stories_to_create": stories_to_create,
            "skipped_existing": skipped_existing,
            "duplicate_warnings": duplicate_warnings,
            "execution_waves": execution_waves,
            "shared_contracts_summary": shared_contracts_summary,
            "total_to_create": len(stories_to_create),
            "warnings": warnings,
            "requires_user_confirmation": True,
            "can_auto_chain_next_station": False,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    spec_by_id = {s["id"]: s for s in stories}
    local_to_jira_key = {}
    created_stories = []
    skipped_stories = []
    failed_stories = []
    failed_local_ids = set()
    used_offline_keys = {f.stem.upper() for f in (active_dir.glob("*.json") if active_dir.exists() else [])}
    offline_seq = 1

    for wave_info in execution_waves:
        wave_num = wave_info["wave"]
        for sid in wave_info["story_ids"]:
            spec = spec_by_id[sid]
            if sid.lower() in existing_by_decompose_id:
                ex_key = existing_by_decompose_id[sid.lower()]
                local_to_jira_key[sid] = ex_key
                skipped_stories.append({
                    "id": sid,
                    "title": spec["title"],
                    "jira_key": ex_key,
                    "wave": wave_num,
                    "reason": "epic_decompose_id_already_exists",
                })
                continue

            failed_deps = [d for d in spec["depends_on"] if d in failed_local_ids or d not in local_to_jira_key]
            if failed_deps:
                err_msg = f"Skipped because upstream dependency failed to create: {', '.join(failed_deps)}"
                failed_local_ids.add(sid)
                failed_stories.append({
                    "id": sid,
                    "title": spec["title"],
                    "wave": wave_num,
                    "error": err_msg,
                })
                continue

            depends_on_keys = [local_to_jira_key[d] for d in spec["depends_on"]]
            design_parts = [spec["summary"] or f"Decomposed from Epic {epic_key} ({sid}, Wave {wave_num})."]
            if depends_on_keys:
                design_parts.append(f"Depends on Stories: {', '.join(depends_on_keys)}")
            if spec["shared_contracts"]:
                design_parts.append(f"Shared Contracts: {'; '.join(spec['shared_contracts'])}")

            story_obj = {
                "jira_key": f"{epic_key}:{sid}",
                "epic_key": epic_key,
                "epic_decompose_id": sid,
                "title": spec["title"],
                "scope_in": spec["scope_in"],
                "scope_out": spec["scope_out"],
                "change_surface": {"has_ui": False, "has_api": False, "has_data": False},
                "risk_level": "medium",
                "acceptance_criteria": spec["acceptance_criteria"],
                "design": {"summary": "\n".join(design_parts)},
                "spec_status": "draft",
                "jira_status": "To Do",
                "depends_on": list(spec["depends_on"]),
                "depends_on_keys": depends_on_keys,
                "wave": wave_num,
                "shared_contracts": list(spec["shared_contracts"]),
                "tasks": [],
                "bugs": [],
            }
            if spec["priority"]:
                story_obj["priority"] = spec["priority"]
            if spec["story_points"] is not None:
                story_obj["story_points"] = spec["story_points"]

            try:
                if client.is_live:
                    desc_md = format_story_description(story_obj, config)
                    create_res = client.create_epic_story(
                        epic_key=epic_key,
                        title=spec["title"],
                        description_md=desc_md,
                        issue_type=eff_issue_type,
                        priority=spec["priority"],
                    )
                    new_key = validate_jira_key(create_res["jira_key"])
                    epic_link_mode = create_res.get("epic_link_mode") or "parent"
                    if create_res.get("warning"):
                        warnings.append(create_res["warning"])
                else:
                    while f"{epic_key}-S{offline_seq}" in used_offline_keys:
                        offline_seq += 1
                    new_key = f"{epic_key}-S{offline_seq}"
                    used_offline_keys.add(new_key)
                    offline_seq += 1
                    story_obj["jira_key"] = new_key
                    desc_md = format_story_description(story_obj, config)
                    epic_link_mode = "offline"

                story_obj["jira_key"] = new_key
                story_obj["last_synced_description_hash"] = _compute_description_hash(
                    _adf_to_plain_text(client.text_to_adf(desc_md)).strip()
                )
                saved_path = save_active_story(repo_root, new_key, story_obj, client=client, sync_remote=True)
                local_to_jira_key[sid] = new_key
                created_stories.append({
                    "id": sid,
                    "jira_key": new_key,
                    "title": spec["title"],
                    "wave": wave_num,
                    "depends_on": list(spec["depends_on"]),
                    "depends_on_keys": depends_on_keys,
                    "shared_contracts": list(spec["shared_contracts"]),
                    "epic_link_mode": epic_link_mode,
                    "active_file": str(saved_path.relative_to(repo_root)),
                })
            except Exception as exc:
                failed_local_ids.add(sid)
                failed_stories.append({
                    "id": sid,
                    "title": spec["title"],
                    "wave": wave_num,
                    "error": str(exc),
                })

    has_failures = bool(failed_stories)
    out = {
        "ok": not has_failures,
        "action": "epic-decompose",
        "mode": "apply",
        "dry_run": False,
        "applied": bool(created_stories),
        "partial_failure": has_failures,
        "execution_mode": "live" if client.is_live else "offline",
        "epic_key": epic_key,
        "epic_summary": epic_summary,
        "issue_type": eff_issue_type,
        "created_stories": created_stories,
        "skipped_stories": skipped_stories,
        "failed_stories": failed_stories,
        "warnings": warnings,
        "duplicate_warnings": duplicate_warnings,
        "execution_waves": execution_waves,
        "shared_contracts_summary": shared_contracts_summary,
        "total_created": len(created_stories),
        "requires_user_confirmation": True,
        "can_auto_chain_next_station": False,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if has_failures:
        print(
            f"ERROR: Epic {epic_key} decomposition had {len(failed_stories)} failure(s); {len(created_stories)} Story(ies) created.",
            file=sys.stderr,
        )
        return 1
    return 0


def _ensure_gitignore_entries(repo_root: Path) -> bool:
    """Ensure .gitignore ignores .ai-workflow/active/, .ai-workflow/credentials.json, and .ai-workflow/.env."""
    gitignore_path = repo_root / ".gitignore"
    required_ignores = [
        (".ai-workflow/active/", (".ai-workflow/active/", ".ai-workflow/active")),
        (".ai-workflow/credentials.json", (".ai-workflow/credentials.json",)),
        (".ai-workflow/.env", (".ai-workflow/.env",)),
    ]
    existing_gitignore = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    lines = [ln.strip() for ln in existing_gitignore.splitlines()]
    missing_ignores = [
        canonical for canonical, aliases in required_ignores
        if not any(a in lines for a in aliases)
    ]
    if missing_ignores:
        prefix = existing_gitignore.rstrip("\n")
        new_content = (prefix + "\n" if prefix else "") + "\n".join(missing_ignores) + "\n"
        gitignore_path.write_text(new_content, encoding="utf-8")
        return True
    return False


def cmd_auth(args) -> int:
    """Bind personal Jira (and optional Qase) credentials to local gitignored .ai-workflow/credentials.json without modifying team-config.json."""
    repo_root = Path(args.repo_root).resolve()
    cfg_path = get_config_path(repo_root)
    if not cfg_path.exists():
        print(
            "ERROR: .ai-workflow/team-config.json does not exist yet. Run 'jira_sync.py init' first.",
            file=sys.stderr,
        )
        return 1

    config = load_team_config(repo_root)
    creds = load_jira_credentials(repo_root)
    role_creds = creds.get("role_credentials") if isinstance(creds.get("role_credentials"), dict) else {}
    qa_backend = (config.get("qa_test_backend") or "").strip().lower()
    qase_token_configured = bool((os.environ.get("QASE_API_TOKEN") or creds.get("qase_api_token") or "").strip())
    has_creds = bool(
        (os.environ.get("JIRA_EMAIL") or creds.get("jira_email"))
        and (os.environ.get("JIRA_API_TOKEN") or creds.get("jira_api_token"))
    ) or any(
        bool((v or {}).get("jira_email") and (v or {}).get("jira_api_token"))
        for v in role_creds.values()
    )

    if getattr(args, "inspect", False):
        claimed_map, unclaimed_list = _partition_claimed_roles(config)
        roles_summary = {}
        for r_key, r_val in (config.get("roles") or {}).items():
            p = resolve_role_person(config, r_key)
            rc = role_creds.get(r_key) if isinstance(role_creds.get(r_key), dict) else {}
            roles_summary[r_key] = {
                "claimed": _is_role_claimed(r_key, r_val),
                "name": p["name"],
                "email": p["email"],
                "jira_account_id": p["jira_account_id"],
                "has_local_token": bool((rc.get("jira_api_token") or "").strip()),
            }
        print(json.dumps({
            "ok": True,
            "action": "auth-inspect",
            "project_key": config.get("project_key", ""),
            "jira_base_url": config.get("jira_base_url", ""),
            "credentials_configured": has_creds,
            "claimed_roles": claimed_map,
            "unclaimed_roles": unclaimed_list,
            "qa_test_backend": qa_backend,
            "qase_required": qa_backend == "qase",
            "qase_project_code": (config.get("qase") or {}).get("project_code", ""),
            "qase_token_configured": qase_token_configured,
            "roles": roles_summary,
            "agent_instruction": (
                "Show the collaborator `claimed_roles` and `unclaimed_roles`, then STOP and ask: "
                "(1) Which role(s) they are responsible for (`--role`, e.g. `qa` or `reviewer,qa`), and "
                "(2) Their personal Jira Email and Jira API Token (plus display name `--name` if desired, and Qase API Token if claiming `qa` when `qase_required` is true). "
                "Then run `jira_sync.py auth --role <ROLES> [--name <NAME>] --jira-email <EMAIL> --jira-token <TOKEN> [--qase-token <QASE_TOKEN>]`."
            ),
        }, ensure_ascii=False, indent=2))
        return 0

    jira_email = getattr(args, "jira_email", None)
    jira_token = getattr(args, "jira_token", None)
    qase_token = getattr(args, "qase_token", None)
    explicit_roles = _parse_role_list(getattr(args, "role", None) or "")
    person_name_arg = (getattr(args, "name", None) or "").strip()
    specialty_arg = (getattr(args, "specialty", None) or "").strip()
    replace_role = bool(getattr(args, "replace_role", False))

    if getattr(args, "interactive", False):
        _, unclaimed_list = _partition_claimed_roles(config)
        print("=== Collaborator Role Claim & Local Credential Binding ===", file=sys.stderr)
        if not explicit_roles:
            hint = f" (unclaimed: {', '.join(unclaimed_list)})" if unclaimed_list else ""
            print(f"Role(s) you own (comma-separated, e.g. qa or reviewer,qa){hint}: ", end="", file=sys.stderr, flush=True)
            explicit_roles = _parse_role_list(sys.stdin.readline().strip())
        if not person_name_arg:
            print("Display Name (optional, auto-filled from Jira if blank): ", end="", file=sys.stderr, flush=True)
            person_name_arg = sys.stdin.readline().strip()
        if not jira_email:
            print("Jira Account Email: ", end="", file=sys.stderr, flush=True)
            jira_email = sys.stdin.readline().strip()
        if not jira_token:
            if sys.stdin.isatty():
                jira_token = getpass.getpass("Jira API Token (hidden): ", stream=sys.stderr).strip()
            else:
                print("Jira API Token: ", end="", file=sys.stderr, flush=True)
                jira_token = sys.stdin.readline().strip()
        if qa_backend == "qase" and not qase_token:
            if sys.stdin.isatty():
                qase_token = getpass.getpass("Qase API Token (optional unless QA, hidden): ", stream=sys.stderr).strip()
            else:
                print("Qase API Token (optional): ", end="", file=sys.stderr, flush=True)
                qase_token = sys.stdin.readline().strip()

    role_tokens_to_save = {}
    if getattr(args, "role_tokens_json", None):
        raw_rt = load_json_arg(args.role_tokens_json)
        if isinstance(raw_rt, dict):
            for rk, rv in raw_rt.items():
                if isinstance(rv, str) and rv.strip():
                    rp = resolve_role_person(config, rk)
                    role_tokens_to_save[rk] = {"jira_email": rp.get("email") or (jira_email or ""), "jira_api_token": rv.strip()}
                elif isinstance(rv, dict) and rv.get("jira_api_token"):
                    rp = resolve_role_person(config, rk)
                    role_tokens_to_save[rk] = {
                        "jira_email": (rv.get("jira_email") or rp.get("email") or jira_email or "").strip(),
                        "jira_api_token": str(rv["jira_api_token"]).strip(),
                    }

    if not ((jira_email and jira_token) or role_tokens_to_save or qase_token):
        print(
            "ERROR: Provide --jira-email <EMAIL> and --jira-token <TOKEN> (or --qase-token / --interactive).",
            file=sys.stderr,
        )
        return 1

    jira_verification = {"mode": "offline", "verified": False}
    base_url = (config.get("jira_base_url") or "").strip()
    if jira_email and jira_token and base_url and not args.offline:
        probe_cfg = dict(config)
        probe_client = JiraClient(probe_cfg, offline=True, repo_root=None)
        probe_client.base_url = base_url.rstrip("/")
        probe_client.email = jira_email.strip()
        probe_client.api_token = jira_token.strip()
        probe_client._explicit_offline = False
        probe_client.offline = False
        try:
            jira_verification = probe_client.verify_connection()
        except Exception as exc:
            print(f"ERROR: Jira credential verification failed: {exc}", file=sys.stderr)
            return 1

    matched_roles = []
    for rk in explicit_roles:
        if jira_email and jira_token:
            role_tokens_to_save[rk] = {
                "jira_email": jira_email.strip(),
                "jira_api_token": jira_token.strip(),
            }
        if rk not in matched_roles:
            matched_roles.append(rk)

    if jira_email and jira_token:
        norm_em = jira_email.strip().lower()
        for r_key in (config.get("roles") or {}):
            if any((m.get("email") or "").strip().lower() == norm_em for m in resolve_role_members(config, r_key)):
                role_tokens_to_save.setdefault(r_key, {
                    "jira_email": jira_email.strip(),
                    "jira_api_token": jira_token.strip(),
                })
                if r_key not in matched_roles:
                    matched_roles.append(r_key)

    # Progressive & Multi-Member Role Claiming: append/merge into role pool (never overwriting distinct members unless --replace-role)
    orig_cfg_raw = cfg_path.read_text(encoding="utf-8")
    raw_cfg_disk = json.loads(orig_cfg_raw)
    disk_roles = raw_cfg_disk.setdefault("roles", {})
    claimed_roles_updated = []
    verified_display_name = (jira_verification.get("display_name") or "").strip()
    verified_account_id = (jira_verification.get("account_id") or "").strip()

    for rk in matched_roles:
        existing_entry = disk_roles.get(rk) if isinstance(disk_roles.get(rk), (dict, list, str)) else {}
        is_explicit = rk in explicit_roles
        cand_email = (jira_email or "").strip()
        cand_name = (
            person_name_arg
            or verified_display_name
            or (cand_email.split("@")[0].strip() if cand_email else rk)
        )
        new_member = {
            "name": cand_name,
            "email": cand_email,
            "jira_account_id": verified_account_id,
            "specialty": specialty_arg,
        }
        updated_entry = upsert_role_member(
            existing_entry=existing_entry,
            role_key=rk,
            new_member=new_member,
            explicit_claim=is_explicit,
            replace=replace_role and is_explicit,
            explicit_name=bool(person_name_arg),
            explicit_specialty=bool(specialty_arg),
        )
        if existing_entry != updated_entry:
            disk_roles[rk] = updated_entry
            claimed_roles_updated.append(rk)

    team_config_modified = False
    if claimed_roles_updated:
        new_cfg_raw = json.dumps(raw_cfg_disk, ensure_ascii=False, indent=2) + "\n"
        if new_cfg_raw != orig_cfg_raw:
            cfg_path.write_text(new_cfg_raw, encoding="utf-8")
            team_config_modified = True

    updated_config = load_team_config(repo_root)
    _, remaining_unclaimed = _partition_claimed_roles(updated_config)

    saved_cred_path = save_jira_credentials(
        repo_root,
        jira_url=base_url or None,
        jira_email=jira_email,
        jira_token=jira_token,
        role_credentials=role_tokens_to_save or None,
        qase_token=qase_token,
    )
    gitignore_updated = _ensure_gitignore_entries(repo_root)

    # Backfill unassigned active Story/Sub-tasks if newly claimed role owns them in live mode
    if claimed_roles_updated and verified_account_id and base_url and not args.offline:
        try:
            bf_client = JiraClient(updated_config, offline=args.offline, repo_root=repo_root)
            active_dir = get_active_dir(repo_root)
            if bf_client.is_live and active_dir.exists():
                for af in sorted(active_dir.glob("*.json")):
                    st_data = json.loads(af.read_text(encoding="utf-8"))
                    cur_role = st_data.get("current_owner_role") or ""
                    if cur_role in claimed_roles_updated and len(resolve_role_members(updated_config, cur_role)) == 1:
                        safe_assign_issue(bf_client, st_data["jira_key"], verified_account_id)
        except Exception:
            pass

    print(json.dumps({
        "ok": True,
        "action": "auth",
        "project_key": updated_config.get("project_key", ""),
        "jira_base_url": base_url,
        "credentials_path": str(saved_cred_path.relative_to(repo_root)),
        "matched_roles": matched_roles,
        "claimed_roles_updated": claimed_roles_updated,
        "remaining_unclaimed_roles": remaining_unclaimed,
        "qase_token_saved": bool(qase_token),
        "jira_verification": jira_verification,
        "gitignore_updated": gitignore_updated,
        "team_config_modified": team_config_modified,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args) -> int:
    repo_root = Path(args.repo_root).resolve()

    if getattr(args, "auth_only", False):
        return cmd_auth(args)

    if getattr(args, "inspect", False):
        print(json.dumps(_inspect_init_state(repo_root), ensure_ascii=False, indent=2))
        return 0

    wf_dir = get_workflow_dir(repo_root)
    wf_dir.mkdir(parents=True, exist_ok=True)
    get_active_dir(repo_root).mkdir(parents=True, exist_ok=True)

    cfg = load_team_config(repo_root)
    local_creds = load_jira_credentials(repo_root)

    interactive_email = None
    interactive_token = None
    interactive_role_creds = {}
    if getattr(args, "interactive", False):
        interactive_email, interactive_token, interactive_role_creds = _prompt_interactive_init(cfg, local_creds)

    extracted_role_creds = dict(interactive_role_creds)
    extracted_member_creds = {}
    if args.project_key:
        cfg["project_key"] = args.project_key.strip().upper()
    if getattr(args, "jira_board_id", None) is not None:
        cfg["jira_board_id"] = str(args.jira_board_id).strip()
    if args.jira_url is not None:
        cfg["jira_base_url"] = args.jira_url.strip().rstrip("/")
    if getattr(args, "language", None):
        cfg["language"] = args.language.strip()
    if getattr(args, "commit_policy", None):
        cfg["commit_policy"] = args.commit_policy.strip()
    if getattr(args, "commit_message_pattern", None) is not None:
        cfg["commit_message_pattern"] = args.commit_message_pattern.strip()
    if getattr(args, "qa_handoff_mode", None):
        cfg["qa_handoff_mode"] = get_effective_qa_handoff_mode({"qa_handoff_mode": args.qa_handoff_mode})
    if getattr(args, "status_dev_done", None) is not None:
        cfg.setdefault("status_mapping", {})["dev_done"] = args.status_dev_done.strip()

    dp_cfg = _sanitize_delivery_pipeline(cfg.get("delivery_pipeline"))
    if getattr(args, "integration_branch", None) is not None:
        dp_cfg["integration_branch"] = _sanitize_git_ref(args.integration_branch)
    if getattr(args, "production_branch", None) is not None:
        dp_cfg["production_branch"] = _sanitize_git_ref(args.production_branch)
    if getattr(args, "qa_tag_prefix", None) is not None:
        clean_qa_pfx = _sanitize_tag_prefix(args.qa_tag_prefix)
        if clean_qa_pfx:
            dp_cfg["environments"]["qa"] = {
                "trigger": "tag",
                "ref": "",
                "prefix": clean_qa_pfx,
                "deployment_tier": "testing",
            }
        else:
            dp_cfg["environments"].pop("qa", None)
    if getattr(args, "uat_tag_prefix", None) is not None:
        clean_uat_pfx = _sanitize_tag_prefix(args.uat_tag_prefix)
        if clean_uat_pfx:
            dp_cfg["environments"]["uat"] = {
                "trigger": "tag",
                "ref": "",
                "prefix": clean_uat_pfx,
                "deployment_tier": "staging",
            }
        else:
            dp_cfg["environments"].pop("uat", None)
    if getattr(args, "jira_deployment_sync", None) is not None:
        dp_cfg["jira_deployment_sync"] = _parse_strict_bool(args.jira_deployment_sync, default=False)
    cfg["delivery_pipeline"] = _sanitize_delivery_pipeline(dp_cfg)
    if args.roles_json:
        custom_roles = load_json_arg(args.roles_json)
        if isinstance(custom_roles, dict):
            for r_key, r_val in custom_roles.items():
                prev_members = resolve_role_members(cfg, r_key)
                prev_primary = prev_members[0] if prev_members else {}
                raw_member_items = []
                if isinstance(r_val, str):
                    raw_member_items = [{"name": r_val, "email": prev_primary.get("email", ""), "jira_account_id": prev_primary.get("jira_account_id", "")}]
                elif isinstance(r_val, list):
                    raw_member_items = [m for m in r_val if isinstance(m, (dict, str))]
                elif isinstance(r_val, dict):
                    if isinstance(r_val.get("members"), list) and r_val["members"]:
                        raw_member_items = [m for m in r_val["members"] if isinstance(m, (dict, str))]
                    else:
                        raw_member_items = [r_val]

                cleaned_members = []
                for m_item in raw_member_items:
                    if isinstance(m_item, str):
                        cleaned_members.append({"name": m_item.strip() or r_key, "email": "", "jira_account_id": "", "specialty": ""})
                        continue
                    m_email = str(m_item.get("email") or "").strip()
                    m_tok = str(
                        m_item.get("api_token")
                        or m_item.get("jira_api_token")
                        or m_item.get("token")
                        or ""
                    ).strip()
                    if m_tok:
                        if m_email:
                            extracted_member_creds[m_email.lower()] = {"jira_email": m_email, "jira_api_token": m_tok}
                        extracted_role_creds.setdefault(r_key, {"jira_email": m_email, "jira_api_token": m_tok})
                    cleaned_members.append({
                        "name": str(m_item.get("name") or prev_primary.get("name") or r_key).strip(),
                        "email": m_email,
                        "jira_account_id": str(m_item.get("jira_account_id") or "").strip(),
                        "specialty": str(m_item.get("specialty") or "").strip(),
                    })
                cfg["roles"][r_key] = serialize_role_entry(r_key, cleaned_members)
    if getattr(args, "role_tokens_json", None):
        custom_rt = load_json_arg(args.role_tokens_json)
        if isinstance(custom_rt, dict):
            for r_key, r_val in custom_rt.items():
                r_person = resolve_role_person(cfg, r_key)
                if isinstance(r_val, str) and r_val.strip():
                    r_em = r_person.get("email", "")
                    extracted_role_creds[r_key] = {
                        "jira_email": r_em,
                        "jira_api_token": r_val.strip(),
                    }
                    if r_em:
                        extracted_member_creds[r_em.lower()] = {"jira_email": r_em, "jira_api_token": r_val.strip()}
                elif isinstance(r_val, dict):
                    r_em = str(r_val.get("jira_email") or r_val.get("email") or r_person.get("email", "")).strip()
                    r_tk = str(r_val.get("jira_api_token") or r_val.get("api_token") or r_val.get("token") or "").strip()
                    if r_tk:
                        extracted_role_creds[r_key] = {
                            "jira_email": r_em,
                            "jira_api_token": r_tk,
                        }
                        if r_em:
                            extracted_member_creds[r_em.lower()] = {"jira_email": r_em, "jira_api_token": r_tk}
    if args.status_mapping_json:
        custom_status = load_json_arg(args.status_mapping_json)
        if isinstance(custom_status, dict):
            cfg["status_mapping"].update(custom_status)
    if getattr(args, "issue_type_mapping_json", None):
        custom_itypes = load_json_arg(args.issue_type_mapping_json)
        if isinstance(custom_itypes, dict):
            cfg["issue_type_mapping"].update(custom_itypes)
    if getattr(args, "qa_backend", None):
        cfg["qa_test_backend"] = args.qa_backend.strip().lower()
    qase_cfg = cfg.setdefault("qase", dict(DEFAULT_TEAM_CONFIG["qase"]))
    if getattr(args, "qase_project_code", None):
        qase_cfg["project_code"] = args.qase_project_code.strip().upper()
    if getattr(args, "qase_base_url", None):
        qase_cfg["base_url"] = args.qase_base_url.strip().rstrip("/")
    if getattr(args, "qase_app_url", None):
        qase_cfg["app_url"] = args.qase_app_url.strip().rstrip("/")
    if getattr(args, "qase_jira_type", None):
        qase_cfg["jira_integration_type"] = args.qase_jira_type.strip()

    jira_email_arg = getattr(args, "jira_email", None)
    jira_token_arg = getattr(args, "jira_token", None)
    qase_token_arg = getattr(args, "qase_token", None)
    eff_email_to_save = jira_email_arg if jira_email_arg is not None else interactive_email
    eff_token_to_save = jira_token_arg if jira_token_arg is not None else interactive_token

    init_explicit_roles = _parse_role_list(getattr(args, "role", None) or "")
    init_name_arg = (getattr(args, "name", None) or "").strip()
    init_specialty_arg = (getattr(args, "specialty", None) or "").strip()
    if init_explicit_roles:
        for rk in init_explicit_roles:
            prev_role = resolve_role_person(cfg, rk)
            r_em = (eff_email_to_save or prev_role.get("email") or "").strip()
            r_nm = init_name_arg or (prev_role.get("name") if _is_role_claimed(rk, cfg["roles"].get(rk)) else "") or (r_em.split("@")[0] if r_em else rk)
            new_m = {
                "name": r_nm,
                "email": r_em,
                "jira_account_id": prev_role.get("jira_account_id", ""),
                "specialty": init_specialty_arg if rk == "developer" or len(init_explicit_roles) == 1 else "",
            }
            if init_specialty_arg and rk in init_explicit_roles:
                new_m["specialty"] = init_specialty_arg
            cfg["roles"][rk] = upsert_role_member(
                existing_entry=cfg["roles"].get(rk),
                role_key=rk,
                new_member=new_m,
                explicit_claim=True,
                explicit_name=bool(init_name_arg),
                explicit_specialty=bool(init_specialty_arg),
            )
            if eff_token_to_save:
                extracted_role_creds.setdefault(rk, {
                    "jira_email": r_em,
                    "jira_api_token": eff_token_to_save.strip(),
                })
                if r_em:
                    extracted_member_creds[r_em.lower()] = {"jira_email": r_em, "jira_api_token": eff_token_to_save.strip()}

    # If no default email/token was passed but at least one role/member token was provided, use the first email/token as fallback default
    if not eff_token_to_save and not local_creds.get("jira_api_token") and (extracted_role_creds or extracted_member_creds):
        first_rc = next(iter((extracted_role_creds or extracted_member_creds).values()))
        if isinstance(first_rc, dict) and first_rc.get("jira_api_token"):
            eff_token_to_save = first_rc["jira_api_token"]
            if not eff_email_to_save and first_rc.get("jira_email"):
                eff_email_to_save = first_rc["jira_email"]

    cred_saved_path = None
    if eff_email_to_save is not None or eff_token_to_save is not None or extracted_role_creds or extracted_member_creds or qase_token_arg:
        cred_saved_path = save_jira_credentials(
            repo_root,
            jira_url=cfg.get("jira_base_url") or None,
            jira_email=eff_email_to_save,
            jira_token=eff_token_to_save,
            role_credentials=extracted_role_creds if extracted_role_creds else None,
            qase_token=qase_token_arg,
            member_credentials=extracted_member_creds if extracted_member_creds else None,
        )
    elif cfg.get("jira_base_url") and get_credentials_path(repo_root).exists():
        cred_saved_path = save_jira_credentials(
            repo_root,
            jira_url=cfg.get("jira_base_url"),
        )

    client = JiraClient(cfg, offline=args.offline, repo_root=repo_root)
    jira_verification = {"mode": "live" if client.is_live else "offline", "verified": False}
    resolved_accounts = {}
    role_verifications = {}

    if client.is_live:
        try:
            jira_verification = client.verify_connection()
        except Exception as exc:
            jira_verification = {"mode": "live", "verified": False, "warning": str(exc)}

    # Verify per-role & per-member tokens (if any) and auto-resolve missing jira_account_id across all role members
    verified_by_email = {}
    if jira_verification.get("verified") and jira_verification.get("email"):
        verified_by_email[jira_verification["email"].strip().lower()] = jira_verification

    for r_key in list(cfg.get("roles", {}).keys()):
        members = resolve_role_members(cfg, r_key)
        updated_members = []
        for idx_m, m_obj in enumerate(members):
            if not _is_member_claimed(r_key, m_obj):
                updated_members.append(m_obj)
                continue
            m_email = (m_obj.get("email") or "").strip()
            m_acct = (m_obj.get("jira_account_id") or "").strip()
            m_client = JiraClient(cfg, offline=args.offline, repo_root=repo_root, role=r_key, member_email=m_email or None)
            if m_client.is_live and (m_client.using_role_token or (m_email and m_client.email.lower() == m_email.lower())):
                em_key = m_client.email.strip().lower()
                rv = verified_by_email.get(em_key)
                if rv is None:
                    try:
                        rv = m_client.verify_connection()
                        verified_by_email[em_key] = rv
                    except Exception as exc:
                        rv = {"mode": "live", "verified": False, "warning": str(exc)}
                if idx_m == 0 and m_client.using_role_token:
                    role_verifications[r_key] = rv
                if rv.get("verified"):
                    if (
                        (r_key in init_explicit_roles and not init_name_arg)
                        or m_obj.get("name", "").lower() in {m_email.split("@")[0].lower(), r_key.lower()}
                    ) and rv.get("display_name") and not args.roles_json:
                        m_obj["name"] = rv["display_name"]
                    if not m_acct and rv.get("account_id"):
                        m_obj["jira_account_id"] = rv["account_id"]
                        m_acct = rv["account_id"]
                        if idx_m == 0:
                            resolved_accounts[r_key] = m_acct
            if m_email and not m_acct:
                lookup_client = m_client if m_client.is_live else client
                if lookup_client.is_live:
                    try:
                        found_acct = lookup_client.resolve_account_id_by_email(m_email)
                        if found_acct:
                            m_obj["jira_account_id"] = found_acct
                            if idx_m == 0:
                                resolved_accounts[r_key] = found_acct
                    except Exception:
                        pass
            updated_members.append(m_obj)
        if any(_is_member_claimed(r_key, m) for m in updated_members):
            cfg["roles"][r_key] = serialize_role_entry(r_key, updated_members)

    cfg_path = get_config_path(repo_root)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Install unified CLI wrapper at .ai-workflow/bin/jira_sync.py
    bin_dir = wf_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    cli_dst = bin_dir / "jira_sync.py"
    src_script = Path(__file__).resolve()
    if src_script != cli_dst.resolve():
        shutil.copy2(src_script, cli_dst)
    cli_dst.chmod(0o755)

    # Ensure .gitignore ignores .ai-workflow/active/, .ai-workflow/credentials.json, and .ai-workflow/.env
    gitignore_updated = _ensure_gitignore_entries(repo_root)

    # Extract docs/shared/engineering-standards.md from AGENTS.md and deduplicate AGENTS.md
    agents_path = repo_root / args.agents_md
    standards_path = repo_root / args.standards_md
    standards_result = extract_and_trim_agents_md(agents_path, standards_path)

    # Compute multi-role summary (which roles share the same person)
    person_to_roles = {}
    for role_name in cfg["roles"]:
        p = resolve_role_person(cfg, role_name)
        key = p["jira_account_id"] or p["email"] or p["name"]
        person_to_roles.setdefault(key, []).append(role_name)

    final_creds = load_jira_credentials(repo_root)
    final_role_creds = final_creds.get("role_credentials") if isinstance(final_creds.get("role_credentials"), dict) else {}
    has_creds = bool(
        (os.environ.get("JIRA_EMAIL") or final_creds.get("jira_email"))
        and (os.environ.get("JIRA_API_TOKEN") or final_creds.get("jira_api_token"))
    ) or any(
        bool((v or {}).get("jira_email") and (v or {}).get("jira_api_token"))
        for v in final_role_creds.values()
    )
    role_has_api_token = {}
    for r_key in cfg["roles"]:
        rc = final_role_creds.get(r_key) if isinstance(final_role_creds.get(r_key), dict) else {}
        env_tok = os.environ.get(f"JIRA_API_TOKEN_{r_key.upper()}", "").strip()
        role_has_api_token[r_key] = bool((rc.get("jira_api_token") or "").strip() or env_tok)

    ci_detect = _detect_ci_delivery_pipeline(repo_root)
    result = {
        "ok": True,
        "action": "init",
        "config_path": str(cfg_path.relative_to(repo_root)),
        "cli_path": ".ai-workflow/bin/jira_sync.py",
        "project_key": cfg["project_key"],
        "jira_base_url": cfg.get("jira_base_url", ""),
        "jira_mode": "live" if client.is_live else "offline",
        "commit_policy": get_effective_commit_policy(cfg),
        "qa_handoff_mode": get_effective_qa_handoff_mode(cfg),
        "delivery_pipeline": cfg["delivery_pipeline"],
        "detected_delivery_pipeline": ci_detect["delivery_pipeline"],
        "detected_ci_checks": ci_detect["detected_ci_checks"],
        "suggested_qa_handoff_mode": ci_detect["suggested_qa_handoff_mode"],
        "credentials_configured": has_creds,
        "credentials_path": (
            str(cred_saved_path.relative_to(repo_root))
            if cred_saved_path is not None
            else (".ai-workflow/credentials.json" if get_credentials_path(repo_root).exists() else None)
        ),
        "role_has_api_token": role_has_api_token,
        "claimed_roles": list(_partition_claimed_roles(cfg)[0].keys()),
        "unclaimed_roles": _partition_claimed_roles(cfg)[1],
        "jira_verification": jira_verification,
        "role_verifications": role_verifications,
        "auto_resolved_account_ids": resolved_accounts,
        "gitignore_updated": gitignore_updated,
        "standards": standards_result,
        "role_consolidation": person_to_roles,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 2: status (used by team-next-step and all stations)
# ---------------------------------------------------------------------------

def _extract_tc_covered_acs(tc_item: dict) -> set:
    covered = set()
    raw = tc_item.get("ac_id") or ""
    if isinstance(raw, list):
        for item in raw:
            for m in re.findall(r"AC-\d+", str(item).upper()):
                covered.add(m)
    else:
        for m in re.findall(r"AC-\d+", str(raw).upper()):
            covered.add(m)
        if not covered and str(raw).strip():
            for part in str(raw).split(","):
                if part.strip():
                    covered.add(part.strip().upper())
    for item in tc_item.get("ac_ids") or []:
        for m in re.findall(r"AC-\d+", str(item).upper()):
            covered.add(m)
    return covered


def find_uncovered_story_acs(story: dict, test_cases: list) -> list:
    story_acs = [ac.get("id") for ac in (story.get("acceptance_criteria") or []) if ac.get("id")]
    if not story_acs:
        return []
    covered = set()
    for tc in test_cases or []:
        covered.update(_extract_tc_covered_acs(tc))
    return [ac_id for ac_id in story_acs if ac_id.upper() not in covered]


def evaluate_story_next_step(story: dict, config: dict, standards_exists: bool) -> dict:
    jira_key = story.get("jira_key", "UNKNOWN")

    # 0. Standalone Bug explicitly closed via Bug Triage (non-code-fix resolution)
    if story.get("issue_kind") == "standalone_bug" and story.get("triage_closed"):
        triage = story.get("triage") or {}
        res_disp = triage.get("resolution_display") or triage.get("resolution") or "Triaged"
        decided_by = triage.get("decided_by") or ""
        return {
            "jira_key": jira_key,
            "issue_kind": "standalone_bug",
            "current_station": f"Closed (Bug Triage: {res_disp})",
            "recommended_skill": None,
            "recommended_mode": None,
            "triage_closed": True,
            "triage": triage,
            "responsible_role": None,
            "responsible_person": decided_by,
            "reason": (
                f"Standalone Bug {jira_key} was closed via Bug Triage as '{res_disp}'"
                + (f" (decided by {decided_by})" if decided_by else "")
                + ". No code fix (`team-dev implement`) is required."
            ),
            "done_when": "No remaining action — standalone Bug closed via Bug Triage.",
        }

    spec_status = story.get("spec_status", "draft")
    tasks = story.get("tasks") or []
    bugs = story.get("bugs") or []
    review = story.get("review") or {}
    qa = story.get("qa") or {}
    candidate_commit = story.get("candidate_commit")

    open_bugs = [b for b in bugs if b.get("status") not in ("completed", "closed")]
    pending_tasks = [t for t in tasks if t.get("status") != "completed"]

    # 1. Spec not yet approved
    if spec_status != "approved":
        owner = resolve_story_role_person(config, story, "pm")
        return {
            "jira_key": jira_key,
            "current_station": "Station 1: Spec & Design",
            "recommended_skill": "team-spec",
            "recommended_mode": None,
            "responsible_role": "pm",
            "responsible_person": owner["name"],
            "reason": f"Story {jira_key} specification is currently '{spec_status}' and needs completion/approval.",
            "done_when": f"Spec & ACs are approved and synced via `jira_sync.py sync-story --key {jira_key} --status approved`.",
        }

    # 2. Open bugs take priority in development
    if open_bugs:
        first_bug = open_bugs[0]
        bug_ass = first_bug.get("assignee")
        owner = (
            bug_ass
            if (isinstance(bug_ass, dict) and _is_member_claimed("developer", bug_ass))
            else resolve_story_role_person(config, story, "developer")
        )
        return {
            "jira_key": jira_key,
            "current_station": "Station 2: Dev (Bug Fix)",
            "recommended_skill": "team-dev",
            "recommended_mode": "implement",
            "responsible_role": "developer",
            "responsible_person": owner["name"],
            "reason": f"Open Bug {first_bug.get('bug_key')} ({first_bug.get('title')}) blocks progression.",
            "done_when": f"Bug {first_bug.get('bug_key')} is fixed, tested, and checked off via `jira_sync.py complete-task --key {jira_key} --task-id {first_bug.get('bug_key')}`.",
        }

    # 3. Spec approved, but no development Sub-tasks planned yet
    if not tasks:
        owner = resolve_story_role_person(config, story, "developer")
        ac_list = story.get("acceptance_criteria") or []
        risk_level = (story.get("risk_level") or "medium").lower()
        dev_fast_track_allowed = len(ac_list) <= 3 and risk_level in ("low", "medium")
        if dev_fast_track_allowed:
            parallel_hint = (
                f"QA (`team-qa` in `plan` mode) may also draft test cases in parallel. "
                f"For single-task stories, Dev Fast-Track allows `complete-task --key {jira_key} --task-id TASK-001` directly."
            )
            done_when = f"Tasks are decomposed and synced via `jira_sync.py create-subtasks --key {jira_key}` (or completed directly via Dev Fast-Track)."
        else:
            parallel_hint = (
                f"QA (`team-qa` in `plan` mode) may also draft test cases in parallel. "
                f"Dev Fast-Track is disabled for this story ({len(ac_list)} ACs, risk={risk_level}); run `create-subtasks --key {jira_key}` first."
            )
            done_when = (
                f"Tasks are decomposed and synced via `jira_sync.py create-subtasks --key {jira_key}` "
                f"(Dev Fast-Track disabled because story has >3 ACs or high/critical risk)."
            )
        return {
            "jira_key": jira_key,
            "current_station": "Station 2: Dev Plan",
            "recommended_skill": "team-dev",
            "recommended_mode": "plan",
            "dev_fast_track_allowed": dev_fast_track_allowed,
            "parallel_hint": parallel_hint,
            "responsible_role": "developer",
            "responsible_person": owner["name"],
            "reason": f"Spec for {jira_key} is approved, but development Sub-tasks (TASK-001..N) have not been decomposed yet.",
            "done_when": done_when,
        }

    # 4. Pending development Sub-tasks
    if pending_tasks:
        completed_ids = {t["id"] for t in tasks if t.get("status") == "completed"}
        next_task = pending_tasks[0]
        for t in pending_tasks:
            deps = t.get("depends_on") or []
            if all(d in completed_ids for d in deps):
                next_task = t
                break
        t_ass = next_task.get("assignee")
        owner = (
            t_ass
            if (isinstance(t_ass, dict) and _is_member_claimed("developer", t_ass))
            else resolve_story_role_person(config, story, "developer")
        )
        return {
            "jira_key": jira_key,
            "current_station": "Station 2: Dev Implement",
            "recommended_skill": "team-dev",
            "recommended_mode": "implement",
            "next_task_id": next_task["id"],
            "responsible_role": "developer",
            "responsible_person": owner["name"],
            "reason": f"{len(pending_tasks)} of {len(tasks)} tasks remain; next eligible task is {next_task['id']} ({next_task.get('title')}).",
            "done_when": f"{next_task['id']} is implemented, verified by tests, and marked Done via `jira_sync.py complete-task --key {jira_key} --task-id {next_task['id']}`.",
        }

    # 5. All tasks completed -> check Code Review
    review_status = review.get("status")
    reviewed_commit = review.get("reviewed_commit")
    open_blocking_findings = [
        f for f in (review.get("findings") or [])
        if f.get("severity") in ("blocker", "major") and f.get("status", "open") == "open"
    ]
    if review_status == "changes-requested" and (
        open_blocking_findings or not candidate_commit or candidate_commit == reviewed_commit
    ):
        owner = resolve_story_role_person(config, story, "developer")
        open_finding_ids = [f.get("id") for f in open_blocking_findings if f.get("id")]
        finding_ids_str = ",".join(open_finding_ids) if open_finding_ids else "REV-001"
        return {
            "jira_key": jira_key,
            "current_station": "Station 2: Dev Remediation (Review Changes Requested)",
            "recommended_skill": "team-dev",
            "recommended_mode": "implement",
            "open_finding_ids": open_finding_ids,
            "responsible_role": "developer",
            "responsible_person": owner["name"],
            "reason": f"Code review on {jira_key} requested changes ({review.get('summary', 'see findings')}).",
            "done_when": (
                f"Review findings ({finding_ids_str}) are fixed with updated commit/tests, marked completed via "
                f"`jira_sync.py complete-task --key {jira_key} --task-id {finding_ids_str}`, and re-submitted to `team-review`."
            ),
        }

    if review_status != "approved" or (candidate_commit and reviewed_commit and reviewed_commit != candidate_commit):
        rev_role, owner, _ = resolve_effective_reviewer(config, story)
        stale_note = (
            f" (candidate commit changed from {reviewed_commit} to {candidate_commit})"
            if (review_status == "approved" and reviewed_commit != candidate_commit)
            else ""
        )
        return {
            "jira_key": jira_key,
            "current_station": "Station 3a: Code Review & Handoff",
            "recommended_skill": "team-review",
            "recommended_mode": None,
            "responsible_role": rev_role,
            "responsible_person": owner["name"],
            "standards_fallback": not standards_exists,
            "reason": f"All {len(tasks)} development tasks are Done; code review & QA handoff is required{stale_note}.",
            "done_when": f"Code review passes and handoff is synced via `jira_sync.py handoff --key {jira_key} --review-status approved`.",
        }

    # 6. Code Review approved -> check QA Deploy Gate (#4) and QA Plan / Verify
    test_cases = qa.get("test_cases") or []
    qa_status = qa.get("status")
    tested_commit = qa.get("tested_commit")
    uncovered_acs = find_uncovered_story_acs(story, test_cases)
    qa_handoff_mode = get_effective_qa_handoff_mode(config)
    awaiting_qa_deploy = _is_awaiting_qa_deploy(story, config)
    in_qa_status_name = (config.get("status_mapping") or {}).get("in_qa") or "In QA"

    if not test_cases or uncovered_acs:
        owner = resolve_story_role_person(config, story, "qa")
        gap_note = f" (missing coverage for {', '.join(uncovered_acs)})" if (test_cases and uncovered_acs) else ""
        qa_backend_val = (config.get("qa_test_backend") or "").strip()
        plan_rec = {
            "jira_key": jira_key,
            "current_station": "Station 3b: QA Plan" + (" (Awaiting QA Deploy)" if awaiting_qa_deploy else ""),
            "recommended_skill": "team-qa",
            "recommended_mode": "plan",
            "qa_test_backend": qa_backend_val,
            "requires_qa_backend_choice": not bool(qa_backend_val),
            "responsible_role": "qa",
            "responsible_person": owner["name"],
            "reason": (
                f"Code review is approved, and QA verification is waiting for QA environment deployment (`{in_qa_status_name}` / `promote-qa`), "
                f"while QA may prepare test cases (TC-001..N) in parallel for {jira_key}{gap_note}."
                if awaiting_qa_deploy
                else f"Code review is approved, but QA test cases (TC-001..N) need planning for {jira_key}{gap_note}."
            ),
            "done_when": (
                f"Test cases covering all Story ACs are planned via `jira_sync.py record-qa --key {jira_key} --mode plan`; "
                f"QA verification (`team-qa verify`) remains gated until Jira reaches `{in_qa_status_name}` or `jira_sync.py promote-qa --key {jira_key}` runs."
                if awaiting_qa_deploy
                else f"Test cases covering all Story ACs are planned via `jira_sync.py record-qa --key {jira_key} --mode plan` (or verified directly via QA Fast-Track `--mode verify --all-pass`)."
            ),
        }
        if awaiting_qa_deploy:
            plan_rec["awaiting_qa_deploy"] = True
            plan_rec["qa_handoff_mode"] = qa_handoff_mode
            plan_rec["qa_deploy_status"] = (story.get("qa_deploy_gate") or {}).get("status") or "awaiting_qa_deploy"
            qa_deploy_hint = _build_qa_deploy_hint(config, jira_key)
            if qa_deploy_hint:
                plan_rec["qa_deploy_hint"] = qa_deploy_hint
                plan_rec["can_auto_chain_next_station"] = False
                plan_rec["requires_user_confirmation_for_next_station"] = True
                plan_rec["done_when"] = f"{plan_rec['done_when']} {qa_deploy_hint}"
            plan_rec["parallel_hint"] = (
                f"QA may draft the test plan (`team-qa plan`) in parallel now; "
                f"`team-qa verify` is gated until QA deploy completes (`promote-qa --key {jira_key}` or Jira status '{in_qa_status_name}')."
            )
        return plan_rec

    if awaiting_qa_deploy:
        dev_owner = resolve_story_role_person(config, story, "developer")
        cur_jira_st = story.get("jira_status") or (config.get("status_mapping") or {}).get("dev_done") or "dev_done"
        qa_deploy_hint = _build_qa_deploy_hint(config, jira_key)
        base_done_when = (
            f"Deploy to QA environment so CI/CD transitions Jira to '{in_qa_status_name}' (sync via `jira_sync.py status`/`pull`), "
            f"or run `jira_sync.py promote-qa --key {jira_key} [--tag <QA-TAG>]` to notify QA and unblock `team-qa (verify)`."
        )
        awaiting_rec = {
            "jira_key": jira_key,
            "current_station": "Station 3a -> 3b: Awaiting QA Deploy",
            "recommended_skill": None,
            "recommended_mode": None,
            "awaiting_qa_deploy": True,
            "qa_handoff_mode": qa_handoff_mode,
            "qa_deploy_status": (story.get("qa_deploy_gate") or {}).get("status") or "awaiting_qa_deploy",
            "responsible_role": "developer",
            "responsible_person": dev_owner["name"],
            "reason": (
                f"Code review for {jira_key} is approved on commit {candidate_commit or 'HEAD'} "
                f"(Jira status: '{cur_jira_st}'), awaiting QA environment deployment before `team-qa (verify)` can start."
            ),
            "done_when": f"{qa_deploy_hint} ({base_done_when})" if qa_deploy_hint else base_done_when,
        }
        if qa_deploy_hint:
            awaiting_rec["qa_deploy_hint"] = qa_deploy_hint
            awaiting_rec["can_auto_chain_next_station"] = False
            awaiting_rec["requires_user_confirmation_for_next_station"] = True
        return awaiting_rec

    unsatisfied_tcs = [c for c in test_cases if not _is_tc_satisfied(c, story)]
    if qa_status != "passed" or unsatisfied_tcs or (candidate_commit and tested_commit and tested_commit != candidate_commit):
        owner = resolve_story_role_person(config, story, "qa")
        qa_backend_val = (config.get("qa_test_backend") or "").strip()
        stale_note = (
            f" (candidate commit changed from {tested_commit} to {candidate_commit})"
            if (qa_status == "passed" and tested_commit != candidate_commit)
            else ""
        )
        pending_automated = [
            c["id"] for c in test_cases
            if not _is_tc_satisfied(c, story) and c.get("execution_type", "automated") == "automated"
        ]
        pending_external = [
            c["id"] for c in test_cases
            if not _is_tc_satisfied(c, story) and c.get("execution_type") == "external"
        ]
        pending_manual = [
            c["id"] for c in test_cases
            if not _is_tc_satisfied(c, story) and c.get("execution_type") == "manual"
        ]
        pending_details = []
        if pending_automated:
            pending_details.append(f"automated: {', '.join(pending_automated)}")
        if pending_external:
            pending_details.append(f"external: {', '.join(pending_external)}")
        if pending_manual:
            pending_details.append(f"manual: {', '.join(pending_manual)}")
        pending_summary = f" (pending — {'; '.join(pending_details)})" if pending_details else ""

        if pending_external or pending_manual:
            explicit_flags = " ".join(f"--result {tc}=pass" for tc in (pending_external + pending_manual))
            done_when = (
                f"Remaining TCs ({'; '.join(pending_details)}) pass on commit {candidate_commit or 'HEAD'} via "
                f"`jira_sync.py record-qa --key {jira_key} --mode verify {explicit_flags}` (or `--junit-xml` / `--results-json`)."
            )
        else:
            done_when = f"All TCs pass on commit {candidate_commit or 'HEAD'} via `jira_sync.py record-qa --key {jira_key} --mode verify`."

        return {
            "jira_key": jira_key,
            "current_station": "Station 3b: QA Verify",
            "recommended_skill": "team-qa",
            "recommended_mode": "verify",
            "qa_test_backend": qa_backend_val,
            "requires_qa_backend_choice": not bool(qa_backend_val),
            "responsible_role": "qa",
            "responsible_person": owner["name"],
            "pending_automated_tcs": pending_automated,
            "pending_external_tcs": pending_external,
            "pending_manual_tcs": pending_manual,
            "reason": f"QA test plan ({len(test_cases)} TCs) is ready; test execution verification is required{pending_summary}{stale_note}.",
            "done_when": done_when,
        }

    # 7. All gates passed -> Station 4: Release & Close
    owner = resolve_story_role_person(config, story, "release_manager")
    release_reminders = _build_release_checklist_reminders(config)
    rel_rec = {
        "jira_key": jira_key,
        "current_station": "Station 4: Release & Close",
        "recommended_skill": "team-release",
        "recommended_mode": None,
        "responsible_role": "release_manager",
        "responsible_person": owner["name"],
        "reason": f"Spec, Sub-tasks, Code Review, and QA Verification are all complete and aligned on commit {candidate_commit} with 0 open Bugs.",
        "done_when": f"Release summary is posted, Jira Story {jira_key} is transitioned to Done, and `.ai-workflow/active/{jira_key}.json` is removed via `jira_sync.py close-story`.",
        "release_checklist_reminders": release_reminders,
    }
    return rel_rec


def _cmd_status_epic(
    repo_root: Path,
    config: dict,
    client: JiraClient,
    standards_exists: bool,
    creds_missing: bool,
    raw_epic: str,
) -> int:
    epic_key = validate_jira_key(raw_epic)
    active_dir = get_active_dir(repo_root)
    stories_by_key = {}

    if active_dir.exists():
        for af in sorted(active_dir.glob("*.json")):
            try:
                st = json.loads(af.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(st, dict) and _clean_optional_text(st.get("epic_key")).upper() == epic_key:
                k = _clean_optional_text(st.get("jira_key") or af.stem).upper()
                stories_by_key[k] = st

    main_root = _resolve_main_worktree_root(repo_root)
    if main_root and get_active_dir(main_root).exists():
        for mf in sorted(get_active_dir(main_root).glob("*.json")):
            try:
                st = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(st, dict) and _clean_optional_text(st.get("epic_key")).upper() == epic_key:
                k = _clean_optional_text(st.get("jira_key") or mf.stem).upper()
                if k not in stories_by_key:
                    stories_by_key[k] = st

    if not stories_by_key and creds_missing:
        base_url = (config.get("jira_base_url") or "").strip()
        claimed_map, unclaimed_list = _partition_claimed_roles(config)
        out = {
            "initialized": True,
            "standards_exists": standards_exists,
            "epic_key": epic_key,
            "credentials_missing": True,
            "requires_member_auth": True,
            "claimed_roles": claimed_map,
            "unclaimed_roles": unclaimed_list,
            "recommended_skill": "team-init",
            "recommended_mode": "auth",
            "reason": (
                f"Project is configured for live Jira ({base_url}), but local `.ai-workflow/credentials.json` is missing "
                f"(cannot fetch Epic {epic_key} progress)."
            ),
            "done_when": "Collaborator claims their role(s) and binds personal Jira credentials via `jira_sync.py auth --role <ROLES> --jira-email <EMAIL> --jira-token <TOKEN>`.",
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    epic_summary = epic_key
    warnings = []
    query_mode = "offline"
    if client.is_live:
        try:
            epic_issue = client.get_issue(epic_key)
            if isinstance(epic_issue, dict):
                e_sum = _clean_optional_text(((epic_issue.get("fields") or {}).get("summary")))
                if e_sum:
                    epic_summary = e_sum
        except Exception:
            pass

        for lk in list(stories_by_key.keys()):
            try:
                rec_st, _ = reconcile_story_with_jira(
                    client, config, repo_root, lk, story=stories_by_key[lk], save=True
                )
                stories_by_key[lk] = rec_st
            except Exception:
                pass

        remote_children, query_mode, fetch_warnings = _fetch_epic_children_live(client, config, epic_key)
        warnings.extend(fetch_warnings)
        for r_iss in remote_children:
            rk = _clean_optional_text(r_iss.get("key")).upper()
            r_flds = r_iss.get("fields") or {}
            r_sum = _clean_optional_text(r_flds.get("summary")) or rk
            r_st_name = _clean_optional_text(((r_flds.get("status") or {}).get("name"))) or "To Do"
            if rk in stories_by_key:
                if r_sum and not stories_by_key[rk].get("title"):
                    stories_by_key[rk]["title"] = r_sum
                if r_st_name:
                    stories_by_key[rk]["jira_status"] = r_st_name
            elif _is_jira_status_done(r_st_name, config):
                stories_by_key[rk] = {
                    "jira_key": rk,
                    "epic_key": epic_key,
                    "title": r_sum,
                    "jira_status": r_st_name or "Done",
                    "spec_status": "approved",
                    "tasks": [],
                    "bugs": [],
                }
            else:
                hydrated = hydrate_story_from_remote(client, repo_root, rk, persist=False)
                if isinstance(hydrated, dict):
                    hydrated.setdefault("epic_key", epic_key)
                    stories_by_key[rk] = hydrated
                else:
                    stories_by_key[rk] = {
                        "jira_key": rk,
                        "epic_key": epic_key,
                        "title": r_sum,
                        "jira_status": r_st_name,
                        "spec_status": "draft",
                        "tasks": [],
                        "bugs": [],
                    }

    def _is_story_completed(st_obj: dict) -> bool:
        if _is_jira_status_done(st_obj.get("jira_status", ""), config):
            return True
        if st_obj.get("issue_kind") == "standalone_bug" and st_obj.get("triage_closed"):
            return True
        return False

    completed_keys = {k for k, st in stories_by_key.items() if _is_story_completed(st)}
    remote_dep_done_cache = {}
    todo_statuses = {"", "to do", "todo", "待辦", "待办"}
    cfg_todo = _clean_optional_text((config.get("status_mapping") or {}).get("todo")).lower()
    if cfg_todo:
        todo_statuses.add(cfg_todo)

    ordered_items = sorted(
        stories_by_key.items(),
        key=lambda kv: (kv[1].get("wave") if isinstance(kv[1].get("wave"), int) else 9999, kv[0]),
    )

    story_rows = []
    station_breakdown = {}
    completed_stories = 0
    in_progress_stories = 0
    blocked_stories = 0
    ready_to_start_stories = 0
    next_candidates = []

    for k, st in ordered_items:
        is_completed = k in completed_keys
        tasks = st.get("tasks") or []
        bugs = st.get("bugs") or []
        open_bugs = len([b for b in bugs if isinstance(b, dict) and b.get("status") not in ("completed", "closed")])
        tasks_done = len([t for t in tasks if isinstance(t, dict) and t.get("status") == "completed"])
        tasks_total = len(tasks)
        depends_on_keys = [str(dk).strip().upper() for dk in (st.get("depends_on_keys") or []) if str(dk).strip()]
        resolved_deps = {str(rk).strip().upper() for rk in (st.get("resolved_depends_on_keys") or []) if str(rk).strip()}
        blocked_by_dependencies = []
        for dk in depends_on_keys:
            if dk in completed_keys or dk in resolved_deps:
                continue
            if client.is_live and dk not in stories_by_key:
                if dk not in remote_dep_done_cache:
                    dep_done = False
                    try:
                        dep_iss = client.get_issue(dk)
                        dep_st = _clean_optional_text((((dep_iss or {}).get("fields") or {}).get("status") or {}).get("name"))
                        dep_done = _is_jira_status_done(dep_st, config)
                    except Exception:
                        dep_done = False
                    remote_dep_done_cache[dk] = dep_done
                    if dep_done:
                        completed_keys.add(dk)
                if remote_dep_done_cache[dk]:
                    continue
            blocked_by_dependencies.append(dk)

        if is_completed:
            current_station = "Done"
            recommended_skill = None
            recommended_mode = None
            rec = {}
        else:
            rec = evaluate_story_next_step(st, config, standards_exists)
            current_station = rec.get("current_station") or "Unknown"
            recommended_skill = rec.get("recommended_skill")
            recommended_mode = rec.get("recommended_mode")

        jira_status_str = _clean_optional_text(st.get("jira_status")) or ("Done" if is_completed else "To Do")
        spec_status_str = _clean_optional_text(st.get("spec_status")) or ("approved" if is_completed else "draft")
        wave_val = st.get("wave") if isinstance(st.get("wave"), int) else None

        row = {
            "key": k,
            "title": _clean_optional_text(st.get("title")) or k,
            "jira_status": jira_status_str,
            "current_station": current_station,
            "recommended_skill": recommended_skill,
            "recommended_mode": recommended_mode,
            "spec_status": spec_status_str,
            "tasks_done": tasks_done,
            "tasks_total": tasks_total,
            "open_bugs": open_bugs,
            "depends_on_keys": depends_on_keys,
            "blocked_by_dependencies": blocked_by_dependencies,
            "is_completed": is_completed,
            "wave": wave_val,
        }
        story_rows.append(row)
        station_breakdown[current_station] = station_breakdown.get(current_station, 0) + 1

        if is_completed:
            completed_stories += 1
        elif blocked_by_dependencies or open_bugs > 0 or (rec.get("awaiting_qa_deploy") and recommended_skill is None):
            blocked_stories += 1
        elif spec_status_str != "approved" and tasks_total == 0 and jira_status_str.strip().lower() in todo_statuses:
            ready_to_start_stories += 1
        else:
            in_progress_stories += 1

        if not is_completed and not blocked_by_dependencies and recommended_skill is not None:
            is_active_work = 0 if (open_bugs > 0 or tasks_total > 0 or spec_status_str == "approved") else 1
            next_candidates.append((wave_val if wave_val is not None else 9999, is_active_work, k, row, rec))

    total_stories = len(story_rows)
    progress_pct = round((completed_stories / total_stories) * 100, 1) if total_stories > 0 else 0.0

    next_recommended_story = None
    if next_candidates:
        next_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        _, _, _, best_row, best_rec = next_candidates[0]
        next_recommended_story = {
            "key": best_row["key"],
            "title": best_row["title"],
            "wave": best_row["wave"],
            "current_station": best_row["current_station"],
            "recommended_skill": best_row["recommended_skill"],
            "recommended_mode": best_row["recommended_mode"],
            "responsible_role": best_rec.get("responsible_role"),
            "responsible_person": best_rec.get("responsible_person"),
            "reason": best_rec.get("reason") or "",
        }

    out = {
        "initialized": True,
        "standards_exists": standards_exists,
        "mode": "live" if client.is_live else "offline",
        "epic_key": epic_key,
        "epic_summary": epic_summary,
        "total_stories": total_stories,
        "completed_stories": completed_stories,
        "in_progress_stories": in_progress_stories,
        "blocked_stories": blocked_stories,
        "ready_to_start_stories": ready_to_start_stories,
        "progress_pct": progress_pct,
        "station_breakdown": station_breakdown,
        "next_recommended_story": next_recommended_story,
        "stories": story_rows,
        "can_auto_chain_next_station": False,
        "requires_user_confirmation": True,
        "requires_user_confirmation_for_next_station": True,
    }
    if client.is_live:
        out["query_mode"] = query_mode
    if warnings:
        out["warnings"] = warnings
    if creds_missing:
        out["credentials_missing"] = True
    dp_epic = _sanitize_delivery_pipeline(config.get("delivery_pipeline"))
    if dp_epic.get("integration_branch"):
        out["integration_branch"] = dp_epic["integration_branch"]
        out["base_branch"] = _resolve_base_branch(repo_root, config)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args) -> int:
    if getattr(args, "key", None) is not None and getattr(args, "epic", None) is not None:
        print("ERROR: --key and --epic cannot be used together.", file=sys.stderr)
        return 1

    repo_root = Path(args.repo_root).resolve()
    cfg_path = get_config_path(repo_root)
    standards_path = repo_root / "docs" / "shared" / "engineering-standards.md"
    standards_exists = standards_path.exists()

    if not cfg_path.exists():
        out = {
            "initialized": False,
            "standards_exists": standards_exists,
            "recommended_skill": "team-init",
            "recommended_mode": None,
            "reason": "Project has not been initialized (.ai-workflow/team-config.json is missing).",
            "done_when": "`.ai-workflow/team-config.json`, `.gitignore`, and `docs/shared/engineering-standards.md` are initialized via `team-init`.",
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    config = load_team_config(repo_root)
    dp_cfg = _sanitize_delivery_pipeline(config.get("delivery_pipeline"))
    configured_int_branch = dp_cfg.get("integration_branch") or ""
    resolved_base_branch = _resolve_base_branch(repo_root, config) if configured_int_branch else ""

    def _attach_branch_context(payload: dict) -> dict:
        if configured_int_branch:
            payload["integration_branch"] = configured_int_branch
            payload["base_branch"] = resolved_base_branch
        return payload

    active_dir = get_active_dir(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root)
    base_url = (config.get("jira_base_url") or "").strip()
    creds_missing = bool(base_url and not args.offline and not client.is_live)

    if getattr(args, "epic", None) is not None:
        return _cmd_status_epic(repo_root, config, client, standards_exists, creds_missing, args.epic)

    if args.key:
        jira_key = validate_jira_key(args.key)
        target_file = active_dir / f"{jira_key}.json"
        hydrated_source = None
        remote_issue = None

        if getattr(args, "fetch_remote", False) and client.is_live:
            try:
                remote_issue = client.get_issue(jira_key)
            except Exception as exc:
                remote_issue = {"error": str(exc)}

        if not target_file.exists():
            wt_story = _hydrate_story_from_worktree(repo_root, jira_key)
            if wt_story is not None:
                hydrated_source = "worktree"
            elif client.is_live:
                rem_story = hydrate_story_from_remote(client, repo_root, jira_key)
                if rem_story is not None:
                    hydrated_source = "jira"

        if not target_file.exists():
            if creds_missing:
                claimed_map, unclaimed_list = _partition_claimed_roles(config)
                out = {
                    "initialized": True,
                    "standards_exists": standards_exists,
                    "jira_key": jira_key,
                    "story_found": False,
                    "credentials_missing": True,
                    "requires_member_auth": True,
                    "claimed_roles": claimed_map,
                    "unclaimed_roles": unclaimed_list,
                    "recommended_skill": "team-init",
                    "recommended_mode": "auth",
                    "reason": (
                        f"Project is configured for live Jira ({base_url}), but local `.ai-workflow/credentials.json` is missing on this machine "
                        f"(cannot fetch remote Story {jira_key})."
                    ),
                    "done_when": "Collaborator claims their role(s) and binds personal Jira credentials via `jira_sync.py auth --role <ROLES> --jira-email <EMAIL> --jira-token <TOKEN>`.",
                }
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0

            owner = resolve_role_person(config, "pm")
            out = {
                "initialized": True,
                "standards_exists": standards_exists,
                "jira_key": jira_key,
                "story_found": False,
                "remote_issue": remote_issue,
                "recommended_skill": "team-spec",
                "recommended_mode": None,
                "responsible_role": "pm",
                "responsible_person": owner["name"],
                "reason": f"No active state file `.ai-workflow/active/{jira_key}.json` exists yet (either a new Story or already closed).",
                "done_when": f"Story {jira_key} specification is drafted, approved, and synced via `team-spec`.",
            }
            _attach_branch_context(out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        story = load_active_story(repo_root, jira_key, client=client)
        rec = evaluate_story_next_step(story, config, standards_exists)
        eff_commit_policy = get_effective_commit_policy(config)
        rec["initialized"] = True
        rec["standards_exists"] = standards_exists
        rec["story_found"] = True
        rec["commit_policy"] = eff_commit_policy
        rec["requires_commit_confirmation"] = eff_commit_policy == "confirm_before_commit"
        if rec.get("recommended_skill") == "team-qa":
            rec["qa_identity_guardrail"] = _build_qa_identity_guardrail(repo_root, config, story=story)
        if creds_missing:
            rec["credentials_missing"] = True
        if hydrated_source:
            rec["hydrated_from"] = hydrated_source
        if remote_issue is not None:
            rec["remote_issue"] = remote_issue
        _attach_branch_context(rec)
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0

    def _filter_open_active_files(files: list) -> list:
        out_files = []
        for af in files:
            try:
                st_peek = json.loads(af.read_text(encoding="utf-8"))
                if isinstance(st_peek, dict) and st_peek.get("triage_closed"):
                    continue
            except Exception:
                pass
            out_files.append(af)
        return out_files

    all_active_files = sorted(active_dir.glob("*.json")) if active_dir.exists() else []
    active_files = _filter_open_active_files(all_active_files)
    hydrated_from_source = None
    discovered_from_jira = []
    if not active_files:
        main_root = _resolve_main_worktree_root(repo_root)
        if main_root and get_active_dir(main_root).exists():
            for mf in sorted(get_active_dir(main_root).glob("*.json")):
                _hydrate_story_from_worktree(repo_root, mf.stem)
            all_active_files = sorted(active_dir.glob("*.json")) if active_dir.exists() else []
            active_files = _filter_open_active_files(all_active_files)
            if active_files:
                hydrated_from_source = "worktree"

    if not active_files and creds_missing:
        qa_backend_val = (config.get("qa_test_backend") or "").strip().lower()
        claimed_map, unclaimed_list = _partition_claimed_roles(config)
        out = {
            "initialized": True,
            "standards_exists": standards_exists,
            "active_stories": [],
            "credentials_missing": True,
            "requires_member_auth": True,
            "claimed_roles": claimed_map,
            "unclaimed_roles": unclaimed_list,
            "qase_required": qa_backend_val == "qase",
            "recommended_skill": "team-init",
            "recommended_mode": "auth",
            "reason": (
                f"Repository is already initialized for live Jira ({base_url}), "
                f"but `.ai-workflow/credentials.json` is gitignored and not yet configured on this machine."
            ),
            "done_when": "Collaborator selects their role(s) and binds personal Jira API token via `jira_sync.py auth --role <ROLES> --jira-email <EMAIL> --jira-token <TOKEN>`.",
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if client.is_live:
        existing_local_keys = {f.stem.upper() for f in all_active_files}
        for af in all_active_files:
            try:
                st_tmp = json.loads(af.read_text(encoding="utf-8"))
                for b_item in st_tmp.get("bugs") or []:
                    if isinstance(b_item, dict) and b_item.get("bug_key"):
                        existing_local_keys.add(str(b_item["bug_key"]).strip().upper())
            except Exception:
                pass
        bug_type = (config.get("issue_type_mapping") or {}).get("bug", "Bug")
        try:
            remote_keys = client.search_active_stories(config.get("project_key", ""), bug_type_name=bug_type)
        except Exception:
            remote_keys = []
        for rk in remote_keys:
            if rk.upper() not in existing_local_keys:
                rem_st = hydrate_story_from_remote(client, repo_root, rk)
                if rem_st is not None:
                    discovered_from_jira.append(rk)
                    for b_item in rem_st.get("bugs") or []:
                        if isinstance(b_item, dict) and b_item.get("bug_key"):
                            existing_local_keys.add(str(b_item["bug_key"]).strip().upper())
        if discovered_from_jira:
            all_active_files = sorted(active_dir.glob("*.json")) if active_dir.exists() else []
            active_files = _filter_open_active_files(all_active_files)
            hydrated_from_source = "jira"

    if not active_files:
        owner = resolve_role_person(config, "pm")
        out = {
            "initialized": True,
            "standards_exists": standards_exists,
            "active_stories": [],
            "recommended_skill": "team-spec",
            "recommended_mode": None,
            "responsible_role": "pm",
            "responsible_person": owner["name"],
            "reason": "No active work items in `.ai-workflow/active/`. Ready to start a new feature specification.",
            "done_when": "A new Jira Story specification is created and approved via `team-spec`.",
        }
        _attach_branch_context(out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if len(active_files) > 1:
        keys = [f.stem for f in active_files]
        out = {
            "initialized": True,
            "standards_exists": standards_exists,
            "active_stories": keys,
            "ambiguous": True,
            "recommended_skill": None,
            "reason": f"Multiple active stories found ({', '.join(keys)}). Ask the user which Jira Story key to route.",
        }
        if creds_missing:
            out["credentials_missing"] = True
        if discovered_from_jira:
            out["discovered_from_jira"] = discovered_from_jira
        _attach_branch_context(out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    story = load_active_story(repo_root, active_files[0].stem, client=client, reconcile=True)
    rec = evaluate_story_next_step(story, config, standards_exists)
    eff_commit_policy = get_effective_commit_policy(config)
    rec["initialized"] = True
    rec["standards_exists"] = standards_exists
    rec["story_found"] = True
    rec["commit_policy"] = eff_commit_policy
    rec["requires_commit_confirmation"] = eff_commit_policy == "confirm_before_commit"
    if rec.get("recommended_skill") == "team-qa":
        rec["qa_identity_guardrail"] = _build_qa_identity_guardrail(repo_root, config, story=story)
    if creds_missing:
        rec["credentials_missing"] = True
    if hydrated_from_source:
        rec["hydrated_from"] = hydrated_from_source
    if discovered_from_jira:
        rec["discovered_from_jira"] = discovered_from_jira
    _attach_branch_context(rec)
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0


def cmd_pull(args) -> int:
    """Explicitly reconcile local `.ai-workflow/active/` state with live Jira Web UI edits."""
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root)
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1

    if getattr(args, "key", None):
        jira_key = validate_jira_key(args.key)
        p = get_active_dir(repo_root) / f"{jira_key}.json"
        if not p.exists():
            rem_st = hydrate_story_from_remote(client, repo_root, jira_key)
            if rem_st is None:
                print(f"ERROR: Could not find or hydrate Jira Story {jira_key}.", file=sys.stderr)
                return 1
            story, delta = reconcile_story_with_jira(client, config, repo_root, jira_key, story=rem_st, save=True)
        else:
            story = json.loads(p.read_text(encoding="utf-8"))
            story, delta = reconcile_story_with_jira(client, config, repo_root, jira_key, story=story, save=True)
        print(json.dumps({
            "ok": True,
            "action": "pull",
            "jira_key": jira_key,
            "reconciled": delta,
            "remote_description_modified": bool(story.get("remote_description_modified")),
            "task_count": len(story.get("tasks") or []),
            "open_bug_count": len([b for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]),
        }, ensure_ascii=False, indent=2))
        return 0

    active_dir = get_active_dir(repo_root)
    discovered = []
    if client.is_live:
        bug_type = (config.get("issue_type_mapping") or {}).get("bug", "Bug")
        existing_files = sorted(active_dir.glob("*.json")) if active_dir.exists() else []
        existing_keys = {f.stem.upper() for f in existing_files}
        for af in existing_files:
            try:
                st_tmp = json.loads(af.read_text(encoding="utf-8"))
                for b_item in st_tmp.get("bugs") or []:
                    if isinstance(b_item, dict) and b_item.get("bug_key"):
                        existing_keys.add(str(b_item["bug_key"]).strip().upper())
            except Exception:
                pass
        for rk in client.search_active_stories(config.get("project_key", ""), bug_type_name=bug_type):
            if rk.upper() not in existing_keys:
                rem_st = hydrate_story_from_remote(client, repo_root, rk)
                if rem_st is not None:
                    discovered.append(rk)
                    for b_item in rem_st.get("bugs") or []:
                        if isinstance(b_item, dict) and b_item.get("bug_key"):
                            existing_keys.add(str(b_item["bug_key"]).strip().upper())
    results = {}
    for af in sorted(active_dir.glob("*.json")) if active_dir.exists() else []:
        st = json.loads(af.read_text(encoding="utf-8"))
        _, d = reconcile_story_with_jira(client, config, repo_root, af.stem, story=st, save=True)
        results[af.stem] = d
    print(json.dumps({
        "ok": True,
        "action": "pull",
        "discovered_stories": discovered,
        "stories": results,
    }, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 3: sync-story (Station 1: team-spec)
# ---------------------------------------------------------------------------

_EPIC_METADATA_FIELDS = (
    "epic_key",
    "epic_decompose_id",
    "depends_on",
    "depends_on_keys",
    "resolved_depends_on_keys",
    "wave",
    "shared_contracts",
)
_PRESERVED_LIFECYCLE_FIELDS = (
    "current_owner_role",
    "role_owners",
    "jira_status",
    "tasks",
    "review",
    "qa",
    "bugs",
    "qa_deploy_gate",
    "candidate_commit",
    "spec_status",
    "approved_by",
    "approved_at",
    "issue_kind",
    "triage",
    "triage_closed",
    "jira_resolution",
) + _EPIC_METADATA_FIELDS


def _parse_bool_flag(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return bool(val) if val is not None else False


def _normalize_open_questions_list(parsed, source_label: str = "--open-questions-json") -> list:
    """
    Validate and normalize a parsed list of open questions (dicts or strings).
    Rejects duplicate explicit Q-### IDs (case-insensitive) among valid entries,
    avoids reserving IDs from blank entries, auto-assigns Q-001.. without collision,
    and parses boolean `blocking` strictly.
    """
    if parsed is None:
        return []
    if not isinstance(parsed, list):
        raise ValueError(f"{source_label} must be a JSON array.")

    valid_raw_items = []
    used_ids_upper = set()
    for item in parsed:
        if item is None:
            continue
        if isinstance(item, str):
            q_text = _clean_optional_text(item)
            if not q_text:
                continue
            valid_raw_items.append(("str", q_text))
        elif isinstance(item, dict):
            q_text = _clean_optional_text(item.get("question") or item.get("text") or item.get("summary"))
            if not q_text:
                continue
            ex_id = _clean_optional_text(item.get("id")).upper()
            if ex_id:
                if ex_id in used_ids_upper:
                    raise ValueError(f"Duplicate open question id '{ex_id}' in {source_label}.")
                used_ids_upper.add(ex_id)
            valid_raw_items.append(("dict", (item, q_text, ex_id)))
        else:
            raise ValueError(f"Each item in {source_label} must be a JSON object or string.")

    next_seq = 1

    def _next_q_id() -> str:
        nonlocal next_seq
        while f"Q-{next_seq:03d}" in used_ids_upper:
            next_seq += 1
        qid = f"Q-{next_seq:03d}"
        used_ids_upper.add(qid)
        next_seq += 1
        return qid

    normalized = []
    for kind, payload in valid_raw_items:
        if kind == "str":
            normalized.append({
                "id": _next_q_id(),
                "question": payload,
                "owner": "",
                "proposed_default": "",
                "impacted_acs": [],
                "blocking": False,
            })
        else:
            item, q_text, explicit_id = payload
            q_id = explicit_id or _next_q_id()
            owner = _clean_optional_text(item.get("owner"))
            proposed_default = _clean_optional_text(item.get("proposed_default"))
            raw_acs = item.get("impacted_acs")
            if isinstance(raw_acs, str):
                impacted_acs = [a.strip() for a in raw_acs.split(",") if a.strip()]
            elif isinstance(raw_acs, list):
                impacted_acs = [_clean_optional_text(a) for a in raw_acs if _clean_optional_text(a)]
            else:
                impacted_acs = []
            normalized.append({
                "id": q_id,
                "question": q_text,
                "owner": owner,
                "proposed_default": proposed_default,
                "impacted_acs": impacted_acs,
                "blocking": _parse_bool_flag(item.get("blocking")),
            })
    return normalized


def _parse_and_normalize_open_questions(raw_val) -> list:
    """Parse and normalize --open-questions-json (raw JSON array, file path, or @file path)."""
    if raw_val is None:
        return []
    s = str(raw_val).strip()
    if not s:
        return []
    try:
        parsed = load_json_arg(s)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        raise ValueError(f"Malformed --open-questions-json: {exc}") from exc
    return _normalize_open_questions_list(parsed, source_label="--open-questions-json")


def format_story_description(story: dict, config: dict = None) -> str:
    zh = is_zh_tw(config)
    lines = [f"# {story.get('jira_key')}: {story.get('title', '')}", ""]
    surface = story.get("change_surface", {})
    if zh:
        lines.append("## 1. 範圍界定")
        lines.append(f"- **納入範圍 (In-Scope)：** {story.get('scope_in') or '無'}")
        lines.append(f"- **排除範圍 (Out-of-Scope)：** {story.get('scope_out') or '無'}")
        lines.append(
            f"- **變更影響面：** UI={surface.get('has_ui', False)}, "
            f"API={surface.get('has_api', False)}, Data={surface.get('has_data', False)} | "
            f"**風險等級：** `{story.get('risk_level', 'medium')}`"
        )
        lines.append("")

        lines.append("## 2. 驗收條件")
        for ac in story.get("acceptance_criteria") or []:
            lines.append(f"- **{ac['id']}**: {ac['description']}")
        lines.append("")

        design = story.get("design") or {}
        lines.append("## 3. 技術設計摘要")
        lines.append(design.get("summary") or "依循專案既有架構與共用工程標準規範。")
        lines.append("")

        if surface.get("has_ui") and design.get("ui_spec"):
            lines.append("### 3.1 畫面狀態與文案")
            lines.append(design["ui_spec"])
            lines.append("")

        if surface.get("has_api") and design.get("api_spec"):
            lines.append("### 3.2 跨邊界 API 與介面契約")
            lines.append(design["api_spec"])
            lines.append("")

        auto_decisions = story.get("auto_decisions") or []
        if auto_decisions:
            lines.append("## 4. AI 預設細節清單（抓大放小）")
            for item in auto_decisions:
                lines.append(f"- {item}")
            lines.append("")

        material_decisions = story.get("material_decisions") or []
        if material_decisions:
            lines.append("## 5. 關鍵人工決策記錄")
            for item in material_decisions:
                lines.append(f"- {item}")
            lines.append("")
    else:
        lines.append("## 1. Scope")
        lines.append(f"- **In-Scope:** {story.get('scope_in') or 'N/A'}")
        lines.append(f"- **Out-of-Scope:** {story.get('scope_out') or 'N/A'}")
        lines.append(
            f"- **Change Surface:** UI={surface.get('has_ui', False)}, "
            f"API={surface.get('has_api', False)}, Data={surface.get('has_data', False)} | "
            f"**Risk Level:** `{story.get('risk_level', 'medium')}`"
        )
        lines.append("")

        lines.append("## 2. Acceptance Criteria")
        for ac in story.get("acceptance_criteria") or []:
            lines.append(f"- **{ac['id']}**: {ac['description']}")
        lines.append("")

        design = story.get("design") or {}
        lines.append("## 3. Technical Design Summary")
        lines.append(design.get("summary") or "Follow repository architecture and shared engineering standards.")
        lines.append("")

        if surface.get("has_ui") and design.get("ui_spec"):
            lines.append("### 3.1 UI States, Wireframe & Copy")
            lines.append(design["ui_spec"])
            lines.append("")

        if surface.get("has_api") and design.get("api_spec"):
            lines.append("### 3.2 Cross-Boundary API / Contract")
            lines.append(design["api_spec"])
            lines.append("")

        auto_decisions = story.get("auto_decisions") or []
        if auto_decisions:
            lines.append("## 4. Auto-Decided Defaults")
            for item in auto_decisions:
                lines.append(f"- {item}")
            lines.append("")

        material_decisions = story.get("material_decisions") or []
        if material_decisions:
            lines.append("## 5. Confirmed Material Decisions")
            for item in material_decisions:
                lines.append(f"- {item}")
            lines.append("")

    spec_meta = story.get("spec") if isinstance(story.get("spec"), dict) else {}
    risks_text = _clean_optional_text(spec_meta.get("risks") if "risks" in spec_meta else story.get("risks"))
    raw_oqs = spec_meta.get("open_questions") if "open_questions" in spec_meta else story.get("open_questions")
    open_questions = [
        q for q in (raw_oqs or [])
        if isinstance(q, dict) and _clean_optional_text(q.get("question"))
    ]

    if risks_text or open_questions:
        lines.append("### 風險與待確認問題 (Risks & Open Questions)" if zh else "### Risks & Open Questions")
        if risks_text:
            risk_lines = [ln.strip() for ln in risks_text.splitlines() if ln.strip()]
            if len(risk_lines) == 1:
                label = "**風險與隱含需求：**" if zh else "**Risks & Implicit Requirements:**"
                lines.append(f"- {label} {risk_lines[0]}")
            elif risk_lines:
                lines.append("**風險與隱含需求：**" if zh else "**Risks & Implicit Requirements:**")
                for rl in risk_lines:
                    cleaned_rl = re.sub(r"^[-*]\s+", "", rl).strip()
                    if cleaned_rl:
                        lines.append(f"- {cleaned_rl}")
        for q in open_questions:
            q_id = _clean_optional_text(q.get("id")) or "Q-001"
            q_text = _clean_optional_text(q.get("question"))
            is_blocking = _parse_bool_flag(q.get("blocking"))
            if zh:
                b_tag = "[阻塞核准]" if is_blocking else "[非阻塞]"
                meta_parts = []
                if _clean_optional_text(q.get("owner")):
                    meta_parts.append(f"責任角色: {_clean_optional_text(q.get('owner'))}")
                if _clean_optional_text(q.get("proposed_default")):
                    meta_parts.append(f"建議預設: {_clean_optional_text(q.get('proposed_default'))}")
                acs_str = ", ".join(str(a).strip() for a in (q.get("impacted_acs") or []) if _clean_optional_text(a))
                if acs_str:
                    meta_parts.append(f"影響 AC: {acs_str}")
            else:
                b_tag = "[Blocking]" if is_blocking else "[Non-blocking]"
                meta_parts = []
                if _clean_optional_text(q.get("owner")):
                    meta_parts.append(f"Owner: {_clean_optional_text(q.get('owner'))}")
                if _clean_optional_text(q.get("proposed_default")):
                    meta_parts.append(f"Proposed default: {_clean_optional_text(q.get('proposed_default'))}")
                acs_str = ", ".join(str(a).strip() for a in (q.get("impacted_acs") or []) if _clean_optional_text(a))
                if acs_str:
                    meta_parts.append(f"Impacted ACs: {acs_str}")
            suffix = f" ({'; '.join(meta_parts)})" if meta_parts else ""
            lines.append(f"- **{q_id}** {b_tag}: {q_text}{suffix}")
        lines.append("")

    return "\n".join(lines).strip()


def cmd_sync_story(args) -> int:
    backfill_mode = bool(getattr(args, "backfill", False))
    preserve_owner = bool(getattr(args, "preserve_owner", False)) or backfill_mode
    if backfill_mode and args.status == "approved":
        print(
            "ERROR: --backfill cannot be combined with --status approved. "
            "Backfill mode only syncs specification documentation without approving the spec or triggering role handoff.",
            file=sys.stderr,
        )
        return 1
    if backfill_mode and getattr(args, "create", False):
        print(
            "ERROR: --backfill cannot be combined with --create. "
            "Backfill mode only updates existing Jira Stories.",
            file=sys.stderr,
        )
        return 1

    parsed_open_questions = None
    raw_oq_arg = getattr(args, "open_questions_json", None)
    if raw_oq_arg is not None:
        try:
            parsed_open_questions = _parse_and_normalize_open_questions(raw_oq_arg)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    spec_payload = None
    if args.spec_json:
        try:
            spec_payload = load_json_arg(args.spec_json)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            print(f"ERROR: Malformed --spec-json: {exc}", file=sys.stderr)
            return 1
        if isinstance(spec_payload, dict):
            nested_spec_status = (
                spec_payload["spec"].get("spec_status")
                if isinstance(spec_payload.get("spec"), dict)
                else None
            )
            if backfill_mode and (
                str(spec_payload.get("spec_status") or "").strip().lower() == "approved"
                or str(nested_spec_status or "").strip().lower() == "approved"
            ):
                print(
                    "ERROR: --backfill cannot approve a spec (spec_status='approved' in --spec-json is rejected).",
                    file=sys.stderr,
                )
                return 1
            try:
                if "open_questions" in spec_payload:
                    spec_payload["open_questions"] = _normalize_open_questions_list(
                        spec_payload["open_questions"], source_label="--spec-json.open_questions"
                    )
                if isinstance(spec_payload.get("spec"), dict) and "open_questions" in spec_payload["spec"]:
                    spec_payload["spec"]["open_questions"] = _normalize_open_questions_list(
                        spec_payload["spec"]["open_questions"], source_label="--spec-json.spec.open_questions"
                    )
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1

    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="pm")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1

    jira_key = validate_jira_key(args.key)
    active_path = get_active_dir(repo_root) / f"{jira_key}.json"
    had_prior_workflow_state = False
    if active_path.exists():
        had_prior_workflow_state = True
        story = json.loads(active_path.read_text(encoding="utf-8"))
        if not args.create and client.is_live:
            story, _ = reconcile_story_with_jira(client, config, repo_root, jira_key, story=story, save=False)
    else:
        hydrated = None
        if not args.create:
            hydrated = _hydrate_story_from_worktree(repo_root, jira_key)
            if hydrated is not None:
                had_prior_workflow_state = True
            elif client.is_live:
                remote_issue = None
                try:
                    remote_issue = client.get_issue(jira_key)
                except RuntimeError as exc:
                    if backfill_mode:
                        print(
                            f"ERROR: Jira Story {jira_key} could not be fetched for --backfill ({exc}).",
                            file=sys.stderr,
                        )
                        return 1
                except Exception:
                    remote_issue = None

                remote_snap = client.get_state_snapshot(jira_key)
                if isinstance(remote_snap, dict) and remote_snap.get("jira_key"):
                    had_prior_workflow_state = True

                hydrated = hydrate_story_from_remote(client, repo_root, jira_key, persist=False)
                if backfill_mode and hydrated is None:
                    print(
                        f"ERROR: Jira Story {jira_key} was not found in Jira; --backfill only updates existing Stories.",
                        file=sys.stderr,
                    )
                    return 1

                if (
                    isinstance(hydrated, dict)
                    and not had_prior_workflow_state
                    and not preserve_owner
                    and (args.title or args.spec_json)
                ):
                    minimal = {"jira_key": jira_key}
                    for ef in _EPIC_METADATA_FIELDS:
                        if ef in hydrated:
                            minimal[ef] = hydrated[ef]
                    if hydrated.get("tasks"):
                        minimal["tasks"] = hydrated["tasks"]
                    if hydrated.get("bugs"):
                        minimal["bugs"] = hydrated["bugs"]
                    if hydrated.get("role_owners"):
                        minimal["role_owners"] = hydrated["role_owners"]
                    hydrated = minimal

                if (
                    backfill_mode
                    and isinstance(hydrated, dict)
                    and not had_prior_workflow_state
                    and not hydrated.get("last_synced_description_hash")
                ):
                    remote_desc_text = _adf_to_plain_text(((remote_issue or {}).get("fields") or {}).get("description")).strip()
                    if remote_desc_text:
                        hydrated["remote_description_modified"] = True
                        hydrated["remote_description_text"] = remote_desc_text
        story = hydrated if isinstance(hydrated, dict) else {"jira_key": jira_key}

    if story.get("remote_description_modified"):
        if getattr(args, "accept_remote_description", False) or getattr(args, "force_overwrite", False):
            story["remote_description_modified"] = False
            story.pop("remote_description_text", None)
        else:
            print(
                f"ERROR: Jira Story {jira_key} description was modified directly in Jira Web UI since last sync. "
                f"Run 'jira_sync.py pull --key {jira_key}' to inspect imported changes, then pass --accept-remote-description "
                f"(or --force-overwrite) to sync-story.",
                file=sys.stderr,
            )
            return 1

    prior_status = story.get("spec_status")
    effective_prior_status = prior_status if had_prior_workflow_state else None
    preserved_snapshot = {}
    if preserve_owner:
        for pf in _PRESERVED_LIFECYCLE_FIELDS:
            if pf in story:
                preserved_snapshot[pf] = json.loads(json.dumps(story[pf]))

    hydrated_epic_meta = {
        ef: json.loads(json.dumps(story[ef]))
        for ef in _EPIC_METADATA_FIELDS
        if ef in story
    }

    prior_acs = [dict(ac) for ac in (story.get("acceptance_criteria") or []) if isinstance(ac, dict) and ac.get("id")]
    prior_material_decisions = list(story.get("material_decisions") or [])
    prior_auto_decisions = list(story.get("auto_decisions") or [])

    if isinstance(spec_payload, dict):
        sp_copy = json.loads(json.dumps(spec_payload))
        if preserve_owner:
            for protected_k in _PRESERVED_LIFECYCLE_FIELDS:
                sp_copy.pop(protected_k, None)
        sp_spec = sp_copy.pop("spec", None)
        story.update(sp_copy)
        if isinstance(sp_spec, dict):
            sp_spec_clean = dict(sp_spec)
            if preserve_owner:
                for protected_k in _PRESERVED_LIFECYCLE_FIELDS:
                    sp_spec_clean.pop(protected_k, None)
            story_spec = story.setdefault("spec", {}) if isinstance(story.get("spec"), dict) else {}
            if not isinstance(story.get("spec"), dict):
                story["spec"] = story_spec
            story_spec.update(sp_spec_clean)
        if preserve_owner:
            for pf in _PRESERVED_LIFECYCLE_FIELDS:
                if pf in preserved_snapshot:
                    story[pf] = json.loads(json.dumps(preserved_snapshot[pf]))
                else:
                    story.pop(pf, None)
        else:
            for ef, ev in hydrated_epic_meta.items():
                if ef not in sp_copy or sp_copy.get(ef) in (None, "", []):
                    story[ef] = json.loads(json.dumps(ev))

    if args.title:
        clean_title = _clean_optional_text(args.title)
        if clean_title:
            story["title"] = clean_title
    if args.scope_in is not None:
        story["scope_in"] = _clean_optional_text(args.scope_in)
    if args.scope_out is not None:
        story["scope_out"] = _clean_optional_text(args.scope_out)
    if args.risk_level:
        story["risk_level"] = args.risk_level

    surface = story.get("change_surface") or {"has_ui": False, "has_api": False, "has_data": False}
    if args.has_ui:
        surface["has_ui"] = True
    elif getattr(args, "no_ui", False):
        surface["has_ui"] = False
    if args.has_api:
        surface["has_api"] = True
    elif getattr(args, "no_api", False):
        surface["has_api"] = False
    if args.has_data:
        surface["has_data"] = True
    elif getattr(args, "no_data", False):
        surface["has_data"] = False
    story["change_surface"] = surface

    if args.ac:
        acs = []
        all_explicit_ids = True
        for idx, raw_ac in enumerate(args.ac, start=1):
            if ":" in raw_ac and re.match(r"^AC-\d+$", raw_ac.split(":", 1)[0].strip(), re.IGNORECASE):
                ac_id, desc = raw_ac.split(":", 1)
                ac_id = ac_id.strip().upper()
                desc = desc.strip()
            else:
                all_explicit_ids = False
                ac_id = f"AC-{idx:03d}"
                desc = raw_ac.strip()
            if not desc:
                print(f"ERROR: Acceptance criterion '{ac_id}' has an empty description.", file=sys.stderr)
                return 1
            acs.append({"id": ac_id, "description": desc})

        if (
            (effective_prior_status in ("draft", "approved") or backfill_mode)
            and prior_acs
            and all_explicit_ids
            and not getattr(args, "replace_acs", False)
        ):
            merged_acs = [dict(item) for item in prior_acs]
            idx_by_id = {item["id"].upper(): i for i, item in enumerate(merged_acs)}
            for item in acs:
                norm_id = item["id"].upper()
                if norm_id in idx_by_id:
                    merged_acs[idx_by_id[norm_id]]["description"] = item["description"]
                else:
                    idx_by_id[norm_id] = len(merged_acs)
                    merged_acs.append({"id": norm_id, "description": item["description"]})
            story["acceptance_criteria"] = merged_acs
        else:
            story["acceptance_criteria"] = acs

    design = story.get("design") or {}
    if args.design_summary is not None:
        design["summary"] = _clean_optional_text(args.design_summary)
    # Conditional sections: only store UI/API specs when surface applies (no empty placeholder bloat)
    if surface.get("has_ui") and args.ui_spec:
        design["ui_spec"] = args.ui_spec.strip()
    elif not surface.get("has_ui"):
        design.pop("ui_spec", None)

    if surface.get("has_api") and args.api_spec:
        design["api_spec"] = args.api_spec.strip()
    elif not surface.get("has_api"):
        design.pop("api_spec", None)
    story["design"] = design

    spec_meta = story.setdefault("spec", {}) if isinstance(story.get("spec"), dict) else {}
    if not isinstance(story.get("spec"), dict):
        story["spec"] = spec_meta
    if getattr(args, "risks", None) is not None:
        cleaned_risks = _clean_optional_text(args.risks)
        if cleaned_risks:
            spec_meta["risks"] = cleaned_risks
        else:
            spec_meta.pop("risks", None)
        story.pop("risks", None)
    elif "risks" in story:
        cleaned_top_risks = _clean_optional_text(story.pop("risks"))
        if cleaned_top_risks:
            spec_meta["risks"] = cleaned_top_risks
    elif "risks" in spec_meta:
        cleaned_spec_risks = _clean_optional_text(spec_meta.get("risks"))
        if cleaned_spec_risks:
            spec_meta["risks"] = cleaned_spec_risks
        else:
            spec_meta.pop("risks", None)

    if parsed_open_questions is not None:
        spec_meta["open_questions"] = parsed_open_questions
        story.pop("open_questions", None)
    elif "open_questions" in story:
        top_oqs = story.pop("open_questions")
        if isinstance(top_oqs, list):
            spec_meta["open_questions"] = _normalize_open_questions_list(
                top_oqs, source_label="open_questions"
            )
    elif isinstance(spec_meta.get("open_questions"), list):
        spec_meta["open_questions"] = _normalize_open_questions_list(
            spec_meta["open_questions"], source_label="spec.open_questions"
        )

    eff_open_questions = spec_meta.get("open_questions") if isinstance(spec_meta.get("open_questions"), list) else []
    open_blocking_questions = [
        q["id"] for q in eff_open_questions
        if isinstance(q, dict) and _parse_bool_flag(q.get("blocking")) and _clean_optional_text(q.get("id"))
    ]

    if backfill_mode:
        status = "approved" if effective_prior_status == "approved" else "draft"
    elif preserve_owner:
        if effective_prior_status == "approved":
            status = "approved"
        else:
            status = args.status or effective_prior_status or "draft"
    else:
        status = args.status or effective_prior_status or story.get("spec_status") or "draft"

    if args.auto_decision:
        new_auto = [d.strip() for d in args.auto_decision if d.strip()]
        if effective_prior_status in ("draft", "approved"):
            merged_auto = list(prior_auto_decisions)
            for item in new_auto:
                if item not in merged_auto:
                    merged_auto.append(item)
            story["auto_decisions"] = merged_auto
        else:
            story["auto_decisions"] = new_auto

    if args.material_decision:
        new_mat = [d.strip() for d in args.material_decision if d.strip()]
        # Single-pass direct approval on a fresh story enforces <= 3 material decisions ("抓大放小");
        # Iterative draft rounds or Spec Gap updates (prior_status in ('draft', 'approved')) allow up to 5 per round and accumulate across rounds.
        if status == "approved" and not effective_prior_status and len(new_mat) > 3:
            print(
                "ERROR: '抓大放小' rule violated — at most 3 material questions/decisions are allowed in single-pass approval (use --status draft rounds for complex features).",
                file=sys.stderr,
            )
            return 1
        if len(new_mat) > 5:
            print(
                "ERROR: At most 5 material questions/decisions are allowed per single draft round. Split remaining questions into the next draft round.",
                file=sys.stderr,
            )
            return 1
        if effective_prior_status in ("draft", "approved"):
            merged_mat = list(prior_material_decisions)
            for item in new_mat:
                if item not in merged_mat:
                    merged_mat.append(item)
            story["material_decisions"] = merged_mat
        else:
            story["material_decisions"] = new_mat
    elif status == "approved" and not effective_prior_status and len(story.get("material_decisions") or []) > 3:
        print(
            "ERROR: '抓大放小' rule violated — at most 3 material questions/decisions are allowed in single-pass approval (use --status draft rounds for complex features).",
            file=sys.stderr,
        )
        return 1

    if not backfill_mode and status == "approved" and not (story.get("acceptance_criteria") or []):
        print("ERROR: Cannot mark spec 'approved' without at least one testable AC (AC-001..N).", file=sys.stderr)
        return 1

    pm_person = resolve_acting_person(config, "pm", client=client, story=story)
    if not preserve_owner and _is_member_claimed("pm", pm_person):
        story.setdefault("role_owners", {})["pm"] = serialize_member_for_config(pm_person, "pm")

    story["spec_status"] = status
    if not backfill_mode and status == "approved" and effective_prior_status != "approved":
        story["approved_by"] = args.approved_by or story.get("approved_by") or pm_person["name"]
        story["approved_at"] = story.get("approved_at") or now_iso()

    desc_md = format_story_description(story, config)
    sync_res = client.update_or_create_story(
        jira_key,
        story.get("title") or jira_key,
        desc_md,
        create=args.create,
        allow_create=not backfill_mode,
    )
    if sync_res.get("jira_key"):
        jira_key = validate_jira_key(sync_res["jira_key"])
        story["jira_key"] = jira_key

    expected_desc_hash = _compute_description_hash(
        _adf_to_plain_text(client.text_to_adf(desc_md)).strip()
    )
    story["last_synced_description_hash"] = expected_desc_hash

    description_verified = True
    if backfill_mode and client.is_live:
        try:
            verify_issue = client.get_issue(jira_key)
            verify_plain = _adf_to_plain_text(((verify_issue or {}).get("fields") or {}).get("description")).strip()
            description_verified = bool(verify_plain and _compute_description_hash(verify_plain) == expected_desc_hash)
        except Exception:
            description_verified = False

    handoff_info = None
    if not preserve_owner:
        if status == "draft":
            story["current_owner_role"] = "pm"
            if pm_person.get("jira_account_id"):
                safe_assign_issue(client, jira_key, pm_person["jira_account_id"])

        if status == "approved" and (args.status == "approved" or effective_prior_status != "approved"):
            zh = is_zh_tw(config)
            ac_total = len(story.get("acceptance_criteria", []))
            s1_comment = (
                f"[站點 1：規格與設計核准] 規格與設計已確認核准（共 {ac_total} 項 AC），請進入站點 2 (`team-dev`) 進行任務拆解與實作。"
                if zh
                else f"[Station 1: Spec Approved] Specification and design approved ({ac_total} ACs). Ready for Station 2 (`team-dev`)."
            )
            handoff_info = execute_role_handoff(
                client=client,
                config=config,
                story=story,
                from_role="pm",
                to_role="developer",
                status_key="in_progress",
                comment_md=s1_comment,
                from_person_override=pm_person,
                to_assignee=getattr(args, "to_assignee", None),
            )
    else:
        skip_restore = {"spec_status"}
        if not backfill_mode and status == "approved":
            skip_restore.update({"approved_by", "approved_at"})
        for pf in _PRESERVED_LIFECYCLE_FIELDS:
            if pf in skip_restore:
                continue
            if pf in preserved_snapshot:
                story[pf] = json.loads(json.dumps(preserved_snapshot[pf]))
            else:
                story.pop(pf, None)
        story["spec_status"] = status

    saved_path = save_active_story(repo_root, jira_key, story, client=client, sync_remote=True)
    if active_path.exists() and active_path != saved_path:
        active_path.unlink()
    out = {
        "ok": True,
        "action": "sync-story",
        "jira_key": jira_key,
        "spec_status": status,
        "ac_count": len(story.get("acceptance_criteria") or []),
        "change_surface": surface,
        "active_file": str(saved_path.relative_to(repo_root)),
        "jira_sync": sync_res,
        "handoff": handoff_info,
    }
    if handoff_info is not None:
        out.update(_handoff_governance_fields(handoff_info))
    if preserve_owner:
        if not backfill_mode and status == "approved":
            backfill_stage = "spec_approved"
        elif open_blocking_questions:
            backfill_stage = "spec_pending_confirmation"
        else:
            backfill_stage = "material_backfilled"
        out.update({
            "backfill_mode": backfill_mode,
            "owner_preserved": True,
            "status_preserved": True,
            "open_blocking_questions": open_blocking_questions,
            "backfill_stage": backfill_stage,
            "description_verified": description_verified,
            "can_auto_chain_next_station": False,
            "requires_user_confirmation_for_next_station": True,
        })
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 4: create-subtasks (Station 2: team-dev plan mode)
# ---------------------------------------------------------------------------

def has_dependency_cycle(tasks: list) -> bool:
    graph = {t["id"]: list(t.get("depends_on") or []) for t in tasks}
    visited = {}

    def dfs(node: str) -> bool:
        state = visited.get(node, 0)
        if state == 1:
            return True
        if state == 2:
            return False
        visited[node] = 1
        for dep in graph.get(node, []):
            if dep in graph and dfs(dep):
                return True
        visited[node] = 2
        return False

    return any(dfs(n) for n in graph)


_SPECIALTY_KEYWORDS = {
    "backend": (
        "backend", "server", "api", "db", "database", "sql", "sqlite", "postgres",
        "mysql", "redis", "schema", "migration", "jwt", "auth", "endpoint", "後端", "資料庫",
    ),
    "frontend": (
        "frontend", "client", "react", "vue", "svelte", "tsx", "jsx", "css", "html",
        "ui", "ux", "modal", "board", "component", "page", "view", "前端", "畫面", "看板", "彈窗", "元件",
    ),
    "mobile": ("mobile", "ios", "android", "swift", "kotlin", "flutter", "react-native", "行動端"),
    "devops": ("devops", "infra", "docker", "k8s", "kubernetes", "terraform", "ci", "cd", "pipeline", "部署"),
}


def _resolve_task_developer(
    config: dict,
    story: dict,
    t_id: str,
    title: str,
    files: list,
    verif: str,
    selector: str = None,
    prev_assignee: dict = None,
) -> dict:
    if selector:
        found = find_member_in_role(config, "developer", selector)
        if found:
            return found
    if isinstance(prev_assignee, dict) and _is_member_claimed("developer", prev_assignee):
        refreshed = find_member_in_role(
            config,
            "developer",
            prev_assignee.get("jira_account_id") or prev_assignee.get("email") or prev_assignee.get("name") or "",
        )
        return refreshed or _normalize_member_dict("developer", prev_assignee)

    claimed_devs = [m for m in resolve_role_members(config, "developer") if _is_member_claimed("developer", m)]
    if len(claimed_devs) <= 1:
        return resolve_story_role_person(config, story, "developer")

    haystack = f"{title} {' '.join(str(f) for f in (files or []))} {verif}".lower()
    best_member = None
    best_score = 0
    for m in claimed_devs:
        spec = (m.get("specialty") or "").strip().lower()
        if not spec:
            continue
        keywords = _SPECIALTY_KEYWORDS.get(spec, (spec,))
        score = sum(1 for kw in keywords if kw in haystack)
        if score > best_score:
            best_score = score
            best_member = m
    if best_member is not None:
        return dict(best_member)
    return resolve_story_role_person(config, story, "developer")


def _normalize_implementation_steps(raw_steps) -> list:
    """Normalize optional implementation_steps (list of strings or multi-line string) into clean list."""
    if not raw_steps:
        return []
    if isinstance(raw_steps, str):
        raw_items = raw_steps.splitlines()
    elif isinstance(raw_steps, list):
        raw_items = raw_steps
    else:
        raw_items = [str(raw_steps)]
    cleaned = []
    for item in raw_items:
        if item is None:
            continue
        s = re.sub(r"^\s*[-*](?:\s+|$)", "", str(item)).strip()
        if s:
            cleaned.append(s)
    return cleaned


def _normalize_string_list(raw_val) -> list:
    """Normalize a list of strings or comma-separated string into a clean list of non-empty strings."""
    if not raw_val:
        return []
    if isinstance(raw_val, str):
        return [x.strip() for x in raw_val.split(",") if x.strip()]
    if isinstance(raw_val, list):
        return [str(x).strip() for x in raw_val if x is not None and str(x).strip()]
    return [str(raw_val).strip()] if raw_val is not None and str(raw_val).strip() else []


def format_subtask_description(
    t_id: str,
    task_dev: dict,
    ac_cov: list,
    deps: list,
    verif: str,
    zh: bool = True,
    description: str = "",
    implementation_steps: list = None,
    files: list = None,
) -> str:
    """Render rich Markdown description for a Jira Sub-task (converted to ADF via text_to_adf)."""
    desc_text = str(description or "").strip()
    steps = _normalize_implementation_steps(implementation_steps)
    file_list = _normalize_string_list(files)
    verif_str = str(verif or "").strip()
    verif_fmt = f"`{verif_str}`" if (verif_str and "`" not in verif_str) else verif_str

    if zh:
        lines = [
            f"任務編號：{t_id}",
            f"負責人：{format_person_badge('developer', task_dev)}",
            f"涵蓋驗收條件：{', '.join(ac_cov) if ac_cov else '前置基礎建設 (Enabling work)'}",
            f"前置依賴任務：{', '.join(deps) if deps else '無'}",
        ]
        if desc_text or steps:
            lines.append("")
            lines.append("### 實施說明與步驟 (Implementation Steps)")
            if desc_text:
                lines.append(desc_text)
            for step in steps:
                lines.append(f"- {step}")
        if file_list:
            lines.append("")
            lines.append("### 目標檔案 (Target Files)")
            for fpath in file_list:
                lines.append(f"- `{fpath}`" if "`" not in fpath else f"- {fpath}")
        if desc_text or steps or file_list:
            lines.append("")
        lines.append(f"驗證方式：{verif_fmt}")
    else:
        lines = [
            f"Task ID: {t_id}",
            f"Owner: {format_person_badge('developer', task_dev)}",
            f"Covers AC: {', '.join(ac_cov) if ac_cov else 'Enabling work'}",
            f"Depends on: {', '.join(deps) if deps else 'None'}",
        ]
        if desc_text or steps:
            lines.append("")
            lines.append("### Implementation Details & Steps")
            if desc_text:
                lines.append(desc_text)
            for step in steps:
                lines.append(f"- {step}")
        if file_list:
            lines.append("")
            lines.append("### Target Files")
            for fpath in file_list:
                lines.append(f"- `{fpath}`" if "`" not in fpath else f"- {fpath}")
        if desc_text or steps or file_list:
            lines.append("")
        lines.append(f"Verification: {verif_fmt}")
    return "\n".join(lines)


def cmd_create_subtasks(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="developer")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key, client=client)

    if story.get("spec_status") != "approved" and not args.proceed_at_risk:
        print(f"ERROR: Story {jira_key} spec_status is '{story.get('spec_status')}'. Approve spec first or pass --proceed-at-risk.", file=sys.stderr)
        return 1

    task_assignee_map = {}
    for raw_ta in getattr(args, "task_assignee", None) or []:
        if "=" not in raw_ta:
            print(f"ERROR: Invalid --task-assignee '{raw_ta}'. Expected 'TASK-001=<email|name|accountId|specialty>'.", file=sys.stderr)
            return 1
        k_tid, v_sel = raw_ta.split("=", 1)
        if k_tid.strip() and v_sel.strip():
            task_assignee_map[k_tid.strip().upper()] = v_sel.strip()

    raw_tasks = []
    if args.tasks_json:
        loaded = load_json_arg(args.tasks_json)
        if isinstance(loaded, dict) and "tasks" in loaded:
            raw_tasks = loaded["tasks"]
        elif isinstance(loaded, list):
            raw_tasks = loaded
    if args.task:
        ac_split_re = re.compile(
            r"^(.*?):\s*((?:AC-\d+(?:\s*,\s*AC-\d+)*)|Enabling work)\s*:(.*)$",
            re.IGNORECASE,
        )
        for idx, t_str in enumerate(args.task, start=len(raw_tasks) + 1):
            first_tok = t_str.split(":", 1)[0].strip()
            if re.match(r"^TASK-\d+$", first_tok, re.IGNORECASE):
                t_id = first_tok.upper()
                rest = t_str.split(":", 1)[1].strip() if ":" in t_str else ""
            else:
                t_id = f"TASK-{idx:03d}"
                rest = t_str.strip()

            m_ac = ac_split_re.match(rest)
            if m_ac:
                title = m_ac.group(1).strip() or t_id
                ac_cov = [x.strip().upper() if re.match(r"^ac-\d+$", x.strip(), re.I) else x.strip() for x in m_ac.group(2).split(",") if x.strip()]
                verif = m_ac.group(3).strip() or "Run unit/integration tests"
            else:
                parts = [p.strip() for p in rest.split(":", 2)]
                title = parts[0] if parts and parts[0] else t_id
                ac_cov = [x.strip() for x in parts[1].split(",") if x.strip()] if len(parts) > 1 else []
                verif = parts[2] if len(parts) > 2 and parts[2] else "Run unit/integration tests"

            raw_tasks.append({
                "id": t_id,
                "title": title,
                "ac_coverage": ac_cov,
                "depends_on": [],
                "verification": verif,
            })

    if not raw_tasks:
        print("ERROR: No tasks supplied to create-subtasks.", file=sys.stderr)
        return 1

    # Pre-validate task IDs and dependency references before creating remote subtasks
    planned_ids = set()
    for idx, item in enumerate(raw_tasks, start=1):
        t_id = (item.get("id") or f"TASK-{idx:03d}").strip().upper()
        if t_id in planned_ids:
            print(f"ERROR: Duplicate task ID '{t_id}' in create-subtasks.", file=sys.stderr)
            return 1
        planned_ids.add(t_id)

    for idx, item in enumerate(raw_tasks, start=1):
        t_id = (item.get("id") or f"TASK-{idx:03d}").strip().upper()
        deps = [str(d).strip().upper() for d in (item.get("depends_on") or []) if str(d).strip()]
        unknown_deps = [d for d in deps if d not in planned_ids]
        if unknown_deps:
            print(f"ERROR: Task '{t_id}' depends_on unknown task ID(s): {', '.join(unknown_deps)}.", file=sys.stderr)
            return 1

    zh = is_zh_tw(config)
    todo_status = config.get("status_mapping", {}).get("todo", "To Do")
    normalized_tasks = []
    existing_by_id = {str(t["id"]).upper(): t for t in (story.get("tasks") or []) if isinstance(t, dict) and t.get("id")}
    for idx, item in enumerate(raw_tasks, start=1):
        t_id = (item.get("id") or f"TASK-{idx:03d}").strip().upper()
        prev = existing_by_id.get(t_id, {})
        title = item.get("title") or prev.get("title") or t_id
        ac_cov = item.get("ac_coverage") or prev.get("ac_coverage") or []
        deps = [str(d).strip().upper() for d in (item.get("depends_on") or prev.get("depends_on") or []) if str(d).strip()]
        verif = item.get("verification") or prev.get("verification") or "Run automated tests"
        files = _normalize_string_list(item.get("files") if "files" in item else prev.get("files"))
        raw_desc = item.get("description") if "description" in item else prev.get("description")
        desc_text = str(raw_desc).strip() if raw_desc is not None else ""
        impl_steps = _normalize_implementation_steps(
            item.get("implementation_steps") if "implementation_steps" in item else prev.get("implementation_steps")
        )

        raw_item_ass = item.get("assignee")
        item_sel = (
            task_assignee_map.get(t_id)
            or (
                (raw_item_ass.get("email") or raw_item_ass.get("jira_account_id") or raw_item_ass.get("name"))
                if isinstance(raw_item_ass, dict)
                else (str(raw_item_ass).strip() if raw_item_ass else None)
            )
            or getattr(args, "assignee", None)
        )
        task_dev = _resolve_task_developer(
            config=config,
            story=story,
            t_id=t_id,
            title=title,
            files=files,
            verif=verif,
            selector=item_sel,
            prev_assignee=prev.get("assignee"),
        )

        desc_md = format_subtask_description(
            t_id=t_id,
            task_dev=task_dev,
            ac_cov=ac_cov,
            deps=deps,
            verif=verif,
            zh=zh,
            description=desc_text,
            implementation_steps=impl_steps,
            files=files,
        )

        sub_key = prev.get("jira_subtask_key") or client.create_subtask(jira_key, f"[{t_id}] {title}", desc_md, idx)
        if task_dev.get("jira_account_id"):
            safe_assign_issue(client, sub_key, task_dev["jira_account_id"])

        task_record = {
            "id": t_id,
            "jira_subtask_key": sub_key,
            "title": title,
            "description": desc_text,
            "implementation_steps": impl_steps,
            "ac_coverage": ac_cov,
            "depends_on": deps,
            "files": files,
            "verification": verif,
            "status": prev.get("status", "pending"),
            "last_synced_jira_status": prev.get("last_synced_jira_status") or todo_status,
            "commit": prev.get("commit"),
            "changed_files": prev.get("changed_files", []),
            "test_result": prev.get("test_result"),
            "completed_at": prev.get("completed_at"),
        }
        if _is_member_claimed("developer", task_dev):
            task_record["assignee"] = serialize_member_for_config(task_dev, "developer")
        normalized_tasks.append(task_record)

    if has_dependency_cycle(normalized_tasks):
        print("ERROR: Task dependency cycle detected in create-subtasks.", file=sys.stderr)
        return 1

    story["tasks"] = normalized_tasks
    story["station"] = "dev"
    saved_path = save_active_story(repo_root, jira_key, story, client=client)
    eff_commit_policy = get_effective_commit_policy(config)
    out = {
        "ok": True,
        "action": "create-subtasks",
        "jira_key": jira_key,
        "task_count": len(normalized_tasks),
        "commit_policy": eff_commit_policy,
        "requires_commit_confirmation": eff_commit_policy == "confirm_before_commit",
        "subtasks": [
            {
                "id": t["id"],
                "jira_subtask_key": t["jira_subtask_key"],
                "title": t["title"],
                "assignee": (t.get("assignee") or {}).get("name", ""),
            }
            for t in normalized_tasks
        ],
        "active_file": str(saved_path.relative_to(repo_root)),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 5: complete-task (Station 2: team-dev implement mode)
# ---------------------------------------------------------------------------

def cmd_complete_task(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="developer")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key, client=client)
    zh = is_zh_tw(config)
    acting_dev = resolve_acting_person(config, "developer", client=client, story=story)
    todo_status = config.get("status_mapping", {}).get("todo", "To Do")

    raw_task_id = (args.task_id or "TASK-001").strip()
    target_ids = [tid.strip() for tid in raw_task_id.split(",") if tid.strip()]
    if not target_ids:
        target_ids = ["TASK-001"]

    commit_sha = (args.commit or "").strip() or _detect_git_head_commit(repo_root) or story.get("candidate_commit") or ""
    changed_files = [f.strip() for f in (args.files or "").split(",") if f.strip()]
    if not changed_files:
        changed_files = _detect_git_changed_files(repo_root, commit_sha, config=config)
    test_result = (args.test_result or "").strip()
    root_cause = _clean_optional_text(getattr(args, "root_cause", None))
    solution = _clean_optional_text(getattr(args, "solution", None))

    if not commit_sha or not test_result:
        print(
            "ERROR: --test-result and a valid --commit (or Git HEAD commit in repository) are required to complete a task.",
            file=sys.stderr,
        )
        return 1

    # Dev Fast-Track: if no tasks are planned yet and spec is approved, auto-create TASK-001 covering all Story ACs
    # Complexity Guard: only allowed for small stories (<= 3 ACs and low/medium risk)
    fast_tracked = False
    if not (story.get("tasks") or []) and story.get("spec_status") == "approved":
        task_like_ids = [tid for tid in target_ids if re.match(r"^TASK-\d+$", tid, re.IGNORECASE)]
        if task_like_ids:
            ac_list = story.get("acceptance_criteria") or []
            risk_level = (story.get("risk_level") or "medium").lower()
            if len(ac_list) > 3 or risk_level in ("high", "critical"):
                print(
                    f"ERROR: Dev Fast-Track is restricted to small stories (<= 3 ACs and low/medium risk; got {len(ac_list)} ACs, risk={risk_level}). Run create-subtasks first to decompose this story.",
                    file=sys.stderr,
                )
                return 1
            all_ac_ids = [ac.get("id") for ac in ac_list if ac.get("id")] or ["AC-001"]
            auto_tasks = []
            for idx, tid in enumerate(task_like_ids, start=1):
                norm_tid = tid.upper()
                title = story.get("title") or "Implement Story"
                desc_md = format_subtask_description(
                    t_id=norm_tid,
                    task_dev=acting_dev,
                    ac_cov=all_ac_ids,
                    deps=[],
                    verif=test_result,
                    zh=zh,
                    files=changed_files,
                )
                sub_key = client.create_subtask(jira_key, f"[{norm_tid}] {title}", desc_md, idx)
                if acting_dev.get("jira_account_id"):
                    safe_assign_issue(client, sub_key, acting_dev["jira_account_id"])
                ft_record = {
                    "id": norm_tid,
                    "jira_subtask_key": sub_key,
                    "title": title,
                    "ac_coverage": all_ac_ids,
                    "depends_on": [],
                    "files": changed_files,
                    "verification": test_result,
                    "status": "pending",
                    "last_synced_jira_status": todo_status,
                    "commit": None,
                    "changed_files": [],
                    "test_result": None,
                    "completed_at": None,
                }
                if _is_member_claimed("developer", acting_dev):
                    ft_record["assignee"] = serialize_member_for_config(acting_dev, "developer")
                auto_tasks.append(ft_record)
            story["tasks"] = auto_tasks
            story["station"] = "dev"
            fast_tracked = True

    resolved_targets = []
    for target_id in target_ids:
        matched_item = None
        item_kind = "task"
        for t in story.get("tasks") or []:
            if t["id"] == target_id or t.get("jira_subtask_key") == target_id:
                matched_item = t
                break

        if (
            matched_item is None
            and story.get("issue_kind") == "standalone_bug"
            and target_id.strip().upper() == jira_key.upper()
            and (story.get("tasks") or [])
        ):
            matched_item = story["tasks"][0]
            item_kind = "task"

        if matched_item is None:
            for b in story.get("bugs") or []:
                if b.get("bug_key") == target_id or b.get("id") == target_id:
                    matched_item = b
                    item_kind = "bug"
                    break

        if matched_item is None:
            for f in (story.get("review") or {}).get("findings") or []:
                if isinstance(f, dict) and f.get("id") == target_id:
                    matched_item = f
                    item_kind = "review-finding"
                    break

        if matched_item is None:
            print(f"ERROR: Task, Bug, or Review Finding '{target_id}' not found in {jira_key}.", file=sys.stderr)
            return 1
        resolved_targets.append((target_id, matched_item, item_kind))

    done_status = config.get("status_mapping", {}).get("done", "Done")
    completed_ids = []
    primary_kind = resolved_targets[0][2]

    for target_id, matched_item, item_kind in resolved_targets:
        if item_kind == "task":
            done_task_ids = {t["id"] for t in (story.get("tasks") or []) if t.get("status") == "completed"}
            unmet_deps = [d for d in (matched_item.get("depends_on") or []) if d not in done_task_ids]
            if unmet_deps:
                print(
                    f"ERROR: Cannot complete task '{matched_item['id']}' before its dependencies are completed: {', '.join(unmet_deps)}.",
                    file=sys.stderr,
                )
                return 1

        matched_item["status"] = "completed"
        matched_item["commit"] = commit_sha
        matched_item["changed_files"] = changed_files
        matched_item["test_result"] = test_result
        matched_item["completed_at"] = now_iso()
        if root_cause:
            matched_item["root_cause"] = root_cause
        elif "root_cause" in matched_item and matched_item["root_cause"] is None:
            matched_item["root_cause"] = ""
        if solution:
            matched_item["solution"] = solution
        elif "solution" in matched_item and matched_item["solution"] is None:
            matched_item["solution"] = ""
        completed_ids.append(target_id)

        jira_issue_key = (
            matched_item.get("jira_subtask_key")
            if item_kind == "task"
            else (matched_item.get("bug_key") if item_kind == "bug" else None)
        )
        if zh:
            comment_lines = [
                f"[任務完成：{target_id}]",
                f"- **完成人員**：{format_person_badge('developer', acting_dev)}",
                f"- **提交版本 (Commit)**：`{commit_sha}`",
                f"- **變更檔案**：{', '.join(changed_files) if changed_files else '無'}",
                f"- **測試驗證結果**：{test_result}",
            ]
            if root_cause:
                comment_lines.append(f"- **問題原因 (Root Cause)**：{root_cause}")
            if solution:
                comment_lines.append(f"- **解決辦法 (Solution)**：{solution}")
            comment_md = "\n".join(comment_lines)
        else:
            comment_lines = [
                f"[Task Completed: {target_id}]",
                f"- **Completed By**: {format_person_badge('developer', acting_dev)}",
                f"- Commit: `{commit_sha}`",
                f"- Changed Files: {', '.join(changed_files) if changed_files else 'N/A'}",
                f"- Verification Evidence: {test_result}",
            ]
            if root_cause:
                comment_lines.append(f"- **Root Cause**: {root_cause}")
            if solution:
                comment_lines.append(f"- **Solution**: {solution}")
            comment_md = "\n".join(comment_lines)
        if jira_issue_key:
            client.add_comment(jira_issue_key, comment_md)
            client.transition_issue(jira_issue_key, done_status)
            matched_item["last_synced_jira_status"] = done_status
        elif story.get("issue_kind") == "standalone_bug" and item_kind == "task":
            # Standalone Bug has no Jira Sub-task: post fix comment directly on the Bug WITHOUT prematurely marking Done
            client.add_comment(jira_key, comment_md)

        if item_kind == "bug" and matched_item.get("qase_defect_id"):
            eff_qa_backend = (
                ((story.get("qa") or {}).get("qa_test_backend"))
                or config.get("qa_test_backend")
                or "jira-comment"
            ).strip().lower()
            if eff_qa_backend == "qase":
                try:
                    qase_client = QaseClient(config, offline=args.offline, repo_root=repo_root)
                    qase_res = qase_client.resolve_defect(int(matched_item["qase_defect_id"]))
                    if qase_res.get("resolved"):
                        matched_item["qase_defect_resolved"] = True
                except Exception:
                    pass

    story["candidate_commit"] = commit_sha

    if story.get("issue_kind") == "standalone_bug":
        bug_rep = story.setdefault("bug_report", {})
        if root_cause:
            bug_rep["root_cause"] = root_cause
        elif "root_cause" not in bug_rep or bug_rep["root_cause"] is None:
            bug_rep["root_cause"] = ""
        if solution:
            bug_rep["solution"] = solution
        elif "solution" not in bug_rep or bug_rep["solution"] is None:
            bug_rep["solution"] = ""
        if root_cause or solution:
            story["bug_fix_summary"] = {
                "commit": commit_sha,
                "root_cause": bug_rep.get("root_cause", ""),
                "solution": bug_rep.get("solution", ""),
                "updated_at": now_iso(),
            }

    is_bug_fix = (story.get("issue_kind") == "standalone_bug") or any(
        ik == "bug"
        or str(tid).strip().upper().startswith("BUG-")
        or "-BUG-" in str(tid).strip().upper()
        for tid, _, ik in resolved_targets
    )
    bug_fix_warnings = []
    if is_bug_fix:
        if story.get("issue_kind") == "standalone_bug":
            eff_rc = root_cause or _clean_optional_text((story.get("bug_report") or {}).get("root_cause"))
            eff_sol = solution or _clean_optional_text((story.get("bug_report") or {}).get("solution"))
        else:
            eff_rc = root_cause
            eff_sol = solution
        if not eff_rc:
            bug_fix_warnings.append(
                "Completing a Bug fix without --root-cause. Pass --root-cause '<analysis>' so Review, QA, and Release can trace why the defect occurred."
            )
        if not eff_sol:
            bug_fix_warnings.append(
                "Completing a Bug fix without --solution. Pass --solution '<fix strategy>' so Review, QA, and Release can trace how the defect was fixed."
            )

    review = story.get("review")
    if isinstance(review, dict) and review.get("status") == "changes-requested":
        open_blocking = [
            f for f in (review.get("findings") or [])
            if isinstance(f, dict) and f.get("severity") in ("blocker", "major") and f.get("status", "open") == "open"
        ]
        if not open_blocking:
            review["status"] = "remediated"

    remaining_tasks = [t["id"] for t in (story.get("tasks") or []) if t.get("status") != "completed"]
    remaining_bugs = [b.get("bug_key") for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]
    remaining_review_findings = [
        f.get("id") for f in ((review or {}).get("findings") or [])
        if isinstance(review, dict)
        and review.get("status") == "changes-requested"
        and isinstance(f, dict)
        and f.get("severity") in ("blocker", "major")
        and f.get("status", "open") == "open"
    ]

    completed_subtask_keys = []
    if not fast_tracked:
        for _, matched_item, item_kind in resolved_targets:
            if item_kind == "task":
                sk = str(matched_item.get("jira_subtask_key") or "").strip()
                if sk and sk not in completed_subtask_keys:
                    completed_subtask_keys.append(sk)

    commit_msg = _read_git_commit_message(repo_root, commit_sha)
    commit_message_warnings = _validate_commit_message(
        commit_msg=commit_msg,
        story_key=jira_key,
        subtask_keys=completed_subtask_keys,
        config=config,
    )
    eff_commit_policy = get_effective_commit_policy(config)

    ready_for_review = (
        len(remaining_tasks) == 0
        and len(remaining_bugs) == 0
        and len(remaining_review_findings) == 0
    )
    saved_path = save_active_story(repo_root, jira_key, story, client=client)
    out = {
        "ok": True,
        "action": "complete-task",
        "jira_key": jira_key,
        "completed_id": completed_ids[0] if len(completed_ids) == 1 else ",".join(completed_ids),
        "completed_ids": completed_ids,
        "kind": primary_kind,
        "fast_track": fast_tracked,
        "commit": commit_sha,
        "commit_policy": eff_commit_policy,
        "commit_message_warnings": commit_message_warnings,
        "bug_fix_warnings": bug_fix_warnings,
        "root_cause": root_cause,
        "solution": solution,
        "changed_files": changed_files,
        "remaining_tasks": remaining_tasks,
        "remaining_bugs": remaining_bugs,
        "remaining_review_findings": remaining_review_findings,
        "ready_for_review": ready_for_review,
        "active_file": str(saved_path.relative_to(repo_root)),
    }
    if ready_for_review:
        _, next_rev_person, _ = resolve_effective_reviewer(config, story)
        dev_actor = resolve_acting_person(config, "developer", client=client, story=story)
        same_dev_rev = is_same_person(dev_actor, next_rev_person)
        out.update({
            "same_person": same_dev_rev,
            "can_auto_chain_next_station": False,
            "requires_user_confirmation_for_next_station": True,
            "cross_person_clean_stop": not same_dev_rev,
            "next_station_prompt_allowed": same_dev_rev,
            "next_station_owner": next_rev_person["name"],
        })
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 6: handoff (Station 3a: team-review & cross-station handoff)
# ---------------------------------------------------------------------------

def cmd_handoff(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    init_role = args.from_role.strip() if args.from_role else "reviewer"
    init_client = JiraClient(config, offline=args.offline, repo_root=repo_root, role=init_role)
    if not ensure_live_or_explicit_offline(init_client, config, args.offline):
        return 1
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key, client=init_client)

    effective_rev_role, rev_person, self_reviewed = resolve_effective_reviewer(config, story)
    from_role = args.from_role.strip() if args.from_role else effective_rev_role
    if from_role == "reviewer" and effective_rev_role == "independent_reviewer":
        from_role = "independent_reviewer"

    client = JiraClient(
        config,
        offline=args.offline,
        repo_root=repo_root,
        role=from_role,
        member_email=rev_person.get("email") or None,
    )
    if from_role not in ("reviewer", "independent_reviewer"):
        rev_person = resolve_acting_person(config, from_role, client=client, story=story)
        dev_person = resolve_story_role_person(config, story, "developer")
        self_reviewed = is_same_person(dev_person, rev_person)
    elif self_reviewed and getattr(client, "email", ""):
        local_rev = find_member_in_role(config, from_role, client.email)
        if local_rev:
            rev_person = local_rev

    to_role = args.to_role.strip()
    review_status = args.review_status or "approved"
    commit_sha = (args.commit or story.get("candidate_commit") or _detect_git_head_commit(repo_root) or "unknown").strip()
    summary = (args.summary or "").strip()
    raw_findings = load_json_arg(args.findings_json) if args.findings_json else []
    findings = raw_findings if isinstance(raw_findings, list) else []

    if review_status == "approved":
        if story.get("spec_status") != "approved":
            print(f"ERROR: Cannot approve review while spec_status is '{story.get('spec_status')}'.", file=sys.stderr)
            return 1
        tasks = story.get("tasks") or []
        incomplete_tasks = [t["id"] for t in tasks if t.get("status") != "completed"]
        if not tasks or incomplete_tasks:
            print(
                f"ERROR: Cannot approve review while development tasks are incomplete ({', '.join(incomplete_tasks) or 'no tasks planned'}).",
                file=sys.stderr,
            )
            return 1
        open_bugs = [b.get("bug_key") for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]
        if open_bugs:
            print(f"ERROR: Cannot approve review while open Bugs exist: {', '.join(open_bugs)}.", file=sys.stderr)
            return 1

    # Check if any blocker/major findings exist when claiming approved
    blocking = [
        f for f in findings
        if isinstance(f, dict) and f.get("severity") in ("blocker", "major") and f.get("status", "open") == "open"
    ]
    if blocking and review_status == "approved":
        print("ERROR: Cannot set --review-status approved while open blocker/major findings exist.", file=sys.stderr)
        return 1

    # Auto-create default REV-001 finding when changes-requested is passed without an open blocking finding
    if review_status == "changes-requested" and not blocking:
        findings.append({
            "id": "REV-001",
            "severity": "major",
            "axis": "review",
            "location": "repo",
            "ac_id": "",
            "observation": summary or "程式碼審查要求修正",
            "status": "open",
        })

    story["review"] = {
        "status": review_status,
        "reviewed_commit": commit_sha,
        "summary": summary,
        "findings": findings,
        "self_reviewed_with_ai": self_reviewed,
        "reviewer_role": from_role,
        "reviewer_person": serialize_member_for_config(rev_person, from_role),
        "reviewed_at": now_iso(),
    }

    if review_status == "changes-requested":
        effective_to_role = "developer"
        status_key = "in_progress"
    else:
        effective_to_role = to_role
        status_key = args.target_status or ("in_qa" if effective_to_role == "qa" else "ready_for_release")

    if commit_sha and commit_sha != "unknown":
        story["candidate_commit"] = commit_sha

    zh = is_zh_tw(config)
    qa_handoff_mode = get_effective_qa_handoff_mode(config)
    use_deploy_gate = (
        review_status == "approved"
        and effective_to_role == "qa"
        and qa_handoff_mode == "gated_by_deploy"
        and not args.target_status
    )

    if use_deploy_gate:
        dev_done_status = str((config.get("status_mapping") or {}).get("dev_done") or "").strip()
        in_qa_status = str((config.get("status_mapping") or {}).get("in_qa") or "In QA").strip()
        qa_person = resolve_story_role_person(config, story, "qa", selector=getattr(args, "to_assignee", None))
        if getattr(args, "to_assignee", None) and _is_member_claimed("qa", qa_person):
            story.setdefault("role_owners", {})["qa"] = serialize_member_for_config(qa_person, "qa")

        deploy_gate_warning = ""
        if dev_done_status:
            trans_res = client.transition_issue(jira_key, dev_done_status)
            if trans_res.get("transitioned_to"):
                story["jira_status"] = dev_done_status
                effective_status_label = dev_done_status
            else:
                effective_status_label = story.get("jira_status") or ""
                deploy_gate_warning = (
                    f"Failed to transition Jira Story {jira_key} to status_mapping.dev_done ('{dev_done_status}'): "
                    f"{trans_res.get('warning') or 'transition not available'}; preserving actual Jira status "
                    f"('{effective_status_label or 'unchanged'}') and keeping QA deploy gate active."
                )
        else:
            effective_status_label = story.get("jira_status") or ""
            deploy_gate_warning = (
                "status_mapping.dev_done is blank while qa_handoff_mode='gated_by_deploy'; "
                f"keeping current Jira status ('{effective_status_label or 'unchanged'}') and NOT transitioning to '{in_qa_status}' prematurely."
            )

        story["qa_deploy_gate"] = {
            "status": "awaiting_qa_deploy",
            "commit": commit_sha,
            "awaiting_in_qa_cycle": bool(_is_jira_status_in_qa(effective_status_label, config)),
            "tag": "",
            "deploy_ref": "",
            "environment": "",
            "promoted_at": "",
            "source": "",
        }

        if zh:
            comment_lines = [
                f"[站點 3a：開發完成 / 等待 QA 環境部署 — {review_status.upper()}]",
                f"- **審查人員**：{format_person_badge(from_role, rev_person)}",
                f"- **審查 Commit**：`{commit_sha}`",
                f"- **審查摘要**：{summary or '已完成所有開發子任務、驗收條件與共用工程標準核對。'}",
                f"- **QA 交接門禁 (`gated_by_deploy`)**：程式碼審查已通過並標記開發完成；目前等待部署至 QA 環境（CI/CD 推進至 `{in_qa_status}` 或執行 `promote-qa`），尚未指派或 `@mention` QA。",
            ]
            if self_reviewed:
                comment_lines.append("- **治理註記**：開發與審查由同一人兼任，已啟用 AI 獨立審查模式（`self_reviewed_with_ai: true`）。")
            if findings:
                comment_lines.append(f"- **審查發現數量**：{len(findings)}")
        else:
            comment_lines = [
                f"[Station 3a: Dev Completed / Awaiting QA Deploy — {review_status.upper()}]",
                f"- **Reviewed By**: {format_person_badge(from_role, rev_person)}",
                f"- Reviewed Commit: `{commit_sha}`",
                f"- Summary: {summary or 'All completed tasks, ACs, and engineering standards verified.'}",
                f"- QA Deploy Gate (`gated_by_deploy`): Review approved; awaiting QA environment deployment (CI/CD transition to `{in_qa_status}` or `promote-qa`). QA has NOT been reassigned or `@mention`ed yet.",
            ]
            if self_reviewed:
                comment_lines.append("- Governance Note: Developer and Reviewer map to the same person (`self_reviewed_with_ai: true`).")
            if findings:
                comment_lines.append(f"- Findings Count: {len(findings)}")

        # Post comment WITHOUT mentioning QA and WITHOUT reassigning Jira issue to QA
        client.add_comment(jira_key, "\n".join(comment_lines))

        same_person_flag = is_same_person(rev_person, qa_person)
        handoff_record = {
            "timestamp": now_iso(),
            "from_role": from_role,
            "to_role": "qa",
            "from_person": rev_person["name"],
            "to_person": qa_person["name"],
            "next_station_owner": qa_person["name"],
            "same_person": same_person_flag,
            "can_auto_chain_next_station": False,
            "requires_user_confirmation_for_next_station": True,
            "cross_person_clean_stop": not same_person_flag,
            "next_station_prompt_allowed": same_person_flag,
            "handoff_mode": "gated-by-deploy",
            "qa_handoff_mode": "gated_by_deploy",
            "qa_deploy_status": "awaiting_qa_deploy",
            "target_jira_status": effective_status_label,
            "assigned_account_id": "",
            "notified": False,
            "qa_notified": False,
        }
        story.setdefault("handoffs", []).append(handoff_record)
        saved_path = save_active_story(repo_root, jira_key, story, client=client)
        out = {
            "ok": True,
            "action": "handoff",
            "jira_key": jira_key,
            "review_status": review_status,
            "reviewed_commit": commit_sha,
            "findings_count": len(findings),
            "qa_handoff_mode": "gated_by_deploy",
            "qa_deploy_status": "awaiting_qa_deploy",
            "qa_notified": False,
            **_handoff_governance_fields(handoff_record),
            "handoff": handoff_record,
            "active_file": str(saved_path.relative_to(repo_root)),
        }
        if deploy_gate_warning:
            out["warning"] = deploy_gate_warning
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if review_status == "approved" and isinstance(story.get("qa_deploy_gate"), dict):
        prev_gate = story["qa_deploy_gate"]
        if prev_gate.get("commit") and commit_sha and prev_gate.get("commit") != commit_sha:
            prev_gate["tag"] = ""
            prev_gate["deploy_ref"] = ""
            prev_gate["environment"] = ""

    if zh:
        comment_lines = [
            f"[站點 3a：開發交接與程式碼審查 — {review_status.upper()}]",
            f"- **審查人員**：{format_person_badge(from_role, rev_person)}",
            f"- **審查 Commit**：`{commit_sha}`",
            f"- **審查摘要**：{summary or '已完成所有開發子任務、驗收條件與共用工程標準核對。'}",
        ]
        if self_reviewed:
            comment_lines.append("- **治理註記**：開發與審查由同一人兼任，已啟用 AI 獨立審查模式（`self_reviewed_with_ai: true`）。")
        if findings:
            comment_lines.append(f"- **審查發現數量**：{len(findings)}")
    else:
        comment_lines = [
            f"[Station 3a: Dev Handoff & Code Review — {review_status.upper()}]",
            f"- **Reviewed By**: {format_person_badge(from_role, rev_person)}",
            f"- Reviewed Commit: `{commit_sha}`",
            f"- Summary: {summary or 'All completed tasks, ACs, and engineering standards verified.'}",
        ]
        if self_reviewed:
            comment_lines.append("- Governance Note: Developer and Reviewer map to the same person (`self_reviewed_with_ai: true`).")
        if findings:
            comment_lines.append(f"- Findings Count: {len(findings)}")

    handoff_record = execute_role_handoff(
        client=client,
        config=config,
        story=story,
        from_role=from_role,
        to_role=effective_to_role,
        status_key=status_key,
        comment_md="\n".join(comment_lines),
        from_person_override=rev_person,
        to_assignee=getattr(args, "to_assignee", None),
    )

    saved_path = save_active_story(repo_root, jira_key, story, client=client)
    out = {
        "ok": True,
        "action": "handoff",
        "jira_key": jira_key,
        "review_status": review_status,
        "reviewed_commit": commit_sha,
        "findings_count": len(findings),
        **_handoff_governance_fields(handoff_record),
        "handoff": handoff_record,
        "active_file": str(saved_path.relative_to(repo_root)),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 6b: promote-qa (Scenario B: explicit QA deploy gate promotion)
# ---------------------------------------------------------------------------

def cmd_promote_qa(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    from_role = _clean_optional_text(getattr(args, "from_role", None)) or "developer"
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role=from_role)
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key, client=client)

    review = story.get("review") or {}
    if review.get("status") != "approved":
        print(
            f"ERROR: Cannot promote {jira_key} to QA because review.status is '{review.get('status') or 'unreviewed'}' (must be 'approved').",
            file=sys.stderr,
        )
        return 1
    if (
        story.get("candidate_commit")
        and review.get("reviewed_commit")
        and story.get("candidate_commit") != review.get("reviewed_commit")
    ):
        print(
            f"ERROR: Cannot promote {jira_key} to QA because candidate commit '{story.get('candidate_commit')}' "
            f"does not match reviewed commit '{review.get('reviewed_commit')}'. Re-run Code Review first.",
            file=sys.stderr,
        )
        return 1

    tasks = story.get("tasks") or []
    incomplete_tasks = [t["id"] for t in tasks if t.get("status") != "completed"]
    if not tasks or incomplete_tasks:
        print(
            f"ERROR: Cannot promote {jira_key} to QA while development tasks are incomplete ({', '.join(incomplete_tasks) or 'no tasks planned'}).",
            file=sys.stderr,
        )
        return 1

    open_bugs = [b.get("bug_key") for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]
    if open_bugs:
        print(
            f"ERROR: Cannot promote {jira_key} to QA while open Bugs exist: {', '.join(open_bugs)}.",
            file=sys.stderr,
        )
        return 1

    from_person = resolve_acting_person(config, from_role, client=client, story=story)
    qa_person = resolve_story_role_person(config, story, "qa", selector=getattr(args, "to_assignee", None))
    tag = _clean_single_line_text(getattr(args, "tag", None))
    raw_deploy_ref = _clean_single_line_text(getattr(args, "deploy_ref", None))
    deploy_ref = raw_deploy_ref or tag
    environment = _clean_single_line_text(getattr(args, "environment", None))
    note = _clean_optional_text(getattr(args, "note", None))
    commit_sha = story.get("candidate_commit") or review.get("reviewed_commit") or "HEAD"
    qa_handoff_mode = get_effective_qa_handoff_mode(config)
    existing_gate = story.get("qa_deploy_gate") if isinstance(story.get("qa_deploy_gate"), dict) else {}
    already_deployed_for_commit = (
        existing_gate.get("status") == "deployed"
        and (not existing_gate.get("commit") or existing_gate.get("commit") == commit_sha)
    )

    if qa_handoff_mode == "immediate" or already_deployed_for_commit:
        updated_gate = dict(existing_gate)
        if "deploy_ref" not in updated_gate:
            updated_gate["deploy_ref"] = _clean_single_line_text(updated_gate.get("tag"))
        if "environment" not in updated_gate:
            updated_gate["environment"] = ""
        metadata_updated = False
        if raw_deploy_ref and raw_deploy_ref != updated_gate.get("deploy_ref", ""):
            updated_gate["deploy_ref"] = raw_deploy_ref
            metadata_updated = True
        if environment and environment != updated_gate.get("environment", ""):
            updated_gate["environment"] = environment
            metadata_updated = True
        if metadata_updated:
            if not updated_gate.get("commit"):
                updated_gate["commit"] = commit_sha
            story["qa_deploy_gate"] = updated_gate
            save_active_story(repo_root, jira_key, story, client=client)
        same_person_flag = is_same_person(from_person, qa_person)
        gov_stub = {"same_person": same_person_flag, "to_person": qa_person["name"]}
        active_rel = str((get_active_dir(repo_root) / f"{jira_key}.json").relative_to(repo_root))
        out = {
            "ok": True,
            "action": "promote-qa",
            "jira_key": jira_key,
            "already_deployed": True,
            "qa_handoff_mode": qa_handoff_mode,
            "qa_deploy_status": "deployed" if already_deployed_for_commit else "immediate",
            "deploy_ref": updated_gate.get("deploy_ref", ""),
            "environment": updated_gate.get("environment", ""),
            "qa_deploy_gate": updated_gate,
            "qa_notified": False,
            **_handoff_governance_fields(gov_stub),
            "handoff": None,
            "active_file": active_rel,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    zh = is_zh_tw(config)
    if zh:
        comment_lines = [
            f"[QA 環境部署就緒 — 正式提測：{jira_key}]",
            f"- **驗證目標 Commit**：`{commit_sha}`",
        ]
        if deploy_ref:
            comment_lines.append(f"- **QA 部署參照 (Deploy Ref)**：`{deploy_ref}`")
        if tag and tag != deploy_ref:
            comment_lines.append(f"- **QA 部署 Tag**：`{tag}`")
        if environment:
            comment_lines.append(f"- **部署環境**：`{environment}`")
        if note:
            comment_lines.append(f"- **提測說明**：{note}")
    else:
        comment_lines = [
            f"[QA Environment Ready — Promoted to QA: {jira_key}]",
            f"- **Candidate Commit**: `{commit_sha}`",
        ]
        if deploy_ref:
            comment_lines.append(f"- **QA Deploy Ref**: `{deploy_ref}`")
        if tag and tag != deploy_ref:
            comment_lines.append(f"- **QA Deploy Tag**: `{tag}`")
        if environment:
            comment_lines.append(f"- **Environment**: `{environment}`")
        if note:
            comment_lines.append(f"- **Note**: {note}")

    handoff_record = execute_role_handoff(
        client=client,
        config=config,
        story=story,
        from_role=from_role,
        to_role="qa",
        status_key="in_qa",
        comment_md="\n".join(comment_lines),
        from_person_override=from_person,
        to_assignee=getattr(args, "to_assignee", None),
    )

    story["qa_deploy_gate"] = {
        "status": "deployed",
        "commit": commit_sha,
        "awaiting_in_qa_cycle": False,
        "tag": tag,
        "deploy_ref": deploy_ref,
        "environment": environment,
        "note": note,
        "promoted_at": now_iso(),
        "source": "cli_promote_qa",
    }

    saved_path = save_active_story(repo_root, jira_key, story, client=client)
    out = {
        "ok": True,
        "action": "promote-qa",
        "jira_key": jira_key,
        "already_deployed": False,
        "qa_handoff_mode": qa_handoff_mode,
        "qa_deploy_status": "deployed",
        "deploy_ref": deploy_ref,
        "environment": environment,
        "qa_deploy_gate": story["qa_deploy_gate"],
        "qa_notified": bool(handoff_record.get("notified")),
        **_handoff_governance_fields(handoff_record),
        "handoff": handoff_record,
        "active_file": str(saved_path.relative_to(repo_root)),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 7: record-qa (Station 3b: team-qa plan & verify modes)
# ---------------------------------------------------------------------------

def parse_junit_xml_results(xml_path: Path) -> dict:
    """
    Parse JUnit XML into {TC-ID: 'pass'|'fail'|'not-executed'}.
    Uses worst-outcome precedence (fail > not-executed > pass) when multiple
    <testcase> elements (e.g. parameterized tests) reference the same TC-ID,
    and extracts all TC-### identifiers present in a testcase label.
    """
    root = ET.parse(xml_path).getroot()
    precedence = {"fail": 3, "blocked": 2, "not-executed": 1, "pass": 0}
    results = {}
    for case in root.iter("testcase"):
        label = f"{case.get('classname') or ''} {case.get('name') or ''}"
        tc_ids = re.findall(r"(?<![A-Za-z0-9])TC-\d{3,}(?![0-9])", label)
        if not tc_ids:
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            st = "fail"
        elif case.find("skipped") is not None:
            st = "not-executed"
        else:
            st = "pass"
        for tc_id in tc_ids:
            prev = results.get(tc_id)
            if prev is None or precedence[st] > precedence[prev]:
                results[tc_id] = st
    return results

def _inspect_qa_state(repo_root: Path, config: dict, story: dict = None) -> dict:
    local_creds = load_jira_credentials(repo_root)
    role_creds = local_creds.get("role_credentials") or {}
    qa_rc = role_creds.get("qa") if isinstance(role_creds.get("qa"), dict) else {}
    qa_person = resolve_role_person(config, "qa")
    qa_backend = (config.get("qa_test_backend") or "").strip().lower()
    qase_cfg = config.get("qase") if isinstance(config.get("qase"), dict) else {}
    qase_code = (os.environ.get("QASE_PROJECT_CODE") or qase_cfg.get("project_code") or "").strip().upper()
    has_qase_token = bool((os.environ.get("QASE_API_TOKEN") or local_creds.get("qase_api_token") or "").strip())
    guardrail = _build_qa_identity_guardrail(repo_root, config, story=story)
    base_instruction = (
        "STOP and ask the user whether QA test cases should be managed in 'jira-comment' (Jira comments) or 'qase' (Qase TestOps). "
        "If 'qase', also confirm Qase Project Code and Qase API Token, then pass --qa-backend to remember the choice."
        if not qa_backend
        else f"QA test backend is already remembered as '{qa_backend}'. Proceed using '{qa_backend}' automatically."
    )
    if guardrail["identity_mismatch"]:
        if qa_backend:
            base_instruction = (
                f"{guardrail['warning']} STOP and wait for explicit user confirmation before running QA "
                f"(QA test backend is already remembered as '{qa_backend}'; do not re-ask backend choice once confirmed)."
            )
        else:
            base_instruction = f"{guardrail['warning']} Also {base_instruction}"
    return {
        "ok": True,
        "action": "qa-inspect",
        "qa_test_backend": qa_backend,
        "qa_backend_configured": bool(qa_backend),
        "requires_qa_backend_choice": not bool(qa_backend),
        "qase": {
            "project_code": qase_code,
            "base_url": qase_cfg.get("base_url") or "https://api.qase.io/v1",
            "app_url": qase_cfg.get("app_url") or "https://app.qase.io",
            "token_configured": has_qase_token,
        },
        "qa_role": {
            "name": qa_person.get("name", ""),
            "email": qa_person.get("email", ""),
            "jira_account_id": qa_person.get("jira_account_id", ""),
            "has_role_token": bool((qa_rc.get("jira_api_token") or "").strip() or os.environ.get("JIRA_API_TOKEN_QA", "").strip()),
        },
        "qa_identity_guardrail": guardrail,
        "agent_instruction": base_instruction,
    }


def cmd_record_qa(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)

    if getattr(args, "inspect", False):
        insp_story = None
        if getattr(args, "key", None):
            try:
                clean_k = validate_jira_key(args.key)
                ap = get_active_dir(repo_root) / f"{clean_k}.json"
                if ap.exists():
                    insp_story = json.loads(ap.read_text(encoding="utf-8"))
            except Exception:
                insp_story = None
        print(json.dumps(_inspect_qa_state(repo_root, config, story=insp_story), ensure_ascii=False, indent=2))
        return 0

    if not args.key or not args.mode:
        print("ERROR: --key and --mode {plan,verify} are required unless --inspect is set.", file=sys.stderr)
        return 1

    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="qa")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1

    # Apply any explicit QA backend / Qase configuration updates (validating Qase credentials before persisting)
    config_dirty = False
    if getattr(args, "qa_backend", None):
        config["qa_test_backend"] = args.qa_backend.strip().lower()
        config_dirty = True
    qase_cfg = config.setdefault("qase", dict(DEFAULT_TEAM_CONFIG["qase"]))
    if getattr(args, "qase_project_code", None):
        qase_cfg["project_code"] = args.qase_project_code.strip().upper()
        config_dirty = True
    if getattr(args, "qase_base_url", None):
        qase_cfg["base_url"] = args.qase_base_url.strip().rstrip("/")
        config_dirty = True
    if getattr(args, "qase_app_url", None):
        qase_cfg["app_url"] = args.qase_app_url.strip().rstrip("/")
        config_dirty = True
    if getattr(args, "qase_jira_type", None):
        qase_cfg["jira_integration_type"] = args.qase_jira_type.strip()
        config_dirty = True

    eff_backend = (config.get("qa_test_backend") or "jira-comment").strip().lower()
    qase_token_arg = getattr(args, "qase_token", None)
    if qase_token_arg:
        save_jira_credentials(repo_root, qase_token=qase_token_arg)

    qase_client = QaseClient(config, offline=args.offline, repo_root=repo_root) if eff_backend == "qase" else None
    if eff_backend == "qase" and not args.offline and not qase_client.has_credentials:
        print(
            "ERROR: qa_test_backend is 'qase', but Qase project_code or Qase API token is missing. "
            "Pass --qase-project-code <CODE> and --qase-token <TOKEN> (stored in .ai-workflow/credentials.json).",
            file=sys.stderr,
        )
        return 1

    if config_dirty and get_config_path(repo_root).exists():
        get_config_path(repo_root).write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key, client=client)
    zh = is_zh_tw(config)

    if getattr(args, "qa_owner", None):
        qa_person = resolve_story_role_person(config, story, "qa", selector=args.qa_owner)
    elif isinstance((story.get("role_owners") or {}).get("qa"), dict) and _is_member_claimed("qa", story["role_owners"]["qa"]):
        qa_person = resolve_story_role_person(config, story, "qa")
    else:
        qa_person = resolve_acting_person(config, "qa", client=client, story=story)

    if _is_member_claimed("qa", qa_person):
        story.setdefault("role_owners", {})["qa"] = serialize_member_for_config(qa_person, "qa")
    if qa_person.get("email") and qa_person["email"].lower() != (client.email or "").lower():
        client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="qa", member_email=qa_person["email"])

    qa_badge = format_person_badge("qa", qa_person)
    qa = story.get("qa") or {"status": "planned", "test_cases": []}
    prior_qase_ids = {
        str(c.get("id") or "").strip().upper(): c.get("qase_case_id")
        for c in (qa.get("test_cases") or [])
        if isinstance(c, dict) and c.get("qase_case_id")
    }
    qa["owner"] = qa_person["name"]
    qa["backend"] = eff_backend
    if qa_person.get("email"):
        qa["owner_email"] = qa_person["email"]

    if args.mode == "plan":
        raw_cases = []
        if args.cases_json:
            loaded = load_json_arg(args.cases_json)
            if isinstance(loaded, dict) and "test_cases" in loaded:
                raw_cases = loaded["test_cases"]
            elif isinstance(loaded, list):
                raw_cases = loaded
        if args.tc:
            allowed_exec_types = ("automated", "external", "manual")
            for idx, tc_str in enumerate(args.tc, start=len(raw_cases) + 1):
                # Format: TC-001:AC-001:automated|external|manual:Title/expected (or AC-001:automated|external|manual:Title)
                first_tok = tc_str.split(":", 1)[0].strip()
                if re.match(r"^TC-\d+$", first_tok, re.IGNORECASE):
                    parts = [p.strip() for p in tc_str.split(":", 3)]
                    tc_id = parts[0].upper()
                    ac_id = parts[1].upper() if len(parts) > 1 and parts[1] else "AC-001"
                    exec_type = (
                        parts[2].lower()
                        if len(parts) > 2 and parts[2].lower() in allowed_exec_types
                        else "automated"
                    )
                    title = parts[3] if len(parts) > 3 else tc_str
                elif re.match(r"^AC-\d+$", first_tok, re.IGNORECASE):
                    parts = [p.strip() for p in tc_str.split(":", 2)]
                    tc_id = f"TC-{idx:03d}"
                    ac_id = parts[0].upper()
                    exec_type = (
                        parts[1].lower()
                        if len(parts) > 1 and parts[1].lower() in allowed_exec_types
                        else "automated"
                    )
                    title = parts[2] if len(parts) > 2 else tc_str
                else:
                    parts = [p.strip() for p in tc_str.split(":", 3)]
                    tc_id = f"TC-{idx:03d}"
                    ac_id = parts[0].upper() if parts and re.match(r"^AC-\d+$", parts[0], re.IGNORECASE) else "AC-001"
                    exec_type = "automated"
                    title = tc_str.strip()
                raw_cases.append({
                    "id": tc_id,
                    "ac_id": ac_id,
                    "execution_type": exec_type,
                    "title": title,
                    "result": "not-executed",
                })

        if not raw_cases:
            print("ERROR: No test cases provided for record-qa --mode plan.", file=sys.stderr)
            return 1

        normalized_cases = []
        for idx, c in enumerate(raw_cases, start=1):
            tc_id = (c.get("id") or f"TC-{idx:03d}").strip().upper()
            raw_et = str(c.get("execution_type") or "automated").strip().lower()
            exec_type = raw_et if raw_et in ("automated", "external", "manual") else "automated"
            item_entry = {
                "id": tc_id,
                "ac_id": c.get("ac_id") or "AC-001",
                "title": c.get("title") or tc_id,
                "execution_type": exec_type,
                "recommended_by_ai": True,
                "result": c.get("result") or "not-executed",
                "notes": c.get("notes") or "",
            }
            preserved_qase_id = c.get("qase_case_id") or prior_qase_ids.get(tc_id)
            if preserved_qase_id:
                item_entry["qase_case_id"] = int(preserved_qase_id)
            normalized_cases.append(item_entry)

        uncovered_acs = find_uncovered_story_acs(story, normalized_cases)
        if uncovered_acs:
            print(
                f"ERROR: Planned test cases do not cover Story AC(s): {', '.join(uncovered_acs)}. Every AC-* must map to at least one TC-*.",
                file=sys.stderr,
            )
            return 1

        qase_sync_info = None
        if eff_backend == "qase":
            qase_sync_info = qase_client.sync_test_plan(
                jira_key=jira_key,
                story_title=story.get("title") or jira_key,
                qa=qa,
                normalized_cases=normalized_cases,
            )

        qa["test_cases"] = normalized_cases
        qa["status"] = "planned"
        qa["planned_at"] = now_iso()
        story["qa"] = qa

        qa_acct_id = qa_person.get("jira_account_id") or ""
        if qa_acct_id and not _is_awaiting_qa_deploy(story, config) and (
            story.get("current_owner_role") in (None, "", "qa")
            or (story.get("review") or {}).get("status") == "approved"
        ):
            safe_assign_issue(client, jira_key, qa_acct_id)

        if eff_backend == "qase" and qase_sync_info:
            if zh:
                comment_lines = [
                    f"[站點 3b：QA 測試計畫 — {jira_key}（已同步至 Qase）]",
                    f"- **測試負責人 (QA Owner)**：{qa_badge}",
                    f"- **Qase 測試套件**：`{qase_sync_info['project_code']}` Suite #{qase_sync_info['suite_id']} "
                    f"（共 {len(normalized_cases)} 項測試案例）— {qase_sync_info['suite_url']}",
                ]
            else:
                comment_lines = [
                    f"[Station 3b: QA Test Plan — {jira_key} (Synced to Qase)]",
                    f"- **QA Owner**: {qa_badge}",
                    f"- **Qase Test Suite**: `{qase_sync_info['project_code']}` Suite #{qase_sync_info['suite_id']} "
                    f"({len(normalized_cases)} Test Cases) — {qase_sync_info['suite_url']}",
                ]
            client.add_comment(jira_key, "\n".join(comment_lines))
        else:
            if zh:
                table_lines = [
                    f"[站點 3b：QA 測試計畫 — {jira_key}]",
                    f"- **測試負責人 (QA Owner)**：{qa_badge}",
                    "",
                    "| TC ID | AC ID | 執行類型（AI 預填） | 測試案例名稱 |",
                    "|---|---|---|---|",
                ]
            else:
                table_lines = [
                    f"[Station 3b: QA Test Plan — {jira_key}]",
                    f"- **QA Owner**: {qa_badge}",
                    "",
                    "| TC ID | AC ID | Type (AI Pre-filled) | Title |",
                    "|---|---|---|---|",
                ]
            for c in normalized_cases:
                table_lines.append(f"| {c['id']} | {c['ac_id']} | {c['execution_type']} | {c['title']} |")
            client.add_comment(jira_key, "\n".join(table_lines))

        saved_path = save_active_story(repo_root, jira_key, story, client=client)
        out = {
            "ok": True,
            "action": "record-qa",
            "mode": "plan",
            "jira_key": jira_key,
            "qa_owner": qa_person["name"],
            "qa_test_backend": eff_backend,
            "tc_count": len(normalized_cases),
            "qa_identity_guardrail": _build_qa_identity_guardrail(repo_root, config, story=story),
            "active_file": str(saved_path.relative_to(repo_root)),
        }
        if qase_sync_info is not None:
            out["qase_sync"] = qase_sync_info
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # verify mode: enforce QA deploy gate when qa_handoff_mode == 'gated_by_deploy'
    if _is_awaiting_qa_deploy(story, config):
        in_qa_name = (config.get("status_mapping") or {}).get("in_qa") or "In QA"
        print(
            f"ERROR: QA verification (`record-qa --mode verify`) for {jira_key} is gated while awaiting QA environment deployment "
            f"(qa_handoff_mode='gated_by_deploy'). Wait for CI/CD to transition Jira to '{in_qa_name}' (sync via `jira_sync.py status`/`pull`), "
            f"or run `jira_sync.py promote-qa --key {jira_key}` first. (Parallel `record-qa --mode plan` remains allowed.)",
            file=sys.stderr,
        )
        return 1

    # verify mode (supports QA Fast-Track auto-deriving TC-001..N from AC-001..N when test_cases is empty)
    test_cases = qa.get("test_cases") or []
    fast_tracked_qa = False
    if not test_cases and (story.get("acceptance_criteria") or []):
        test_cases = [
            {
                "id": f"TC-{idx:03d}",
                "ac_id": ac["id"],
                "title": ac.get("description") or ac["id"],
                "execution_type": "automated",
                "recommended_by_ai": True,
                "result": "not-executed",
                "notes": "Auto-derived from Story ACs via QA Fast-Track",
            }
            for idx, ac in enumerate(story.get("acceptance_criteria") or [], start=1)
        ]
        qa["test_cases"] = test_cases
        qa["planned_at"] = qa.get("planned_at") or now_iso()
        fast_tracked_qa = True

    if not test_cases:
        print(f"ERROR: No planned test cases or Story ACs exist in {jira_key}. Run record-qa --mode plan first.", file=sys.stderr)
        return 1

    for c in test_cases:
        c["id"] = str(c.get("id") or "").strip().upper()
    known_tc_ids = {c["id"] for c in test_cases}
    incoming_results = {}
    incoming_notes = {}
    incoming_tc_attach_paths = {}
    if args.junit_xml:
        xml_p = Path(args.junit_xml)
        if not xml_p.exists():
            print(f"ERROR: JUnit XML file not found: {xml_p}", file=sys.stderr)
            return 1
        incoming_results.update(parse_junit_xml_results(xml_p))

    if args.results_json:
        loaded = load_json_arg(args.results_json)
        if isinstance(loaded, dict):
            for k, v in loaded.items():
                tc_id = str(k).strip().upper()
                if tc_id not in known_tc_ids:
                    print(f"ERROR: Unknown test case ID '{tc_id}' in --results-json (planned: {sorted(known_tc_ids)}).", file=sys.stderr)
                    return 1
                incoming_results[tc_id] = str(v).strip().lower()
        elif isinstance(loaded, list):
            for item in loaded:
                if isinstance(item, dict) and item.get("id"):
                    tc_id = str(item["id"]).strip().upper()
                    if tc_id not in known_tc_ids:
                        print(f"ERROR: Unknown test case ID '{tc_id}' in --results-json (planned: {sorted(known_tc_ids)}).", file=sys.stderr)
                        return 1
                    incoming_results[tc_id] = str(item.get("result", "not-executed")).strip().lower()
                    note_val = str(item.get("notes") or item.get("note") or "").strip()
                    if note_val:
                        incoming_notes[tc_id] = note_val
                    if isinstance(item.get("attachments"), list):
                        incoming_tc_attach_paths.setdefault(tc_id, []).extend(
                            [str(ap).strip() for ap in item["attachments"] if str(ap).strip()]
                        )

    if getattr(args, "tc_attach", None):
        for att_item in args.tc_attach:
            m_att = re.match(r"^([^=:]+)[=:](.+)$", str(att_item or "").strip())
            if not m_att:
                print(
                    f"ERROR: Invalid --tc-attach format '{att_item}'. Expected 'TC-ID=/path/to/file'.",
                    file=sys.stderr,
                )
                return 1
            tc_id = m_att.group(1).strip().upper()
            f_path = m_att.group(2).strip()
            if tc_id not in known_tc_ids:
                print(
                    f"ERROR: Unknown test case ID '{tc_id}' in --tc-attach (planned: {sorted(known_tc_ids)}).",
                    file=sys.stderr,
                )
                return 1
            incoming_tc_attach_paths.setdefault(tc_id, []).append(f_path)

    tc_validated_attachments = {}
    for tc_id, raw_paths in incoming_tc_attach_paths.items():
        try:
            tc_validated_attachments[tc_id] = validate_attachment_files(raw_paths, repo_root=repo_root)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if args.result:
        for r_item in args.result:
            # Accept both '=' and ':' as delimiter, plus optional ':note' suffix:
            # e.g. TC-001=pass, TC-001:pass, TC-003=pass:Verified on staging, TC-003:pass:Newman green
            m_res = re.match(r"^([^=:]+)[=:]([^=:]+?)(?::(.*))?$", r_item.strip())
            if not m_res:
                print(
                    f"ERROR: Invalid --result format '{r_item}'. Expected 'TC-ID=pass|fail|blocked|not-executed[:note]' (or 'TC-ID:pass[:note]').",
                    file=sys.stderr,
                )
                return 1
            tc_id = m_res.group(1).strip().upper()
            val = m_res.group(2).strip().lower()
            note_val = (m_res.group(3) or "").strip()
            if tc_id not in known_tc_ids:
                print(f"ERROR: Unknown test case ID '{tc_id}' in --result (planned: {sorted(known_tc_ids)}).", file=sys.stderr)
                return 1
            incoming_results[tc_id] = val
            if note_val:
                incoming_notes[tc_id] = note_val

    # Anti-False-Pass Guardrail: --all-pass ONLY marks execution_type == 'automated' test cases as 'pass'
    # (and never overwrites already waived / not-applicable TCs).
    # Test cases with execution_type in ('external', 'manual') require explicit results via --junit-xml, --results-json, or --result.
    if getattr(args, "all_pass", False):
        for c in test_cases:
            if (
                c["id"] not in incoming_results
                and c.get("execution_type", "automated") == "automated"
                and c.get("result") not in ("waived", "not-applicable")
            ):
                incoming_results[c["id"]] = "pass"

    allowed_results = {"pass", "fail", "blocked", "not-executed"}
    for c in test_cases:
        if c["id"] in incoming_results:
            val = incoming_results[c["id"]]
            if val not in allowed_results:
                print(f"ERROR: Invalid test result '{val}' for {c['id']}. Allowed: {sorted(allowed_results)}", file=sys.stderr)
                return 1
            c["result"] = val
            if val in ("pass", "fail"):
                for stale_k in ("waived_bug_id", "triage_resolution", "waived_by", "waived_reason", "waived_at"):
                    c.pop(stale_k, None)
        if c["id"] in incoming_notes:
            c["notes"] = incoming_notes[c["id"]]
        if c["id"] in tc_validated_attachments:
            existing_meta = [a for a in (c.get("attachments") or []) if isinstance(a, dict)]
            seen_shas = {a.get("sha256") for a in existing_meta if a.get("sha256")}
            for vf in tc_validated_attachments[c["id"]]:
                if vf["sha256"] not in seen_shas:
                    existing_meta.append({
                        "filename": vf["filename"],
                        "local_path": vf["local_path"],
                        "size": vf["size"],
                        "mime_type": vf["mime_type"],
                        "sha256": vf["sha256"],
                    })
                    seen_shas.add(vf["sha256"])
            c["attachments"] = existing_meta

    counts = {"pass": 0, "fail": 0, "blocked": 0, "not-executed": 0, "waived": 0, "not-applicable": 0}
    by_type = {"automated": 0, "external": 0, "manual": 0}
    for c in test_cases:
        st = c.get("result", "not-executed")
        counts[st] = counts.get(st, 0) + 1
        et = c.get("execution_type", "automated")
        by_type[et] = by_type.get(et, 0) + 1

    pending_external_tcs = [
        c["id"] for c in test_cases if not _is_tc_satisfied(c, story) and c.get("execution_type") == "external"
    ]
    pending_manual_tcs = [
        c["id"] for c in test_cases if not _is_tc_satisfied(c, story) and c.get("execution_type") == "manual"
    ]

    tested_commit = (args.commit or story.get("candidate_commit") or _detect_git_head_commit(repo_root) or "unknown").strip()
    open_bugs = [b for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]
    uncovered_acs = find_uncovered_story_acs(story, test_cases)
    invalid_waivers = [
        c["id"] for c in test_cases
        if c.get("result") in ("waived", "not-applicable") and not _is_tc_waiver_valid(c, story)
    ]

    if counts["fail"] > 0 or counts["blocked"] > 0 or invalid_waivers:
        qa_status = "failed"
    elif counts["not-executed"] > 0 or uncovered_acs:
        qa_status = "incomplete"
    elif open_bugs:
        qa_status = "blocked-by-open-bugs"
    else:
        qa_status = "passed"

    qase_sync_info = None
    if eff_backend == "qase":
        qase_sync_info = qase_client.sync_verification_run(
            jira_key=jira_key,
            story_title=story.get("title") or jira_key,
            qa=qa,
            test_cases=test_cases,
            tested_commit=tested_commit,
            qa_status=qa_status,
            tc_validated_attachments=tc_validated_attachments,
        )

    explicit_deploy_ref = _clean_single_line_text(getattr(args, "deploy_ref", None))
    gate_obj = story.get("qa_deploy_gate") if isinstance(story.get("qa_deploy_gate"), dict) else {}
    gate_matches_commit = (
        not gate_obj.get("commit")
        or not tested_commit
        or gate_obj.get("commit") == tested_commit
    )
    gate_deploy_ref = (
        _clean_single_line_text(gate_obj.get("deploy_ref") or gate_obj.get("tag"))
        if gate_matches_commit
        else ""
    )
    verified_deploy_ref = explicit_deploy_ref or gate_deploy_ref

    qa["status"] = qa_status
    qa["tested_commit"] = tested_commit
    if verified_deploy_ref:
        qa["verified_deploy_ref"] = verified_deploy_ref
    else:
        qa.pop("verified_deploy_ref", None)
    qa["counts"] = counts
    qa["by_type"] = by_type
    qa["verified_at"] = now_iso()
    story["qa"] = qa

    waived_suffix = ""
    if counts.get("waived", 0) > 0:
        waived_suffix += f", waived={counts['waived']}"
    if counts.get("not-applicable", 0) > 0:
        waived_suffix += f", not-applicable={counts['not-applicable']}"

    if eff_backend == "qase" and qase_sync_info:
        if zh:
            report_lines = [
                f"[站點 3b：QA 測試驗證報告 — {qa_status.upper()}（已同步至 Qase）]",
                f"- **測試負責人 (QA Owner)**：{qa_badge}",
                f"- **驗證 Commit**：`{tested_commit}`",
            ]
            if verified_deploy_ref:
                report_lines.append(f"- **驗證部署參照 (Deploy Ref)**：`{verified_deploy_ref}`")
            report_lines.extend([
                f"- **結果統計**：pass={counts['pass']}, fail={counts['fail']}, blocked={counts['blocked']}, not-executed={counts['not-executed']}{waived_suffix} "
                f"（automated={by_type.get('automated', 0)}, external={by_type.get('external', 0)}, manual={by_type.get('manual', 0)}）",
                f"- **Qase 測試執行紀錄**：`{qase_sync_info['project_code']}` Run #{qase_sync_info['run_id']} — {qase_sync_info['run_url']}",
            ])
        else:
            report_lines = [
                f"[Station 3b: QA Verification Report — {qa_status.upper()} (Synced to Qase)]",
                f"- **QA Owner**: {qa_badge}",
                f"- **Tested Commit**: `{tested_commit}`",
            ]
            if verified_deploy_ref:
                report_lines.append(f"- **Verified Deploy Ref**: `{verified_deploy_ref}`")
            report_lines.extend([
                f"- **Summary Counts**: pass={counts['pass']}, fail={counts['fail']}, blocked={counts['blocked']}, not-executed={counts['not-executed']}{waived_suffix} "
                f"(automated={by_type.get('automated', 0)}, external={by_type.get('external', 0)}, manual={by_type.get('manual', 0)})",
                f"- **Qase Test Run**: `{qase_sync_info['project_code']}` Run #{qase_sync_info['run_id']} — {qase_sync_info['run_url']}",
            ])
        report_md = "\n".join(report_lines)
    else:
        if zh:
            table_lines = [
                f"[站點 3b：QA 測試驗證報告 — {qa_status.upper()}]",
                f"- **測試負責人 (QA Owner)**：{qa_badge}",
                f"- **驗證 Commit**：`{tested_commit}`",
            ]
            if verified_deploy_ref:
                table_lines.append(f"- **驗證部署參照 (Deploy Ref)**：`{verified_deploy_ref}`")
            table_lines.extend([
                f"- **結果統計**：pass={counts['pass']}, fail={counts['fail']}, blocked={counts['blocked']}, not-executed={counts['not-executed']}{waived_suffix}",
                f"- **執行類型分佈**：automated={by_type.get('automated', 0)}, external={by_type.get('external', 0)}, manual={by_type.get('manual', 0)}",
                "",
                "| TC ID | AC ID | 執行類型 | 測試結果 |",
                "|---|---|---|---|",
            ])
        else:
            table_lines = [
                f"[Station 3b: QA Verification Report — {qa_status.upper()}]",
                f"- **QA Owner**: {qa_badge}",
                f"- Tested Commit: `{tested_commit}`",
            ]
            if verified_deploy_ref:
                table_lines.append(f"- **Verified Deploy Ref**: `{verified_deploy_ref}`")
            table_lines.extend([
                f"- Summary Counts: pass={counts['pass']}, fail={counts['fail']}, blocked={counts['blocked']}, not-executed={counts['not-executed']}{waived_suffix}",
                f"- Execution Breakdown: automated={by_type.get('automated', 0)}, external={by_type.get('external', 0)}, manual={by_type.get('manual', 0)}",
                "",
                "| TC ID | AC ID | Type | Result |",
                "|---|---|---|---|",
            ])
        for c in test_cases:
            table_lines.append(f"| {c['id']} | {c['ac_id']} | {c['execution_type']} | **{c['result']}** |")
        report_md = "\n".join(table_lines)

    handoff_info = None
    if qa_status == "passed":
        if story.get("issue_kind") == "standalone_bug":
            bug_rep = story.get("bug_report")
            if isinstance(bug_rep, dict) and bug_rep.get("qase_defect_id") and eff_backend == "qase" and qase_client is not None:
                try:
                    q_res = qase_client.resolve_defect(int(bug_rep["qase_defect_id"]))
                    if q_res.get("resolved"):
                        bug_rep["qase_defect_resolved"] = True
                except Exception:
                    pass
        handoff_info = execute_role_handoff(
            client=client,
            config=config,
            story=story,
            from_role="qa",
            to_role="release_manager",
            status_key="ready_for_release",
            comment_md=report_md,
            from_person_override=qa_person,
            to_assignee=getattr(args, "to_assignee", None),
        )
    elif qa_status == "failed" and story.get("issue_kind") == "standalone_bug":
        # Standalone Bug failed regression retest: attach failure evidence directly onto the Bug, reopen TASK-001, and hand back to Developer without nested Bug tickets
        fail_vfiles = []
        seen_fail_shas = set()
        for vfs in tc_validated_attachments.values():
            for vf in vfs:
                if vf["sha256"] not in seen_fail_shas:
                    fail_vfiles.append(vf)
                    seen_fail_shas.add(vf["sha256"])
        if fail_vfiles:
            att_res = safe_add_jira_attachments(client, jira_key, fail_vfiles)
            if att_res.get("uploaded"):
                story.setdefault("bug_report", {}).setdefault("attachments", []).extend(att_res["uploaded"])
        for t_item in story.get("tasks") or []:
            t_item["status"] = "pending"
        bug_dev = resolve_story_role_person(config, story, "developer")
        handoff_info = execute_role_handoff(
            client=client,
            config=config,
            story=story,
            from_role="qa",
            to_role="developer",
            status_key="in_progress",
            comment_md=report_md,
            from_person_override=qa_person,
            to_assignee=getattr(args, "to_assignee", None) or bug_dev.get("email") or bug_dev.get("jira_account_id") or None,
        )
    else:
        client.add_comment(jira_key, report_md)

    saved_path = save_active_story(repo_root, jira_key, story, client=client)
    out = {
        "ok": True,
        "action": "record-qa",
        "mode": "verify",
        "jira_key": jira_key,
        "qa_owner": qa_person["name"],
        "qa_test_backend": eff_backend,
        "qa_status": qa_status,
        "fast_track": fast_tracked_qa,
        "tested_commit": tested_commit,
        "verified_deploy_ref": verified_deploy_ref,
        "counts": counts,
        "by_type": by_type,
        "pending_external_tcs": pending_external_tcs,
        "pending_manual_tcs": pending_manual_tcs,
        "qa_identity_guardrail": _build_qa_identity_guardrail(repo_root, config, story=story),
        "handoff": handoff_info,
        "active_file": str(saved_path.relative_to(repo_root)),
    }
    if handoff_info is not None:
        out.update(_handoff_governance_fields(handoff_info))
    if qase_sync_info is not None:
        out["qase_sync"] = qase_sync_info
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 8: create-bug (Station 3b: team-qa defect loop)
# ---------------------------------------------------------------------------

def _resolve_bug_developer(
    config: dict,
    story: dict,
    tc_id: str,
    ac_id: str = "",
    to_assignee: str = None,
) -> dict:
    if to_assignee:
        found = find_member_in_role(config, "developer", to_assignee)
        if found:
            return found

    target_acs = set()
    if ac_id:
        for part in str(ac_id).split(","):
            if part.strip():
                target_acs.add(part.strip().upper())
    norm_tc = (tc_id or "").strip().upper()
    for tc in ((story.get("qa") or {}).get("test_cases") or []):
        if isinstance(tc, dict) and str(tc.get("id") or "").strip().upper() == norm_tc:
            for part in str(tc.get("ac_id") or "").split(","):
                if part.strip():
                    target_acs.add(part.strip().upper())

    if target_acs:
        for t in story.get("tasks") or []:
            t_cov = {str(a).strip().upper() for a in (t.get("ac_coverage") or []) if str(a).strip()}
            t_ass = t.get("assignee")
            if (target_acs & t_cov) and isinstance(t_ass, dict) and _is_member_claimed("developer", t_ass):
                refreshed = find_member_in_role(
                    config,
                    "developer",
                    t_ass.get("jira_account_id") or t_ass.get("email") or t_ass.get("name") or "",
                )
                return refreshed or _normalize_member_dict("developer", t_ass)

    return resolve_story_role_person(config, story, "developer")


def cmd_create_bug(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="qa")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1

    # Fail-fast validation of explicit --attach files BEFORE mutating local or remote state
    explicit_attach_paths = list(getattr(args, "attach", None) or [])
    try:
        explicit_validated = validate_attachment_files(explicit_attach_paths, repo_root=repo_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    zh = is_zh_tw(config)
    is_standalone = bool(getattr(args, "standalone", False) or getattr(args, "adopt", False))
    exec_type_arg = (getattr(args, "execution_type", None) or "manual").strip().lower()

    # -----------------------------------------------------------------------
    # Branch A: Standalone Production / User-Reported Bug (`--standalone` / `--adopt`)
    # -----------------------------------------------------------------------
    if is_standalone:
        adopt_existing = bool(getattr(args, "adopt", False))
        if adopt_existing and not getattr(args, "key", None):
            print("ERROR: --adopt requires --key <BUG-KEY> naming the existing Jira Bug ticket.", file=sys.stderr)
            return 1

        eff_backend = (
            getattr(args, "qa_backend", None)
            or config.get("qa_test_backend")
            or "jira-comment"
        ).strip().lower()
        qase_client = QaseClient(config, offline=args.offline, repo_root=repo_root) if eff_backend == "qase" else None
        if eff_backend == "qase" and not args.offline and not qase_client.has_credentials:
            print(
                "ERROR: qa_test_backend is 'qase', but Qase project_code or Qase API token is missing.",
                file=sys.stderr,
            )
            return 1

        qa_person = resolve_acting_person(config, "qa", client=client)
        bug_dev = (
            find_member_in_role(config, "developer", args.to_assignee)
            if getattr(args, "to_assignee", None)
            else None
        ) or resolve_role_person(config, "developer")
        tested_commit = (args.commit or _detect_git_head_commit(repo_root) or "HEAD").strip()

        remote_summary = ""
        remote_desc_plain = ""
        if adopt_existing and client.is_live:
            clean_adopt_key = validate_jira_key(args.key)
            try:
                rem_iss = client.get_issue(clean_adopt_key)
                r_fields = (rem_iss or {}).get("fields") or {}
                remote_summary = (r_fields.get("summary") or "").strip()
                remote_desc_plain = _adf_to_plain_text(r_fields.get("description")).strip()
            except Exception as exc:
                print(f"ERROR: Failed to fetch existing Jira Bug {clean_adopt_key}: {exc}", file=sys.stderr)
                return 1

        eff_title = (args.title or remote_summary or "").strip()
        eff_steps = (args.steps or remote_desc_plain or eff_title or "").strip()
        eff_expected = (args.expected or (f"修復報修問題：{eff_title}" if eff_title else "")).strip()
        eff_actual = (args.actual or eff_title or "").strip()

        if not adopt_existing and (not eff_title or not args.steps or not args.expected or not args.actual):
            print(
                "ERROR: --title, --steps, --expected, and --actual are required when creating a new standalone Bug.",
                file=sys.stderr,
            )
            return 1
        if not eff_title:
            print("ERROR: Bug title could not be determined (provide --title).", file=sys.stderr)
            return 1

        if adopt_existing:
            bug_key = validate_jira_key(args.key)
        else:
            proj_key = config.get("project_key") or "PROJ"
            if getattr(args, "key", None):
                fallback_key = validate_jira_key(args.key)
            else:
                active_dir = get_active_dir(repo_root)
                idx_b = 1
                while (active_dir / f"{proj_key}-BUG-{idx_b}.json").exists():
                    idx_b += 1
                fallback_key = f"{proj_key}-BUG-{idx_b}"

            if zh:
                desc_md = (
                    f"報修類型：獨立線上／巡檢缺陷\n"
                    f"回報人員：{format_person_badge('qa', qa_person)}\n"
                    f"指派修復人員：{format_person_badge('developer', bug_dev)}\n"
                    f"嚴重等級：{args.severity}\n"
                    f"發現 Commit：{tested_commit}\n\n"
                    f"### 重現步驟\n{eff_steps}\n\n"
                    f"### 預期結果\n{eff_expected}\n\n"
                    f"### 實際結果\n{eff_actual}"
                )
                if explicit_validated:
                    att_bullets = "\n".join(f"- `{vf['filename']}` ({vf['size']} bytes)" for vf in explicit_validated)
                    desc_md += f"\n\n### 附件證據\n{att_bullets}"
            else:
                desc_md = (
                    f"Bug Type: Standalone Production / Exploratory Defect\n"
                    f"Reported By: {format_person_badge('qa', qa_person)}\n"
                    f"Assigned Developer: {format_person_badge('developer', bug_dev)}\n"
                    f"Severity: {args.severity}\n"
                    f"Reported Commit: {tested_commit}\n\n"
                    f"### Reproduction Steps\n{eff_steps}\n\n"
                    f"### Expected Result\n{eff_expected}\n\n"
                    f"### Actual Result\n{eff_actual}"
                )
                if explicit_validated:
                    att_bullets = "\n".join(f"- `{vf['filename']}` ({vf['size']} bytes)" for vf in explicit_validated)
                    desc_md += f"\n\n### Attachments\n{att_bullets}"

            bug_key = client.create_standalone_bug(eff_title, desc_md, fallback_key=fallback_key)

        if bug_dev.get("jira_account_id"):
            safe_assign_issue(client, bug_key, bug_dev["jira_account_id"])

        jira_att_res = safe_add_jira_attachments(client, bug_key, explicit_validated)
        uploaded_jira_atts = jira_att_res.get("uploaded") or []
        attachment_warning = jira_att_res.get("warning")

        qa_block = {
            "status": "planned",
            "qa_test_backend": eff_backend,
            "planned_at": now_iso(),
            "test_cases": [
                {
                    "id": "TC-001",
                    "ac_id": "AC-001",
                    "title": f"回歸驗證：{eff_title}",
                    "execution_type": exec_type_arg,
                    "recommended_by_ai": True,
                    "result": "not-executed",
                    "notes": eff_steps,
                }
            ],
        }
        qase_defect_info = None
        if eff_backend == "qase" and qase_client is not None:
            qase_client.sync_test_plan(
                jira_key=bug_key,
                story_title=eff_title,
                qa=qa_block,
                normalized_cases=qa_block["test_cases"],
            )
            qase_hashes, _, att_cache = qase_client.upload_attachments(explicit_validated, existing_cache={})
            if att_cache:
                qa_block["qase_attachment_cache"] = att_cache
            qase_actual = (
                f"Jira Standalone Bug: {bug_key} (Commit: {tested_commit})\n"
                f"Steps:\n{eff_steps}\n\nExpected:\n{eff_expected}\n\nActual:\n{eff_actual}"
            )
            qase_defect_info = qase_client.create_defect(
                title=f"[{bug_key}] {eff_title}",
                actual_result=qase_actual,
                severity=args.severity,
                attachment_hashes=qase_hashes,
            )

        bug_report_meta = {
            "severity": args.severity,
            "steps": eff_steps,
            "expected": eff_expected,
            "actual": eff_actual,
            "reported_commit": tested_commit,
        }
        if uploaded_jira_atts:
            bug_report_meta["attachments"] = uploaded_jira_atts
        if qase_defect_info is not None:
            if qase_defect_info.get("defect_id"):
                bug_report_meta["qase_defect_id"] = qase_defect_info["defect_id"]
            if qase_defect_info.get("attachments"):
                bug_report_meta["qase_attachment_hashes"] = qase_defect_info["attachments"]

        todo_status = config.get("status_mapping", {}).get("todo", "To Do")
        task_item = {
            "id": "TASK-001",
            "jira_subtask_key": None,
            "title": f"修復報修問題：{eff_title}",
            "ac_coverage": ["AC-001"],
            "depends_on": [],
            "files": [],
            "verification": "Run regression tests & verify reproduction steps",
            "status": "pending",
            "last_synced_jira_status": todo_status,
            "commit": None,
            "changed_files": [],
            "test_result": None,
            "completed_at": None,
        }
        if _is_member_claimed("developer", bug_dev):
            task_item["assignee"] = serialize_member_for_config(bug_dev, "developer")

        standalone_story = {
            "jira_key": bug_key,
            "issue_kind": "standalone_bug",
            "preserve_remote_description": True,
            "title": eff_title,
            "scope_in": eff_expected,
            "scope_out": "N/A",
            "change_surface": {"has_ui": False, "has_api": False, "has_data": False},
            "risk_level": "high" if args.severity == "blocker" else "medium",
            "acceptance_criteria": [{"id": "AC-001", "description": eff_expected}],
            "design": {"summary": f"Standalone Bug ({args.severity}): {eff_actual}"},
            "bug_report": bug_report_meta,
            "spec_status": "approved",
            "tasks": [task_item],
            "bugs": [],
            "qa": qa_block,
            "role_owners": {},
        }
        if _is_member_claimed("qa", qa_person):
            standalone_story["role_owners"]["qa"] = serialize_member_for_config(qa_person, "qa")
        if _is_member_claimed("developer", bug_dev):
            standalone_story["role_owners"]["developer"] = serialize_member_for_config(bug_dev, "developer")

        att_note_zh = f"（已附掛 {len(explicit_validated)} 個媒體證據檔）" if explicit_validated else ""
        att_note_en = f" ({len(explicit_validated)} media attachment(s) uploaded)" if explicit_validated else ""
        standalone_comment = (
            f"[獨立報修單已納管：{bug_key}]{att_note_zh} 嚴重度：`{args.severity}` — {eff_title}。請定位根因、補齊回歸測試並執行 `complete-task --key {bug_key}`。"
            if zh
            else f"[Standalone Bug Tracked: {bug_key}]{att_note_en} Severity: `{args.severity}` — {eff_title}. Fix root cause with regression test and run `complete-task --key {bug_key}`."
        )
        handoff_target_sel = (
            getattr(args, "to_assignee", None)
            or bug_dev.get("email")
            or bug_dev.get("jira_account_id")
            or None
        )
        handoff_info = execute_role_handoff(
            client=client,
            config=config,
            story=standalone_story,
            from_role="qa",
            to_role="developer",
            status_key="in_progress",
            comment_md=standalone_comment,
            from_person_override=qa_person,
            to_assignee=handoff_target_sel,
        )
        saved_path = save_active_story(repo_root, bug_key, standalone_story, client=client)
        out = {
            "ok": True,
            "action": "create-bug",
            "issue_kind": "standalone_bug",
            "adopted": adopt_existing,
            "jira_key": bug_key,
            "bug_key": bug_key,
            "tc_id": "TC-001",
            "assigned_to": bug_dev.get("name", ""),
            "severity": args.severity,
            "qa_test_backend": eff_backend,
            "attachments": uploaded_jira_atts,
            **_handoff_governance_fields(handoff_info),
            "handoff": handoff_info,
            "active_file": str(saved_path.relative_to(repo_root)),
        }
        if attachment_warning:
            out["attachment_warning"] = attachment_warning
        if qase_defect_info is not None:
            out["qase_defect"] = qase_defect_info
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # -----------------------------------------------------------------------
    # Branch B: Story Bug (`--key <STORY-KEY>`, planned TC failure OR exploratory ad-hoc bug)
    # -----------------------------------------------------------------------
    if not getattr(args, "key", None):
        print("ERROR: --key <STORY-KEY> is required (or pass --standalone for a standalone production Bug).", file=sys.stderr)
        return 1
    if not args.title or not args.steps or not args.expected or not args.actual:
        print("ERROR: --title, --steps, --expected, and --actual are required for create-bug.", file=sys.stderr)
        return 1

    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key, client=client)

    qa = story.setdefault("qa", {"status": "failed", "test_cases": []})
    tcs_list = qa.setdefault("test_cases", [])
    norm_tc_id = (args.tc_id or "").strip().upper()
    matched_tc = None
    inherited_paths = []
    if norm_tc_id:
        for c in tcs_list:
            if str(c.get("id") or "").strip().upper() == norm_tc_id:
                matched_tc = c
                for att_meta in (c.get("attachments") or []):
                    if isinstance(att_meta, dict) and att_meta.get("local_path"):
                        lp = Path(att_meta["local_path"])
                        if lp.is_file():
                            inherited_paths.append(str(lp))
                break

    combined_paths = inherited_paths + [vf["local_path"] for vf in explicit_validated]
    try:
        validated_files = validate_attachment_files(combined_paths, repo_root=repo_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    eff_backend = (
        getattr(args, "qa_backend", None)
        or qa.get("qa_test_backend")
        or config.get("qa_test_backend")
        or "jira-comment"
    ).strip().lower()
    qase_client = QaseClient(config, offline=args.offline, repo_root=repo_root) if eff_backend == "qase" else None
    if eff_backend == "qase" and not args.offline and not qase_client.has_credentials:
        print(
            "ERROR: qa_test_backend is 'qase', but Qase project_code or Qase API token is missing.",
            file=sys.stderr,
        )
        return 1

    exploratory = False
    if matched_tc is None:
        # Exploratory / Ad-Hoc Bug discovered outside the planned TC list -> append TC-EXP-### without polluting passing TCs!
        exploratory = True
        existing_tc_ids = {str(c.get("id") or "").strip().upper() for c in tcs_list if isinstance(c, dict)}
        if not norm_tc_id:
            exp_idx = 1
            while f"TC-EXP-{exp_idx:03d}" in existing_tc_ids:
                exp_idx += 1
            norm_tc_id = f"TC-EXP-{exp_idx:03d}"
        matched_tc = {
            "id": norm_tc_id,
            "ac_id": (args.ac_id or "EXPLORATORY").strip(),
            "title": args.title,
            "execution_type": exec_type_arg,
            "recommended_by_ai": False,
            "exploratory": True,
            "result": "fail",
            "notes": args.actual,
        }
        tcs_list.append(matched_tc)
        if eff_backend == "qase" and qase_client is not None:
            qase_client.sync_test_plan(
                jira_key=jira_key,
                story_title=story.get("title") or jira_key,
                qa=qa,
                normalized_cases=tcs_list,
            )

    qa_person = resolve_acting_person(config, "qa", client=client, story=story)
    bug_dev = _resolve_bug_developer(
        config=config,
        story=story,
        tc_id=norm_tc_id,
        ac_id=args.ac_id or "",
        to_assignee=getattr(args, "to_assignee", None),
    )

    bugs = story.setdefault("bugs", [])
    bug_idx = len(bugs) + 1
    tested_commit = args.commit or story.get("candidate_commit") or "unknown"
    if zh:
        desc_md = (
            f"關聯需求單：{jira_key}\n"
            f"回報人員：{format_person_badge('qa', qa_person)}\n"
            f"指派修復人員：{format_person_badge('developer', bug_dev)}\n"
            f"失敗測試案例：{norm_tc_id}\n"
            f"影響驗收條件：{args.ac_id or '無'}\n"
            f"嚴重等級：{args.severity}\n"
            f"驗證 Commit：{tested_commit}\n\n"
            f"### 重現步驟\n{args.steps}\n\n"
            f"### 預期結果\n{args.expected}\n\n"
            f"### 實際結果\n{args.actual}"
        )
        if validated_files:
            att_bullets = "\n".join(f"- `{vf['filename']}` ({vf['size']} bytes)" for vf in validated_files)
            desc_md += f"\n\n### 附件證據\n{att_bullets}"
    else:
        desc_md = (
            f"Linked Story: {jira_key}\n"
            f"Reported By: {format_person_badge('qa', qa_person)}\n"
            f"Assigned Developer: {format_person_badge('developer', bug_dev)}\n"
            f"Failed Test Case: {norm_tc_id}\n"
            f"Affected AC: {args.ac_id or 'N/A'}\n"
            f"Severity: {args.severity}\n"
            f"Tested Commit: {tested_commit}\n\n"
            f"### Reproduction Steps\n{args.steps}\n\n"
            f"### Expected Result\n{args.expected}\n\n"
            f"### Actual Result\n{args.actual}"
        )
        if validated_files:
            att_bullets = "\n".join(f"- `{vf['filename']}` ({vf['size']} bytes)" for vf in validated_files)
            desc_md += f"\n\n### Attachments\n{att_bullets}"

    bug_key = client.create_bug(jira_key, f"[{jira_key}][{norm_tc_id}] {args.title}", desc_md, bug_idx)
    if bug_dev.get("jira_account_id"):
        safe_assign_issue(client, bug_key, bug_dev["jira_account_id"])

    jira_att_res = safe_add_jira_attachments(client, bug_key, validated_files)
    uploaded_jira_atts = jira_att_res.get("uploaded") or []
    attachment_warning = jira_att_res.get("warning")

    # Conditional Qase sync when qa_test_backend == 'qase'
    qase_defect_info = None
    if eff_backend == "qase" and qase_client is not None:
        att_cache = dict(qa.get("qase_attachment_cache") or {})
        qase_hashes, qase_att_records, att_cache = qase_client.upload_attachments(
            validated_files, existing_cache=att_cache
        )
        if att_cache:
            qa["qase_attachment_cache"] = att_cache
        qase_actual = (
            f"Jira Bug: {bug_key} (Story: {jira_key}, TC: {norm_tc_id}, AC: {args.ac_id or 'N/A'}, Commit: {tested_commit})\n"
            f"Steps:\n{args.steps}\n\nExpected:\n{args.expected}\n\nActual:\n{args.actual}"
        )
        qase_defect_info = qase_client.create_defect(
            title=f"[{jira_key}][{norm_tc_id}] {args.title}",
            actual_result=qase_actual,
            severity=args.severity,
            attachment_hashes=qase_hashes,
        )
        if matched_tc is not None and qase_hashes:
            prior_tc_hashes = [h for h in (matched_tc.get("qase_attachment_hashes") or []) if h]
            merged_tc_hashes = list(dict.fromkeys(prior_tc_hashes + qase_hashes))
            new_hashes_added = set(merged_tc_hashes) != set(prior_tc_hashes)
            matched_tc["qase_attachment_hashes"] = merged_tc_hashes
            run_id = qa.get("qase_last_run_id")
            case_id = matched_tc.get("qase_case_id")
            if new_hashes_added and run_id and case_id:
                try:
                    qase_client.update_run_case_attachments(
                        run_id=int(run_id),
                        case_id=int(case_id),
                        status_str="failed",
                        comment=f"[{bug_key}] {args.title}",
                        attachment_hashes=merged_tc_hashes,
                    )
                except Exception:
                    pass

    todo_status = config.get("status_mapping", {}).get("todo", "To Do")
    bug_record = {
        "bug_key": bug_key,
        "tc_id": norm_tc_id,
        "ac_id": args.ac_id or "",
        "title": args.title,
        "severity": args.severity,
        "steps": args.steps,
        "expected": args.expected,
        "actual": args.actual,
        "reported_commit": tested_commit,
        "status": "open",
        "last_synced_jira_status": todo_status,
        "created_at": now_iso(),
    }
    if uploaded_jira_atts:
        bug_record["attachments"] = uploaded_jira_atts
    if attachment_warning:
        bug_record["attachment_warning"] = attachment_warning
    if qase_defect_info is not None:
        if qase_defect_info.get("defect_id"):
            bug_record["qase_defect_id"] = qase_defect_info["defect_id"]
        if qase_defect_info.get("attachments"):
            bug_record["qase_attachment_hashes"] = qase_defect_info["attachments"]
    if _is_member_claimed("developer", bug_dev):
        bug_record["assignee"] = serialize_member_for_config(bug_dev, "developer")
    bugs.append(bug_record)

    # Mark matching TC as fail and QA status as failed
    qa["status"] = "failed"
    for c in qa.get("test_cases") or []:
        if str(c.get("id") or "").strip().upper() == norm_tc_id:
            c["result"] = "fail"

    att_note_zh = f"（已附掛 {len(validated_files)} 個媒體證據檔）" if validated_files else ""
    att_note_en = f" ({len(validated_files)} media attachment(s) uploaded)" if validated_files else ""
    bug_comment = (
        f"[缺陷單已建立：{bug_key}]{att_note_zh} 測試案例 `{norm_tc_id}` 驗證失敗（嚴重度：`{args.severity}`）：{args.title}。請修復並執行 `complete-task --task-id {bug_key}`。"
        if zh
        else f"[Bug Created: {bug_key}]{att_note_en} Test case `{norm_tc_id}` failed (Severity: `{args.severity}`): {args.title}. Remediate and run `complete-task --task-id {bug_key}`."
    )
    handoff_target_sel = (
        getattr(args, "to_assignee", None)
        or bug_dev.get("email")
        or bug_dev.get("jira_account_id")
        or None
    )
    handoff_info = execute_role_handoff(
        client=client,
        config=config,
        story=story,
        from_role="qa",
        to_role="developer",
        status_key="in_progress",
        comment_md=bug_comment,
        from_person_override=qa_person,
        to_assignee=handoff_target_sel,
    )

    saved_path = save_active_story(repo_root, jira_key, story, client=client)
    out = {
        "ok": True,
        "action": "create-bug",
        "jira_key": jira_key,
        "bug_key": bug_key,
        "tc_id": norm_tc_id,
        "exploratory": exploratory,
        "assigned_to": bug_dev.get("name", ""),
        "severity": args.severity,
        "qa_test_backend": eff_backend,
        "attachments": uploaded_jira_atts,
        **_handoff_governance_fields(handoff_info),
        "handoff": handoff_info,
        "active_file": str(saved_path.relative_to(repo_root)),
    }
    if attachment_warning:
        out["attachment_warning"] = attachment_warning
    if qase_defect_info is not None:
        out["qase_defect"] = qase_defect_info
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 8b: triage-bug (Station 3b: team-qa Mode 4 — Bug Triage)
# ---------------------------------------------------------------------------

def cmd_triage_bug(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="qa")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1

    jira_key = validate_jira_key(args.key)

    try:
        res_slug, res_display = normalize_triage_resolution(getattr(args, "resolution", "") or "")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    reason = _clean_optional_text(getattr(args, "reason", None))
    if not reason:
        print("ERROR: --reason is required and must be non-empty.", file=sys.stderr)
        return 1

    decided_by = _clean_optional_text(getattr(args, "decided_by", None))
    if not decided_by:
        print("ERROR: --decided-by is required (human / PO / Dev / QA decision maker authorization).", file=sys.stderr)
        return 1

    raw_dup = _clean_optional_text(getattr(args, "duplicate_of", None))
    duplicate_of = ""
    if res_slug == "duplicate":
        if not raw_dup:
            print("ERROR: --duplicate-of <EXISTING-JIRA-KEY> is required when --resolution is 'duplicate'.", file=sys.stderr)
            return 1
        duplicate_of = validate_jira_key(raw_dup)
    elif raw_dup:
        duplicate_of = validate_jira_key(raw_dup)

    story = load_active_story(repo_root, jira_key, client=client)
    is_standalone = story.get("issue_kind") == "standalone_bug"

    raw_bug_id = _clean_optional_text(getattr(args, "bug_id", None))
    target_bug = None
    if is_standalone:
        if raw_bug_id and raw_bug_id.upper() != jira_key.upper():
            # Allow matching a linked bug entry if one exists, otherwise reject unknown bug ID
            for b in (story.get("bugs") or []):
                if isinstance(b, dict) and (
                    str(b.get("bug_key") or "").strip().upper() == raw_bug_id.upper()
                    or str(b.get("id") or "").strip().upper() == raw_bug_id.upper()
                ):
                    target_bug = b
                    break
            if target_bug is None:
                print(
                    f"ERROR: Bug '{raw_bug_id}' does not match standalone Bug '{jira_key}'.",
                    file=sys.stderr,
                )
                return 1
        bug_ref_id = target_bug.get("bug_key") if target_bug is not None else jira_key
    else:
        if not raw_bug_id:
            print("ERROR: --bug-id <BUG-ID-OR-JIRA-BUG-KEY> is required for Story-linked Bugs.", file=sys.stderr)
            return 1
        for b in (story.get("bugs") or []):
            if isinstance(b, dict) and (
                str(b.get("bug_key") or "").strip().upper() == raw_bug_id.upper()
                or str(b.get("id") or "").strip().upper() == raw_bug_id.upper()
            ):
                target_bug = b
                break
        if target_bug is None:
            print(f"ERROR: Bug '{raw_bug_id}' not found in Story {jira_key}.", file=sys.stderr)
            return 1
        bug_ref_id = str(target_bug.get("bug_key") or target_bug.get("id") or raw_bug_id).strip().upper()

    raw_waive = _clean_optional_text(getattr(args, "waive_tcs", None))
    waive_tc_ids = []
    for part in raw_waive.split(","):
        p_clean = part.strip().upper()
        if p_clean and p_clean not in waive_tc_ids:
            waive_tc_ids.append(p_clean)

    raw_disp = _clean_optional_text(getattr(args, "tc_disposition", None)).lower()
    if raw_disp and raw_disp not in ("waived", "not-applicable"):
        print(
            f"ERROR: Invalid --tc-disposition '{raw_disp}'. Allowed: 'waived' or 'not-applicable' (never 'pass').",
            file=sys.stderr,
        )
        return 1
    tc_disposition = raw_disp or "waived"

    if waive_tc_ids:
        if res_slug == "fixed":
            print(
                "ERROR: --waive-tcs cannot be used with --resolution 'fixed'. Fix the Bug and re-verify TCs instead.",
                file=sys.stderr,
            )
            return 1
        qa_tcs = ((story.get("qa") or {}).get("test_cases")) or []
        tc_by_id = {
            str(c.get("id") or "").strip().upper(): c
            for c in qa_tcs
            if isinstance(c, dict) and c.get("id")
        }
        unknown_tcs = [tid for tid in waive_tc_ids if tid not in tc_by_id]
        if unknown_tcs:
            print(
                f"ERROR: Unknown test case ID(s) in --waive-tcs: {', '.join(unknown_tcs)} (planned: {sorted(tc_by_id.keys())}).",
                file=sys.stderr,
            )
            return 1
        passing_tcs = [tid for tid in waive_tc_ids if str(tc_by_id[tid].get("result") or "").strip().lower() == "pass"]
        if passing_tcs:
            print(
                f"ERROR: Cannot waive already-passing test case(s): {', '.join(passing_tcs)}.",
                file=sys.stderr,
            )
            return 1

    qa_person = resolve_acting_person(config, "qa", client=client, story=story)
    decided_at = now_iso()
    done_status = config.get("status_mapping", {}).get("done", "Done")
    triage_record = {
        "resolution": res_slug,
        "resolution_display": res_display,
        "reason": reason,
        "decided_by": decided_by,
        "duplicate_of": duplicate_of,
        "decided_at": decided_at,
    }

    zh = is_zh_tw(config)
    transition_res = None
    reopen_warning = ""
    waived_tc_records = []

    if res_slug in NON_CODE_FIX_RESOLUTIONS:
        if target_bug is not None:
            target_bug["status"] = "closed"
            target_bug["jira_status"] = done_status
            target_bug["jira_resolution"] = res_display
            target_bug["last_synced_jira_status"] = done_status
            target_bug["triage"] = triage_record
            target_bug["closed_at"] = decided_at
            jira_issue_to_update = str(target_bug.get("bug_key") or bug_ref_id)
            bug_status = "closed"
        else:
            story["triage"] = triage_record
            story["triage_closed"] = True
            story["jira_status"] = done_status
            story["jira_resolution"] = res_display
            story["closed_at"] = decided_at
            bug_rep = story.setdefault("bug_report", {})
            bug_rep["status"] = "closed"
            bug_rep["triage"] = triage_record
            jira_issue_to_update = jira_key
            bug_status = "closed"

        if waive_tc_ids:
            qa_obj = story.setdefault("qa", {"status": "planned", "test_cases": []})
            for c in qa_obj.get("test_cases") or []:
                cid = str(c.get("id") or "").strip().upper()
                if cid in waive_tc_ids:
                    # NEVER mark a waived/not-applicable TC as 'pass'
                    c["result"] = tc_disposition
                    c["waived_bug_id"] = bug_ref_id
                    c["triage_resolution"] = res_slug
                    c["waived_by"] = decided_by
                    c["waived_reason"] = reason
                    c["waived_at"] = decided_at
                    waive_note = f"[{tc_disposition} via {bug_ref_id} ({res_display}) by {decided_by}: {reason}]"
                    c["notes"] = f"{c['notes']} {waive_note}".strip() if c.get("notes") else waive_note
                    waived_tc_records.append({
                        "id": cid,
                        "result": tc_disposition,
                        "waived_bug_id": bug_ref_id,
                        "triage_resolution": res_slug,
                        "waived_by": decided_by,
                        "waived_reason": reason,
                        "waived_at": decided_at,
                    })

        # Recompute Story QA counts & status after bug closure / TC waiver
        qa_obj = story.get("qa")
        if isinstance(qa_obj, dict) and (qa_obj.get("test_cases") or []):
            tcs = qa_obj.get("test_cases") or []
            counts = {"pass": 0, "fail": 0, "blocked": 0, "not-executed": 0, "waived": 0, "not-applicable": 0}
            for c in tcs:
                st = c.get("result", "not-executed")
                counts[st] = counts.get(st, 0) + 1
            qa_obj["counts"] = counts
            open_bugs = [b for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]
            uncovered_acs = find_uncovered_story_acs(story, tcs)
            invalid_waivers = [
                c["id"] for c in tcs
                if c.get("result") in ("waived", "not-applicable") and not _is_tc_waiver_valid(c, story)
            ]
            if counts["fail"] > 0 or counts["blocked"] > 0 or invalid_waivers:
                qa_obj["status"] = "failed"
            elif counts["not-executed"] > 0 or uncovered_acs:
                qa_obj["status"] = "incomplete"
            elif open_bugs:
                qa_obj["status"] = "blocked-by-open-bugs"
            else:
                qa_obj["status"] = "passed"
                if not qa_obj.get("tested_commit"):
                    qa_obj["tested_commit"] = story.get("candidate_commit") or _detect_git_head_commit(repo_root) or "HEAD"

        if zh:
            comment_lines = [
                f"[缺陷分診結案 (Bug Triage)：{bug_ref_id} — {res_display}]",
                f"- **處置結論 (Resolution)**：`{res_display}` (`{res_slug}`)",
                f"- **判定理由 / 規格依據 (Reason)**：{reason}",
                f"- **授權決策人 (Decided By)**：{decided_by}",
                f"- **記錄人員**：{format_person_badge('qa', qa_person)}",
            ]
            if duplicate_of:
                comment_lines.append(f"- **重複單據 (Duplicate Of)**：`{duplicate_of}`")
            if waived_tc_records:
                tc_list_str = ", ".join(f"`{w['id']}` -> `{w['result']}`" for w in waived_tc_records)
                comment_lines.append(f"- **測試案例處置 (TC Disposition)**：{tc_list_str}（未偽標為 `pass`）")
        else:
            comment_lines = [
                f"[Bug Triage Closed: {bug_ref_id} — {res_display}]",
                f"- **Resolution**: `{res_display}` (`{res_slug}`)",
                f"- **Reason**: {reason}",
                f"- **Decided By**: {decided_by}",
                f"- **Recorded By**: {format_person_badge('qa', qa_person)}",
            ]
            if duplicate_of:
                comment_lines.append(f"- **Duplicate Of**: `{duplicate_of}`")
            if waived_tc_records:
                tc_list_str = ", ".join(f"`{w['id']}` -> `{w['result']}`" for w in waived_tc_records)
                comment_lines.append(f"- **TC Disposition**: {tc_list_str} (never marked `pass`)")

        client.add_comment(jira_issue_to_update, "\n".join(comment_lines))
        transition_res = client.transition_issue(jira_issue_to_update, done_status, resolution_name=res_display)

        # Resolve linked Qase defect gracefully if present
        qase_def_id = (
            target_bug.get("qase_defect_id")
            if target_bug is not None
            else (story.get("bug_report") or {}).get("qase_defect_id")
        )
        if qase_def_id:
            eff_qa_backend = (
                ((story.get("qa") or {}).get("qa_test_backend"))
                or config.get("qa_test_backend")
                or "jira-comment"
            ).strip().lower()
            if eff_qa_backend == "qase":
                try:
                    qase_client = QaseClient(config, offline=args.offline, repo_root=repo_root)
                    q_res = qase_client.resolve_defect(int(qase_def_id))
                    if q_res.get("resolved"):
                        if target_bug is not None:
                            target_bug["qase_defect_resolved"] = True
                        else:
                            story.setdefault("bug_report", {})["qase_defect_resolved"] = True
                except Exception:
                    pass
    else:
        # res_slug == "fixed": record triage note and reopen (both locally and in Jira) if previously closed
        if target_bug is not None:
            was_closed = (
                target_bug.get("status") in ("completed", "closed")
                or _is_jira_status_done(target_bug.get("jira_status", ""), config)
            )
            target_bug["status"] = "open"
            target_bug.pop("closed_at", None)
            target_bug["triage"] = triage_record
            if isinstance(story.get("qa"), dict) and story["qa"].get("status") == "passed":
                story["qa"]["status"] = "blocked-by-open-bugs"
            jira_issue_to_update = str(target_bug.get("bug_key") or bug_ref_id)
            bug_status = "open"
        else:
            was_closed = (
                bool(story.get("triage_closed"))
                or _is_jira_status_done(story.get("jira_status", ""), config)
            )
            story["triage"] = triage_record
            story["triage_closed"] = False
            story.pop("closed_at", None)
            bug_rep = story.setdefault("bug_report", {})
            bug_rep["triage"] = triage_record
            bug_rep["status"] = "open"
            for t_item in story.get("tasks") or []:
                if isinstance(t_item, dict) and t_item.get("status") == "completed":
                    t_item["status"] = "pending"
            jira_issue_to_update = jira_key
            bug_status = "open"

        if zh:
            comment_lines = [
                f"[缺陷分診紀錄 (Bug Triage)：{bug_ref_id} — 確認需修復 ({res_display})]",
                f"- **處置結論 (Resolution)**：`{res_display}` (`{res_slug}`)",
                f"- **分析說明 (Reason)**：{reason}",
                f"- **決策人 (Decided By)**：{decided_by}",
                f"- **後續流程**：本缺陷維持開啟狀態，請依標準流程進入 `team-dev (implement)` 修復並執行 `complete-task`（附上 `--root-cause` 與 `--solution`），再經 `team-review` 與 `team-qa (verify)` 複測結案。",
            ]
        else:
            comment_lines = [
                f"[Bug Triage Recorded: {bug_ref_id} — Confirmed Fix Required ({res_display})]",
                f"- **Resolution**: `{res_display}` (`{res_slug}`)",
                f"- **Reason**: {reason}",
                f"- **Decided By**: {decided_by}",
                f"- **Next Step**: Bug remains open; proceed via standard `team-dev (implement)` (`complete-task` with `--root-cause` and `--solution`), `team-review`, and `team-qa (verify)` re-verification.",
            ]
        client.add_comment(jira_issue_to_update, "\n".join(comment_lines))

        if was_closed and jira_issue_to_update:
            in_progress_status = (config.get("status_mapping") or {}).get("in_progress") or "In Progress"
            todo_status = (config.get("status_mapping") or {}).get("todo") or "To Do"
            reopened_status = ""
            if not client.is_live:
                transition_res = client.transition_issue(jira_issue_to_update, in_progress_status)
                reopened_status = in_progress_status
            else:
                candidates = [in_progress_status]
                if todo_status and todo_status.strip().lower() != in_progress_status.strip().lower():
                    candidates.append(todo_status)
                last_err = ""
                for cand_status in candidates:
                    try:
                        cand_res = client.transition_issue(jira_issue_to_update, cand_status)
                    except Exception as exc:
                        last_err = str(exc)
                        transition_res = {"mode": "live", "transitioned_to": None, "warning": last_err}
                        continue
                    transition_res = cand_res
                    if cand_res.get("transitioned_to"):
                        reopened_status = cand_status
                        break
                    last_err = str(cand_res.get("warning") or f"Transition '{cand_status}' unavailable")
                if not reopened_status:
                    reopen_warning = (
                        f"Failed to transition reopened Bug {jira_issue_to_update} out of Done "
                        f"(tried {', '.join(candidates)}: {last_err}); local jira_status preserved as-is."
                    )
            if reopened_status:
                if target_bug is not None:
                    target_bug["jira_status"] = reopened_status
                    target_bug["last_synced_jira_status"] = reopened_status
                    target_bug["jira_resolution"] = ""
                else:
                    story["jira_status"] = reopened_status
                    story["jira_resolution"] = ""

    saved_path = save_active_story(repo_root, jira_key, story, client=client)
    out = {
        "ok": True,
        "action": "triage-bug",
        "jira_key": jira_key,
        "bug_id": bug_ref_id,
        "issue_kind": story.get("issue_kind", "story"),
        "resolution": res_slug,
        "resolution_display": res_display,
        "reason": reason,
        "decided_by": decided_by,
        "duplicate_of": duplicate_of,
        "bug_status": bug_status,
        "triage_closed": bool(story.get("triage_closed")) if is_standalone and target_bug is None else (bug_status == "closed"),
        "triage": triage_record,
        "waived_tcs": waived_tc_records,
        "tc_disposition": tc_disposition if waive_tc_ids else None,
        "transition": transition_res,
        "active_file": str(saved_path.relative_to(repo_root)),
    }
    if reopen_warning:
        out["warning"] = reopen_warning
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 9: close-story (Station 4: team-release)
# ---------------------------------------------------------------------------

def cmd_close_story(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline, repo_root=repo_root, role="release_manager")
    if not ensure_live_or_explicit_offline(client, config, args.offline):
        return 1
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key, client=client)

    commit_sha = (args.commit or story.get("candidate_commit") or _detect_git_head_commit(repo_root) or "").strip()
    version = (args.version or "v1.0.0").strip()
    unmet_gates = []

    if story.get("spec_status") != "approved":
        unmet_gates.append(f"Spec status is '{story.get('spec_status')}' (must be 'approved').")

    tasks = story.get("tasks") or []
    if not tasks:
        unmet_gates.append("No development Sub-tasks recorded.")
    else:
        incomplete_tasks = [t["id"] for t in tasks if t.get("status") != "completed"]
        if incomplete_tasks:
            unmet_gates.append(f"Incomplete development tasks: {', '.join(incomplete_tasks)}.")

    review = story.get("review") or {}
    if review.get("status") != "approved":
        unmet_gates.append(f"Code review status is '{review.get('status')}' (must be 'approved').")
    elif review.get("reviewed_commit") and review.get("reviewed_commit") != commit_sha:
        unmet_gates.append(
            f"Candidate commit '{commit_sha}' does not match reviewed commit '{review.get('reviewed_commit')}'."
        )

    qa = story.get("qa") or {}
    test_cases = qa.get("test_cases") or []
    uncovered_acs = find_uncovered_story_acs(story, test_cases)
    invalid_waived_tcs = [
        c["id"] for c in test_cases
        if c.get("result") in ("waived", "not-applicable") and not _is_tc_waiver_valid(c, story)
    ]
    non_passing_tcs = [
        c["id"] for c in test_cases
        if not _is_tc_satisfied(c, story) and c["id"] not in invalid_waived_tcs
    ]
    if qa.get("status") != "passed":
        unmet_gates.append(f"QA verification status is '{qa.get('status')}' (must be 'passed').")
    elif qa.get("tested_commit") and qa.get("tested_commit") != commit_sha:
        unmet_gates.append(
            f"Candidate commit '{commit_sha}' does not match QA tested commit '{qa.get('tested_commit')}'."
        )
    if uncovered_acs:
        unmet_gates.append(f"Uncovered Story ACs in QA test cases: {', '.join(uncovered_acs)}.")
    if invalid_waived_tcs:
        unmet_gates.append(
            f"Waived/not-applicable QA test cases missing valid Bug Triage audit metadata: {', '.join(invalid_waived_tcs)}."
        )
    if non_passing_tcs:
        unmet_gates.append(f"Non-passing QA test cases: {', '.join(non_passing_tcs)}.")

    open_bugs = [b.get("bug_key") for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]
    if open_bugs:
        unmet_gates.append(f"Open Bugs remain unresolved: {', '.join(open_bugs)}.")

    if unmet_gates:
        print(
            json.dumps({
                "ok": False,
                "action": "close-story",
                "jira_key": jira_key,
                "blocked": True,
                "unmet_gates": unmet_gates,
            }, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 1

    ac_count = len(story.get("acceptance_criteria") or [])
    tc_count = len(test_cases)
    passed_tc_count = sum(1 for c in test_cases if c.get("result") == "pass")
    waived_tcs = [
        c for c in test_cases
        if c.get("result") in ("waived", "not-applicable") and _is_tc_waiver_valid(c, story)
    ]
    triaged_bugs = [
        b for b in (story.get("bugs") or [])
        if isinstance(b, dict)
        and isinstance(b.get("triage"), dict)
        and str(b["triage"].get("resolution") or "").strip().lower() in NON_CODE_FIX_RESOLUTIONS
    ]
    triage_disclosure = {
        "has_risk_acceptance": bool(triaged_bugs or waived_tcs),
        "triaged_bugs": [
            {
                "bug_key": b.get("bug_key"),
                "resolution": (b.get("triage") or {}).get("resolution"),
                "resolution_display": (b.get("triage") or {}).get("resolution_display"),
                "decided_by": (b.get("triage") or {}).get("decided_by"),
                "reason": (b.get("triage") or {}).get("reason"),
                "duplicate_of": (b.get("triage") or {}).get("duplicate_of") or "",
            }
            for b in triaged_bugs
        ],
        "waived_tcs": [
            {
                "id": c.get("id"),
                "result": c.get("result"),
                "waived_bug_id": c.get("waived_bug_id"),
                "triage_resolution": c.get("triage_resolution"),
                "waived_by": c.get("waived_by"),
                "waived_reason": c.get("waived_reason"),
            }
            for c in waived_tcs
        ],
    }

    qa_breakdown = {"automated": 0, "external": 0, "manual": 0}
    for c in test_cases:
        et = c.get("execution_type", "automated")
        qa_breakdown[et] = qa_breakdown.get(et, 0) + 1

    rm_person = resolve_acting_person(config, "release_manager", client=client, story=story)
    if rm_person.get("jira_account_id"):
        safe_assign_issue(client, jira_key, rm_person["jira_account_id"])

    if is_zh_tw(config):
        tc_summary_zh = (
            f"{passed_tc_count} 項通過 / {len(waived_tcs)} 項受控豁免或不適用（共 {tc_count} 項）"
            if waived_tcs
            else f"{tc_count} 項 QA 測試案例全數通過"
        )
        release_lines = [
            f"[站點 4：發佈與結案摘要 — {version}]",
            f"- **發佈負責人**：{format_person_badge('release_manager', rm_person)}",
            f"- **需求單號**：`{jira_key}`",
            f"- **發佈版本 / Tag**：`{version}`",
            f"- **驗證通過 Commit**：`{commit_sha}`",
            f"- **交付覆蓋率**：共 {ac_count} 項 AC、{len(tasks)} 項開發子任務完成、{tc_summary_zh} "
            f"（automated={qa_breakdown['automated']}, external={qa_breakdown['external']}, manual={qa_breakdown['manual']}）、0 個未結案 Bug",
            f"- **發佈說明 (Release Notes)**：{args.notes or '已完整通過四站式研發品質門檻並完成發佈結案。'}",
        ]
        if triage_disclosure["has_risk_acceptance"]:
            release_lines.append("")
            release_lines.append("### Bug Triage & Risk Acceptance Disclosure (缺陷分診與風險接受揭露)")
            for tb in triage_disclosure["triaged_bugs"]:
                dup_str = f"（Duplicate Of: `{tb['duplicate_of']}`）" if tb["duplicate_of"] else ""
                release_lines.append(
                    f"- **Bug `{tb['bug_key']}`**：`{tb['resolution_display']}`{dup_str} — 決策人：{tb['decided_by']}；理由：{tb['reason']}"
                )
            for wt in triage_disclosure["waived_tcs"]:
                release_lines.append(
                    f"- **TC `{wt['id']}`**：`{wt['result']}`（關聯 `{wt['waived_bug_id']}` / `{wt['triage_resolution']}`）— 決策人：{wt['waived_by']}；理由：{wt['waived_reason']}"
                )
        release_md = "\n".join(release_lines)
    else:
        tc_summary_en = (
            f"{passed_tc_count} Passed / {len(waived_tcs)} Waived or Not-Applicable ({tc_count} total)"
            if waived_tcs
            else f"{tc_count} QA Test Cases Passed"
        )
        release_lines = [
            f"[Station 4: Release & Closure — {version}]",
            f"- **Released By**: {format_person_badge('release_manager', rm_person)}",
            f"- Jira Story: `{jira_key}`",
            f"- Release Version / Tag: `{version}`",
            f"- Verified Commit: `{commit_sha}`",
            f"- Coverage: {ac_count} ACs, {len(tasks)} Sub-tasks Done, {tc_summary_en} "
            f"(automated={qa_breakdown['automated']}, external={qa_breakdown['external']}, manual={qa_breakdown['manual']}), 0 Open Bugs",
            f"- Release Notes: {args.notes or 'Released and verified through 4-station workflow.'}",
        ]
        if triage_disclosure["has_risk_acceptance"]:
            release_lines.append("")
            release_lines.append("### Bug Triage & Risk Acceptance Disclosure")
            for tb in triage_disclosure["triaged_bugs"]:
                dup_str = f" (Duplicate Of: `{tb['duplicate_of']}`)" if tb["duplicate_of"] else ""
                release_lines.append(
                    f"- **Bug `{tb['bug_key']}`**: `{tb['resolution_display']}`{dup_str} — Decided By: {tb['decided_by']}; Reason: {tb['reason']}"
                )
            for wt in triage_disclosure["waived_tcs"]:
                release_lines.append(
                    f"- **TC `{wt['id']}`**: `{wt['result']}` (Bug `{wt['waived_bug_id']}` / `{wt['triage_resolution']}`) — Decided By: {wt['waived_by']}; Reason: {wt['waived_reason']}"
                )
        release_md = "\n".join(release_lines)

    done_status = config.get("status_mapping", {}).get("done", "Done")
    client.add_comment(jira_key, release_md)
    trans_res = client.transition_issue(jira_key, done_status)

    resolved_dependents = []
    scan_dirs = [get_active_dir(repo_root)]
    main_root = _resolve_main_worktree_root(repo_root)
    if main_root:
        main_active_dir = get_active_dir(main_root)
        if main_active_dir != scan_dirs[0]:
            scan_dirs.append(main_active_dir)

    seen_sib_keys = set()
    for s_dir in scan_dirs:
        if not s_dir.exists():
            continue
        for sib_file in sorted(s_dir.glob("*.json")):
            sib_key = sib_file.stem.upper()
            if sib_key == jira_key or sib_key in seen_sib_keys:
                continue
            try:
                sib_data = json.loads(sib_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(sib_data, dict):
                continue
            sib_deps = [str(d).strip().upper() for d in (sib_data.get("depends_on_keys") or []) if str(d).strip()]
            if jira_key not in sib_deps:
                continue
            seen_sib_keys.add(sib_key)
            res_list = [str(r).strip().upper() for r in (sib_data.get("resolved_depends_on_keys") or []) if str(r).strip()]
            if jira_key not in res_list:
                res_list.append(jira_key)
                sib_data["resolved_depends_on_keys"] = res_list
                if s_dir == scan_dirs[0]:
                    save_active_story(repo_root, sib_key, sib_data, client=client, sync_remote=True)
                else:
                    sib_data["updated_at"] = now_iso()
                    sib_file.write_text(json.dumps(sib_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    if client.is_live:
                        client.sync_state_snapshot(sib_key, sib_data)
            resolved_dependents.append(sib_key)

    active_file = get_active_dir(repo_root) / f"{jira_key}.json"
    if active_file.exists():
        active_file.unlink()
    if main_root:
        main_active_file = get_active_dir(main_root) / f"{jira_key}.json"
        if main_active_file.exists():
            try:
                main_active_file.unlink()
            except Exception:
                pass

    release_checklist_reminders = _build_release_checklist_reminders(config)
    print(json.dumps({
        "ok": True,
        "action": "close-story",
        "jira_key": jira_key,
        "version": version,
        "commit": commit_sha,
        "jira_status": done_status,
        "qa_breakdown": qa_breakdown,
        "triage_disclosure": triage_disclosure,
        "release_checklist_reminders": release_checklist_reminders,
        "release_summary": release_md,
        "transition": trans_res,
        "resolved_dependent_stories": resolved_dependents,
        "active_file_removed": True,
    }, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Argument Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jira_sync.py",
        description="Unified Jira & Local Active State CLI for Epic/Sprint planning and the 4-Station Small-Team Workflow.",
    )
    p.add_argument("--repo-root", default=".", help="Path to repository root (default: current directory)")
    p.add_argument("--offline", action="store_true", help="Force local/offline mode even if Jira env vars are set")

    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument("--offline", dest="_sub_offline", action="store_true", default=False, help=argparse.SUPPRESS)

    sub = p.add_subparsers(dest="subcommand", required=True)

    # 1. init
    p_init = sub.add_parser("init", parents=[sub_common], help="Initialize .ai-workflow/team-config.json, gitignored credentials.json, .gitignore, and engineering-standards.md")
    p_init.add_argument("--inspect", action="store_true", help="Inspect current initialization state and return required user inquiry questions without modifying files")
    p_init.add_argument("--auth-only", action="store_true", help="Collaborator mode: bind local gitignored credentials.json and optionally claim role(s)")
    p_init.add_argument("--role", help="Comma-separated role(s) owned by the current user (e.g. 'pm,tech_lead,developer'); leaves remaining roles unclaimed for teammates")
    p_init.add_argument("--specialty", default=None, help="Optional specialty tag for multi-member roles (e.g. 'backend', 'frontend', 'mobile', 'devops')")
    p_init.add_argument("--language", choices=["zh-TW", "en"], default=None, help="Default prose language for Jira comments and descriptions (default: zh-TW)")
    p_init.add_argument("--commit-policy", choices=list(VALID_COMMIT_POLICIES), default=None, help="Commit confirmation policy ('confirm_before_commit' default or 'auto_on_test_pass' opt-in)")
    p_init.add_argument("--commit-message-pattern", default=None, help="Optional regex for non-blocking commit message format validation in complete-task")
    p_init.add_argument("--qa-handoff-mode", choices=list(VALID_QA_HANDOFF_MODES), default=None, help="QA handoff mode ('immediate' default or 'gated_by_deploy' for multi-environment CI/CD decoupling)")
    p_init.add_argument("--status-dev-done", default=None, help="Optional Jira status name for status_mapping.dev_done (used when qa_handoff_mode='gated_by_deploy')")
    p_init.add_argument("--integration-branch", default=None, help="Optional delivery_pipeline.integration_branch (e.g. 'release', 'develop')")
    p_init.add_argument("--production-branch", default=None, help="Optional delivery_pipeline.production_branch (e.g. 'main', 'master')")
    p_init.add_argument("--qa-tag-prefix", default=None, help="Optional delivery_pipeline.environments.qa tag prefix (e.g. 'qa-')")
    p_init.add_argument("--uat-tag-prefix", default=None, help="Optional delivery_pipeline.environments.uat tag prefix (e.g. 'uat-')")
    p_init.add_argument("--jira-deployment-sync", nargs="?", const="true", default=None, help="Optional delivery_pipeline.jira_deployment_sync boolean flag (true/false)")
    p_init.add_argument("--name", help="Display name for the role(s) claimed via --role (auto-resolved from Jira /myself if omitted)")
    p_init.add_argument("-i", "--interactive", action="store_true", help="Prompt interactively for Jira URL, project key, account email, API token, and role mappings")
    p_init.add_argument("--project-key", default=None, help="Jira project key (e.g. PROJ)")
    p_init.add_argument("--jira-board-id", default=None, help="Optional Jira Scrum board id used by team-plan")
    p_init.add_argument("--jira-url", default=None, help="Jira base URL (e.g. https://org.atlassian.net)")
    p_init.add_argument("--jira-email", default=None, help="Jira account email (stored in gitignored .ai-workflow/credentials.json)")
    p_init.add_argument("--jira-token", default=None, help="Jira API token (stored in gitignored .ai-workflow/credentials.json, mode 0600)")
    p_init.add_argument("--roles-json", help="JSON string or file mapping roles to people/members (optional api_token fields are extracted to credentials.json)")
    p_init.add_argument("--role-tokens-json", help="JSON string or file mapping roles to Jira API tokens (stored in gitignored .ai-workflow/credentials.json)")
    p_init.add_argument("--status-mapping-json", help="JSON string or file mapping workflow states to Jira statuses")
    p_init.add_argument("--issue-type-mapping-json", help="JSON string or file mapping story/subtask/bug to Jira issue types")
    p_init.add_argument("--qa-backend", choices=["jira-comment", "qase"], help="Optional remembered QA test backend ('jira-comment' or 'qase')")
    p_init.add_argument("--qase-project-code", help="Optional Qase project code (e.g. AP)")
    p_init.add_argument("--qase-token", help="Optional Qase API token (stored in gitignored .ai-workflow/credentials.json)")
    p_init.add_argument("--qase-base-url", help="Optional Qase API v1 base URL (default: https://api.qase.io/v1)")
    p_init.add_argument("--qase-app-url", help="Optional Qase web app URL (default: https://app.qase.io)")
    p_init.add_argument("--qase-jira-type", help="Optional Qase Jira external issue integration type (default: jira-cloud)")
    p_init.add_argument("--agents-md", default="AGENTS.md", help="Relative path to AGENTS.md")
    p_init.add_argument("--standards-md", default="docs/shared/engineering-standards.md", help="Relative path to engineering-standards.md")
    p_init.set_defaults(func=cmd_init)

    # 1b. auth (collaborator role claiming & personal credential onboarding after git clone)
    p_auth = sub.add_parser("auth", parents=[sub_common], help="Claim/append role(s) and bind personal Jira/Qase API tokens into local gitignored .ai-workflow/credentials.json")
    p_auth.add_argument("--inspect", action="store_true", help="Inspect collaborator credential readiness, claimed roles, and unclaimed roles")
    p_auth.add_argument("-i", "--interactive", action="store_true", help="Prompt interactively for owned role(s), display name, Jira Email, Jira API Token, and optional Qase API Token")
    p_auth.add_argument("--role", default=None, help="Comma-separated role(s) claimed by this collaborator (appends to role pool by default)")
    p_auth.add_argument("--specialty", default=None, help="Optional specialty tag when joining a multi-member role pool (e.g. 'backend', 'frontend')")
    p_auth.add_argument("--replace-role", action="store_true", help="Replace existing member(s) in --role instead of appending to the role pool")
    p_auth.add_argument("--name", default=None, help="Collaborator display name (auto-filled from Jira /rest/api/3/myself if omitted)")
    p_auth.add_argument("--jira-email", default=None, help="Personal Jira account email")
    p_auth.add_argument("--jira-token", default=None, help="Personal Jira API token (stored in gitignored .ai-workflow/credentials.json, mode 0600)")
    p_auth.add_argument("--role-tokens-json", default=None, help="Optional JSON mapping role names to Jira API tokens")
    p_auth.add_argument("--qase-token", default=None, help="Optional Qase API token (stored in gitignored .ai-workflow/credentials.json, mode 0600)")
    p_auth.set_defaults(func=cmd_auth)

    # 2. status
    p_sprint_plan = sub.add_parser("sprint-plan", parents=[sub_common], help="Preview or apply a Jira Sprint plan and assign Story/Task/Bug issues")
    p_sprint_plan.add_argument("--name", help="Future Sprint name (required when creating a Sprint)")
    p_sprint_plan.add_argument("--goal", default=None, help="Sprint goal")
    p_sprint_plan.add_argument("--start-date", dest="start_date", default=None, help="Sprint start date/time")
    p_sprint_plan.add_argument("--end-date", dest="end_date", default=None, help="Sprint end date/time")
    p_sprint_plan.add_argument("--board-id", dest="board_id", default=None, help="Jira Scrum board id (or use jira_board_id in team-config.json)")
    p_sprint_plan.add_argument("--sprint-id", dest="sprint_id", default=None, help="Existing Sprint id; omit to create a Future Sprint")
    p_sprint_plan.add_argument("--epic-key", dest="epic_key", default=None, help="Epic key used to derive Story candidates")
    p_sprint_plan.add_argument("--jql", default=None, help="JQL used to select candidate issues")
    p_sprint_plan.add_argument("--issue-key", action="append", default=[], help="Explicit Story/Task/Bug key; repeatable")
    p_sprint_plan.add_argument("--apply", action="store_true", help="Create/update Sprint membership after preview")
    p_sprint_plan.set_defaults(func=cmd_sprint_plan)

    p_sprint_status = sub.add_parser("sprint-status", parents=[sub_common], help="Show Jira Sprint state and issues")
    p_sprint_status.add_argument("--board-id", dest="board_id", default=None, help="Jira Scrum board id")
    p_sprint_status.add_argument("--sprint-id", dest="sprint_id", default=None, help="Specific Sprint id")
    p_sprint_status.add_argument("--state", default=None, help="Filter board sprints by future, active, or closed")
    p_sprint_status.set_defaults(func=cmd_sprint_status)

    # 2a. epic-decompose
    p_epic_dec = sub.add_parser("epic-decompose", parents=[sub_common], help="Preview (default dry_run) or apply Epic-to-Story decomposition with wave ordering and duplicate detection")
    p_epic_dec.add_argument("--epic", required=True, help="Parent Jira Epic key (e.g. PROJ-10)")
    p_epic_dec.add_argument("--stories-json", required=True, help="JSON array string or @file/file path of Story specs")
    p_epic_dec.add_argument("--apply", action="store_true", help="Create Stories in Jira and persist local active state (default is dry_run preview)")
    p_epic_dec.add_argument("--issue-type", default=None, help="Jira issue type for created Stories (default: Story)")
    p_epic_dec.set_defaults(func=cmd_epic_decompose)

    # 2b. status
    p_status = sub.add_parser("status", parents=[sub_common], help="Inspect active workflow state, reconcile live Jira changes, and recommend the next station/role")
    p_status.add_argument("--key", help="Specific Jira Story key (e.g. PROJ-101)")
    p_status.add_argument("--epic", default=None, help="Aggregate workflow progress across all Stories under a Jira Epic key (e.g. PROJ-10)")
    p_status.add_argument("--fetch-remote", action="store_true", help="Fetch remote Jira issue details when in live mode")
    p_status.set_defaults(func=cmd_status)

    # 2c. pull (explicit bidirectional Jira UI -> local active state reconciliation)
    p_pull = sub.add_parser("pull", parents=[sub_common], help="Pull & reconcile manual Jira Web UI changes (Sub-tasks, Bugs, Status, Assignee, Description/ACs) into .ai-workflow/active/")
    p_pull.add_argument("--key", default=None, help="Optional Jira Story key (reconciles all active/discovered project Stories if omitted)")
    p_pull.set_defaults(func=cmd_pull)

    # 3. sync-story
    p_sync = sub.add_parser("sync-story", parents=[sub_common], help="Sync Story specification to .ai-workflow/active/<KEY>.json and Jira")
    p_sync.add_argument("--key", required=True, help="Jira Story key (e.g. PROJ-101)")
    p_sync.add_argument("--title", help="Story summary/title")
    p_sync.add_argument("--scope-in", help="In-scope summary")
    p_sync.add_argument("--scope-out", help="Out-of-scope summary")
    p_sync.add_argument("--has-ui", action="store_true", help="Mark feature as having UI changes")
    p_sync.add_argument("--no-ui", action="store_true", help="Mark feature as having no UI changes")
    p_sync.add_argument("--has-api", action="store_true", help="Mark feature as having cross-boundary API changes")
    p_sync.add_argument("--no-api", action="store_true", help="Mark feature as having no cross-boundary API changes")
    p_sync.add_argument("--has-data", action="store_true", help="Mark feature as having data/schema changes")
    p_sync.add_argument("--no-data", action="store_true", help="Mark feature as having no data/schema changes")
    p_sync.add_argument("--risk-level", choices=["low", "medium", "high", "critical"], help="Risk level")
    p_sync.add_argument("--ac", action="append", help="Acceptance criterion (e.g. 'AC-001:User can export CSV'; upserts by ID on existing draft/approved stories)")
    p_sync.add_argument("--replace-acs", action="store_true", help="Replace the entire acceptance_criteria list instead of upserting by AC ID")
    p_sync.add_argument("--design-summary", help="Technical design summary")
    p_sync.add_argument("--ui-spec", help="UI states, wireframe, and copy (only stored when --has-ui is true)")
    p_sync.add_argument("--api-spec", help="API contract specification (only stored when --has-api is true)")
    p_sync.add_argument("--auto-decision", action="append", help="AI auto-decided conventional detail (抓大放小)")
    p_sync.add_argument("--material-decision", action="append", help="Human-confirmed material decision (<=3 for single-pass approval, <=5 per draft round)")
    p_sync.add_argument("--risks", default=None, help="Optional risks / implicit requirements summary (omitted for simple stories)")
    p_sync.add_argument("--open-questions-json", default=None, help="Optional JSON array or @file of open questions (Q-###)")
    p_sync.add_argument("--preserve-owner", action="store_true", help="Preserve current_owner_role, Jira Assignee/Status, and existing tasks/review/qa/bugs")
    p_sync.add_argument("--backfill", action="store_true", help="Backfill specification on an existing Story in draft/preserved mode (implies --preserve-owner; rejects --status approved)")
    p_sync.add_argument("--spec-json", help="JSON string or file with full story spec fields")
    p_sync.add_argument("--status", choices=["draft", "approved"], help="Spec status (draft or approved)")
    p_sync.add_argument("--approved-by", help="Role or person approving the spec")
    p_sync.add_argument("--to-assignee", default=None, help="Optional developer selector (email/name/accountId/specialty) when handing off an approved spec")
    p_sync.add_argument("--accept-remote-description", action="store_true", help="Acknowledge manual Jira Web UI description edits (already merged into ACs) and update description")
    p_sync.add_argument("--force-overwrite", action="store_true", help="Force overwrite Jira Story description even if modified in Jira Web UI")
    p_sync.add_argument("--create", action="store_true", help="Create a new Story in Jira when in live mode")
    p_sync.set_defaults(func=cmd_sync_story)

    # 4. create-subtasks
    p_subtasks = sub.add_parser("create-subtasks", parents=[sub_common], help="Create & auto-assign TASK-001..N in active JSON and Jira Sub-tasks")
    p_subtasks.add_argument("--key", required=True, help="Jira Story key")
    p_subtasks.add_argument("--tasks-json", help="JSON string or file defining tasks (supports per-task 'assignee' field)")
    p_subtasks.add_argument("--task", action="append", help="Task spec 'TASK-001:Title:AC-001,AC-002:Verification'")
    p_subtasks.add_argument("--assignee", default=None, help="Default developer selector (email/name/accountId/specialty) for created Sub-tasks")
    p_subtasks.add_argument("--task-assignee", action="append", help="Per-task developer assignment 'TASK-001=<email|name|accountId|specialty>'")
    p_subtasks.add_argument("--proceed-at-risk", action="store_true", help="Allow planning tasks before spec approval")
    p_subtasks.set_defaults(func=cmd_create_subtasks)

    # 5. complete-task
    p_comp = sub.add_parser("complete-task", parents=[sub_common], help="Mark one or more TASK-###, BUG-*, or REV-### completed with commit, test evidence, and optional bug root-cause/solution")
    p_comp.add_argument("--key", required=True, help="Jira Story key")
    p_comp.add_argument("--task-id", default="TASK-001", help="Comma-separated IDs: Task ID (TASK-001), Bug key (PROJ-101-BUG-1), or Finding ID (REV-001,REV-002)")
    p_comp.add_argument("--commit", default=None, help="Git commit SHA implementing the task/fix (auto-detected from git HEAD if omitted)")
    p_comp.add_argument("--files", default=None, help="Comma-separated list of changed file paths (auto-detected from git if omitted)")
    p_comp.add_argument("--test-result", required=True, help="Executed test command and pass summary")
    p_comp.add_argument("--root-cause", default=None, help="Optional root cause analysis (recommended/guided when completing a Bug fix)")
    p_comp.add_argument("--solution", default=None, help="Optional fix strategy / solution summary (recommended/guided when completing a Bug fix)")
    p_comp.set_defaults(func=cmd_complete_task)

    # 6. handoff
    p_handoff = sub.add_parser("handoff", parents=[sub_common], help="Record Code Review & handoff with peer-review routing and multi-member assignee support")
    p_handoff.add_argument("--key", required=True, help="Jira Story key")
    p_handoff.add_argument("--from-role", default="reviewer", help="Source role (default: reviewer)")
    p_handoff.add_argument("--to-role", default="qa", help="Target role (default: qa)")
    p_handoff.add_argument("--to-assignee", default=None, help="Specific target member (email/name/accountId/specialty) in --to-role pool")
    p_handoff.add_argument("--review-status", choices=["approved", "changes-requested"], default="approved")
    p_handoff.add_argument("--commit", help="Reviewed git commit SHA")
    p_handoff.add_argument("--summary", help="Handoff and review summary")
    p_handoff.add_argument("--findings-json", help="JSON string or file with review findings")
    p_handoff.add_argument("--target-status", help="Optional status_mapping key override")
    p_handoff.set_defaults(func=cmd_handoff)

    # 6b. promote-qa
    p_promote_qa = sub.add_parser("promote-qa", parents=[sub_common], help="Promote a review-approved Story to in_qa and notify QA after QA environment deployment")
    p_promote_qa.add_argument("--key", required=True, help="Jira Story key")
    p_promote_qa.add_argument("--from-role", default="developer", help="Acting role promoting the build to QA (default: developer)")
    p_promote_qa.add_argument("--tag", default="", help="Optional QA deployment tag/release identifier (informational only; never invokes git tag)")
    p_promote_qa.add_argument("--deploy-ref", default="", help="Optional QA deployment reference (tag, TestFlight build, or release ref; informational only)")
    p_promote_qa.add_argument("--environment", default="", help="Optional target QA environment name (e.g. qa, staging)")
    p_promote_qa.add_argument("--note", default="", help="Optional QA promotion note")
    p_promote_qa.add_argument("--to-assignee", default=None, help="Optional specific QA member (email/name/accountId) in roles.qa pool")
    p_promote_qa.set_defaults(func=cmd_promote_qa)

    # 7. record-qa
    p_qa = sub.add_parser("record-qa", parents=[sub_common], help="Record QA test plan (plan) or verification results (verify) via Jira comments or Qase")
    p_qa.add_argument("--inspect", action="store_true", help="Inspect current remembered QA backend ('jira-comment' vs 'qase') and Qase credential readiness")
    p_qa.add_argument("--key", default=None, help="Jira Story key (required unless --inspect)")
    p_qa.add_argument("--mode", choices=["plan", "verify"], default=None, help="QA mode: plan or verify (required unless --inspect)")
    p_qa.add_argument("--qa-owner", default=None, help="Specific QA member (email/name/accountId) owning this Story's QA")
    p_qa.add_argument("--to-assignee", default=None, help="Specific Release Manager member when verify passes")
    p_qa.add_argument("--qa-backend", choices=["jira-comment", "qase"], help="Choose and remember QA backend in team-config.json ('jira-comment' or 'qase')")
    p_qa.add_argument("--qase-project-code", help="Qase project code (persisted in team-config.json)")
    p_qa.add_argument("--qase-token", help="Qase API token (persisted in gitignored .ai-workflow/credentials.json, mode 0600)")
    p_qa.add_argument("--qase-base-url", help="Qase API v1 base URL override (default: https://api.qase.io/v1)")
    p_qa.add_argument("--qase-app-url", help="Qase web app URL override (default: https://app.qase.io)")
    p_qa.add_argument("--qase-jira-type", help="Qase external Jira integration type (default: jira-cloud)")
    p_qa.add_argument("--cases-json", help="JSON string or file with test cases for plan mode")
    p_qa.add_argument("--tc", action="append", help="Test case 'TC-001:AC-001:automated|external|manual:Title'")
    p_qa.add_argument("--junit-xml", help="Path to JUnit XML report for verify mode")
    p_qa.add_argument("--results-json", help="JSON string or file with TC results for verify mode")
    p_qa.add_argument("--result", action="append", help="Single TC result 'TC-001=pass|fail|blocked[:note]' (also accepts 'TC-001:pass[:note]')")
    p_qa.add_argument("--tc-attach", action="append", default=[], help="Attach screenshot/media/log file to a test case in verify mode: 'TC-002=/path/to/fail.png' (repeatable)")
    p_qa.add_argument("--all-pass", action="store_true", help="Mark all un-overridden 'automated' test cases as pass in verify mode (external/manual TCs require explicit result)")
    p_qa.add_argument("--commit", help="Tested git commit SHA")
    p_qa.add_argument("--deploy-ref", default="", help="Optional deployed build/tag reference verified by QA (falls back to qa_deploy_gate.deploy_ref when aligned)")
    p_qa.set_defaults(func=cmd_record_qa)

    # 8. create-bug (verify failures, Story exploratory bugs, and standalone production bugs)
    p_bug = sub.add_parser("create-bug", parents=[sub_common], help="Create/adopt a linked Story Bug (planned TC or exploratory) or standalone production Bug and assign to Developer")
    p_bug.add_argument("--key", default=None, help="Jira Story key (or existing Jira Bug key with --standalone --adopt)")
    p_bug.add_argument("--standalone", action="store_true", help="Report or adopt a standalone production/user Bug (skips team-spec and extra Sub-tasks)")
    p_bug.add_argument("--adopt", action="store_true", help="Adopt an existing standalone Jira Bug ticket (--key <BUG-KEY>) without overwriting its description")
    p_bug.add_argument("--tc-id", default=None, help="Failing test case ID (optional — omit for exploratory ad-hoc Story bugs to auto-generate TC-EXP-###)")
    p_bug.add_argument("--execution-type", choices=["automated", "external", "manual"], default=None, help="Execution type for auto-created exploratory/standalone regression TC (default: manual)")
    p_bug.add_argument("--qa-backend", choices=["jira-comment", "qase"], default=None, help="Optional QA backend override ('jira-comment' or 'qase')")
    p_bug.add_argument("--ac-id", help="Affected AC ID (e.g. AC-001)")
    p_bug.add_argument("--to-assignee", default=None, help="Optional developer selector override for Bug assignment")
    p_bug.add_argument("--title", default=None, help="Concise bug title (auto-read from Jira when --adopt is used)")
    p_bug.add_argument("--severity", choices=["blocker", "major", "minor"], default="major")
    p_bug.add_argument("--steps", default=None, help="Reproduction steps (auto-read from Jira when --adopt is used)")
    p_bug.add_argument("--expected", default=None, help="Expected behavior")
    p_bug.add_argument("--actual", default=None, help="Actual observed behavior")
    p_bug.add_argument("--attach", action="append", default=[], help="Screenshot, video, or log file to upload to the Jira Bug (and Qase when qa_test_backend='qase'); repeatable")
    p_bug.add_argument("--commit", help="Tested commit SHA where the bug occurred")
    p_bug.set_defaults(func=cmd_create_bug)

    # 8b. triage-bug (Bug Triage: Not a Bug / Won't Fix / Duplicate / Cannot Reproduce / Fixed)
    p_triage = sub.add_parser("triage-bug", parents=[sub_common], help="Triage a Story-linked or standalone Bug with human decision audit and optional controlled TC waiver")
    p_triage.add_argument("--key", required=True, help="Jira Story key or standalone Bug key")
    p_triage.add_argument("--bug-id", default=None, help="Bug key or ID (required for Story-linked bugs; optional for standalone_bug)")
    p_triage.add_argument("--resolution", required=True, help="Triage resolution: not-a-bug | wont-fix | duplicate | cannot-reproduce | fixed (case-insensitive display aliases accepted)")
    p_triage.add_argument("--reason", required=True, help="Rationale, spec reference, or evidence supporting the triage decision")
    p_triage.add_argument("--decided-by", required=True, help="Human / PO / Dev / QA decision maker authorizing this triage")
    p_triage.add_argument("--duplicate-of", default="", help="Existing Jira issue key (required when resolution is 'duplicate')")
    p_triage.add_argument("--waive-tcs", default="", help="Optional comma-separated TC IDs (e.g. TC-001,TC-002) to mark waived/not-applicable for non-code-fix resolutions (never marks 'pass')")
    p_triage.add_argument("--tc-disposition", choices=["waived", "not-applicable"], default=None, help="Disposition for --waive-tcs (default: 'waived'; 'not-applicable' primarily for not-a-bug)")
    p_triage.set_defaults(func=cmd_triage_bug)

    # 9. close-story
    p_close = sub.add_parser("close-story", parents=[sub_common], help="Verify all gates, transition Story to Done, and delete active JSON")
    p_close.add_argument("--key", required=True, help="Jira Story key")
    p_close.add_argument("--version", default=None, help="Release version or tag (defaults to v1.0.0 if omitted)")
    p_close.add_argument("--commit", default=None, help="Release candidate git commit SHA (defaults to candidate_commit or git HEAD if omitted)")
    p_close.add_argument("--notes", default="", help="Release summary notes")
    p_close.set_defaults(func=cmd_close_story)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "_sub_offline", False):
        args.offline = True
    try:
        src_script = Path(__file__).resolve()
        repo_root = Path(args.repo_root).resolve()
        local_bin = get_workflow_dir(repo_root) / "bin" / "jira_sync.py"
        if local_bin.exists() and local_bin.resolve() != src_script:
            try:
                if local_bin.read_bytes() != src_script.read_bytes():
                    shutil.copy2(src_script, local_bin)
                    local_bin.chmod(0o755)
            except Exception:
                pass
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
