#!/usr/bin/env python3
"""
jira_sync.py — Unified Jira & Local Active State CLI for the 4-Station Small-Team Workflow.

Uses ONLY the Python 3 standard library.
Supports both:
  1. Live Jira REST API v3 mode (when JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN are set)
  2. Local / Offline mode (.ai-workflow/active/<JIRA-KEY>.json + formatted console output)

Subcommands:
  init            Initialize .ai-workflow/team-config.json, .gitignore, and extract engineering-standards.md
  status          Inspect active workflow state and recommend the exact next station/role
  sync-story      Create/update Story spec (Scope, AC-*, conditional UI/API design, 抓大放小 decisions)
  create-subtasks Create development tasks (TASK-001..N) and Jira Sub-tasks
  complete-task   Mark a task or Bug completed with commit SHA, changed files, and test evidence
  handoff         Record review/station handoff with multi-role awareness (auto-consolidate vs @mention)
  record-qa       Record QA test plan (plan mode) or verification results (verify mode, incl. JUnit/JSON)
  create-bug      Create a linked Jira Bug ticket on test failure and route back to Developer
  close-story     Verify all release gates, post release summary, transition Story to Done, delete active JSON
"""

import argparse
import base64
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


DEFAULT_TEAM_CONFIG = {
    "version": "1.0",
    "project_key": "PROJ",
    "jira_base_url": "",
    "sync_mode": "auto",
    "notification_channel": "jira",
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
        "in_qa": "In QA",
        "ready_for_release": "Ready for Release",
        "done": "Done",
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
- 測試案例識別碼（`TC-001..N`）必須對應驗收條件（`AC-001..N`）；未執行或缺少證據不得視為通過。

## 3. Security & Data Hygiene (安全與資料保護)
- 嚴禁在程式碼、設定檔、日誌或版本控制中寫入 API Token、密碼或私鑰。
- 所有外部輸入與跨邊界 API 必須執行格式驗證、權限檢查（AuthN/AuthZ）與輸出跳脫處理。
"""

AGENTS_STANDARDS_POINTER_MARKER = "<!-- ai-workflow:engineering-standards-link -->"
AGENTS_STANDARDS_POINTER_BLOCK = f"""{AGENTS_STANDARDS_POINTER_MARKER}
## Engineering Standards (工程標準規範)

專案所有的編碼、架構、測試與安全規範已統一收斂至唯一權威來源：
- 請參閱 [`docs/shared/engineering-standards.md`](docs/shared/engineering-standards.md)
- 請勿在本文件重複維護上述工程細則，以免規範分歧。
{AGENTS_STANDARDS_POINTER_MARKER}
"""


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def load_json_arg(val: str):
    """Load JSON either from a file path (if it exists) or from a raw JSON string."""
    if not val:
        return None
    p = Path(val)
    if p.exists() and p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(val)


def get_workflow_dir(repo_root: Path) -> Path:
    return repo_root / ".ai-workflow"


def get_active_dir(repo_root: Path) -> Path:
    return get_workflow_dir(repo_root) / "active"


def get_config_path(repo_root: Path) -> Path:
    return get_workflow_dir(repo_root) / "team-config.json"


def load_team_config(repo_root: Path) -> dict:
    cfg_path = get_config_path(repo_root)
    if not cfg_path.exists():
        return dict(DEFAULT_TEAM_CONFIG)
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_TEAM_CONFIG)
        merged.update(data)
        merged["roles"] = {**DEFAULT_TEAM_CONFIG["roles"], **data.get("roles", {})}
        merged["status_mapping"] = {**DEFAULT_TEAM_CONFIG["status_mapping"], **data.get("status_mapping", {})}
        return merged
    except Exception as exc:
        raise ValueError(f"Invalid team-config.json at {cfg_path}: {exc}") from exc


def resolve_role_person(config: dict, role: str) -> dict:
    roles = config.get("roles", {})
    entry = roles.get(role) or {}
    if isinstance(entry, str):
        return {"role": role, "name": entry, "email": "", "jira_account_id": ""}
    return {
        "role": role,
        "name": str(entry.get("name") or role).strip(),
        "email": str(entry.get("email") or "").strip(),
        "jira_account_id": str(entry.get("jira_account_id") or "").strip(),
    }


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


def resolve_effective_reviewer(config: dict) -> tuple:
    """
    Resolve the effective reviewer role and person.
    If developer and reviewer map to the same person and independent_reviewer is configured
    as a distinct person, route to independent_reviewer per governance §3.1.
    Returns (effective_role, reviewer_person_dict, self_reviewed_with_ai).
    """
    dev_person = resolve_role_person(config, "developer")
    rev_person = resolve_role_person(config, "reviewer")
    if is_same_person(dev_person, rev_person):
        ind_entry = (config.get("roles") or {}).get("independent_reviewer")
        if ind_entry:
            ind_person = resolve_role_person(config, "independent_reviewer")
            has_identity = bool(
                ind_person.get("jira_account_id")
                or ind_person.get("email")
                or (ind_person.get("name") and ind_person["name"] != "independent_reviewer")
            )
            if has_identity and not is_same_person(dev_person, ind_person):
                return ("independent_reviewer", ind_person, False)
        return ("reviewer", rev_person, True)
    return ("reviewer", rev_person, False)


class JiraClient:
    """Dependency-free Jira REST API v3 client with automatic local/offline fallback."""

    def __init__(self, config: dict, offline: bool = False):
        self.config = config
        self.base_url = (os.environ.get("JIRA_BASE_URL") or config.get("jira_base_url") or "").rstrip("/")
        if self.base_url:
            scheme = urllib.parse.urlparse(self.base_url).scheme.lower()
            if scheme not in ("http", "https"):
                raise ValueError(f"Invalid jira_base_url scheme '{scheme}'. Only http and https are allowed.")
        self.email = (os.environ.get("JIRA_EMAIL") or "").strip()
        self.api_token = (os.environ.get("JIRA_API_TOKEN") or "").strip()
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

    def _request(self, method: str, path: str, payload: dict = None) -> dict:
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

    @staticmethod
    def text_to_adf(text: str, mention_account_id: str = "", mention_name: str = "") -> dict:
        """Convert Markdown/plain text into Atlassian Document Format (ADF) v1."""
        content_nodes = []
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
                    {"type": "text", "text": " 請接手下一階段工作："},
                ],
            })
        elif mention_name:
            content_nodes.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": f"@{mention_name} 請接手下一階段工作："}],
            })

        for block in (text or "").strip().split("\n\n"):
            block_text = block.strip()
            if not block_text:
                continue
            content_nodes.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": block_text}],
            })
        if not content_nodes:
            content_nodes.append({"type": "paragraph", "content": [{"type": "text", "text": "Updated by ai-engineering-workflow"}]})
        return {"type": "doc", "version": 1, "content": content_nodes}

    def update_or_create_story(self, jira_key: str, title: str, description_md: str, create: bool = False) -> dict:
        adf = self.text_to_adf(description_md)
        if not self.is_live:
            return {"mode": "offline", "jira_key": jira_key, "action": "sync-story", "summary": title}
        if create:
            proj_key = self.config.get("project_key") or jira_key.split("-")[0]
            payload = {
                "fields": {
                    "project": {"key": proj_key},
                    "summary": title,
                    "description": adf,
                    "issuetype": {"name": "Story"},
                }
            }
            res = self._request("POST", "/rest/api/3/issue", payload)
            return {"mode": "live", "jira_key": res.get("key", jira_key), "action": "create-story"}
        payload = {"fields": {"summary": title, "description": adf}}
        self._request("PUT", f"/rest/api/3/issue/{urllib.parse.quote(jira_key)}", payload)
        return {"mode": "live", "jira_key": jira_key, "action": "update-story"}

    def create_subtask(self, parent_key: str, summary: str, description_md: str, index: int) -> str:
        if not self.is_live:
            return f"{parent_key}-SUB-{index}"
        proj_key = self.config.get("project_key") or parent_key.split("-")[0]
        payload = {
            "fields": {
                "project": {"key": proj_key},
                "parent": {"key": parent_key},
                "summary": summary,
                "description": self.text_to_adf(description_md),
                "issuetype": {"name": "Sub-task"},
            }
        }
        res = self._request("POST", "/rest/api/3/issue", payload)
        return res.get("key") or f"{parent_key}-SUB-{index}"

    def create_bug(self, parent_key: str, summary: str, description_md: str, index: int) -> str:
        if not self.is_live:
            return f"{parent_key}-BUG-{index}"
        proj_key = self.config.get("project_key") or parent_key.split("-")[0]
        payload = {
            "fields": {
                "project": {"key": proj_key},
                "summary": summary,
                "description": self.text_to_adf(description_md),
                "issuetype": {"name": "Bug"},
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

    def add_comment(self, issue_key: str, body_md: str, mention_account_id: str = "", mention_name: str = "") -> dict:
        adf = self.text_to_adf(body_md, mention_account_id=mention_account_id, mention_name=mention_name)
        if not self.is_live:
            return {"mode": "offline", "issue_key": issue_key, "comment_posted": False}
        self._request("POST", f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/comment", {"body": adf})
        return {"mode": "live", "issue_key": issue_key, "comment_posted": True}

    def transition_issue(self, issue_key: str, target_status_name: str) -> dict:
        if not self.is_live or not target_status_name:
            return {"mode": "offline" if not self.is_live else "live", "transitioned_to": target_status_name}
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
            self._request(
                "POST",
                f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}/transitions",
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

    def get_issue(self, issue_key: str) -> dict:
        if not self.is_live:
            return {}
        return self._request("GET", f"/rest/api/3/issue/{urllib.parse.quote(issue_key)}")


def load_active_story(repo_root: Path, jira_key: str) -> dict:
    clean_key = validate_jira_key(jira_key)
    p = get_active_dir(repo_root) / f"{clean_key}.json"
    if not p.exists():
        raise FileNotFoundError(f"Active story state not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_active_story(repo_root: Path, jira_key: str, data: dict) -> Path:
    clean_key = validate_jira_key(jira_key)
    active_dir = get_active_dir(repo_root)
    active_dir.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_iso()
    p = active_dir / f"{clean_key}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def execute_role_handoff(
    client: JiraClient,
    config: dict,
    story: dict,
    from_role: str,
    to_role: str,
    status_key: str,
    comment_md: str,
) -> dict:
    """Execute role handoff with multi-role consolidation awareness."""
    from_person = resolve_role_person(config, from_role)
    to_person = resolve_role_person(config, to_role)
    target_jira_status = config.get("status_mapping", {}).get(status_key, status_key)
    jira_key = story["jira_key"]

    if is_same_person(from_person, to_person):
        # Same person holds both roles: skip notification noise and approval friction
        client.add_comment(jira_key, comment_md)
        client.transition_issue(jira_key, target_jira_status)
        handoff_record = {
            "timestamp": now_iso(),
            "from_role": from_role,
            "to_role": to_role,
            "from_person": from_person["name"],
            "to_person": to_person["name"],
            "same_person": True,
            "handoff_mode": "auto-consolidated",
            "target_jira_status": target_jira_status,
            "notified": False,
        }
    else:
        # Different people: transition status, reassign issue, and @mention in comment
        client.transition_issue(jira_key, target_jira_status)
        if to_person.get("jira_account_id"):
            client.assign_issue(jira_key, to_person["jira_account_id"])
        client.add_comment(
            jira_key,
            comment_md,
            mention_account_id=to_person.get("jira_account_id", ""),
            mention_name=to_person.get("name", to_role),
        )
        handoff_record = {
            "timestamp": now_iso(),
            "from_role": from_role,
            "to_role": to_role,
            "from_person": from_person["name"],
            "to_person": to_person["name"],
            "same_person": False,
            "handoff_mode": "jira-notification",
            "target_jira_status": target_jira_status,
            "assigned_account_id": to_person.get("jira_account_id", ""),
            "mention": f"@{to_person.get('name') or to_role}",
            "notified": True,
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
        "規範",
        "安全",
        "測試標準",
        "編碼規範",
        "工程標準",
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
        if not is_pointer_section and any(k in title.lower() for k in standards_keywords):
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


def cmd_init(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    wf_dir = get_workflow_dir(repo_root)
    wf_dir.mkdir(parents=True, exist_ok=True)
    get_active_dir(repo_root).mkdir(parents=True, exist_ok=True)

    cfg = load_team_config(repo_root)
    if args.project_key:
        cfg["project_key"] = args.project_key.strip().upper()
    if args.jira_url is not None:
        cfg["jira_base_url"] = args.jira_url.strip().rstrip("/")
    if args.roles_json:
        custom_roles = load_json_arg(args.roles_json)
        if isinstance(custom_roles, dict):
            for r_key, r_val in custom_roles.items():
                prev_role = cfg["roles"].get(r_key) if isinstance(cfg["roles"].get(r_key), dict) else {}
                if isinstance(r_val, str):
                    cfg["roles"][r_key] = {
                        "name": r_val,
                        "email": prev_role.get("email", ""),
                        "jira_account_id": prev_role.get("jira_account_id", ""),
                    }
                elif isinstance(r_val, dict):
                    cfg["roles"][r_key] = {
                        "name": r_val.get("name", prev_role.get("name", r_key)),
                        "email": r_val.get("email", prev_role.get("email", "")),
                        "jira_account_id": r_val.get("jira_account_id", prev_role.get("jira_account_id", "")),
                    }
    if args.status_mapping_json:
        custom_status = load_json_arg(args.status_mapping_json)
        if isinstance(custom_status, dict):
            cfg["status_mapping"].update(custom_status)

    cfg_path = get_config_path(repo_root)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Ensure .gitignore ignores .ai-workflow/active/
    gitignore_path = repo_root / ".gitignore"
    ignore_entry = ".ai-workflow/active/"
    existing_gitignore = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    lines = [ln.strip() for ln in existing_gitignore.splitlines()]
    gitignore_updated = False
    if ignore_entry not in lines and ".ai-workflow/active" not in lines:
        prefix = existing_gitignore.rstrip("\n")
        new_content = (prefix + "\n" if prefix else "") + ignore_entry + "\n"
        gitignore_path.write_text(new_content, encoding="utf-8")
        gitignore_updated = True

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

    result = {
        "ok": True,
        "action": "init",
        "config_path": str(cfg_path.relative_to(repo_root)),
        "project_key": cfg["project_key"],
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
        owner = resolve_role_person(config, "pm")
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
        owner = resolve_role_person(config, "developer")
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
        owner = resolve_role_person(config, "developer")
        return {
            "jira_key": jira_key,
            "current_station": "Station 2: Dev Plan",
            "recommended_skill": "team-dev",
            "recommended_mode": "plan",
            "parallel_hint": "QA (`team-qa` in `plan` mode) may also draft test cases in parallel.",
            "responsible_role": "developer",
            "responsible_person": owner["name"],
            "reason": f"Spec for {jira_key} is approved, but development Sub-tasks (TASK-001..N) have not been decomposed yet.",
            "done_when": f"Tasks are decomposed and synced via `jira_sync.py create-subtasks --key {jira_key}`.",
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
        owner = resolve_role_person(config, "developer")
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
        owner = resolve_role_person(config, "developer")
        return {
            "jira_key": jira_key,
            "current_station": "Station 2: Dev Remediation (Review Changes Requested)",
            "recommended_skill": "team-dev",
            "recommended_mode": "implement",
            "responsible_role": "developer",
            "responsible_person": owner["name"],
            "reason": f"Code review on {jira_key} requested changes ({review.get('summary', 'see findings')}).",
            "done_when": "Review findings are fixed with updated commit/tests and re-submitted to `team-review`.",
        }

    if review_status != "approved" or (candidate_commit and reviewed_commit and reviewed_commit != candidate_commit):
        rev_role, owner, _ = resolve_effective_reviewer(config)
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

    # 6. Code Review approved -> check QA Plan & Verify
    test_cases = qa.get("test_cases") or []
    qa_status = qa.get("status")
    tested_commit = qa.get("tested_commit")
    uncovered_acs = find_uncovered_story_acs(story, test_cases)

    if not test_cases or uncovered_acs:
        owner = resolve_role_person(config, "qa")
        gap_note = f" (missing coverage for {', '.join(uncovered_acs)})" if (test_cases and uncovered_acs) else ""
        return {
            "jira_key": jira_key,
            "current_station": "Station 3b: QA Plan",
            "recommended_skill": "team-qa",
            "recommended_mode": "plan",
            "responsible_role": "qa",
            "responsible_person": owner["name"],
            "reason": f"Code review is approved, but QA test cases (TC-001..N) need planning for {jira_key}{gap_note}.",
            "done_when": f"Test cases covering all Story ACs are planned and recorded via `jira_sync.py record-qa --key {jira_key} --mode plan`.",
        }

    if qa_status != "passed" or (candidate_commit and tested_commit and tested_commit != candidate_commit):
        owner = resolve_role_person(config, "qa")
        stale_note = (
            f" (candidate commit changed from {tested_commit} to {candidate_commit})"
            if (qa_status == "passed" and tested_commit != candidate_commit)
            else ""
        )
        return {
            "jira_key": jira_key,
            "current_station": "Station 3b: QA Verify",
            "recommended_skill": "team-qa",
            "recommended_mode": "verify",
            "responsible_role": "qa",
            "responsible_person": owner["name"],
            "reason": f"QA test plan ({len(test_cases)} TCs) is ready; test execution verification is required{stale_note}.",
            "done_when": f"All TCs pass on commit {candidate_commit or 'HEAD'} via `jira_sync.py record-qa --key {jira_key} --mode verify`.",
        }

    # 7. All gates passed -> Station 4: Release & Close
    owner = resolve_role_person(config, "release_manager")
    return {
        "jira_key": jira_key,
        "current_station": "Station 4: Release & Close",
        "recommended_skill": "team-release",
        "recommended_mode": None,
        "responsible_role": "release_manager",
        "responsible_person": owner["name"],
        "reason": f"Spec, Sub-tasks, Code Review, and QA Verification are all complete and aligned on commit {candidate_commit} with 0 open Bugs.",
        "done_when": f"Release summary is posted, Jira Story {jira_key} is transitioned to Done, and `.ai-workflow/active/{jira_key}.json` is removed via `jira_sync.py close-story`.",
    }


def cmd_status(args) -> int:
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
    active_dir = get_active_dir(repo_root)
    active_files = sorted(active_dir.glob("*.json")) if active_dir.exists() else []

    if args.key:
        jira_key = validate_jira_key(args.key)
        target_file = active_dir / f"{jira_key}.json"
        remote_issue = None
        if getattr(args, "fetch_remote", False):
            client = JiraClient(config, offline=args.offline)
            if client.is_live:
                try:
                    remote_issue = client.get_issue(jira_key)
                except Exception as exc:
                    remote_issue = {"error": str(exc)}
        if not target_file.exists():
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
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        story = load_active_story(repo_root, jira_key)
        rec = evaluate_story_next_step(story, config, standards_exists)
        rec["initialized"] = True
        rec["standards_exists"] = standards_exists
        rec["story_found"] = True
        if remote_issue is not None:
            rec["remote_issue"] = remote_issue
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0

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
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    story = json.loads(active_files[0].read_text(encoding="utf-8"))
    rec = evaluate_story_next_step(story, config, standards_exists)
    rec["initialized"] = True
    rec["standards_exists"] = standards_exists
    rec["story_found"] = True
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 3: sync-story (Station 1: team-spec)
# ---------------------------------------------------------------------------

def format_story_description(story: dict) -> str:
    lines = [f"# {story.get('jira_key')}: {story.get('title', '')}", ""]
    lines.append("## 1. Scope (範圍界定)")
    lines.append(f"- **In-Scope:** {story.get('scope_in') or 'N/A'}")
    lines.append(f"- **Out-of-Scope:** {story.get('scope_out') or 'N/A'}")
    surface = story.get("change_surface", {})
    lines.append(
        f"- **Change Surface:** UI={surface.get('has_ui', False)}, "
        f"API={surface.get('has_api', False)}, Data={surface.get('has_data', False)} | "
        f"**Risk Level:** `{story.get('risk_level', 'medium')}`"
    )
    lines.append("")

    lines.append("## 2. Acceptance Criteria (驗收條件)")
    for ac in story.get("acceptance_criteria") or []:
        lines.append(f"- **{ac['id']}**: {ac['description']}")
    lines.append("")

    design = story.get("design") or {}
    lines.append("## 3. Technical Design Summary (技術設計摘要)")
    lines.append(design.get("summary") or "Follow repository architecture and shared engineering standards.")
    lines.append("")

    if surface.get("has_ui") and design.get("ui_spec"):
        lines.append("### 3.1 UI States, Wireframe & Copy (畫面狀態與文案)")
        lines.append(design["ui_spec"])
        lines.append("")

    if surface.get("has_api") and design.get("api_spec"):
        lines.append("### 3.2 Cross-Boundary API / Contract (介面契約)")
        lines.append(design["api_spec"])
        lines.append("")

    auto_decisions = story.get("auto_decisions") or []
    if auto_decisions:
        lines.append("## 4. AI 預設細節清單 (Auto-Decided Defaults — 抓大放小)")
        for item in auto_decisions:
            lines.append(f"- {item}")
        lines.append("")

    material_decisions = story.get("material_decisions") or []
    if material_decisions:
        lines.append("## 5. 關鍵人工決策記錄 (Confirmed Material Decisions)")
        for item in material_decisions:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines).strip()


def cmd_sync_story(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline)

    jira_key = validate_jira_key(args.key)
    active_path = get_active_dir(repo_root) / f"{jira_key}.json"
    story = json.loads(active_path.read_text(encoding="utf-8")) if active_path.exists() else {"jira_key": jira_key}

    if args.spec_json:
        spec_payload = load_json_arg(args.spec_json)
        if isinstance(spec_payload, dict):
            story.update(spec_payload)

    if args.title:
        story["title"] = args.title.strip()
    if args.scope_in is not None:
        story["scope_in"] = args.scope_in.strip()
    if args.scope_out is not None:
        story["scope_out"] = args.scope_out.strip()
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
        for idx, raw_ac in enumerate(args.ac, start=1):
            if ":" in raw_ac and re.match(r"^AC-\d+", raw_ac.split(":", 1)[0].strip(), re.IGNORECASE):
                ac_id, desc = raw_ac.split(":", 1)
                ac_id = ac_id.strip().upper()
                desc = desc.strip()
            else:
                ac_id = f"AC-{idx:03d}"
                desc = raw_ac.strip()
            if not desc:
                print(f"ERROR: Acceptance criterion '{ac_id}' has an empty description.", file=sys.stderr)
                return 1
            acs.append({"id": ac_id, "description": desc})
        story["acceptance_criteria"] = acs

    design = story.get("design") or {}
    if args.design_summary is not None:
        design["summary"] = args.design_summary.strip()
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

    if args.auto_decision:
        story["auto_decisions"] = [d.strip() for d in args.auto_decision if d.strip()]
    if args.material_decision:
        if len(args.material_decision) > 3:
            print("ERROR: '抓大放小' rule violated — at most 3 material questions/decisions are allowed.", file=sys.stderr)
            return 1
        story["material_decisions"] = [d.strip() for d in args.material_decision if d.strip()]
    elif len(story.get("material_decisions") or []) > 3:
        print("ERROR: '抓大放小' rule violated — at most 3 material questions/decisions are allowed.", file=sys.stderr)
        return 1

    status = args.status or story.get("spec_status") or "draft"
    if status == "approved" and not (story.get("acceptance_criteria") or []):
        print("ERROR: Cannot mark spec 'approved' without at least one testable AC (AC-001..N).", file=sys.stderr)
        return 1

    story["spec_status"] = status
    if status == "approved":
        story["approved_by"] = args.approved_by or resolve_role_person(config, "pm")["name"]
        story["approved_at"] = now_iso()

    desc_md = format_story_description(story)
    sync_res = client.update_or_create_story(
        jira_key,
        story.get("title") or jira_key,
        desc_md,
        create=args.create,
    )
    if sync_res.get("jira_key"):
        jira_key = validate_jira_key(sync_res["jira_key"])
        story["jira_key"] = jira_key

    handoff_info = None
    if status == "approved":
        handoff_info = execute_role_handoff(
            client=client,
            config=config,
            story=story,
            from_role="pm",
            to_role="developer",
            status_key="in_progress",
            comment_md=f"[Station 1: Spec Approved] 規格與設計已確認核准（共 {len(story.get('acceptance_criteria', []))} 項 AC），請進入 Station 2 (`team-dev`) 拆解與實作。",
        )

    saved_path = save_active_story(repo_root, jira_key, story)
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


def cmd_create_subtasks(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline)
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key)

    if story.get("spec_status") != "approved" and not args.proceed_at_risk:
        print(f"ERROR: Story {jira_key} spec_status is '{story.get('spec_status')}'. Approve spec first or pass --proceed-at-risk.", file=sys.stderr)
        return 1

    raw_tasks = []
    if args.tasks_json:
        loaded = load_json_arg(args.tasks_json)
        if isinstance(loaded, dict) and "tasks" in loaded:
            raw_tasks = loaded["tasks"]
        elif isinstance(loaded, list):
            raw_tasks = loaded
    if args.task:
        for idx, t_str in enumerate(args.task, start=len(raw_tasks) + 1):
            parts = [p.strip() for p in t_str.split(":")]
            t_id = parts[0] if (parts and re.match(r"^TASK-\d+", parts[0])) else f"TASK-{idx:03d}"
            title = parts[1] if len(parts) > 1 else parts[0]
            ac_cov = [x.strip() for x in parts[2].split(",") if x.strip()] if len(parts) > 2 else []
            verif = parts[3] if len(parts) > 3 else "Run unit/integration tests"
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
        t_id = item.get("id") or f"TASK-{idx:03d}"
        if t_id in planned_ids:
            print(f"ERROR: Duplicate task ID '{t_id}' in create-subtasks.", file=sys.stderr)
            return 1
        planned_ids.add(t_id)

    for idx, item in enumerate(raw_tasks, start=1):
        t_id = item.get("id") or f"TASK-{idx:03d}"
        deps = item.get("depends_on") or []
        unknown_deps = [d for d in deps if d not in planned_ids]
        if unknown_deps:
            print(f"ERROR: Task '{t_id}' depends_on unknown task ID(s): {', '.join(unknown_deps)}.", file=sys.stderr)
            return 1

    normalized_tasks = []
    existing_by_id = {t["id"]: t for t in (story.get("tasks") or [])}
    for idx, item in enumerate(raw_tasks, start=1):
        t_id = item.get("id") or f"TASK-{idx:03d}"
        prev = existing_by_id.get(t_id, {})
        title = item.get("title") or prev.get("title") or t_id
        ac_cov = item.get("ac_coverage") or prev.get("ac_coverage") or []
        deps = item.get("depends_on") or prev.get("depends_on") or []
        verif = item.get("verification") or prev.get("verification") or "Run automated tests"
        files = item.get("files") or prev.get("files") or []

        desc_md = (
            f"Task ID: {t_id}\n"
            f"Covers AC: {', '.join(ac_cov) if ac_cov else 'Enabling work'}\n"
            f"Depends on: {', '.join(deps) if deps else 'None'}\n"
            f"Verification: {verif}"
        )
        sub_key = prev.get("jira_subtask_key") or client.create_subtask(jira_key, f"[{t_id}] {title}", desc_md, idx)
        normalized_tasks.append({
            "id": t_id,
            "jira_subtask_key": sub_key,
            "title": title,
            "ac_coverage": ac_cov,
            "depends_on": deps,
            "files": files,
            "verification": verif,
            "status": prev.get("status", "pending"),
            "commit": prev.get("commit"),
            "changed_files": prev.get("changed_files", []),
            "test_result": prev.get("test_result"),
            "completed_at": prev.get("completed_at"),
        })

    if has_dependency_cycle(normalized_tasks):
        print("ERROR: Task dependency cycle detected in create-subtasks.", file=sys.stderr)
        return 1

    story["tasks"] = normalized_tasks
    story["station"] = "dev"
    saved_path = save_active_story(repo_root, jira_key, story)
    out = {
        "ok": True,
        "action": "create-subtasks",
        "jira_key": jira_key,
        "task_count": len(normalized_tasks),
        "subtasks": [{"id": t["id"], "jira_subtask_key": t["jira_subtask_key"], "title": t["title"]} for t in normalized_tasks],
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
    client = JiraClient(config, offline=args.offline)
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key)

    target_id = args.task_id.strip()
    commit_sha = args.commit.strip()
    changed_files = [f.strip() for f in (args.files or "").split(",") if f.strip()]
    test_result = args.test_result.strip()

    if not commit_sha or not test_result:
        print("ERROR: --commit and --test-result are required to complete a task.", file=sys.stderr)
        return 1

    matched_item = None
    item_kind = "task"
    for t in story.get("tasks") or []:
        if t["id"] == target_id or t.get("jira_subtask_key") == target_id:
            matched_item = t
            break

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

    if item_kind == "task":
        completed_ids = {t["id"] for t in (story.get("tasks") or []) if t.get("status") == "completed"}
        unmet_deps = [d for d in (matched_item.get("depends_on") or []) if d not in completed_ids]
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
    story["candidate_commit"] = commit_sha

    review = story.get("review")
    if isinstance(review, dict) and review.get("status") == "changes-requested":
        open_blocking = [
            f for f in (review.get("findings") or [])
            if isinstance(f, dict) and f.get("severity") in ("blocker", "major") and f.get("status", "open") == "open"
        ]
        if not open_blocking:
            review["status"] = "remediated"

    jira_issue_key = (
        matched_item.get("jira_subtask_key")
        if item_kind == "task"
        else (matched_item.get("bug_key") if item_kind == "bug" else None)
    )
    done_status = config.get("status_mapping", {}).get("done", "Done")
    comment_md = (
        f"[Task Completed: {target_id}]\n"
        f"- Commit: `{commit_sha}`\n"
        f"- Changed Files: {', '.join(changed_files) if changed_files else 'N/A'}\n"
        f"- Verification Evidence: {test_result}"
    )
    if jira_issue_key:
        client.add_comment(jira_issue_key, comment_md)
        client.transition_issue(jira_issue_key, done_status)

    remaining_tasks = [t["id"] for t in (story.get("tasks") or []) if t.get("status") != "completed"]
    remaining_bugs = [b.get("bug_key") for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]

    saved_path = save_active_story(repo_root, jira_key, story)
    out = {
        "ok": True,
        "action": "complete-task",
        "jira_key": jira_key,
        "completed_id": target_id,
        "kind": item_kind,
        "commit": commit_sha,
        "remaining_tasks": remaining_tasks,
        "remaining_bugs": remaining_bugs,
        "ready_for_review": len(remaining_tasks) == 0 and len(remaining_bugs) == 0,
        "active_file": str(saved_path.relative_to(repo_root)),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 6: handoff (Station 3a: team-review & cross-station handoff)
# ---------------------------------------------------------------------------

def cmd_handoff(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline)
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key)

    effective_rev_role, _, self_reviewed = resolve_effective_reviewer(config)
    from_role = args.from_role.strip() if args.from_role else effective_rev_role
    if from_role == "reviewer" and effective_rev_role == "independent_reviewer":
        from_role = "independent_reviewer"
    to_role = args.to_role.strip()
    review_status = args.review_status or "approved"
    commit_sha = (args.commit or story.get("candidate_commit") or "unknown").strip()
    summary = (args.summary or "").strip()
    findings = load_json_arg(args.findings_json) if args.findings_json else []

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
    if isinstance(findings, list):
        blocking = [
            f for f in findings
            if f.get("severity") in ("blocker", "major") and f.get("status", "open") == "open"
        ]
        if blocking and review_status == "approved":
            print("ERROR: Cannot set --review-status approved while open blocker/major findings exist.", file=sys.stderr)
            return 1

    if from_role != "reviewer":
        dev_person = resolve_role_person(config, "developer")
        from_person = resolve_role_person(config, from_role)
        self_reviewed = is_same_person(dev_person, from_person)

    story["review"] = {
        "status": review_status,
        "reviewed_commit": commit_sha,
        "summary": summary,
        "findings": findings if isinstance(findings, list) else [],
        "self_reviewed_with_ai": self_reviewed,
        "reviewer_role": from_role,
        "reviewed_at": now_iso(),
    }

    if review_status == "changes-requested":
        effective_to_role = "developer"
        status_key = "in_progress"
    else:
        effective_to_role = to_role
        status_key = args.target_status or ("in_qa" if effective_to_role == "qa" else "ready_for_release")

    comment_lines = [
        f"[Station 3a: Dev Handoff & Code Review — {review_status.upper()}]",
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
    )

    saved_path = save_active_story(repo_root, jira_key, story)
    out = {
        "ok": True,
        "action": "handoff",
        "jira_key": jira_key,
        "review_status": review_status,
        "reviewed_commit": commit_sha,
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


def cmd_record_qa(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline)
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key)

    qa = story.get("qa") or {"status": "planned", "test_cases": []}

    if args.mode == "plan":
        raw_cases = []
        if args.cases_json:
            loaded = load_json_arg(args.cases_json)
            if isinstance(loaded, dict) and "test_cases" in loaded:
                raw_cases = loaded["test_cases"]
            elif isinstance(loaded, list):
                raw_cases = loaded
        if args.tc:
            for idx, tc_str in enumerate(args.tc, start=len(raw_cases) + 1):
                # Format: TC-001:AC-001:automated:Title/expected
                parts = [p.strip() for p in tc_str.split(":")]
                tc_id = parts[0] if (parts and re.match(r"^TC-\d+", parts[0])) else f"TC-{idx:03d}"
                ac_id = parts[1] if len(parts) > 1 else "AC-001"
                exec_type = parts[2].lower() if len(parts) > 2 and parts[2].lower() in ("automated", "manual") else "automated"
                title = parts[3] if len(parts) > 3 else tc_str
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
            tc_id = c.get("id") or f"TC-{idx:03d}"
            normalized_cases.append({
                "id": tc_id,
                "ac_id": c.get("ac_id") or "AC-001",
                "title": c.get("title") or tc_id,
                "execution_type": c.get("execution_type") or "automated",
                "recommended_by_ai": True,
                "result": c.get("result") or "not-executed",
                "notes": c.get("notes") or "",
            })

        uncovered_acs = find_uncovered_story_acs(story, normalized_cases)
        if uncovered_acs:
            print(
                f"ERROR: Planned test cases do not cover Story AC(s): {', '.join(uncovered_acs)}. Every AC-* must map to at least one TC-*.",
                file=sys.stderr,
            )
            return 1

        qa["test_cases"] = normalized_cases
        qa["status"] = "planned"
        qa["planned_at"] = now_iso()
        story["qa"] = qa

        table_lines = [
            f"[Station 3b: QA Test Plan — {jira_key}]",
            "| TC ID | AC ID | Type (AI Pre-filled) | Title |",
            "|---|---|---|---|",
        ]
        for c in normalized_cases:
            table_lines.append(f"| {c['id']} | {c['ac_id']} | {c['execution_type']} | {c['title']} |")
        client.add_comment(jira_key, "\n".join(table_lines))

        saved_path = save_active_story(repo_root, jira_key, story)
        print(json.dumps({
            "ok": True,
            "action": "record-qa",
            "mode": "plan",
            "jira_key": jira_key,
            "tc_count": len(normalized_cases),
            "active_file": str(saved_path.relative_to(repo_root)),
        }, ensure_ascii=False, indent=2))
        return 0

    # verify mode
    test_cases = qa.get("test_cases") or []
    if not test_cases:
        print(f"ERROR: No planned test cases exist in {jira_key}. Run record-qa --mode plan first.", file=sys.stderr)
        return 1

    known_tc_ids = {c["id"] for c in test_cases}
    incoming_results = {}
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
                tc_id = str(k).strip()
                if tc_id not in known_tc_ids:
                    print(f"ERROR: Unknown test case ID '{tc_id}' in --results-json (planned: {sorted(known_tc_ids)}).", file=sys.stderr)
                    return 1
                incoming_results[tc_id] = str(v).strip().lower()
        elif isinstance(loaded, list):
            for item in loaded:
                if isinstance(item, dict) and item.get("id"):
                    tc_id = str(item["id"]).strip()
                    if tc_id not in known_tc_ids:
                        print(f"ERROR: Unknown test case ID '{tc_id}' in --results-json (planned: {sorted(known_tc_ids)}).", file=sys.stderr)
                        return 1
                    incoming_results[tc_id] = str(item.get("result", "not-executed")).strip().lower()

    if args.result:
        for r_item in args.result:
            if "=" not in r_item:
                print(
                    f"ERROR: Invalid --result format '{r_item}'. Expected 'TC-ID=pass|fail|blocked|not-executed'.",
                    file=sys.stderr,
                )
                return 1
            tc_id, val = r_item.split("=", 1)
            tc_id = tc_id.strip()
            if tc_id not in known_tc_ids:
                print(f"ERROR: Unknown test case ID '{tc_id}' in --result (planned: {sorted(known_tc_ids)}).", file=sys.stderr)
                return 1
            incoming_results[tc_id] = val.strip().lower()

    allowed_results = {"pass", "fail", "blocked", "not-executed"}
    for c in test_cases:
        if c["id"] in incoming_results:
            val = incoming_results[c["id"]]
            if val not in allowed_results:
                print(f"ERROR: Invalid test result '{val}' for {c['id']}. Allowed: {sorted(allowed_results)}", file=sys.stderr)
                return 1
            c["result"] = val

    counts = {"pass": 0, "fail": 0, "blocked": 0, "not-executed": 0}
    for c in test_cases:
        st = c.get("result", "not-executed")
        counts[st] = counts.get(st, 0) + 1

    tested_commit = (args.commit or story.get("candidate_commit") or "unknown").strip()
    open_bugs = [b for b in (story.get("bugs") or []) if b.get("status") not in ("completed", "closed")]
    uncovered_acs = find_uncovered_story_acs(story, test_cases)

    if counts["fail"] > 0 or counts["blocked"] > 0:
        qa_status = "failed"
    elif counts["not-executed"] > 0 or uncovered_acs:
        qa_status = "incomplete"
    elif open_bugs:
        qa_status = "blocked-by-open-bugs"
    else:
        qa_status = "passed"

    qa["status"] = qa_status
    qa["tested_commit"] = tested_commit
    qa["counts"] = counts
    qa["verified_at"] = now_iso()
    story["qa"] = qa

    table_lines = [
        f"[Station 3b: QA Verification Report — {qa_status.upper()}]",
        f"- Tested Commit: `{tested_commit}`",
        f"- Summary Counts: pass={counts['pass']}, fail={counts['fail']}, blocked={counts['blocked']}, not-executed={counts['not-executed']}",
        "",
        "| TC ID | AC ID | Type | Result |",
        "|---|---|---|---|",
    ]
    for c in test_cases:
        table_lines.append(f"| {c['id']} | {c['ac_id']} | {c['execution_type']} | **{c['result']}** |")

    handoff_info = None
    if qa_status == "passed":
        handoff_info = execute_role_handoff(
            client=client,
            config=config,
            story=story,
            from_role="qa",
            to_role="release_manager",
            status_key="ready_for_release",
            comment_md="\n".join(table_lines),
        )
    else:
        client.add_comment(jira_key, "\n".join(table_lines))

    saved_path = save_active_story(repo_root, jira_key, story)
    print(json.dumps({
        "ok": True,
        "action": "record-qa",
        "mode": "verify",
        "jira_key": jira_key,
        "qa_status": qa_status,
        "tested_commit": tested_commit,
        "counts": counts,
        "handoff": handoff_info,
        "active_file": str(saved_path.relative_to(repo_root)),
    }, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 8: create-bug (Station 3b: team-qa defect loop)
# ---------------------------------------------------------------------------

def cmd_create_bug(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline)
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key)

    bugs = story.setdefault("bugs", [])
    bug_idx = len(bugs) + 1
    desc_md = (
        f"Linked Story: {jira_key}\n"
        f"Failed Test Case: {args.tc_id}\n"
        f"Affected AC: {args.ac_id or 'N/A'}\n"
        f"Severity: {args.severity}\n"
        f"Tested Commit: {args.commit or story.get('candidate_commit') or 'unknown'}\n\n"
        f"### Reproduction Steps\n{args.steps}\n\n"
        f"### Expected Result\n{args.expected}\n\n"
        f"### Actual Result\n{args.actual}"
    )
    bug_key = client.create_bug(jira_key, f"[{jira_key}][{args.tc_id}] {args.title}", desc_md, bug_idx)
    bug_record = {
        "bug_key": bug_key,
        "tc_id": args.tc_id,
        "ac_id": args.ac_id or "",
        "title": args.title,
        "severity": args.severity,
        "steps": args.steps,
        "expected": args.expected,
        "actual": args.actual,
        "reported_commit": args.commit or story.get("candidate_commit") or "unknown",
        "status": "open",
        "created_at": now_iso(),
    }
    bugs.append(bug_record)

    # Mark matching TC as fail and QA status as failed
    qa = story.setdefault("qa", {"status": "failed", "test_cases": []})
    qa["status"] = "failed"
    for c in qa.get("test_cases") or []:
        if c["id"] == args.tc_id:
            c["result"] = "fail"

    handoff_info = execute_role_handoff(
        client=client,
        config=config,
        story=story,
        from_role="qa",
        to_role="developer",
        status_key="in_progress",
        comment_md=f"[Bug Created: {bug_key}] 測試案例 `{args.tc_id}` 失敗（Severity: `{args.severity}`）：{args.title}。請修復並執行 `complete-task --task-id {bug_key}`。",
    )

    saved_path = save_active_story(repo_root, jira_key, story)
    print(json.dumps({
        "ok": True,
        "action": "create-bug",
        "jira_key": jira_key,
        "bug_key": bug_key,
        "severity": args.severity,
        "handoff": handoff_info,
        "active_file": str(saved_path.relative_to(repo_root)),
    }, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand 9: close-story (Station 4: team-release)
# ---------------------------------------------------------------------------

def cmd_close_story(args) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = load_team_config(repo_root)
    client = JiraClient(config, offline=args.offline)
    jira_key = validate_jira_key(args.key)
    story = load_active_story(repo_root, jira_key)

    commit_sha = args.commit.strip()
    version = args.version.strip()
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
    non_passing_tcs = [c["id"] for c in test_cases if c.get("result") != "pass"]
    if qa.get("status") != "passed":
        unmet_gates.append(f"QA verification status is '{qa.get('status')}' (must be 'passed').")
    elif qa.get("tested_commit") and qa.get("tested_commit") != commit_sha:
        unmet_gates.append(
            f"Candidate commit '{commit_sha}' does not match QA tested commit '{qa.get('tested_commit')}'."
        )
    if uncovered_acs:
        unmet_gates.append(f"Uncovered Story ACs in QA test cases: {', '.join(uncovered_acs)}.")
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
    release_md = (
        f"[Station 4: Release & Closure — {version}]\n"
        f"- Jira Story: `{jira_key}`\n"
        f"- Release Version / Tag: `{version}`\n"
        f"- Verified Commit: `{commit_sha}`\n"
        f"- Coverage: {ac_count} ACs, {len(tasks)} Sub-tasks Done, {tc_count} QA Test Cases Passed, 0 Open Bugs\n"
        f"- Release Notes: {args.notes or 'Released and verified through 4-station workflow.'}"
    )

    done_status = config.get("status_mapping", {}).get("done", "Done")
    client.add_comment(jira_key, release_md)
    trans_res = client.transition_issue(jira_key, done_status)

    active_file = get_active_dir(repo_root) / f"{jira_key}.json"
    if active_file.exists():
        active_file.unlink()

    print(json.dumps({
        "ok": True,
        "action": "close-story",
        "jira_key": jira_key,
        "version": version,
        "commit": commit_sha,
        "jira_status": done_status,
        "transition": trans_res,
        "active_file_removed": True,
    }, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Argument Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jira_sync.py",
        description="Unified Jira & Local Active State CLI for the 4-Station Small-Team Workflow.",
    )
    p.add_argument("--repo-root", default=".", help="Path to repository root (default: current directory)")
    p.add_argument("--offline", action="store_true", help="Force local/offline mode even if Jira env vars are set")

    sub = p.add_subparsers(dest="subcommand", required=True)

    # 1. init
    p_init = sub.add_parser("init", help="Initialize .ai-workflow/team-config.json, .gitignore, and engineering-standards.md")
    p_init.add_argument("--project-key", default="PROJ", help="Jira project key (e.g. PROJ)")
    p_init.add_argument("--jira-url", default=None, help="Jira base URL (e.g. https://org.atlassian.net)")
    p_init.add_argument("--roles-json", help="JSON string or file mapping roles to people")
    p_init.add_argument("--status-mapping-json", help="JSON string or file mapping workflow states to Jira statuses")
    p_init.add_argument("--agents-md", default="AGENTS.md", help="Relative path to AGENTS.md")
    p_init.add_argument("--standards-md", default="docs/shared/engineering-standards.md", help="Relative path to engineering-standards.md")
    p_init.set_defaults(func=cmd_init)

    # 2. status
    p_status = sub.add_parser("status", help="Inspect active workflow state and recommend the next station/role")
    p_status.add_argument("--key", help="Specific Jira Story key (e.g. PROJ-101)")
    p_status.add_argument("--fetch-remote", action="store_true", help="Fetch remote Jira issue details when in live mode")
    p_status.set_defaults(func=cmd_status)

    # 3. sync-story
    p_sync = sub.add_parser("sync-story", help="Sync Story specification to .ai-workflow/active/<KEY>.json and Jira")
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
    p_sync.add_argument("--ac", action="append", help="Acceptance criterion (e.g. 'AC-001:User can export CSV')")
    p_sync.add_argument("--design-summary", help="Technical design summary")
    p_sync.add_argument("--ui-spec", help="UI states, wireframe, and copy (only stored when --has-ui is true)")
    p_sync.add_argument("--api-spec", help="API contract specification (only stored when --has-api is true)")
    p_sync.add_argument("--auto-decision", action="append", help="AI auto-decided conventional detail (抓大放小)")
    p_sync.add_argument("--material-decision", action="append", help="Human-confirmed material decision (max 3)")
    p_sync.add_argument("--spec-json", help="JSON string or file with full story spec fields")
    p_sync.add_argument("--status", choices=["draft", "approved"], help="Spec status (draft or approved)")
    p_sync.add_argument("--approved-by", help="Role or person approving the spec")
    p_sync.add_argument("--create", action="store_true", help="Create a new Story in Jira when in live mode")
    p_sync.set_defaults(func=cmd_sync_story)

    # 4. create-subtasks
    p_subtasks = sub.add_parser("create-subtasks", help="Create TASK-001..N in active JSON and Jira Sub-tasks")
    p_subtasks.add_argument("--key", required=True, help="Jira Story key")
    p_subtasks.add_argument("--tasks-json", help="JSON string or file defining tasks")
    p_subtasks.add_argument("--task", action="append", help="Task spec 'TASK-001:Title:AC-001,AC-002:Verification'")
    p_subtasks.add_argument("--proceed-at-risk", action="store_true", help="Allow planning tasks before spec approval")
    p_subtasks.set_defaults(func=cmd_create_subtasks)

    # 5. complete-task
    p_comp = sub.add_parser("complete-task", help="Mark a TASK-###, BUG-*, or REV-### completed with commit and test evidence")
    p_comp.add_argument("--key", required=True, help="Jira Story key")
    p_comp.add_argument("--task-id", required=True, help="Task ID (TASK-001), Bug key (PROJ-101-BUG-1), or Finding ID (REV-001)")
    p_comp.add_argument("--commit", required=True, help="Git commit SHA implementing the task/fix")
    p_comp.add_argument("--files", required=True, help="Comma-separated list of changed file paths")
    p_comp.add_argument("--test-result", required=True, help="Executed test command and pass summary")
    p_comp.set_defaults(func=cmd_complete_task)

    # 6. handoff
    p_handoff = sub.add_parser("handoff", help="Record Code Review & handoff with multi-role notification awareness")
    p_handoff.add_argument("--key", required=True, help="Jira Story key")
    p_handoff.add_argument("--from-role", default="reviewer", help="Source role (default: reviewer)")
    p_handoff.add_argument("--to-role", default="qa", help="Target role (default: qa)")
    p_handoff.add_argument("--review-status", choices=["approved", "changes-requested"], default="approved")
    p_handoff.add_argument("--commit", help="Reviewed git commit SHA")
    p_handoff.add_argument("--summary", help="Handoff and review summary")
    p_handoff.add_argument("--findings-json", help="JSON string or file with review findings")
    p_handoff.add_argument("--target-status", help="Optional status_mapping key override")
    p_handoff.set_defaults(func=cmd_handoff)

    # 7. record-qa
    p_qa = sub.add_parser("record-qa", help="Record QA test plan (plan) or verification results (verify)")
    p_qa.add_argument("--key", required=True, help="Jira Story key")
    p_qa.add_argument("--mode", choices=["plan", "verify"], required=True, help="QA mode: plan or verify")
    p_qa.add_argument("--cases-json", help="JSON string or file with test cases for plan mode")
    p_qa.add_argument("--tc", action="append", help="Test case 'TC-001:AC-001:automated|manual:Title'")
    p_qa.add_argument("--junit-xml", help="Path to JUnit XML report for verify mode")
    p_qa.add_argument("--results-json", help="JSON string or file with TC results for verify mode")
    p_qa.add_argument("--result", action="append", help="Single TC result 'TC-001=pass|fail|blocked'")
    p_qa.add_argument("--commit", help="Tested git commit SHA")
    p_qa.set_defaults(func=cmd_record_qa)

    # 8. create-bug
    p_bug = sub.add_parser("create-bug", help="Create a linked Jira Bug ticket on QA failure and route to Developer")
    p_bug.add_argument("--key", required=True, help="Jira Story key")
    p_bug.add_argument("--tc-id", required=True, help="Failing test case ID (e.g. TC-002)")
    p_bug.add_argument("--ac-id", help="Affected AC ID (e.g. AC-001)")
    p_bug.add_argument("--title", required=True, help="Concise bug title")
    p_bug.add_argument("--severity", choices=["blocker", "major", "minor"], default="major")
    p_bug.add_argument("--steps", required=True, help="Reproduction steps")
    p_bug.add_argument("--expected", required=True, help="Expected behavior")
    p_bug.add_argument("--actual", required=True, help="Actual observed behavior")
    p_bug.add_argument("--commit", help="Tested commit SHA where the bug occurred")
    p_bug.set_defaults(func=cmd_create_bug)

    # 9. close-story
    p_close = sub.add_parser("close-story", help="Verify all gates, transition Story to Done, and delete active JSON")
    p_close.add_argument("--key", required=True, help="Jira Story key")
    p_close.add_argument("--version", required=True, help="Release version or tag (e.g. v1.2.0)")
    p_close.add_argument("--commit", required=True, help="Release candidate git commit SHA")
    p_close.add_argument("--notes", default="", help="Release summary notes")
    p_close.set_defaults(func=cmd_close_story)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
