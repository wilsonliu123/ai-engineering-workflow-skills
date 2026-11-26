#!/usr/bin/env python3
"""
End-to-end and unit test suite for the planning layer + 4-station, 8-skill Jira-centered workflow.
Tests:
  1. Skill structure, YAML frontmatter, evals.json validity, and shared-file byte-identity
  2. team-init: .ai-workflow/team-config.json, .gitignore, AGENTS.md extraction & deduplication, idempotency
  3. team-next-step (status): uninitialized, new story, spec -> dev plan -> dev implement -> review -> qa -> bug loop -> release, and multi-story ambiguity
  4. team-spec (sync-story): conditional UI/API sections, 抓大放小 max-3 material decisions limit, missing AC guard, same-person vs cross-person handoff
  5. team-dev (create-subtasks & complete-task): cycle detection, unapproved spec guard, task completion & commit tracking
  6. team-review (handoff): blocker finding guard, self-review transparency flag, cross-person Jira @mention vs same-person auto-consolidation
  7. team-qa (record-qa & create-bug): plan mode pre-filled types, verify mode JUnit XML & CLI intake, create-bug routing back to developer
  8. team-release (close-story): gate enforcement (blocks on open bug, incomplete task, commit drift), Story closure, and active JSON deletion
  9. Live Jira REST API v3 mode against a local mock HTTP server (Basic Auth, ADF payloads, transitions, assignee updates, issue links)
 10. team-plan Sprint preview/apply/status against mocked Jira Agile Sprint endpoints
"""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
JIRA_SYNC = SKILLS_DIR / "team-init" / "scripts" / "jira_sync.py"

EXPECTED_SKILLS = {
    "team-init",
    "team-next-step",
    "team-spec",
    "team-dev",
    "team-review",
    "team-qa",
    "team-release",
    "team-plan",
}


def run_cli(repo_root: Path, *args, env_override: dict = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_override is None:
        for k in list(env.keys()):
            if k in (
                "JIRA_BASE_URL",
                "JIRA_URL",
                "JIRA_EMAIL",
                "JIRA_API_TOKEN",
                "QASE_API_TOKEN",
                "QASE_PROJECT_CODE",
                "QASE_BASE_URL",
                "QASE_APP_URL",
                "QASE_JIRA_INTEGRATION_TYPE",
            ) or k.startswith("JIRA_EMAIL_") or k.startswith("JIRA_API_TOKEN_"):
                env.pop(k, None)
    else:
        for k, v in env_override.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = str(v)
    cmd = [sys.executable, str(JIRA_SYNC), "--repo-root", str(repo_root), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _parse_multipart_files(content_type: str, raw_bytes: bytes) -> list:
    header_bytes = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    msg = BytesParser(policy=email_default_policy).parsebytes(header_bytes + raw_bytes)
    files = []
    for part in msg.iter_parts():
        if part.get_content_disposition() == "form-data":
            field_name = part.get_param("name", header="content-disposition")
            filename = part.get_filename() or ""
            payload = part.get_payload(decode=True) or b""
            files.append({
                "field": field_name,
                "filename": filename,
                "mime": part.get_content_type(),
                "bytes": payload,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
    return files


class MockJiraHandler(BaseHTTPRequestHandler):
    requests_log = []
    stored_issues = {}
    stored_properties = {}
    stored_attachments = {}
    stored_sprints = {}
    next_sprint_id = 900
    simulate_put_404_keys = set()
    reject_subtask_types = set()
    reject_attachment_keys = set()
    reject_resolution_transition_keys = set()
    reject_parent_on_create_epics = set()
    reject_parent_jql_epics = set()
    fail_create_story_titles = set()
    simulate_get_404_keys = set()
    reject_customfield_on_create_epics = set()

    def log_message(self, format, *args):
        pass

    def _read_raw(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length > 0 else b""

    def _read_json(self):
        raw = self._read_raw().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _send_json(self, code: int, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.requests_log.append(("GET", self.path, None, dict(self.headers)))
        clean_path = self.path.split("?", 1)[0]
        if self.path == "/rest/api/3/myself":
            auth_hdr = self.headers.get("Authorization", "")
            decoded_auth = ""
            if auth_hdr.startswith("Basic "):
                import base64
                try:
                    decoded_auth = base64.b64decode(auth_hdr.split(" ", 1)[1]).decode("utf-8")
                except Exception:
                    decoded_auth = ""
            if decoded_auth.endswith(":invalid-token"):
                self._send_json(401, {"errorMessages": ["Client must be authenticated to access this resource."]})
            elif decoded_auth.startswith("carol@team.io:"):
                self._send_json(200, {
                    "accountId": "acc-carol-from-token",
                    "displayName": "Carol Wu",
                    "emailAddress": "carol@team.io",
                })
            elif decoded_auth.startswith("bob@team.io:"):
                self._send_json(200, {
                    "accountId": "acc-bob-from-token",
                    "displayName": "Bob Lin",
                    "emailAddress": "bob@team.io",
                })
            elif decoded_auth.startswith("dave@team.io:"):
                self._send_json(200, {
                    "accountId": "acc-dave-from-token",
                    "displayName": "Dave Ho",
                    "emailAddress": "dave@team.io",
                })
            else:
                self._send_json(200, {
                    "accountId": "acc-alice",
                    "displayName": "Alice Chen",
                    "emailAddress": "alice@team.io",
                })
        elif self.path.startswith("/rest/agile/1.0/board/") and "/sprint" in self.path:
            board_id = self.path.split("/rest/agile/1.0/board/", 1)[1].split("/", 1)[0]
            sprints = [s for s in self.stored_sprints.values() if str(s.get("originBoardId")) == board_id]
            self._send_json(200, {"values": sprints, "isLast": True})
        elif clean_path.startswith("/rest/agile/1.0/sprint/") and clean_path.endswith("/issue"):
            sprint_id = clean_path.split("/rest/agile/1.0/sprint/", 1)[1].split("/", 1)[0]
            sprint = self.stored_sprints.get(sprint_id) or {}
            issues = [self.stored_issues[k] for k in sprint.get("issue_keys", []) if k in self.stored_issues]
            self._send_json(200, {"issues": issues})
        elif clean_path.startswith("/rest/agile/1.0/sprint/"):
            sprint_id = clean_path.split("/rest/agile/1.0/sprint/", 1)[1].split("/", 1)[0]
            if sprint_id in self.stored_sprints:
                self._send_json(200, self.stored_sprints[sprint_id])
            else:
                self._send_json(404, {"errorMessages": ["Sprint not found"]})
        elif self.path.startswith("/rest/api/3/user/search"):
            if "bob%40team.io" in self.path or "bob@team.io" in self.path:
                self._send_json(200, [{"accountId": "acc-bob-resolved", "emailAddress": "bob@team.io", "displayName": "Bob Lin"}])
            elif "carol%40team.io" in self.path or "carol@team.io" in self.path:
                self._send_json(200, [{"accountId": "acc-carol-resolved", "emailAddress": "carol@team.io", "displayName": "Carol Wu"}])
            else:
                self._send_json(200, [{"accountId": "acc-alice-resolved", "emailAddress": "alice@team.io", "displayName": "Alice Chen"}])
        elif self.path.endswith("/transitions"):
            self._send_json(200, {
                "transitions": [
                    {"id": "11", "name": "In Progress", "to": {"name": "In Progress"}},
                    {"id": "15", "name": "Dev Completed", "to": {"name": "Dev Completed"}},
                    {"id": "21", "name": "In QA", "to": {"name": "In QA"}},
                    {"id": "31", "name": "Ready for Release", "to": {"name": "Ready for Release"}},
                    {"id": "41", "name": "Done", "to": {"name": "Done"}},
                ]
            })
        elif self.path.startswith("/rest/api/3/issue/") and "/properties/ai_workflow_state" in self.path:
            key = self.path.split("/rest/api/3/issue/", 1)[1].split("/")[0]
            if key in self.stored_properties:
                self._send_json(200, {"key": "ai_workflow_state", "value": self.stored_properties[key]})
            else:
                self._send_json(404, {"errorMessages": ["Property not found"]})
        elif self.path.startswith("/rest/api/3/issue/"):
            key = self.path.split("/rest/api/3/issue/", 1)[1].split("/")[0]
            if key in self.simulate_get_404_keys:
                self._send_json(404, {"errorMessages": ["Issue Does Not Exist"]})
            elif key in self.stored_issues:
                self._send_json(200, self.stored_issues[key])
            else:
                self._send_json(200, {"key": key, "fields": {"summary": key, "status": {"name": "In Progress"}}})
        else:
            self._send_json(200, {"key": "PROJ-101", "fields": {"status": {"name": "In Progress"}}})

    def do_POST(self):
        ctype = self.headers.get("Content-Type", "")
        if self.path.startswith("/rest/api/3/issue/") and self.path.endswith("/attachments"):
            raw = self._read_raw()
            key = self.path.split("/rest/api/3/issue/", 1)[1].split("/")[0]
            self.requests_log.append(("POST", self.path, {"multipart_len": len(raw)}, dict(self.headers)))
            if self.headers.get("X-Atlassian-Token") != "no-check":
                self._send_json(403, {"errorMessages": ["XSRF check failed"]})
                return
            if key in self.reject_attachment_keys:
                self._send_json(403, {"errorMessages": ["Attachments disabled for issue"]})
                return
            parsed_files = _parse_multipart_files(ctype, raw)
            out_items = []
            bucket = self.stored_attachments.setdefault(key, [])
            for idx, pf in enumerate(parsed_files, start=len(bucket) + 1):
                rec = {
                    "id": f"att-{key}-{idx}",
                    "filename": pf["filename"],
                    "size": pf["size"],
                    "mimeType": pf["mime"],
                    "sha256": pf["sha256"],
                    "field": pf["field"],
                    "content": f"http://jira.mock/secure/attachment/{idx}/{pf['filename']}",
                }
                bucket.append(rec)
                out_items.append(rec)
            self._send_json(200, out_items)
            return

        data = self._read_json()
        self.requests_log.append(("POST", self.path, data, dict(self.headers)))
        if self.path == "/rest/agile/1.0/sprint":
            sprint_id = str(self.next_sprint_id)
            self.next_sprint_id += 1
            sprint = {
                "id": int(sprint_id),
                "state": "future",
                "name": data.get("name") or sprint_id,
                "originBoardId": data.get("originBoardId"),
                "goal": data.get("goal"),
                "startDate": data.get("startDate"),
                "endDate": data.get("endDate"),
                "issue_keys": [],
            }
            self.stored_sprints[sprint_id] = sprint
            self._send_json(201, sprint)
            return
        if self.path.startswith("/rest/agile/1.0/sprint/") and self.path.endswith("/issue"):
            sprint_id = self.path.split("/rest/agile/1.0/sprint/", 1)[1].split("/", 1)[0]
            sprint = self.stored_sprints.setdefault(sprint_id, {"id": int(sprint_id), "issue_keys": []})
            for key in data.get("issues") or []:
                if key not in sprint.setdefault("issue_keys", []):
                    sprint["issue_keys"].append(key)
            self._send_json(204, {})
            return
        if self.path == "/rest/api/3/search/jql":
            jql = data.get("jql") or ""
            m_parent = re.search(r'parent\s*=\s*"([^"]+)"', jql, re.IGNORECASE)
            m_epic_link = re.search(r'"Epic Link"\s*=\s*"([^"]+)"', jql, re.IGNORECASE)
            if m_parent:
                p_key = m_parent.group(1).strip().upper()
                if p_key in self.reject_parent_jql_epics:
                    self._send_json(400, {"errorMessages": [f"Field 'parent' not supported for Epic {p_key}"]})
                    return
                issues = [
                    iss for iss in self.stored_issues.values()
                    if str(((iss.get("fields") or {}).get("parent") or {}).get("key") or "").strip().upper() == p_key
                ]
                self._send_json(200, {"issues": issues})
                return
            if m_epic_link:
                el_key = m_epic_link.group(1).strip().upper()
                issues = []
                for iss in self.stored_issues.values():
                    flds = iss.get("fields") or {}
                    p_match = str(((flds.get("parent") or {}).get("key") or "")).strip().upper() == el_key
                    cf_match = any(
                        k.startswith("customfield_") and str(v or "").strip().upper() == el_key
                        for k, v in flds.items()
                    )
                    if p_match or cf_match:
                        issues.append(iss)
                self._send_json(200, {"issues": issues})
                return
            issues = []
            for k, iss in self.stored_issues.items():
                st_name = (((iss.get("fields") or {}).get("status") or {}).get("name") or "").lower()
                if "statuscategory != done" in jql.lower() and st_name == "done":
                    continue
                issues.append(iss)
            self._send_json(200, {"issues": issues})
            return
        if self.path == "/rest/api/3/issue":
            fields = data.get("fields") or {}
            proj_key = (fields.get("project") or {}).get("key") or "PROJ"
            itype = (fields.get("issuetype") or {}).get("name", "")
            if itype in self.reject_subtask_types:
                self._send_json(400, {"errorMessages": [f"Issue type '{itype}' invalid"]})
                return
            if itype in ("Sub-task", "Subtask", "子任務"):
                existing_subs = [
                    k for k, v in self.stored_issues.items()
                    if ((v.get("fields") or {}).get("issuetype") or {}).get("subtask") is True
                    and k.startswith(f"{proj_key}-")
                ]
                sub_num = 102 + len(existing_subs)
                sub_key = f"{proj_key}-{sub_num}"
                parent_key = (fields.get("parent") or {}).get("key") or ""
                sub_obj = {
                    "key": sub_key,
                    "fields": {
                        "summary": fields.get("summary") or sub_key,
                        "description": fields.get("description"),
                        "issuetype": {"name": itype, "subtask": True},
                        "status": {"name": "To Do"},
                        "parent": {"key": parent_key},
                    },
                }
                self.stored_issues[sub_key] = sub_obj
                if parent_key and parent_key in self.stored_issues:
                    p_subs = self.stored_issues[parent_key].setdefault("fields", {}).setdefault("subtasks", [])
                    p_subs.append(sub_obj)
                self._send_json(201, {"key": sub_key})
            elif itype == "Bug":
                existing_bugs = [
                    k for k, v in self.stored_issues.items()
                    if ((v.get("fields") or {}).get("issuetype") or {}).get("name") == "Bug"
                    and k.startswith(f"{proj_key}-")
                ]
                bug_num = 999 - len(existing_bugs)
                bug_key = f"{proj_key}-{bug_num}"
                self.stored_issues[bug_key] = {
                    "key": bug_key,
                    "fields": {
                        "summary": fields.get("summary") or bug_key,
                        "description": fields.get("description"),
                        "issuetype": {"name": "Bug", "subtask": False},
                        "status": {"name": "To Do"},
                    },
                }
                self._send_json(201, {"key": bug_key})
            else:
                summary_text = fields.get("summary") or ""
                if summary_text in self.fail_create_story_titles:
                    self._send_json(500, {"errorMessages": [f"Simulated failure creating '{summary_text}'"]})
                    return
                parent_obj = fields.get("parent") if isinstance(fields.get("parent"), dict) else {}
                parent_key = str(parent_obj.get("key") or "").strip().upper()
                if parent_key and parent_key in self.reject_parent_on_create_epics:
                    self._send_json(400, {"errors": {"parent": "Field 'parent' cannot be set on create screen."}})
                    return
                for fk, fv in fields.items():
                    if fk.startswith("customfield_") and str(fv or "").strip().upper() in self.reject_customfield_on_create_epics:
                        self._send_json(400, {"errors": {fk: f"Field '{fk}' cannot be set on create screen."}})
                        return

                if "404" in summary_text:
                    key = f"{proj_key}-404"
                elif proj_key != "PROJ":
                    seq = 1
                    while f"{proj_key}-{seq}" in self.stored_issues:
                        seq += 1
                    key = f"{proj_key}-{seq}"
                else:
                    seq = 101
                    while f"PROJ-{seq}" in self.stored_issues:
                        seq += 1
                    key = f"PROJ-{seq}"

                has_epic_field = bool(parent_key) or any(k.startswith("customfield_") for k in fields)
                stored_fields = {
                    "summary": summary_text or key,
                    "description": fields.get("description"),
                    "issuetype": {"name": itype or "Story", "subtask": False},
                    "status": {"name": "To Do" if has_epic_field else "In Progress"},
                }
                if parent_obj:
                    stored_fields["parent"] = parent_obj
                if fields.get("priority"):
                    stored_fields["priority"] = fields["priority"]
                for fk, fv in fields.items():
                    if fk.startswith("customfield_"):
                        stored_fields[fk] = fv
                self.stored_issues[key] = {
                    "key": key,
                    "fields": stored_fields,
                }
                self._send_json(201, {"key": key})
        elif self.path == "/rest/api/3/issueLink":
            inward_key = ((data.get("inwardIssue") or {}).get("key") or "").strip()
            outward_key = ((data.get("outwardIssue") or {}).get("key") or "").strip()
            if outward_key in self.stored_issues and inward_key in self.stored_issues:
                links = self.stored_issues[outward_key].setdefault("fields", {}).setdefault("issuelinks", [])
                links.append({"type": data.get("type") or {"name": "Blocks"}, "inwardIssue": self.stored_issues[inward_key]})
            self._send_json(201, {"ok": True})
        elif self.path.startswith("/rest/api/3/issue/") and self.path.endswith("/transitions"):
            key = self.path.split("/rest/api/3/issue/", 1)[1].split("/")[0]
            if (data.get("fields") or {}).get("resolution") and key in self.reject_resolution_transition_keys:
                self._send_json(400, {"errors": {"resolution": "Field 'resolution' cannot be set on this screen."}})
                return
            tid = str(((data.get("transition") or {}).get("id")) or "")
            id_to_status = {
                "11": "In Progress",
                "15": "Dev Completed",
                "21": "In QA",
                "31": "Ready for Release",
                "41": "Done",
            }
            if key in self.stored_issues and tid in id_to_status:
                self.stored_issues[key].setdefault("fields", {})["status"] = {"name": id_to_status[tid]}
                if (data.get("fields") or {}).get("resolution"):
                    self.stored_issues[key]["fields"]["resolution"] = data["fields"]["resolution"]
                elif tid != "41":
                    self.stored_issues[key]["fields"]["resolution"] = None
            self._send_json(204, {})
        else:
            self._send_json(200, {"ok": True})

    def do_PUT(self):
        data = self._read_json()
        self.requests_log.append(("PUT", self.path, data, dict(self.headers)))
        if self.path.startswith("/rest/agile/1.0/sprint/"):
            sprint_id = self.path.split("/rest/agile/1.0/sprint/", 1)[1].split("/", 1)[0]
            sprint = self.stored_sprints.setdefault(sprint_id, {"id": int(sprint_id), "issue_keys": []})
            sprint.update({k: v for k, v in data.items() if k != "id"})
            self._send_json(200, sprint)
            return
        if self.path.startswith("/rest/api/3/issue/") and "/properties/ai_workflow_state" in self.path:
            key = self.path.split("/rest/api/3/issue/", 1)[1].split("/")[0]
            self.stored_properties[key] = data
            self._send_json(200, {"ok": True})
            return
        if self.path.startswith("/rest/api/3/issue/") and self.path.endswith("/assignee"):
            key = self.path.split("/rest/api/3/issue/", 1)[1].split("/")[0]
            existing = self.stored_issues.get(key) or {"key": key, "fields": {"status": {"name": "In Progress"}}}
            acct_id = data.get("accountId")
            existing.setdefault("fields", {})["assignee"] = {"accountId": acct_id} if acct_id else None
            self.stored_issues[key] = existing
            self._send_json(204, {})
            return
        if self.path.startswith("/rest/api/3/issue/") and not self.path.endswith("/assignee"):
            key = self.path.split("/rest/api/3/issue/", 1)[1].split("/")[0]
            if key in self.simulate_put_404_keys:
                self.simulate_put_404_keys.discard(key)
                self._send_json(404, {"errorMessages": ["Issue Does Not Exist"]})
                return
            fields = data.get("fields") or {}
            existing = self.stored_issues.get(key) or {"key": key, "fields": {"status": {"name": "In Progress"}}}
            existing["fields"].update(fields)
            self.stored_issues[key] = existing
        self._send_json(204, {})


class MockQaseHandler(BaseHTTPRequestHandler):
    requests_log = []
    uploaded_files_log = []
    suites = {}
    cases = {}
    runs = {}
    attachments = {}
    defects = {}
    next_suite_id = 10
    next_case_id = 100
    next_run_id = 500
    next_defect_id = 700
    expected_token = "qase-secret-123"
    fail_external_issue = False

    def log_message(self, format, *args):
        pass

    def _read_raw(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length > 0 else b""

    def _read_json(self):
        raw = self._read_raw().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _send_json(self, code: int, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        tok = self.headers.get("Token", "")
        if tok != self.expected_token:
            self._send_json(401, {"status": False, "errorMessage": "Invalid API token"})
            return False
        return True

    def do_GET(self):
        self.requests_log.append(("GET", self.path, None, dict(self.headers)))
        if not self._check_auth():
            return
        if self.path.startswith("/v1/project/"):
            code = self.path.split("/v1/project/", 1)[1].split("?")[0]
            self._send_json(200, {"status": True, "result": {"code": code, "title": code}})
        else:
            self._send_json(200, {"status": True, "result": {}})

    def do_POST(self):
        ctype = self.headers.get("Content-Type", "")
        if self.path.startswith("/v1/attachment/"):
            raw = self._read_raw()
            self.requests_log.append(("POST", self.path, {"multipart_len": len(raw)}, dict(self.headers)))
            if not self._check_auth():
                return
            code = self.path.split("/v1/attachment/", 1)[1].split("?")[0]
            parsed_files = _parse_multipart_files(ctype, raw)
            out_items = []
            for pf in parsed_files:
                MockQaseHandler.uploaded_files_log.append(pf["filename"])
                h = hashlib.sha1(pf["bytes"] + pf["filename"].encode("utf-8")).hexdigest()
                rec = {
                    "filename": pf["filename"],
                    "url": f"https://api.qase.io/v1/files/{code}/{h}/{pf['filename']}",
                    "hash": h,
                    "mime": pf["mime"],
                    "size": pf["size"],
                    "sha256": pf["sha256"],
                    "field": pf["field"],
                }
                MockQaseHandler.attachments[h] = rec
                out_items.append(rec)
            self._send_json(200, {"status": True, "result": out_items})
            return

        data = self._read_json()
        self.requests_log.append(("POST", self.path, data, dict(self.headers)))
        if not self._check_auth():
            return
        if self.path.startswith("/v1/suite/"):
            sid = MockQaseHandler.next_suite_id
            MockQaseHandler.next_suite_id += 1
            MockQaseHandler.suites[sid] = data
            self._send_json(200, {"status": True, "result": {"id": sid}})
        elif self.path.endswith("/external-issue/attach") or self.path.endswith("/external-issue"):
            if MockQaseHandler.fail_external_issue:
                self._send_json(422, {"status": False, "errorMessage": "Jira integration not configured"})
            else:
                self._send_json(200, {"status": True, "result": {}})
        elif self.path.startswith("/v1/case/") and self.path.endswith("/bulk"):
            ids = []
            for item in data.get("cases") or []:
                cid = MockQaseHandler.next_case_id
                MockQaseHandler.next_case_id += 1
                MockQaseHandler.cases[cid] = item
                ids.append(cid)
            self._send_json(200, {"status": True, "result": {"ids": ids}})
        elif self.path.startswith("/v1/case/"):
            cid = MockQaseHandler.next_case_id
            MockQaseHandler.next_case_id += 1
            MockQaseHandler.cases[cid] = data
            self._send_json(200, {"status": True, "result": {"id": cid}})
        elif self.path.startswith("/v1/run/") and self.path.endswith("/complete"):
            rid = int(self.path.split("/v1/run/", 1)[1].split("/")[1])
            if rid in MockQaseHandler.runs:
                MockQaseHandler.runs[rid]["completed"] = True
            self._send_json(200, {"status": True, "result": {}})
        elif self.path.startswith("/v1/run/"):
            rid = MockQaseHandler.next_run_id
            MockQaseHandler.next_run_id += 1
            MockQaseHandler.runs[rid] = {"payload": data, "results": [], "completed": False}
            self._send_json(200, {"status": True, "result": {"id": rid}})
        elif self.path.startswith("/v1/result/") and self.path.endswith("/bulk"):
            rid = int(self.path.split("/v1/result/", 1)[1].split("/")[1])
            if rid in MockQaseHandler.runs:
                MockQaseHandler.runs[rid]["results"].extend(data.get("results") or [])
            self._send_json(200, {"status": True, "result": {}})
        elif self.path.startswith("/v1/result/"):
            rid = int(self.path.split("/v1/result/", 1)[1].split("/")[1])
            if rid in MockQaseHandler.runs:
                MockQaseHandler.runs[rid]["results"].append(data)
            self._send_json(200, {"status": True, "result": {"hash": "res-single-1"}})
        elif self.path.startswith("/v1/defect/"):
            did = MockQaseHandler.next_defect_id
            MockQaseHandler.next_defect_id += 1
            MockQaseHandler.defects[did] = {"payload": data, "resolved": False}
            self._send_json(200, {"status": True, "result": {"id": did}})
        else:
            self._send_json(200, {"status": True, "result": {}})

    def do_PATCH(self):
        data = self._read_json()
        self.requests_log.append(("PATCH", self.path, data, dict(self.headers)))
        if not self._check_auth():
            return
        if self.path.startswith("/v1/defect/") and self.path.endswith("/resolve"):
            parts = self.path.split("/v1/defect/", 1)[1].split("/")
            did = int(parts[1])
            if did in MockQaseHandler.defects:
                MockQaseHandler.defects[did]["resolved"] = True
            self._send_json(200, {"status": True, "result": {"id": did}})
        elif self.path.startswith("/v1/case/"):
            parts = self.path.split("/v1/case/", 1)[1].split("/")
            cid = int(parts[1])
            if cid not in MockQaseHandler.cases:
                self._send_json(404, {"status": False, "errorMessage": "Case not found"})
                return
            MockQaseHandler.cases[cid].update(data)
            self._send_json(200, {"status": True, "result": {"id": cid}})
        elif self.path.startswith("/v1/suite/"):
            parts = self.path.split("/v1/suite/", 1)[1].split("/")
            sid = int(parts[1])
            if sid not in MockQaseHandler.suites:
                self._send_json(404, {"status": False, "errorMessage": "Suite not found"})
                return
            MockQaseHandler.suites[sid].update(data)
            self._send_json(200, {"status": True, "result": {"id": sid}})
        else:
            self._send_json(200, {"status": True, "result": {}})


class WorkflowTestSuite(unittest.TestCase):

    def setUp(self):
        self._orig_cred_dir_env = os.environ.get("AI_WORKFLOW_CREDENTIALS_DIR")
        self._orig_prov_dir_env = os.environ.get("AI_WORKFLOW_PROVENANCE_DIR")
        self._orig_xdg_env = os.environ.get("XDG_CONFIG_HOME")
        self._isolated_cred_tmp = tempfile.TemporaryDirectory(prefix="aiwf-test-creds-")
        os.environ["AI_WORKFLOW_CREDENTIALS_DIR"] = str(Path(self._isolated_cred_tmp.name) / "creds")
        os.environ["AI_WORKFLOW_PROVENANCE_DIR"] = str(Path(self._isolated_cred_tmp.name) / "provenance")

    def tearDown(self):
        if self._orig_cred_dir_env is None:
            os.environ.pop("AI_WORKFLOW_CREDENTIALS_DIR", None)
        else:
            os.environ["AI_WORKFLOW_CREDENTIALS_DIR"] = self._orig_cred_dir_env
        if self._orig_prov_dir_env is None:
            os.environ.pop("AI_WORKFLOW_PROVENANCE_DIR", None)
        else:
            os.environ["AI_WORKFLOW_PROVENANCE_DIR"] = self._orig_prov_dir_env
        if self._orig_xdg_env is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg_env
        self._isolated_cred_tmp.cleanup()

    def test_01_skill_bundle_structure_and_shared_files(self):
        actual_skills = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and p.name != "system"}
        self.assertEqual(actual_skills, EXPECTED_SKILLS | {"team-provenance-audit"})

        for s in EXPECTED_SKILLS:
            skill_md = SKILLS_DIR / s / "SKILL.md"
            openai_yaml = SKILLS_DIR / s / "agents" / "openai.yaml"
            sync_py = SKILLS_DIR / s / "scripts" / "jira_sync.py"
            gov_md = SKILLS_DIR / s / "references" / "common-workflow-governance.md"
            self.assertTrue(skill_md.exists(), f"Missing {skill_md}")
            self.assertTrue(openai_yaml.exists(), f"Missing {openai_yaml}")
            self.assertTrue(sync_py.exists(), f"Missing {sync_py}")
            self.assertTrue(gov_md.exists(), f"Missing {gov_md}")

            text = skill_md.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), f"{skill_md} missing YAML frontmatter")
            self.assertIn(f"name: {s}", text)
            self.assertIn("description: Use when", text)

        # Verify standalone 9th skill: team-provenance-audit (never bundles jira_sync.py)
        pa_dir = SKILLS_DIR / "team-provenance-audit"
        pa_skill_md = pa_dir / "SKILL.md"
        self.assertTrue(pa_skill_md.exists())
        self.assertTrue((pa_dir / "agents" / "openai.yaml").exists())
        self.assertFalse((pa_dir / "scripts" / "jira_sync.py").exists())
        pa_text = pa_skill_md.read_text(encoding="utf-8")
        self.assertTrue(pa_text.startswith("---\n"))
        self.assertIn("name: team-provenance-audit", pa_text)
        for pa_script in ("check_env.py", "static_scan.py", "fetch_reference.py", "fingerprint.py", "gate.py"):
            self.assertTrue((pa_dir / "scripts" / pa_script).exists(), f"Missing team-provenance-audit script {pa_script}")

        # Verify check_shared.sh passes
        res = subprocess.run([str(REPO_ROOT / "tools" / "check_shared.sh")], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

        # Verify evals.json files are valid JSON
        for eval_skill in ("team-next-step", "team-qa", "team-plan"):
            eval_path = SKILLS_DIR / eval_skill / "evals" / "evals.json"
            self.assertTrue(eval_path.exists())
            data = json.loads(eval_path.read_text(encoding="utf-8"))
            self.assertEqual(data["skill_name"], eval_skill)
            self.assertGreaterEqual(len(data["evals"]), 3)

    def test_02_init_extracts_standards_and_deduplicates_agents_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents_md = root / "AGENTS.md"
            agents_md.write_text(
                "# Project Overview\nBuild command: `pytest`\n\n"
                "## Coding Standards\n- Always use type hints.\n- Never log raw passwords.\n\n"
                "## Security Rules\n- Validate all JWT tokens.\n",
                encoding="utf-8",
            )

            roles = {
                "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "tech_lead": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "developer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acc-bob"},
                "reviewer": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "qa": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                "release_manager": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
            }
            res = run_cli(root, "init", "--project-key", "TEAM", "--roles-json", json.dumps(roles))
            self.assertEqual(res.returncode, 0, res.stderr)
            out = json.loads(res.stdout)
            self.assertTrue(out["ok"])
            self.assertEqual(out["project_key"], "TEAM")

            # Verify .gitignore has .ai-workflow/active/
            gitignore = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn(".ai-workflow/active/", gitignore)

            # Verify docs/shared/engineering-standards.md was extracted
            standards_path = root / "docs" / "shared" / "engineering-standards.md"
            self.assertTrue(standards_path.exists())
            standards_text = standards_path.read_text(encoding="utf-8")
            self.assertIn("Always use type hints", standards_text)
            self.assertIn("Validate all JWT tokens", standards_text)

            # Verify AGENTS.md was trimmed and contains reference link instead of duplicated rules
            trimmed_agents = agents_md.read_text(encoding="utf-8")
            self.assertIn("docs/shared/engineering-standards.md", trimmed_agents)
            self.assertNotIn("Always use type hints", trimmed_agents)
            self.assertIn("# Project Overview", trimmed_agents)

            # Re-run init to verify idempotency
            res2 = run_cli(root, "init", "--project-key", "TEAM")
            self.assertEqual(res2.returncode, 0, res2.stderr)
            self.assertEqual((root / ".gitignore").read_text(encoding="utf-8").count(".ai-workflow/active/"), 1)

    def test_03_full_four_station_lifecycle_and_edge_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Step 0: status on uninitialized repo recommends team-init
            st0 = json.loads(run_cli(root, "status").stdout)
            self.assertFalse(st0["initialized"])
            self.assertEqual(st0["recommended_skill"], "team-init")

            # Initialize with multi-role team:
            # Alice = PM + Developer (same person!), Carol = Reviewer + QA, Dave = Release Manager
            roles = {
                "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "developer": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "reviewer": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                "qa": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                "release_manager": {"name": "Dave", "email": "dave@team.io", "jira_account_id": "acc-dave"},
            }
            run_cli(root, "init", "--project-key", "PROJ", "--roles-json", json.dumps(roles), "--provenance-audit-mode", "off")

            # Edge case: sync-story rejects >3 material decisions ("抓大放小" enforcement)
            bad_spec = run_cli(
                root,
                "sync-story",
                "--key", "PROJ-101",
                "--title", "Export CSV",
                "--ac", "AC-001:Export valid CSV",
                "--material-decision", "D1",
                "--material-decision", "D2",
                "--material-decision", "D3",
                "--material-decision", "D4",
                "--status", "approved",
            )
            self.assertNotEqual(bad_spec.returncode, 0)
            self.assertIn("at most 3 material questions/decisions", bad_spec.stderr)

            # Edge case: sync-story rejects approved status with 0 ACs
            no_ac_spec = run_cli(root, "sync-story", "--key", "PROJ-101", "--title", "Export CSV", "--status", "approved")
            self.assertNotEqual(no_ac_spec.returncode, 0)

            # Station 1: Valid sync-story (backend API only: has_api=True, has_ui=False)
            # Even if --ui-spec is passed accidentally when --has-ui is False, it must be stripped!
            spec_res = run_cli(
                root,
                "sync-story",
                "--key", "PROJ-101",
                "--title", "Export CSV API",
                "--scope-in", "Backend CSV endpoint",
                "--scope-out", "PDF export",
                "--has-api",
                "--ui-spec", "SHOULD_BE_OMITTED",
                "--api-spec", "GET /api/v1/export -> 200 text/csv",
                "--ac", "AC-001:Authenticated user receives UTF-8 CSV",
                "--ac", "AC-002:Unauthorized user receives HTTP 403",
                "--auto-decision", "Use RFC 4180 comma delimiter and UTF-8 BOM",
                "--material-decision", "Synchronous streaming response up to 10k rows",
                "--status", "approved",
            )
            self.assertEqual(spec_res.returncode, 0, spec_res.stderr)
            spec_out = json.loads(spec_res.stdout)
            # Because PM (Alice) == Developer (Alice), handoff must be auto-consolidated!
            self.assertEqual(spec_out["handoff"]["handoff_mode"], "auto-consolidated")
            self.assertTrue(spec_out["handoff"]["same_person"])

            story_data = json.loads((root / ".ai-workflow/active/PROJ-101.json").read_text(encoding="utf-8"))
            self.assertNotIn("ui_spec", story_data["design"])
            self.assertIn("api_spec", story_data["design"])

            # Router check after Station 1 -> recommends team-dev (plan mode)
            st1 = json.loads(run_cli(root, "status", "--key", "PROJ-101").stdout)
            self.assertEqual(st1["recommended_skill"], "team-dev")
            self.assertEqual(st1["recommended_mode"], "plan")

            # Edge case: create-subtasks rejects dependency cycle
            cycle_tasks = [
                {"id": "TASK-001", "title": "T1", "depends_on": ["TASK-002"]},
                {"id": "TASK-002", "title": "T2", "depends_on": ["TASK-001"]},
            ]
            cycle_res = run_cli(root, "create-subtasks", "--key", "PROJ-101", "--tasks-json", json.dumps(cycle_tasks))
            self.assertNotEqual(cycle_res.returncode, 0)
            self.assertIn("cycle", cycle_res.stderr.lower())

            # Station 2 (plan): Create valid subtasks
            valid_tasks = [
                {
                    "id": "TASK-001",
                    "title": "Implement CSV serializer",
                    "ac_coverage": ["AC-001"],
                    "depends_on": [],
                    "verification": "pytest tests/test_csv.py",
                },
                {
                    "id": "TASK-002",
                    "title": "Add /api/v1/export endpoint with 403 guard",
                    "ac_coverage": ["AC-001", "AC-002"],
                    "depends_on": ["TASK-001"],
                    "verification": "pytest tests/test_api.py",
                },
            ]
            sub_res = run_cli(root, "create-subtasks", "--key", "PROJ-101", "--tasks-json", json.dumps(valid_tasks))
            self.assertEqual(sub_res.returncode, 0, sub_res.stderr)

            # Router check -> recommends team-dev (implement mode) for TASK-001
            st2 = json.loads(run_cli(root, "status", "--key", "PROJ-101").stdout)
            self.assertEqual(st2["recommended_skill"], "team-dev")
            self.assertEqual(st2["recommended_mode"], "implement")
            self.assertEqual(st2["next_task_id"], "TASK-001")

            # Complete TASK-001 and TASK-002
            c1 = run_cli(
                root, "complete-task",
                "--key", "PROJ-101",
                "--task-id", "TASK-001",
                "--commit", "111aaaa",
                "--files", "src/csv.py,tests/test_csv.py",
                "--test-result", "pytest tests/test_csv.py: 3 passed",
            )
            self.assertEqual(c1.returncode, 0, c1.stderr)
            self.assertFalse(json.loads(c1.stdout)["ready_for_review"])

            c2 = run_cli(
                root, "complete-task",
                "--key", "PROJ-101",
                "--task-id", "TASK-002",
                "--commit", "222bbbb",
                "--files", "src/api.py,tests/test_api.py",
                "--test-result", "pytest tests/test_api.py: 4 passed",
            )
            self.assertEqual(c2.returncode, 0, c2.stderr)
            self.assertTrue(json.loads(c2.stdout)["ready_for_review"])

            # Router check -> recommends team-review
            st3 = json.loads(run_cli(root, "status", "--key", "PROJ-101").stdout)
            self.assertEqual(st3["recommended_skill"], "team-review")

            # Edge case: handoff blocks --review-status approved if open blocker finding exists
            bad_rev = run_cli(
                root, "handoff",
                "--key", "PROJ-101",
                "--review-status", "approved",
                "--commit", "222bbbb",
                "--findings-json", json.dumps([{"id": "REV-001", "severity": "blocker", "status": "open"}]),
            )
            self.assertNotEqual(bad_rev.returncode, 0)

            # Station 3a: Approve review (Reviewer Carol -> QA Carol is same person, auto-consolidated)
            rev_res = run_cli(
                root, "handoff",
                "--key", "PROJ-101",
                "--from-role", "reviewer",
                "--to-role", "qa",
                "--review-status", "approved",
                "--commit", "222bbbb",
                "--summary", "All ACs and security rules verified.",
            )
            self.assertEqual(rev_res.returncode, 0, rev_res.stderr)

            # Station 3b (plan): Plan QA test cases
            qa_plan = run_cli(
                root, "record-qa",
                "--key", "PROJ-101",
                "--mode", "plan",
                "--tc", "TC-001:AC-001:automated:Valid CSV export returns 200",
                "--tc", "TC-002:AC-002:automated:Unauthorized request returns 403",
            )
            self.assertEqual(qa_plan.returncode, 0, qa_plan.stderr)

            # Station 3b (verify failure & Bug loop): TC-002 fails!
            junit_xml = root / "junit.xml"
            junit_xml.write_text(
                '<testsuite tests="2">'
                '<testcase classname="tests.test_api" name="test_export_ok_TC-001"/>'
                '<testcase classname="tests.test_api" name="test_export_unauth_TC-002">'
                '<failure message="Expected 403, got 200"/>'
                '</testcase>'
                '</testsuite>',
                encoding="utf-8",
            )
            qa_fail = run_cli(
                root, "record-qa",
                "--key", "PROJ-101",
                "--mode", "verify",
                "--commit", "222bbbb",
                "--junit-xml", str(junit_xml),
            )
            self.assertEqual(qa_fail.returncode, 0, qa_fail.stderr)
            self.assertEqual(json.loads(qa_fail.stdout)["qa_status"], "failed")

            # Create linked Bug -> routes from QA (Carol) to Developer (Alice) -> cross-person Jira notification!
            bug_res = run_cli(
                root, "create-bug",
                "--key", "PROJ-101",
                "--tc-id", "TC-002",
                "--ac-id", "AC-002",
                "--title", "Missing 403 check on /api/v1/export",
                "--severity", "blocker",
                "--steps", "Call GET /api/v1/export without role",
                "--expected", "HTTP 403",
                "--actual", "HTTP 200",
                "--commit", "222bbbb",
            )
            self.assertEqual(bug_res.returncode, 0, bug_res.stderr)
            bug_out = json.loads(bug_res.stdout)
            bug_key = bug_out["bug_key"]
            self.assertEqual(bug_out["handoff"]["handoff_mode"], "jira-notification")
            self.assertFalse(bug_out["handoff"]["same_person"])
            self.assertEqual(bug_out["handoff"]["mention"], "@Alice")

            # Router check with open Bug -> must prioritize team-dev (implement) for the Bug
            st_bug = json.loads(run_cli(root, "status", "--key", "PROJ-101").stdout)
            self.assertEqual(st_bug["recommended_skill"], "team-dev")
            self.assertEqual(st_bug["recommended_mode"], "implement")
            self.assertIn(bug_key, st_bug["reason"])

            # Attempt premature close-story -> MUST BLOCK!
            premature_close = run_cli(
                root, "close-story",
                "--key", "PROJ-101",
                "--version", "v1.0.0",
                "--commit", "222bbbb",
            )
            self.assertNotEqual(premature_close.returncode, 0)
            self.assertTrue((root / ".ai-workflow/active/PROJ-101.json").exists())

            # Fix the Bug via complete-task with new commit 333cccc
            fix_res = run_cli(
                root, "complete-task",
                "--key", "PROJ-101",
                "--task-id", bug_key,
                "--commit", "333cccc",
                "--files", "src/api.py,tests/test_api.py",
                "--test-result", "pytest: all 5 passed",
            )
            self.assertEqual(fix_res.returncode, 0, fix_res.stderr)

            # Router check after new commit 333cccc -> detects commit drift vs reviewed_commit 222bbbb!
            st_drift = json.loads(run_cli(root, "status", "--key", "PROJ-101").stdout)
            self.assertEqual(st_drift["recommended_skill"], "team-review")
            self.assertIn("333cccc", st_drift["reason"])

            # Re-review on commit 333cccc
            run_cli(
                root, "handoff",
                "--key", "PROJ-101",
                "--review-status", "approved",
                "--commit", "333cccc",
                "--summary", "Bug fix verified.",
            )

            # Re-verify QA on commit 333cccc (all pass -> QA Carol hands off to Release Manager Dave via notification)
            qa_pass = run_cli(
                root, "record-qa",
                "--key", "PROJ-101",
                "--mode", "verify",
                "--commit", "333cccc",
                "--result", "TC-001=pass",
                "--result", "TC-002=pass",
            )
            self.assertEqual(qa_pass.returncode, 0, qa_pass.stderr)
            qa_pass_out = json.loads(qa_pass.stdout)
            self.assertEqual(qa_pass_out["qa_status"], "passed")
            self.assertEqual(qa_pass_out["handoff"]["handoff_mode"], "jira-notification")
            self.assertEqual(qa_pass_out["handoff"]["mention"], "@Dave")

            # Router check -> recommends team-release
            st_rel = json.loads(run_cli(root, "status", "--key", "PROJ-101").stdout)
            self.assertEqual(st_rel["recommended_skill"], "team-release")

            # Station 4: close-story succeeds and deletes .ai-workflow/active/PROJ-101.json
            close_res = run_cli(
                root, "close-story",
                "--key", "PROJ-101",
                "--version", "v1.0.0",
                "--commit", "333cccc",
                "--notes", "CSV export feature released cleanly.",
            )
            self.assertEqual(close_res.returncode, 0, close_res.stderr)
            close_out = json.loads(close_res.stdout)
            self.assertTrue(close_out["ok"])
            self.assertTrue(close_out["active_file_removed"])
            self.assertFalse((root / ".ai-workflow/active/PROJ-101.json").exists())

    def test_04_multi_story_ambiguity_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_cli(root, "init", "--project-key", "PROJ")
            run_cli(root, "sync-story", "--key", "PROJ-101", "--title", "S1", "--status", "draft")
            run_cli(root, "sync-story", "--key", "PROJ-202", "--title", "S2", "--status", "draft")

            # Calling status without --key when 2 active stories exist must flag ambiguous=True
            st = json.loads(run_cli(root, "status").stdout)
            self.assertTrue(st.get("ambiguous"))
            self.assertEqual(sorted(st.get("active_stories")), ["PROJ-101", "PROJ-202"])
            self.assertIsNone(st.get("recommended_skill"))

    def test_05_live_jira_rest_api_v3_client_with_mock_server(self):
        MockJiraHandler.requests_log.clear()
        server = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                roles = {
                    "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acc-bob"},
                    "reviewer": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "qa": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                    "release_manager": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                run_cli(
                    root, "init",
                    "--project-key", "PROJ",
                    "--jira-url", f"http://127.0.0.1:{port}",
                    "--roles-json", json.dumps(roles),
                )

                live_env = {
                    "JIRA_BASE_URL": f"http://127.0.0.1:{port}",
                    "JIRA_EMAIL": "bot@team.io",
                    "JIRA_API_TOKEN": "secret-token-123",
                }

                # 1. sync-story in live mode (PM Alice -> Dev Bob triggers transition, assignee PUT, and @mention comment)
                r1 = run_cli(
                    root, "sync-story",
                    "--key", "PROJ-101",
                    "--title", "Live Sync Test",
                    "--ac", "AC-001:Works with Jira v3 API",
                    "--status", "approved",
                    env_override=live_env,
                )
                self.assertEqual(r1.returncode, 0, r1.stderr)
                out1 = json.loads(r1.stdout)
                self.assertEqual(out1["jira_sync"]["mode"], "live")

                # Verify HTTP calls made to mock Jira server
                paths = [(m, p) for (m, p, _, _) in MockJiraHandler.requests_log]
                self.assertIn(("PUT", "/rest/api/3/issue/PROJ-101"), paths)
                self.assertIn(("POST", "/rest/api/3/issue/PROJ-101/transitions"), paths)
                self.assertIn(("PUT", "/rest/api/3/issue/PROJ-101/assignee"), paths)
                self.assertIn(("POST", "/rest/api/3/issue/PROJ-101/comment"), paths)

                # Check ADF mention node in comment payload
                comment_calls = [body for (m, p, body, _) in MockJiraHandler.requests_log if p.endswith("/comment")]
                self.assertTrue(comment_calls)
                first_para = comment_calls[0]["body"]["content"][0]["content"]
                self.assertEqual(first_para[0]["type"], "mention")
                self.assertEqual(first_para[0]["attrs"]["id"], "acc-bob")
        finally:
            server.shutdown()
            server.server_close()

    def test_06_adversarial_edge_cases_and_bug_regressions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # 1. Bug 1a & 1b: AGENTS.md with fenced code blocks containing '# comment'
            #    and pre-existing docs/shared/engineering-standards.md
            agents_md = root / "AGENTS.md"
            agents_md.write_text(
                "# Repo Guide\n"
                "```bash\n"
                "# Setup comment inside code fence\n"
                "npm install\n"
                "```\n\n"
                "## Coding Standards\n"
                "- Use strict TypeScript.\n"
                "```bash\n"
                "# Run linter inside standards code fence\n"
                "npm run lint\n"
                "```\n"
                "- Keep functions under 50 lines.\n",
                encoding="utf-8",
            )
            standards_path = root / "docs" / "shared" / "engineering-standards.md"
            standards_path.parent.mkdir(parents=True, exist_ok=True)
            standards_path.write_text("# Existing Standards\n- Existing baseline rule.\n", encoding="utf-8")

            roles = {
                "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "developer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acc-bob"},
                "reviewer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acc-bob"},
                "independent_reviewer": {"name": "Eve", "email": "eve@team.io", "jira_account_id": "acc-eve"},
                "qa": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                "release_manager": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
            }
            init_res = run_cli(root, "init", "--project-key", "PROJ", "--roles-json", json.dumps(roles))
            self.assertEqual(init_res.returncode, 0, init_res.stderr)

            # Both parts of Coding Standards (before and after '# Run linter') must be in engineering-standards.md
            std_text = standards_path.read_text(encoding="utf-8")
            self.assertIn("Existing baseline rule.", std_text)
            self.assertIn("Use strict TypeScript.", std_text)
            self.assertIn("Keep functions under 50 lines.", std_text)

            # And AGENTS.md must not have dangling half-extracted Coding Standards lines
            new_agents = agents_md.read_text(encoding="utf-8")
            self.assertIn("# Setup comment inside code fence", new_agents)
            self.assertNotIn("Keep functions under 50 lines.", new_agents)

            # 2. Bug 5a: Path traversal in --key must be rejected
            traversal = run_cli(root, "sync-story", "--key", "../../evil", "--title", "Bad")
            self.assertNotEqual(traversal.returncode, 0)

            # Create valid story with AC-001 and AC-002
            run_cli(
                root, "sync-story",
                "--key", "PROJ-200",
                "--title", "Search API",
                "--has-api",
                "--api-spec", "GET /search",
                "--ac", "AC-001:Returns matching items",
                "--ac", "AC-002:Rejects invalid query with 400",
                "--status", "approved",
            )

            # Lowercase --key in status must normalize to uppercase PROJ-200
            st_lower = json.loads(run_cli(root, "status", "--key", "proj-200").stdout)
            self.assertTrue(st_lower["story_found"])
            self.assertEqual(st_lower["jira_key"], "PROJ-200")

            # 3. Bug 4b: create-subtasks must reject unknown task IDs in depends_on
            bad_dep_tasks = [
                {"id": "TASK-001", "title": "T1", "ac_coverage": ["AC-001"], "depends_on": ["TASK-999"]},
            ]
            bad_dep_res = run_cli(root, "create-subtasks", "--key", "PROJ-200", "--tasks-json", json.dumps(bad_dep_tasks))
            self.assertNotEqual(bad_dep_res.returncode, 0)

            # Create valid tasks with TASK-002 depending on TASK-001
            good_tasks = [
                {"id": "TASK-001", "title": "T1", "ac_coverage": ["AC-001"], "depends_on": []},
                {"id": "TASK-002", "title": "T2", "ac_coverage": ["AC-002"], "depends_on": ["TASK-001"]},
            ]
            self.assertEqual(run_cli(root, "create-subtasks", "--key", "PROJ-200", "--tasks-json", json.dumps(good_tasks)).returncode, 0)

            # Out-of-order completion: completing TASK-002 before TASK-001 is completed must fail
            early_complete = run_cli(
                root, "complete-task",
                "--key", "PROJ-200",
                "--task-id", "TASK-002",
                "--commit", "c1",
                "--files", "a.py",
                "--test-result", "ok",
            )
            self.assertNotEqual(early_complete.returncode, 0)

            # Premature review approval before tasks are completed must fail
            early_review = run_cli(root, "handoff", "--key", "PROJ-200", "--review-status", "approved", "--commit", "c1")
            self.assertNotEqual(early_review.returncode, 0)

            # Complete TASK-001 and TASK-002 in order
            self.assertEqual(run_cli(root, "complete-task", "--key", "PROJ-200", "--task-id", "TASK-001", "--commit", "c1", "--files", "a.py", "--test-result", "ok").returncode, 0)
            self.assertEqual(run_cli(root, "complete-task", "--key", "PROJ-200", "--task-id", "TASK-002", "--commit", "c2", "--files", "b.py", "--test-result", "ok").returncode, 0)

            # 4. Bug 5b: Because developer (Bob) == reviewer (Bob) and independent_reviewer (Eve) is configured,
            #    status must route review to independent_reviewer Eve!
            st_rev = json.loads(run_cli(root, "status", "--key", "PROJ-200").stdout)
            self.assertEqual(st_rev["recommended_skill"], "team-review")
            self.assertEqual(st_rev["responsible_person"], "Eve")

            # 5. Bug 3: Review requests changes with finding REV-001 -> dev remediates via complete-task -> status routes back to team-review
            rev_cr = run_cli(
                root, "handoff",
                "--key", "PROJ-200",
                "--review-status", "changes-requested",
                "--commit", "c2",
                "--summary", "Missing 400 validation",
                "--findings-json", json.dumps([{"id": "REV-001", "severity": "major", "status": "open"}]),
            )
            self.assertEqual(rev_cr.returncode, 0, rev_cr.stderr)
            st_after_cr = json.loads(run_cli(root, "status", "--key", "PROJ-200").stdout)
            self.assertEqual(st_after_cr["recommended_skill"], "team-dev")

            # Developer remediates REV-001 with new commit c3
            rem_res = run_cli(
                root, "complete-task",
                "--key", "PROJ-200",
                "--task-id", "REV-001",
                "--commit", "c3",
                "--files", "b.py",
                "--test-result", "pytest: 2 passed",
            )
            self.assertEqual(rem_res.returncode, 0, rem_res.stderr)

            # Now status must route back to team-review (NOT stay stuck on team-dev!)
            st_re_rev = json.loads(run_cli(root, "status", "--key", "PROJ-200").stdout)
            self.assertEqual(st_re_rev["recommended_skill"], "team-review")

            # Approve review on c3
            self.assertEqual(run_cli(root, "handoff", "--key", "PROJ-200", "--review-status", "approved", "--commit", "c3").returncode, 0)

            # 6. Bug 4a: record-qa --mode plan must reject incomplete AC coverage (missing AC-002)
            partial_qa = run_cli(
                root, "record-qa",
                "--key", "PROJ-200",
                "--mode", "plan",
                "--tc", "TC-001:AC-001:automated:Test search ok",
            )
            self.assertNotEqual(partial_qa.returncode, 0)

            # Plan covering both AC-001 and AC-002 succeeds
            full_qa = run_cli(
                root, "record-qa",
                "--key", "PROJ-200",
                "--mode", "plan",
                "--tc", "TC-001:AC-001:automated:Test search ok",
                "--tc", "TC-002:AC-002:automated:Test search 400",
            )
            self.assertEqual(full_qa.returncode, 0, full_qa.stderr)

            # 7. Bug 2: Parameterized JUnit XML where first testcase for TC-001 fails and second passes must record TC-001 as FAIL!
            param_xml = root / "param_junit.xml"
            param_xml.write_text(
                '<testsuite tests="3">'
                '<testcase classname="t" name="test_search_TC-001[case1]"><failure message="bad"/></testcase>'
                '<testcase classname="t" name="test_search_TC-001[case2]"/>'
                '<testcase classname="t" name="test_search_TC-002"/>'
                '</testsuite>',
                encoding="utf-8",
            )
            verify_param = run_cli(root, "record-qa", "--key", "PROJ-200", "--mode", "verify", "--commit", "c3", "--junit-xml", str(param_xml))
            self.assertEqual(verify_param.returncode, 0, verify_param.stderr)
            self.assertEqual(json.loads(verify_param.stdout)["qa_status"], "failed")
            self.assertEqual(json.loads(verify_param.stdout)["counts"]["fail"], 1)

            # Malformed --result (missing '=' and ':') or unknown TC-999 must fail closed
            bad_res_fmt = run_cli(root, "record-qa", "--key", "PROJ-200", "--mode", "verify", "--commit", "c3", "--result", "TC-001")
            self.assertNotEqual(bad_res_fmt.returncode, 0)
            unknown_tc = run_cli(root, "record-qa", "--key", "PROJ-200", "--mode", "verify", "--commit", "c3", "--result", "TC-999=pass")
            self.assertNotEqual(unknown_tc.returncode, 0)

    def test_07_fast_track_git_autodetect_colon_split_and_multi_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Initialize a real Git repo so git rev-parse / git show / git worktree work
            subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "dev@team.io"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Dev"], cwd=str(root), check=True, capture_output=True)

            init_res = run_cli(root, "init", "--project-key", "PROJ", "--provenance-audit-mode", "off")
            self.assertEqual(init_res.returncode, 0, init_res.stderr)
            init_out = json.loads(init_res.stdout)
            # Verify .ai-workflow/bin/jira_sync.py is generated and executable
            self.assertEqual(init_out.get("cli_path"), ".ai-workflow/bin/jira_sync.py")
            local_cli = root / ".ai-workflow" / "bin" / "jira_sync.py"
            self.assertTrue(local_cli.exists())
            self.assertTrue(os.access(local_cli, os.X_OK))

            # Commit initial files and a feature file in Git
            (root / "src").mkdir(parents=True, exist_ok=True)
            (root / "src" / "export.py").write_text("def export_csv(): return 'a,b'\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feat: add export_csv"], cwd=str(root), check=True, capture_output=True)

            # 1. Test colon split preservation in --task and --tc
            run_cli(
                root, "sync-story",
                "--key", "PROJ-300",
                "--title", "Colon Split Story",
                "--ac", "AC-001:First AC: with colon",
                "--ac", "AC-002:Second AC: with colon",
                "--status", "approved",
            )
            sub_res = run_cli(
                root, "create-subtasks",
                "--key", "PROJ-300",
                "--task", "TASK-001:Implement API: Login:AC-001:pytest tests/test_a.py::test_login -k 'http:200'",
                "--task", " Auto-ID Task:AC-002:npm run test:unit -- --grep 'a:b'",
            )
            self.assertEqual(sub_res.returncode, 0, sub_res.stderr)
            story_300 = json.loads((root / ".ai-workflow/active/PROJ-300.json").read_text(encoding="utf-8"))
            self.assertEqual(story_300["tasks"][0]["id"], "TASK-001")
            self.assertEqual(story_300["tasks"][0]["verification"], "pytest tests/test_a.py::test_login -k 'http:200'")
            self.assertEqual(story_300["tasks"][1]["id"], "TASK-002")
            self.assertEqual(story_300["tasks"][1]["verification"], "npm run test:unit -- --grep 'a:b'")

            qa_colon = run_cli(
                root, "record-qa",
                "--key", "PROJ-300",
                "--mode", "plan",
                "--tc", "TC-001:AC-001:automated:Verify endpoint: returns HTTP 200: JSON",
                "--tc", "AC-002:manual:Verify error state: HTTP 400: Invalid input",
            )
            self.assertEqual(qa_colon.returncode, 0, qa_colon.stderr)
            story_300 = json.loads((root / ".ai-workflow/active/PROJ-300.json").read_text(encoding="utf-8"))
            self.assertEqual(story_300["qa"]["test_cases"][0]["title"], "Verify endpoint: returns HTTP 200: JSON")
            self.assertEqual(story_300["qa"]["test_cases"][1]["id"], "TC-002")
            self.assertEqual(story_300["qa"]["test_cases"][1]["title"], "Verify error state: HTTP 400: Invalid input")

            # 2. Test multi-ID complete-task + Git auto-detection of --commit and --files (omitting both flags!)
            comp_multi = run_cli(
                root, "complete-task",
                "--key", "PROJ-300",
                "--task-id", "TASK-001,TASK-002",
                "--test-result", "pytest: 2 passed",
            )
            self.assertEqual(comp_multi.returncode, 0, comp_multi.stderr)
            comp_out = json.loads(comp_multi.stdout)
            self.assertEqual(comp_out["completed_ids"], ["TASK-001", "TASK-002"])
            self.assertTrue(comp_out["ready_for_review"])
            self.assertTrue(len(comp_out["commit"]) >= 4)
            self.assertIn("src/export.py", comp_out["changed_files"])

            # 3. Test changes-requested WITHOUT --findings-json auto-creating REV-001
            cr_no_findings = run_cli(
                root, "handoff",
                "--key", "PROJ-300",
                "--review-status", "changes-requested",
                "--summary", "Please add rate limit header",
            )
            self.assertEqual(cr_no_findings.returncode, 0, cr_no_findings.stderr)
            st_cr = json.loads(run_cli(root, "status", "--key", "PROJ-300").stdout)
            self.assertEqual(st_cr["recommended_skill"], "team-dev")
            self.assertEqual(st_cr["open_finding_ids"], ["REV-001"])
            self.assertIn("REV-001", st_cr["done_when"])

            # Remediate REV-001 without passing --commit or --files
            rem_rev = run_cli(
                root, "complete-task",
                "--key", "PROJ-300",
                "--task-id", "REV-001",
                "--test-result", "pytest: rate limit verified",
            )
            self.assertEqual(rem_rev.returncode, 0, rem_rev.stderr)

            # 4. Test Dev Fast-Track + QA Fast-Track (--all-pass) + default close-story on PROJ-301
            run_cli(
                root, "sync-story",
                "--key", "PROJ-301",
                "--title", "Fast-Track Story",
                "--ac", "AC-001:Fast track works",
                "--ac", "AC-002:Zero ceremony",
                "--status", "approved",
            )
            # Directly run complete-task without create-subtasks (Dev Fast-Track)
            ft_dev = run_cli(
                root, "complete-task",
                "--key", "PROJ-301",
                "--task-id", "TASK-001",
                "--test-result", "pytest: all passed",
            )
            self.assertEqual(ft_dev.returncode, 0, ft_dev.stderr)
            ft_dev_out = json.loads(ft_dev.stdout)
            self.assertTrue(ft_dev_out["fast_track"])
            self.assertTrue(ft_dev_out["ready_for_review"])

            # Approve review
            self.assertEqual(
                run_cli(root, "handoff", "--key", "PROJ-301", "--review-status", "approved", "--summary", "LGTM").returncode,
                0,
            )

            # Directly run record-qa --mode verify --all-pass without --mode plan (QA Fast-Track)
            ft_qa = run_cli(
                root, "record-qa",
                "--key", "PROJ-301",
                "--mode", "verify",
                "--all-pass",
            )
            self.assertEqual(ft_qa.returncode, 0, ft_qa.stderr)
            ft_qa_out = json.loads(ft_qa.stdout)
            self.assertTrue(ft_qa_out["fast_track"])
            self.assertEqual(ft_qa_out["qa_status"], "passed")
            self.assertEqual(ft_qa_out["counts"]["pass"], 2)

            # Close story without --commit or --version
            ft_close = run_cli(root, "close-story", "--key", "PROJ-301")
            self.assertEqual(ft_close.returncode, 0, ft_close.stderr)
            ft_close_out = json.loads(ft_close.stdout)
            self.assertEqual(ft_close_out["version"], "v1.0.0")
            self.assertTrue(ft_close_out["active_file_removed"])

    def test_08_cross_machine_worktree_hydration_and_rich_adf(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.simulate_put_404_keys = {"PROJ-404"}
        MockJiraHandler.reject_subtask_types = {"Sub-task"}

        server = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            live_env = {
                "JIRA_BASE_URL": f"http://127.0.0.1:{port}",
                "JIRA_EMAIL": "bot@team.io",
                "JIRA_API_TOKEN": "secret-token-123",
            }
            with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
                root_a = Path(tmp_a)
                root_b = Path(tmp_b)
                for r in (root_a, root_b):
                    run_cli(r, "init", "--project-key", "PROJ", "--jira-url", f"http://127.0.0.1:{port}")

                # 1. Machine A: sync-story on PROJ-404 where PUT returns 404 -> must auto-fallback to POST create-story!
                res_a = run_cli(
                    root_a, "sync-story",
                    "--key", "PROJ-404",
                    "--title", "Story 404 Fallback & Hydration",
                    "--ac", "AC-001:Hydrate across machines",
                    "--ac", "AC-002:Rich ADF tables and headings",
                    "--status", "approved",
                    env_override=live_env,
                )
                self.assertEqual(res_a.returncode, 0, res_a.stderr)
                out_a = json.loads(res_a.stdout)
                self.assertEqual(out_a["jira_sync"]["action"], "create-story")

                # Verify Rich ADF conversion (heading & bulletList) in stored issue description
                # AND ensure Story description is 100% clean of raw [ai-workflow-state] JSON dump!
                stored_desc = MockJiraHandler.stored_issues["PROJ-404"]["fields"]["description"]
                node_types = [n["type"] for n in stored_desc["content"]]
                self.assertIn("heading", node_types)
                self.assertIn("bulletList", node_types)
                self.assertNotIn("[ai-workflow-state]", json.dumps(stored_desc, ensure_ascii=False))
                self.assertIn("PROJ-404", MockJiraHandler.stored_properties)

                # Machine A creates subtask where "Sub-task" returns HTTP 400 -> auto-falls back to "Subtask"!
                sub_a = run_cli(
                    root_a, "create-subtasks",
                    "--key", "PROJ-404",
                    "--task", "TASK-001:Cross-machine task:AC-001,AC-002:pytest",
                    env_override=live_env,
                )
                self.assertEqual(sub_a.returncode, 0, sub_a.stderr)

                # Machine A records QA plan -> posts Markdown table converted to ADF 'table' node
                qa_a = run_cli(
                    root_a, "record-qa",
                    "--key", "PROJ-404",
                    "--mode", "plan",
                    "--tc", "TC-001:AC-001:automated:Check hydration",
                    "--tc", "TC-002:AC-002:automated:Check ADF table",
                    env_override=live_env,
                )
                self.assertEqual(qa_a.returncode, 0, qa_a.stderr)
                comment_bodies = [body["body"] for (m, p, body, _) in MockJiraHandler.requests_log if p.endswith("/comment")]
                self.assertTrue(any(any(n["type"] == "table" for n in b["content"]) for b in comment_bodies))

                # 2. Machine B: has NO local .ai-workflow/active/PROJ-404.json!
                self.assertFalse((root_b / ".ai-workflow/active/PROJ-404.json").exists())

                # Machine B runs status --key PROJ-404 -> must auto-hydrate from Mock Jira Server!
                st_b = run_cli(root_b, "status", "--key", "PROJ-404", env_override=live_env)
                self.assertEqual(st_b.returncode, 0, st_b.stderr)
                st_b_out = json.loads(st_b.stdout)
                self.assertTrue(st_b_out["story_found"])
                self.assertEqual(st_b_out.get("hydrated_from"), "jira")
                self.assertEqual(st_b_out["recommended_skill"], "team-dev")
                self.assertEqual(st_b_out["recommended_mode"], "implement")
                self.assertEqual(st_b_out["next_task_id"], "TASK-001")
                self.assertTrue((root_b / ".ai-workflow/active/PROJ-404.json").exists())

                # 3. Git Worktree Hydration: create a secondary Git worktree from root_b
                subprocess.run(["git", "init"], cwd=str(root_b), check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "b@team.io"], cwd=str(root_b), check=True, capture_output=True)
                subprocess.run(["git", "config", "user.name", "Bob"], cwd=str(root_b), check=True, capture_output=True)
                (root_b / "README.md").write_text("# Worktree Test\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=str(root_b), check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "chore: init"], cwd=str(root_b), check=True, capture_output=True)

                wt_parent = Path(tempfile.mkdtemp())
                wt_dir = wt_parent / "wt-feature"
                try:
                    subprocess.run(
                        ["git", "worktree", "add", "-b", "feat/wt", str(wt_dir)],
                        cwd=str(root_b),
                        check=True,
                        capture_output=True,
                    )
                    # .ai-workflow/active/PROJ-404.json is gitignored, so it does not exist in wt_dir initially
                    self.assertFalse((wt_dir / ".ai-workflow/active/PROJ-404.json").exists())
                    # Running status in offline mode inside wt_dir must auto-hydrate from main worktree root_b!
                    st_wt = json.loads(run_cli(wt_dir, "status").stdout)
                    self.assertTrue(st_wt["story_found"])
                    self.assertEqual(st_wt["jira_key"], "PROJ-404")
                    self.assertTrue((wt_dir / ".ai-workflow/active/PROJ-404.json").exists())
                finally:
                    subprocess.run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=str(root_b), capture_output=True)
                    import shutil as _shutil
                    _shutil.rmtree(wt_parent, ignore_errors=True)
        finally:
            MockJiraHandler.simulate_put_404_keys.clear()
            MockJiraHandler.reject_subtask_types.clear()
            server.shutdown()
            server.server_close()

    def test_09_scale_aware_draft_guard_and_3tier_qa_anti_false_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_cli(root, "init", "--project-key", "PROJ", "--provenance-audit-mode", "off")

            # 1. Iterative draft deep-dive for a large story: accumulate >3 material decisions across draft rounds
            r1 = run_cli(
                root, "sync-story",
                "--key", "PROJ-500",
                "--title", "Enterprise Billing & Rebate Engine",
                "--risk-level", "high",
                "--ac", "AC-001:Calculate tiered rebate",
                "--ac", "AC-002:Tenant isolation on invoices",
                "--material-decision", "D1: Use async ledger batching",
                "--material-decision", "D2: Lock rebate period at UTC midnight",
                "--material-decision", "D3: Reject negative adjustments > 100%",
                "--status", "draft",
            )
            self.assertEqual(r1.returncode, 0, r1.stderr)

            # Round 2 passes ONLY new AC-003, AC-004 (incremental Upsert!) and 2 more material decisions
            r2 = run_cli(
                root, "sync-story",
                "--key", "PROJ-500",
                "--ac", "AC-003:Idempotent webhook retry handling",
                "--ac", "AC-004:Stripe sandbox compensation rollback",
                "--material-decision", "D4: Idempotency key TTL = 72h",
                "--material-decision", "D5: Dead-letter queue after 5 webhook failures",
                "--auto-decision", "A1: Use standard exponential backoff",
                "--status", "approved",
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)
            story_500 = json.loads((root / ".ai-workflow/active/PROJ-500.json").read_text(encoding="utf-8"))
            self.assertEqual(len(story_500["material_decisions"]), 5)
            self.assertEqual([ac["id"] for ac in story_500["acceptance_criteria"]], ["AC-001", "AC-002", "AC-003", "AC-004"])

            # Spec Gap Escalation Loop while prior_status == 'approved': update AC-004 description + append D6 and A2 without losing AC-001..003 or D1..D5
            r3_gap = run_cli(
                root, "sync-story",
                "--key", "PROJ-500",
                "--ac", "AC-004:Stripe sandbox compensation rollback with PagerDuty alert",
                "--material-decision", "D6: Alert PagerDuty on DLQ",
                "--auto-decision", "A2: Log correlation_id on webhook retry",
                "--status", "approved",
            )
            self.assertEqual(r3_gap.returncode, 0, r3_gap.stderr)
            story_500 = json.loads((root / ".ai-workflow/active/PROJ-500.json").read_text(encoding="utf-8"))
            self.assertEqual(len(story_500["material_decisions"]), 6)
            self.assertEqual(len(story_500["auto_decisions"]), 2)
            self.assertEqual(len(story_500["acceptance_criteria"]), 4)
            self.assertIn("PagerDuty", story_500["acceptance_criteria"][3]["description"])

            # Check status at Station 2: Dev Plan -> dev_fast_track_allowed must be False and hints must say disabled
            st_dev_plan = json.loads(run_cli(root, "status", "--key", "PROJ-500").stdout)
            self.assertEqual(st_dev_plan["current_station"], "Station 2: Dev Plan")
            self.assertFalse(st_dev_plan["dev_fast_track_allowed"])
            self.assertIn("Dev Fast-Track is disabled", st_dev_plan["parallel_hint"])
            self.assertIn("Dev Fast-Track disabled", st_dev_plan["done_when"])

            # 2. Dev Fast-Track Complexity Guard: must REJECT Fast-Track when >3 ACs or risk_level is high/critical
            blocked_ft = run_cli(
                root, "complete-task",
                "--key", "PROJ-500",
                "--task-id", "TASK-001",
                "--commit", "c500",
                "--files", "billing.py",
                "--test-result", "pytest: 4 passed",
            )
            self.assertNotEqual(blocked_ft.returncode, 0)
            self.assertIn("Dev Fast-Track is restricted to small stories", blocked_ft.stderr)

            # Must decompose via create-subtasks first
            sub_500 = run_cli(
                root, "create-subtasks",
                "--key", "PROJ-500",
                "--task", "TASK-001:Core rebate & authz:AC-001,AC-002:pytest tests/test_rebate.py",
                "--task", "TASK-002:Webhook idempotency & compensation:AC-003,AC-004:pytest tests/test_webhook.py",
            )
            self.assertEqual(sub_500.returncode, 0, sub_500.stderr)
            self.assertEqual(
                run_cli(
                    root, "complete-task",
                    "--key", "PROJ-500",
                    "--task-id", "TASK-001,TASK-002",
                    "--commit", "c500",
                    "--files", "billing.py,webhook.py",
                    "--test-result", "pytest: 12 passed",
                ).returncode,
                0,
            )
            self.assertEqual(
                run_cli(root, "handoff", "--key", "PROJ-500", "--review-status", "approved", "--commit", "c500").returncode,
                0,
            )

            # 3. 3-Tier Hybrid QA (automated / external / manual) & Anti-False-Pass Guardrail
            qa_plan = run_cli(
                root, "record-qa",
                "--key", "PROJ-500",
                "--mode", "plan",
                "--tc", "TC-001:AC-001:automated:Unit test tiered rebate calculation",
                "--tc", "TC-002:AC-002:automated:Unit test tenant isolation 403",
                "--tc", "TC-003:AC-003:external:Postman Newman staging webhook replay test",
                "--tc", "TC-004:AC-004:manual:Human check of Stripe sandbox dashboard refund state",
            )
            self.assertEqual(qa_plan.returncode, 0, qa_plan.stderr)

            # Running verify with --all-pass MUST ONLY pass automated TCs (TC-001, TC-002)
            # and keep external (TC-003) and manual (TC-004) as not-executed -> qa_status == "incomplete"!
            qa_verify_partial = run_cli(
                root, "record-qa",
                "--key", "PROJ-500",
                "--mode", "verify",
                "--commit", "c500",
                "--all-pass",
            )
            self.assertEqual(qa_verify_partial.returncode, 0, qa_verify_partial.stderr)
            partial_out = json.loads(qa_verify_partial.stdout)
            self.assertEqual(partial_out["qa_status"], "incomplete")
            self.assertEqual(partial_out["counts"]["pass"], 2)
            self.assertEqual(partial_out["counts"]["not-executed"], 2)
            self.assertEqual(partial_out["pending_external_tcs"], ["TC-003"])
            self.assertEqual(partial_out["pending_manual_tcs"], ["TC-004"])

            # Status router must report pending_external_tcs and pending_manual_tcs and include them in reason/done_when
            st_qa = json.loads(run_cli(root, "status", "--key", "PROJ-500").stdout)
            self.assertEqual(st_qa["current_station"], "Station 3b: QA Verify")
            self.assertEqual(st_qa["pending_automated_tcs"], [])
            self.assertEqual(st_qa["pending_external_tcs"], ["TC-003"])
            self.assertEqual(st_qa["pending_manual_tcs"], ["TC-004"])
            self.assertIn("external: TC-003", st_qa["reason"])
            self.assertIn("manual: TC-004", st_qa["reason"])
            self.assertIn("--result TC-003=pass --result TC-004=pass", st_qa["done_when"])

            # Now provide explicit external and manual verification results using both ':' and '=' plus optional notes and lowercase tc_id
            qa_verify_full = run_cli(
                root, "record-qa",
                "--key", "PROJ-500",
                "--mode", "verify",
                "--commit", "c500",
                "--result", "tc-003:pass:Newman staging 12 assertions green",
                "--result", "TC-004=pass:Verified Stripe refund in dashboard",
            )
            self.assertEqual(qa_verify_full.returncode, 0, qa_verify_full.stderr)
            full_out = json.loads(qa_verify_full.stdout)
            self.assertEqual(full_out["qa_status"], "passed")
            self.assertEqual(full_out["by_type"], {"automated": 2, "external": 1, "manual": 1})
            story_500_qa = json.loads((root / ".ai-workflow/active/PROJ-500.json").read_text(encoding="utf-8"))["qa"]["test_cases"]
            self.assertEqual(story_500_qa[2]["notes"], "Newman staging 12 assertions green")
            self.assertEqual(story_500_qa[3]["notes"], "Verified Stripe refund in dashboard")

            # Close story and verify qa_breakdown is included
            close_500 = run_cli(root, "close-story", "--key", "PROJ-500", "--commit", "c500", "--version", "v2.0.0")
            self.assertEqual(close_500.returncode, 0, close_500.stderr)
            close_out = json.loads(close_500.stdout)
            self.assertEqual(close_out["qa_breakdown"], {"automated": 2, "external": 1, "manual": 1})

    def test_10_init_inquiry_inspect_credentials_and_auto_account_resolution(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()

        server = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                # 1. Pre-init --inspect must report requires_user_inquiry=True and all 4 structured questions without creating files
                insp = run_cli(root, "init", "--inspect")
                self.assertEqual(insp.returncode, 0, insp.stderr)
                insp_out = json.loads(insp.stdout)
                self.assertEqual(insp_out["action"], "init-inspect")
                self.assertFalse(insp_out["config_exists"])
                self.assertFalse(insp_out["credentials_configured"])
                self.assertTrue(insp_out["requires_user_inquiry"])
                self.assertEqual(
                    set(insp_out["missing_confirmations"]),
                    {"project_url", "project_key", "jira_credentials", "roles"},
                )
                q_ids = [q["id"] for q in insp_out["questions_to_ask"]]
                self.assertEqual(
                    q_ids,
                    ["project_url_and_key", "role_definitions_and_tokens", "status_and_issue_types"],
                )
                self.assertIn("role_has_api_token", insp_out)
                self.assertFalse((root / ".ai-workflow/team-config.json").exists())

                # 2. Run init with confirmed URL, key, account email, API token, and roles (including per-role tokens)
                roles_input = {
                    "pm": {"name": "Alice", "email": "alice@team.io"},
                    "tech_lead": {"name": "Alice", "email": "alice@team.io"},
                    "developer": {"name": "Bob", "email": "bob@team.io", "api_token": "dev-token-bob"},
                    "reviewer": {"name": "Alice", "email": "alice@team.io"},
                    "qa": {"name": "Carol", "email": "carol@team.io"},
                    "release_manager": {"name": "Alice", "email": "alice@team.io"},
                }
                role_tokens_input = {
                    "qa": "qa-token-carol",
                }
                init_res = run_cli(
                    root, "init",
                    "--project-key", "TEAM",
                    "--jira-url", f"http://127.0.0.1:{port}",
                    "--jira-email", "alice@team.io",
                    "--jira-token", "super-secret-token-999",
                    "--roles-json", json.dumps(roles_input),
                    "--role-tokens-json", json.dumps(role_tokens_input),
                )
                self.assertEqual(init_res.returncode, 0, init_res.stderr)
                init_out = json.loads(init_res.stdout)
                self.assertTrue(init_out["ok"])
                self.assertEqual(init_out["project_key"], "TEAM")
                self.assertEqual(init_out["jira_base_url"], f"http://127.0.0.1:{port}")
                self.assertEqual(init_out["jira_mode"], "live")
                self.assertTrue(init_out["credentials_configured"])
                self.assertEqual(init_out["credential_store"], "user")
                self.assertFalse((root / ".ai-workflow/credentials.json").exists())
                self.assertTrue(init_out["jira_verification"]["verified"])
                self.assertEqual(init_out["jira_verification"]["account_id"], "acc-alice")
                self.assertTrue(init_out["role_has_api_token"]["developer"])
                self.assertTrue(init_out["role_has_api_token"]["qa"])
                self.assertEqual(init_out["role_verifications"]["developer"]["account_id"], "acc-bob-from-token")
                self.assertEqual(init_out["role_verifications"]["qa"]["account_id"], "acc-carol-from-token")

                # Verify auto-resolved jira_account_id from role token /rest/api/3/myself
                self.assertEqual(init_out["auto_resolved_account_ids"]["developer"], "acc-bob-from-token")
                self.assertEqual(init_out["auto_resolved_account_ids"]["qa"], "acc-carol-from-token")

                # Verify out-of-repo user credentials file exists (0600) and .ai-workflow/team-config.json NEVER contains ANY tokens
                cred_path = Path(init_out["credentials_path"])
                self.assertTrue(cred_path.exists())
                self.assertNotIn(root.resolve(), cred_path.resolve().parents)
                cred_data = json.loads(cred_path.read_text(encoding="utf-8"))
                self.assertEqual(cred_data["jira_email"], "alice@team.io")
                self.assertEqual(cred_data["jira_api_token"], "super-secret-token-999")
                self.assertEqual(cred_data["role_credentials"]["developer"]["jira_api_token"], "dev-token-bob")
                self.assertEqual(cred_data["role_credentials"]["qa"]["jira_api_token"], "qa-token-carol")

                cfg_raw = (root / ".ai-workflow/team-config.json").read_text(encoding="utf-8")
                self.assertNotIn("super-secret-token-999", cfg_raw)
                self.assertNotIn("dev-token-bob", cfg_raw)
                self.assertNotIn("qa-token-carol", cfg_raw)
                cfg_data = json.loads(cfg_raw)
                self.assertEqual(cfg_data["roles"]["developer"]["jira_account_id"], "acc-bob-from-token")
                self.assertEqual(cfg_data["roles"]["qa"]["jira_account_id"], "acc-carol-from-token")

                # Verify .gitignore ignores credentials.json and .env
                gitignore_text = (root / ".gitignore").read_text(encoding="utf-8")
                self.assertIn(".ai-workflow/credentials.json", gitignore_text)
                self.assertIn(".ai-workflow/.env", gitignore_text)

                # 3. Verify subsequent sync-story and record-qa automatically use per-role credentials in live mode!
                sync_res = run_cli(
                    root, "sync-story",
                    "--key", "TEAM-101",
                    "--title", "Live Sync via Saved Credentials",
                    "--ac", "AC-001:Auto load credentials.json",
                    "--status", "approved",
                    "--create",
                )
                self.assertEqual(sync_res.returncode, 0, sync_res.stderr)
                sync_out = json.loads(sync_res.stdout)
                self.assertEqual(sync_out["jira_sync"]["mode"], "live")
                self.assertEqual(sync_out["handoff"]["assigned_account_id"], "acc-bob-from-token")

                MockJiraHandler.requests_log.clear()
                qa_res = run_cli(
                    root, "record-qa",
                    "--key", "TEAM-101",
                    "--mode", "plan",
                    "--tc", "TC-001:AC-001:automated:Verify QA comment uses Carol's token and QA Owner badge",
                )
                self.assertEqual(qa_res.returncode, 0, qa_res.stderr)
                qa_out = json.loads(qa_res.stdout)
                self.assertEqual(qa_out["qa_owner"], "Carol")
                comment_reqs = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(comment_reqs), 1)
                import base64
                expected_carol_auth = "Basic " + base64.b64encode(b"carol@team.io:qa-token-carol").decode("ascii")
                self.assertEqual(comment_reqs[0][3].get("Authorization"), expected_carol_auth)
                comment_body_str = json.dumps(comment_reqs[0][2], ensure_ascii=False)
                self.assertIn("QA Owner", comment_body_str)
                self.assertIn("Carol", comment_body_str)

                # 4. Verify interactive mode (-i / --interactive) with piped stdin (including per-role API token prompt)
                with tempfile.TemporaryDirectory() as tmp_i:
                    root_i = Path(tmp_i)
                    stdin_lines = "\n".join([
                        f"http://127.0.0.1:{port}",  # 1. Jira Base URL
                        "INTP",                      # 2. Project Key
                        "alice@team.io",             # 3. Jira Email
                        "interactive-tok-777",       # 4. Jira API Token
                        "Alice", "alice@team.io", "", "",             # pm (Name, Email, Token, AccountId)
                        "Alice", "alice@team.io", "", "",             # tech_lead
                        "Bob", "bob@team.io", "bob-tok-123", "",      # developer
                        "Alice", "alice@team.io", "", "",             # reviewer
                        "Carol", "carol@team.io", "carol-tok-456", "",# qa
                        "Alice", "alice@team.io", "", "",             # release_manager
                    ]) + "\n"
                    env_clean = os.environ.copy()
                    env_clean.pop("JIRA_BASE_URL", None)
                    env_clean.pop("JIRA_EMAIL", None)
                    env_clean.pop("JIRA_API_TOKEN", None)
                    proc = subprocess.run(
                        [sys.executable, str(JIRA_SYNC), "--repo-root", str(root_i), "init", "--interactive"],
                        input=stdin_lines,
                        capture_output=True,
                        text=True,
                        env=env_clean,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    i_out = json.loads(proc.stdout)
                    self.assertEqual(i_out["project_key"], "INTP")
                    self.assertEqual(i_out["jira_mode"], "live")
                    self.assertTrue(i_out["credentials_configured"])
                    self.assertTrue(i_out["role_has_api_token"]["developer"])
                    self.assertTrue(i_out["role_has_api_token"]["qa"])
                    self.assertEqual(i_out["auto_resolved_account_ids"]["developer"], "acc-bob-from-token")
                    self.assertEqual(i_out["auto_resolved_account_ids"]["qa"], "acc-carol-from-token")
        finally:
            server.shutdown()
            server.server_close()

    def test_11_qa_self_mention_fix_and_qase_backend_persistence_and_sync(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockQaseHandler.requests_log.clear()
        MockQaseHandler.suites.clear()
        MockQaseHandler.cases.clear()
        MockQaseHandler.runs.clear()
        MockQaseHandler.next_suite_id = 10
        MockQaseHandler.next_case_id = 100
        MockQaseHandler.next_run_id = 500
        MockQaseHandler.expected_token = "qase-secret-123"
        MockQaseHandler.fail_external_issue = False

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        qase_srv = HTTPServer(("127.0.0.1", 0), MockQaseHandler)
        qase_port = qase_srv.server_port

        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_qase = threading.Thread(target=qase_srv.serve_forever, daemon=True)
        t_jira.start()
        t_qase.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                roles_input = {
                    "pm": {"name": "Alice", "email": "alice@team.io"},
                    "tech_lead": {"name": "Alice", "email": "alice@team.io"},
                    "developer": {"name": "Bob", "email": "bob@team.io", "api_token": "dev-token-bob"},
                    "reviewer": {"name": "Alice", "email": "alice@team.io"},
                    "qa": {"name": "Carol", "email": "carol@team.io", "api_token": "qa-token-carol"},
                    "release_manager": {"name": "Alice", "email": "alice@team.io"},
                }
                init_res = run_cli(
                    root, "init",
                    "--project-key", "AP",
                    "--jira-url", f"http://127.0.0.1:{jira_port}",
                    "--jira-email", "alice@team.io",
                    "--jira-token", "alice-tok-111",
                    "--roles-json", json.dumps(roles_input),
                )
                self.assertEqual(init_res.returncode, 0, init_res.stderr)

                # S2: Before choosing qa_test_backend, record-qa --inspect reports requires_qa_backend_choice=True
                qa_insp = run_cli(root, "record-qa", "--inspect")
                self.assertEqual(qa_insp.returncode, 0, qa_insp.stderr)
                qa_insp_out = json.loads(qa_insp.stdout)
                self.assertFalse(qa_insp_out["qa_backend_configured"])
                self.assertTrue(qa_insp_out["requires_qa_backend_choice"])
                self.assertEqual(qa_insp_out["qa_test_backend"], "")

                # Create Story AP-1
                s1 = run_cli(
                    root, "sync-story",
                    "--key", "AP-1",
                    "--title", "Qase & Clean QA Attribution",
                    "--ac", "AC-001:User login",
                    "--ac", "AC-002:Guest 401 guard",
                    "--status", "approved",
                    "--create",
                )
                self.assertEqual(s1.returncode, 0, s1.stderr)
                # Fast-track dev + review so status reaches Station 3b
                c1 = run_cli(
                    root, "complete-task",
                    "--key", "AP-1",
                    "--task-id", "TASK-001",
                    "--commit", "abc7777",
                    "--files", "auth.py",
                    "--test-result", "pytest: 2 passed",
                )
                self.assertEqual(c1.returncode, 0, c1.stderr)
                h1 = run_cli(root, "handoff", "--key", "AP-1", "--review-status", "approved", "--commit", "abc7777")
                self.assertEqual(h1.returncode, 0, h1.stderr)

                st_qa = json.loads(run_cli(root, "status", "--key", "AP-1").stdout)
                self.assertEqual(st_qa["recommended_skill"], "team-qa")
                self.assertTrue(st_qa["requires_qa_backend_choice"])

                # S1: In jira-comment mode, QA Test Plan must NEVER self-mention "@Carol 請接手下一階段工作："
                MockJiraHandler.requests_log.clear()
                plan_jc = run_cli(
                    root, "record-qa",
                    "--key", "AP-1",
                    "--mode", "plan",
                    "--qa-backend", "jira-comment",
                    "--tc", "TC-001:AC-001:automated:Login happy path",
                    "--tc", "TC-002:AC-002:automated:Guest 401 guard",
                )
                self.assertEqual(plan_jc.returncode, 0, plan_jc.stderr)
                jc_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(jc_comments), 1)
                jc_body_str = json.dumps(jc_comments[0][2], ensure_ascii=False)
                self.assertNotIn("請接手下一階段工作", jc_body_str)
                self.assertNotIn("@Carol", jc_body_str)
                self.assertIn("[站點 3b：QA 測試計畫 — AP-1]", jc_body_str)

                # Verify jira-comment choice was persisted to team-config.json
                cfg_after_jc = json.loads((root / ".ai-workflow/team-config.json").read_text(encoding="utf-8"))
                self.assertEqual(cfg_after_jc["qa_test_backend"], "jira-comment")

                # S5a: Switching to qase WITHOUT token must fail closed when not offline
                bad_qase = run_cli(
                    root, "record-qa",
                    "--key", "AP-1",
                    "--mode", "plan",
                    "--qa-backend", "qase",
                    "--qase-project-code", "AP",
                    "--qase-base-url", f"http://127.0.0.1:{qase_port}/v1",
                    "--tc", "TC-001:AC-001:automated:Login happy path",
                    "--tc", "TC-002:AC-002:automated:Guest 401 guard",
                )
                self.assertNotEqual(bad_qase.returncode, 0)
                # Failed qase switch must NOT corrupt persisted qa_test_backend in team-config.json
                self.assertEqual(
                    json.loads((root / ".ai-workflow/team-config.json").read_text(encoding="utf-8"))["qa_test_backend"],
                    "jira-comment",
                )

                # S3: Choose `qase` with valid token & project code -> persists config & token, creates Suite + Bulk Cases + Jira attach
                MockJiraHandler.requests_log.clear()
                MockQaseHandler.requests_log.clear()
                plan_qase = run_cli(
                    root, "record-qa",
                    "--key", "AP-1",
                    "--mode", "plan",
                    "--qa-backend", "qase",
                    "--qase-project-code", "AP",
                    "--qase-base-url", f"http://127.0.0.1:{qase_port}/v1",
                    "--qase-app-url", "https://app.qase.io",
                    "--qase-token", "qase-secret-123",
                    "--tc", "TC-001:AC-001:automated:Login happy path",
                    "--tc", "TC-002:AC-002:manual:Guest 401 modal visual check",
                )
                self.assertEqual(plan_qase.returncode, 0, plan_qase.stderr)
                pq_out = json.loads(plan_qase.stdout)
                self.assertEqual(pq_out["qa_test_backend"], "qase")
                self.assertEqual(pq_out["qase_sync"]["suite_id"], 10)
                self.assertEqual(pq_out["qase_sync"]["case_ids"], [100, 101])

                # Secret hygiene: qase-secret-123 must NEVER appear in team-config.json, ONLY in out-of-repo user credentials
                cfg_raw = (root / ".ai-workflow/team-config.json").read_text(encoding="utf-8")
                self.assertNotIn("qase-secret-123", cfg_raw)
                cfg_qase = json.loads(cfg_raw)
                self.assertEqual(cfg_qase["qa_test_backend"], "qase")
                self.assertEqual(cfg_qase["qase"]["project_code"], "AP")
                self.assertFalse((root / ".ai-workflow/credentials.json").exists())
                user_cred_qase = Path(json.loads(run_cli(root, "init", "--inspect").stdout)["user_credentials_path"])
                cred_qase = json.loads(user_cred_qase.read_text(encoding="utf-8"))
                self.assertEqual(cred_qase["qase_api_token"], "qase-secret-123")

                # Verify Jira comment in qase mode is concise (contains Qase Suite URL, no self-mention, no table spam)
                qase_jc = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(qase_jc), 1)
                qase_jc_str = json.dumps(qase_jc[0][2], ensure_ascii=False)
                self.assertIn("https://app.qase.io/project/AP?suite=10", qase_jc_str)
                self.assertNotIn("請接手下一階段工作", qase_jc_str)
                self.assertNotIn("tableHeader", qase_jc_str)

                # S3b & S5b: Re-run plan WITHOUT --qa-backend flags (also simulate external-issue 422 warning):
                # Must remember `qase` automatically, PATCH existing 100/101, bulk-create only new TC-003 (102), and survive 422 external-issue warning!
                MockQaseHandler.requests_log.clear()
                MockQaseHandler.fail_external_issue = True
                replan_qase = run_cli(
                    root, "record-qa",
                    "--key", "AP-1",
                    "--mode", "plan",
                    "--tc", "TC-001:AC-001:automated:Login happy path updated",
                    "--tc", "TC-002:AC-002:manual:Guest 401 modal visual check",
                    "--tc", "TC-003:AC-002:external:Playwright E2E guest redirect",
                )
                self.assertEqual(replan_qase.returncode, 0, replan_qase.stderr)
                rp_out = json.loads(replan_qase.stdout)
                self.assertEqual(rp_out["qa_test_backend"], "qase")
                self.assertEqual(rp_out["qase_sync"]["case_ids"], [100, 101, 102])
                self.assertTrue(rp_out["qase_sync"].get("external_link_warning"))
                patch_paths = [r[1] for r in MockQaseHandler.requests_log if r[0] == "PATCH" and r[1].startswith("/v1/case/")]
                self.assertEqual(sorted(patch_paths), ["/v1/case/AP/100", "/v1/case/AP/101"])
                bulk_posts = [r for r in MockQaseHandler.requests_log if r[0] == "POST" and r[1] == "/v1/case/AP/bulk"]
                self.assertEqual(len(bulk_posts), 1)
                self.assertEqual(len(bulk_posts[0][2]["cases"]), 1)

                # S4: Verify mode WITHOUT --qa-backend flags automatically creates Qase Test Run, submits bulk results, and completes run!
                MockQaseHandler.fail_external_issue = False
                MockQaseHandler.requests_log.clear()
                ver_qase = run_cli(
                    root, "record-qa",
                    "--key", "AP-1",
                    "--mode", "verify",
                    "--commit", "abc7777",
                    "--all-pass",
                    "--result", "TC-002=pass:Visual check OK",
                    "--result", "TC-003=pass:Playwright green",
                )
                self.assertEqual(ver_qase.returncode, 0, ver_qase.stderr)
                vq_out = json.loads(ver_qase.stdout)
                self.assertEqual(vq_out["qa_status"], "passed")
                self.assertEqual(vq_out["qa_test_backend"], "qase")
                self.assertEqual(vq_out["qase_sync"]["run_id"], 500)
                self.assertTrue(MockQaseHandler.runs[500]["completed"])
                self.assertEqual(len(MockQaseHandler.runs[500]["results"]), 3)
                self.assertTrue(all(r["status"] == "passed" for r in MockQaseHandler.runs[500]["results"]))
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()
            qase_srv.shutdown()
            qase_srv.server_close()

    def test_18_team_plan_sprint_preview_apply_and_status(self):
        MockJiraHandler.requests_log = []
        MockJiraHandler.stored_issues = {
            "PROJ-101": {"key": "PROJ-101", "fields": {"summary": "匯出報表", "issuetype": {"name": "Story"}, "status": {"name": "To Do"}}},
            "PROJ-102": {"key": "PROJ-102", "fields": {"summary": "權限檢查", "issuetype": {"name": "Story"}, "status": {"name": "To Do"}}},
            "PROJ-EPIC-12": {"key": "PROJ-EPIC-12", "fields": {"summary": "報表 Epic", "issuetype": {"name": "Epic"}, "status": {"name": "To Do"}}},
        }
        MockJiraHandler.stored_sprints = {}
        MockJiraHandler.next_sprint_id = 900
        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jt = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        jt.start()
        jira_url = f"http://127.0.0.1:{jira_srv.server_port}"
        live_env = {
            "JIRA_BASE_URL": jira_url,
            "JIRA_EMAIL": "alice@team.io",
            "JIRA_API_TOKEN": "secret-token-123",
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                init = run_cli(
                    root, "init", "--project-key", "PROJ", "--jira-url", jira_url,
                    "--jira-email", "alice@team.io", "--jira-token", "secret-token-123",
                    "--jira-board-id", "42",
                )
                self.assertEqual(init.returncode, 0, init.stderr)

                preview = run_cli(
                    root, "sprint-plan", "--board-id", "42", "--name", "2026-11 Sprint 3",
                    "--goal", "完成 CSV 匯出", "--start-date", "2026-11-16", "--end-date", "2026-11-27",
                    "--issue-key", "PROJ-101", "--issue-key", "PROJ-102", "--issue-key", "PROJ-EPIC-12",
                    env_override=live_env,
                )
                self.assertEqual(preview.returncode, 0, preview.stderr)
                preview_out = json.loads(preview.stdout)
                self.assertTrue(preview_out["dry_run"])
                self.assertFalse(preview_out["applied"])
                self.assertEqual(preview_out["selected_issue_keys"], ["PROJ-101", "PROJ-102"])
                self.assertEqual(MockJiraHandler.stored_sprints, {})

                applied = run_cli(
                    root, "sprint-plan", "--board-id", "42", "--name", "2026-11 Sprint 3",
                    "--goal", "完成 CSV 匯出", "--start-date", "2026-11-16", "--end-date", "2026-11-27",
                    "--issue-key", "PROJ-101", "--issue-key", "PROJ-102", "--issue-key", "PROJ-EPIC-12",
                    "--apply", env_override=live_env,
                )
                self.assertEqual(applied.returncode, 0, applied.stderr)
                applied_out = json.loads(applied.stdout)
                self.assertTrue(applied_out["applied"])
                sprint_id = str(applied_out["sprint_id"])
                self.assertEqual(MockJiraHandler.stored_sprints[sprint_id]["issue_keys"], ["PROJ-101", "PROJ-102"])

                status = run_cli(root, "sprint-status", "--sprint-id", sprint_id, env_override=live_env)
                self.assertEqual(status.returncode, 0, status.stderr)
                status_out = json.loads(status.stdout)
                self.assertEqual(status_out["sprint"]["name"], "2026-11 Sprint 3")
                self.assertEqual(sorted(i["key"] for i in status_out["issues"]), ["PROJ-101", "PROJ-102"])

                MockJiraHandler.requests_log.clear()
                renamed = run_cli(
                    root, "sprint-plan", "--sprint-id", sprint_id, "--name", "2026-11 Sprint 3 Renamed",
                    "--issue-key", "PROJ-101", "--apply", env_override=live_env,
                )
                self.assertEqual(renamed.returncode, 0, renamed.stderr)
                self.assertEqual(MockJiraHandler.stored_sprints[sprint_id]["name"], "2026-11 Sprint 3 Renamed")
                self.assertTrue(any(m == "PUT" and p == f"/rest/agile/1.0/sprint/{sprint_id}" for m, p, _, _ in MockJiraHandler.requests_log))
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_12_collaborator_git_clone_auth_onboarding_and_remote_jql_discovery(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            with tempfile.TemporaryDirectory() as tmp_alice, tempfile.TemporaryDirectory() as tmp_carol:
                alice_root = Path(tmp_alice)
                carol_root = Path(tmp_carol)

                roles_input = {
                    "pm": {"name": "Alice", "email": "alice@team.io"},
                    "tech_lead": {"name": "Alice", "email": "alice@team.io"},
                    "developer": {"name": "Bob", "email": "bob@team.io", "api_token": "dev-token-bob"},
                    "reviewer": {"name": "Alice", "email": "alice@team.io"},
                    "qa": {"name": "Carol", "email": "carol@team.io", "api_token": "qa-token-carol"},
                    "release_manager": {"name": "Alice", "email": "alice@team.io"},
                }
                # Machine A (Alice): initializes project AP with Qase backend & creates Story AP-1 + Sub-task AP-102
                init_a = run_cli(
                    alice_root, "init",
                    "--project-key", "AP",
                    "--jira-url", f"http://127.0.0.1:{jira_port}",
                    "--jira-email", "alice@team.io",
                    "--jira-token", "alice-tok-111",
                    "--qa-backend", "qase",
                    "--qase-project-code", "AP",
                    "--qase-token", "qase-secret-123",
                    "--roles-json", json.dumps(roles_input),
                )
                self.assertEqual(init_a.returncode, 0, init_a.stderr)

                s_a = run_cli(
                    alice_root, "sync-story",
                    "--key", "AP-1",
                    "--title", "Multi-Collaborator Story",
                    "--ac", "AC-001:Shared via Jira property",
                    "--status", "approved",
                    "--create",
                )
                self.assertEqual(s_a.returncode, 0, s_a.stderr)
                sub_a = run_cli(
                    alice_root, "create-subtasks",
                    "--key", "AP-1",
                    "--task", "TASK-001:Implement feature:AC-001:pytest",
                )
                self.assertEqual(sub_a.returncode, 0, sub_a.stderr)

                # Simulate `git push` -> `git clone` to Machine B (Carol):
                # Copy ONLY Git-tracked files (.ai-workflow/team-config.json, .gitignore, docs/shared/engineering-standards.md)
                # Do NOT copy .ai-workflow/credentials.json or .ai-workflow/active/!
                (carol_root / ".ai-workflow").mkdir(parents=True, exist_ok=True)
                cfg_bytes = (alice_root / ".ai-workflow/team-config.json").read_bytes()
                (carol_root / ".ai-workflow/team-config.json").write_bytes(cfg_bytes)
                (carol_root / ".gitignore").write_bytes((alice_root / ".gitignore").read_bytes())
                (carol_root / "docs/shared").mkdir(parents=True, exist_ok=True)
                (carol_root / "docs/shared/engineering-standards.md").write_bytes(
                    (alice_root / "docs/shared/engineering-standards.md").read_bytes()
                )
                self.assertFalse((carol_root / ".ai-workflow/credentials.json").exists())
                self.assertFalse((carol_root / ".ai-workflow/active").exists())

                # S1a: Carol runs `status` without --key and without credentials -> must report requires_member_auth!
                st_no_cred = run_cli(carol_root, "status")
                self.assertEqual(st_no_cred.returncode, 0, st_no_cred.stderr)
                st_nc_out = json.loads(st_no_cred.stdout)
                self.assertTrue(st_nc_out["initialized"])
                self.assertTrue(st_nc_out["credentials_missing"])
                self.assertTrue(st_nc_out["requires_member_auth"])
                self.assertEqual(st_nc_out["recommended_skill"], "team-init")
                self.assertEqual(st_nc_out["recommended_mode"], "auth")

                # S1b: Carol attempts a mutating command (`record-qa` or `complete-task`) without credentials -> MUST fail closed!
                mut_no_cred = run_cli(
                    carol_root, "record-qa",
                    "--key", "AP-1",
                    "--mode", "plan",
                    "--tc", "TC-001:AC-001:automated:Should fail closed without credentials",
                )
                self.assertNotEqual(mut_no_cred.returncode, 0)
                self.assertIn("auth", mut_no_cred.stderr.lower())

                # S4: Explicit --offline flag is still allowed if Carol explicitly chooses offline mode
                st_offline = run_cli(carol_root, "--offline", "status")
                self.assertEqual(st_offline.returncode, 0, st_offline.stderr)
                self.assertFalse(json.loads(st_offline.stdout).get("requires_member_auth", False))

                # S2a: `auth --inspect` shows configured roles & missing local token state
                auth_insp = run_cli(carol_root, "auth", "--inspect")
                self.assertEqual(auth_insp.returncode, 0, auth_insp.stderr)
                ai_out = json.loads(auth_insp.stdout)
                self.assertFalse(ai_out["credentials_configured"])
                self.assertTrue(ai_out["qase_required"])

                # S2b: Invalid Jira token fails closed without writing credentials.json
                bad_auth = run_cli(
                    carol_root, "auth",
                    "--jira-email", "carol@team.io",
                    "--jira-token", "invalid-token",
                )
                self.assertNotEqual(bad_auth.returncode, 0)
                self.assertFalse((carol_root / ".ai-workflow/credentials.json").exists())

                # S2c: Valid `auth` binds Carol's Jira token (auto-matching `qa` role!) and Qase token WITHOUT touching team-config.json!
                good_auth = run_cli(
                    carol_root, "auth",
                    "--jira-email", "carol@team.io",
                    "--jira-token", "qa-token-carol",
                    "--qase-token", "qase-secret-123",
                )
                self.assertEqual(good_auth.returncode, 0, good_auth.stderr)
                ga_out = json.loads(good_auth.stdout)
                self.assertTrue(ga_out["ok"])
                self.assertIn("qa", ga_out["matched_roles"])
                # Verify team-config.json is byte-for-byte untouched and credentials stored outside repo!
                self.assertEqual((carol_root / ".ai-workflow/team-config.json").read_bytes(), cfg_bytes)
                self.assertFalse((carol_root / ".ai-workflow/credentials.json").exists())
                carol_cred = json.loads(Path(ga_out["credentials_path"]).read_text(encoding="utf-8"))
                self.assertEqual(carol_cred["jira_email"], "carol@team.io")
                self.assertEqual(carol_cred["role_credentials"]["qa"]["jira_api_token"], "qa-token-carol")
                self.assertEqual(carol_cred["qase_api_token"], "qase-secret-123")

                # S3: Now Carol runs `status` (WITHOUT --key!) with empty .ai-workflow/active/ ->
                # Must automatically query POST /rest/api/3/search/jql, ignore Sub-task AP-102, hydrate AP-1.json, and route!
                st_live = run_cli(carol_root, "status")
                self.assertEqual(st_live.returncode, 0, st_live.stderr)
                sl_out = json.loads(st_live.stdout)
                self.assertTrue(sl_out["story_found"])
                self.assertEqual(sl_out["jira_key"], "AP-1")
                self.assertEqual(sl_out.get("hydrated_from"), "jira")
                self.assertEqual(sl_out.get("discovered_from_jira"), ["AP-1"])
                self.assertEqual(sl_out["recommended_skill"], "team-dev")
                self.assertEqual(sl_out["recommended_mode"], "implement")
                self.assertTrue((carol_root / ".ai-workflow/active/AP-1.json").exists())
                self.assertFalse((carol_root / ".ai-workflow/active/AP-102.json").exists())
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_13_progressive_role_claiming_from_partial_init_to_collaborator_auth(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            with tempfile.TemporaryDirectory() as tmp_alice, tempfile.TemporaryDirectory() as tmp_carol:
                alice_root = Path(tmp_alice)
                carol_root = Path(tmp_carol)

                # S1: Alice initializes repo filling ONLY her own roles (`pm,tech_lead,developer`) — leaving reviewer, qa, release_manager unclaimed!
                init_a = run_cli(
                    alice_root, "init",
                    "--project-key", "AP",
                    "--jira-url", f"http://127.0.0.1:{jira_port}",
                    "--role", "pm,tech_lead,developer",
                    "--name", "Alice Chen",
                    "--jira-email", "alice@team.io",
                    "--jira-token", "alice-tok-111",
                )
                self.assertEqual(init_a.returncode, 0, init_a.stderr)
                ia_out = json.loads(init_a.stdout)
                self.assertEqual(sorted(ia_out["claimed_roles"]), ["developer", "pm", "tech_lead"])
                self.assertEqual(sorted(ia_out["unclaimed_roles"]), ["qa", "release_manager", "reviewer"])

                cfg_a = json.loads((alice_root / ".ai-workflow/team-config.json").read_text(encoding="utf-8"))
                self.assertEqual(cfg_a["roles"]["pm"]["email"], "alice@team.io")
                self.assertEqual(cfg_a["roles"]["pm"]["jira_account_id"], "acc-alice")
                self.assertEqual(cfg_a["roles"]["developer"]["jira_account_id"], "acc-alice")
                self.assertEqual(cfg_a["roles"]["qa"]["email"], "")
                self.assertEqual(cfg_a["roles"]["qa"]["jira_account_id"], "")

                # S2: Alice creates Story AP-1, completes TASK-001 via Fast-Track, and runs handoff while `qa` is STILL unclaimed ->
                # Must NOT post fake "@QA Owner 請接手下一階段工作：" mention!
                self.assertEqual(
                    run_cli(
                        alice_root, "sync-story",
                        "--key", "AP-1",
                        "--title", "Progressive Role Claiming Story",
                        "--ac", "AC-001:Progressive claiming works",
                        "--status", "approved",
                        "--create",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        alice_root, "complete-task",
                        "--key", "AP-1",
                        "--task-id", "TASK-001",
                        "--commit", "c1a1m01",
                        "--files", "app.py",
                        "--test-result", "pytest: 1 passed",
                    ).returncode,
                    0,
                )
                MockJiraHandler.requests_log.clear()
                ho_unclaimed = run_cli(
                    alice_root, "handoff",
                    "--key", "AP-1",
                    "--review-status", "approved",
                    "--commit", "c1a1m01",
                )
                self.assertEqual(ho_unclaimed.returncode, 0, ho_unclaimed.stderr)
                ho_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(ho_comments), 1)
                ho_comment_str = json.dumps(ho_comments[0][2], ensure_ascii=False)
                self.assertNotIn("@QA Owner", ho_comment_str)

                # S3: Simulate git clone on Carol's machine (copying only Git-tracked files)
                (carol_root / ".ai-workflow").mkdir(parents=True, exist_ok=True)
                (carol_root / ".ai-workflow/team-config.json").write_bytes(
                    (alice_root / ".ai-workflow/team-config.json").read_bytes()
                )
                (carol_root / ".gitignore").write_bytes((alice_root / ".gitignore").read_bytes())

                # Carol runs `status` -> sees requires_member_auth AND unclaimed_roles: ["qa", "release_manager", "reviewer"]!
                st_c = run_cli(carol_root, "status")
                self.assertEqual(st_c.returncode, 0, st_c.stderr)
                stc_out = json.loads(st_c.stdout)
                self.assertTrue(stc_out["requires_member_auth"])
                self.assertEqual(sorted(stc_out["unclaimed_roles"]), ["qa", "release_manager", "reviewer"])
                self.assertIn("pm", stc_out["claimed_roles"])

                # Carol claims `reviewer,qa` via `auth` (without even passing --name; displayName & accountId auto-resolved from /myself!)
                claim_res = run_cli(
                    carol_root, "auth",
                    "--role", "reviewer,qa",
                    "--jira-email", "carol@team.io",
                    "--jira-token", "qa-token-carol",
                )
                self.assertEqual(claim_res.returncode, 0, claim_res.stderr)
                cr_out = json.loads(claim_res.stdout)
                self.assertTrue(cr_out["team_config_modified"])
                self.assertEqual(sorted(cr_out["claimed_roles_updated"]), ["qa", "reviewer"])
                self.assertEqual(cr_out["remaining_unclaimed_roles"], ["release_manager"])

                # Verify team-config.json on Carol's machine now has Carol Wu + acc-carol-from-token, and ZERO tokens!
                carol_cfg_raw = (carol_root / ".ai-workflow/team-config.json").read_text(encoding="utf-8")
                self.assertNotIn("qa-token-carol", carol_cfg_raw)
                carol_cfg = json.loads(carol_cfg_raw)
                self.assertEqual(carol_cfg["roles"]["qa"]["name"], "Carol Wu")
                self.assertEqual(carol_cfg["roles"]["qa"]["email"], "carol@team.io")
                self.assertEqual(carol_cfg["roles"]["qa"]["jira_account_id"], "acc-carol-from-token")
                self.assertEqual(carol_cfg["roles"]["reviewer"]["jira_account_id"], "acc-carol-from-token")

                # And Carol immediately runs `status` -> discovers AP-1 at Station 3b owned by Carol Wu!
                st_after = json.loads(run_cli(carol_root, "status").stdout)
                self.assertEqual(st_after["jira_key"], "AP-1")
                self.assertEqual(st_after["recommended_skill"], "team-qa")
                self.assertEqual(st_after["responsible_person"], "Carol Wu")
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_14_multi_member_role_pool_auto_assignment_peer_review_and_zhtw_default(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            with tempfile.TemporaryDirectory() as tmp_alice, \
                 tempfile.TemporaryDirectory() as tmp_bob, \
                 tempfile.TemporaryDirectory() as tmp_dave, \
                 tempfile.TemporaryDirectory() as tmp_en:
                alice_root = Path(tmp_alice)
                bob_root = Path(tmp_bob)
                dave_root = Path(tmp_dave)
                en_root = Path(tmp_en)

                # S1: Alice initializes AP claiming `pm,tech_lead,developer,reviewer,release_manager` with --specialty backend
                init_a = run_cli(
                    alice_root, "init",
                    "--project-key", "AP",
                    "--jira-url", f"http://127.0.0.1:{jira_port}",
                    "--role", "pm,tech_lead,developer,reviewer,release_manager",
                    "--specialty", "backend",
                    "--name", "Alice Chen",
                    "--jira-email", "alice@team.io",
                    "--jira-token", "alice-tok-111",
                )
                self.assertEqual(init_a.returncode, 0, init_a.stderr)

                # Simulate Bob cloning and running `auth --role developer,reviewer --specialty frontend` ->
                # MUST append Bob to `developer` and `reviewer` pools instead of overwriting Alice!
                (bob_root / ".ai-workflow").mkdir(parents=True, exist_ok=True)
                (bob_root / ".ai-workflow/team-config.json").write_bytes(
                    (alice_root / ".ai-workflow/team-config.json").read_bytes()
                )
                auth_bob = run_cli(
                    bob_root, "auth",
                    "--role", "developer,reviewer",
                    "--specialty", "frontend",
                    "--jira-email", "bob@team.io",
                    "--jira-token", "bob-tok-222",
                )
                self.assertEqual(auth_bob.returncode, 0, auth_bob.stderr)
                ab_out = json.loads(auth_bob.stdout)
                self.assertTrue(ab_out["team_config_modified"])

                # Re-running auth for Bob is idempotent (no duplicate Bob in members!)
                auth_bob_again = run_cli(
                    bob_root, "auth",
                    "--role", "developer,reviewer",
                    "--specialty", "frontend",
                    "--jira-email", "bob@team.io",
                    "--jira-token", "bob-tok-222",
                )
                self.assertEqual(auth_bob_again.returncode, 0, auth_bob_again.stderr)
                cfg_after_bob = json.loads((bob_root / ".ai-workflow/team-config.json").read_text(encoding="utf-8"))
                dev_members = cfg_after_bob["roles"]["developer"].get("members") or []
                self.assertEqual(len(dev_members), 2)
                self.assertEqual([m["email"] for m in dev_members], ["alice@team.io", "bob@team.io"])
                self.assertEqual([m.get("specialty") for m in dev_members], ["backend", "frontend"])

                # Carol & Dave both claim `qa` -> 2 QA members in pool!
                (dave_root / ".ai-workflow").mkdir(parents=True, exist_ok=True)
                (dave_root / ".ai-workflow/team-config.json").write_bytes(
                    (bob_root / ".ai-workflow/team-config.json").read_bytes()
                )
                self.assertEqual(
                    run_cli(
                        dave_root, "auth",
                        "--role", "qa",
                        "--jira-email", "carol@team.io",
                        "--jira-token", "carol-tok-333",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        dave_root, "auth",
                        "--role", "qa",
                        "--jira-email", "dave@team.io",
                        "--jira-token", "dave-tok-444",
                    ).returncode,
                    0,
                )
                # Sync merged team-config.json back to Alice & Bob (as via git pull)
                merged_cfg_bytes = (dave_root / ".ai-workflow/team-config.json").read_bytes()
                (alice_root / ".ai-workflow/team-config.json").write_bytes(merged_cfg_bytes)
                (bob_root / ".ai-workflow/team-config.json").write_bytes(merged_cfg_bytes)
                merged_cfg = json.loads(merged_cfg_bytes.decode("utf-8"))
                self.assertEqual(len(merged_cfg["roles"]["qa"]["members"]), 2)

                # S2a: Alice creates Draft Story AP-1 -> MUST immediately assign AP-1 to PM Alice (`acc-alice`) AND format Description in zh-TW!
                draft_res = run_cli(
                    alice_root, "sync-story",
                    "--key", "AP-1",
                    "--title", "多人分工與繁體中文預設驗證",
                    "--scope-in", "後端 API 與前端看板",
                    "--has-ui", "--has-api",
                    "--ac", "AC-001:後端資料庫與 API",
                    "--ac", "AC-002:前端看板元件",
                    "--status", "draft",
                    "--create",
                )
                self.assertEqual(draft_res.returncode, 0, draft_res.stderr)
                self.assertEqual(
                    (MockJiraHandler.stored_issues["AP-1"]["fields"].get("assignee") or {}).get("accountId"),
                    "acc-alice",
                )
                desc_str = json.dumps(MockJiraHandler.stored_issues["AP-1"]["fields"]["description"], ensure_ascii=False)
                self.assertIn("1. 範圍界定", desc_str)
                self.assertIn("2. 驗收條件", desc_str)

                # Approve AP-1 -> posts zh-TW Station 1 comment
                MockJiraHandler.requests_log.clear()
                app_res = run_cli(alice_root, "sync-story", "--key", "AP-1", "--status", "approved")
                self.assertEqual(app_res.returncode, 0, app_res.stderr)
                s1_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertIn("[站點 1：規格與設計核准]", json.dumps(s1_comments[0][2], ensure_ascii=False))

                # S2b: Decompose into Backend TASK-001 (AP-102) & Frontend TASK-002 (AP-103) ->
                # Auto-matches specialty (`backend` -> Alice `acc-alice`, `frontend` -> Bob `acc-bob-from-token`) and assigns BOTH Jira Sub-tasks!
                tasks_payload = [
                    {
                        "id": "TASK-001",
                        "title": "後端資料庫 Schema 與 API 模組",
                        "ac_coverage": ["AC-001"],
                        "files": ["src/server/api.ts"],
                        "verification": "npm test -- server",
                    },
                    {
                        "id": "TASK-002",
                        "title": "前端 React 看板與互動元件",
                        "ac_coverage": ["AC-002"],
                        "depends_on": ["TASK-001"],
                        "files": ["src/client/Board.tsx"],
                        "verification": "npm test -- client",
                    },
                ]
                sub_res = run_cli(
                    alice_root, "create-subtasks",
                    "--key", "AP-1",
                    "--tasks-json", json.dumps(tasks_payload, ensure_ascii=False),
                )
                self.assertEqual(sub_res.returncode, 0, sub_res.stderr)
                self.assertEqual(
                    (MockJiraHandler.stored_issues["AP-102"]["fields"].get("assignee") or {}).get("accountId"),
                    "acc-alice",
                )
                self.assertEqual(
                    (MockJiraHandler.stored_issues["AP-103"]["fields"].get("assignee") or {}).get("accountId"),
                    "acc-bob-from-token",
                )
                sub_desc_str = json.dumps(MockJiraHandler.stored_issues["AP-102"]["fields"]["description"], ensure_ascii=False)
                self.assertIn("任務編號：TASK-001", sub_desc_str)

                # Alice completes TASK-001; Bob (on Bob's machine!) completes TASK-002 ->
                # Bob's completion comment uses Bob's token & zh-TW `[任務完成：TASK-002]` attributed to Bob Lin!
                self.assertEqual(
                    run_cli(
                        alice_root, "complete-task",
                        "--key", "AP-1",
                        "--task-id", "TASK-001",
                        "--commit", "be00001",
                        "--files", "src/server/api.ts",
                        "--test-result", "vitest server passed",
                    ).returncode,
                    0,
                )
                MockJiraHandler.requests_log.clear()
                comp_bob = run_cli(
                    bob_root, "complete-task",
                    "--key", "AP-1",
                    "--task-id", "TASK-002",
                    "--commit", "fe00002",
                    "--files", "src/client/Board.tsx",
                    "--test-result", "vitest client passed",
                )
                self.assertEqual(comp_bob.returncode, 0, comp_bob.stderr)
                bob_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                bob_comment_str = json.dumps(bob_comments[0][2], ensure_ascii=False)
                self.assertIn("[任務完成：TASK-002]", bob_comment_str)
                self.assertIn("Bob Lin", bob_comment_str)

                # S2c: Peer Review & Multi-QA Handoff (`--to-assignee dave@team.io`)
                # Let's also check Peer Review when a single developer (Alice) owns the story:
                # Here `reviewer` has [Alice, Bob]; handing off to `--to-assignee dave@team.io` assigns AP-1 to Dave (`acc-dave-from-token`) and remembers `role_owners.qa`!
                MockJiraHandler.requests_log.clear()
                ho_res = run_cli(
                    bob_root, "handoff",
                    "--key", "AP-1",
                    "--review-status", "approved",
                    "--commit", "fe00002",
                    "--to-assignee", "dave@team.io",
                )
                self.assertEqual(ho_res.returncode, 0, ho_res.stderr)
                self.assertEqual(
                    (MockJiraHandler.stored_issues["AP-1"]["fields"].get("assignee") or {}).get("accountId"),
                    "acc-dave-from-token",
                )
                ho_str = json.dumps(
                    [r[2] for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")][0],
                    ensure_ascii=False,
                )
                self.assertIn("[站點 3a：開發交接與程式碼審查", ho_str)

                # Verify sticky `role_owners.qa` on Dave's machine: status shows Dave Ho as responsible_person for QA!
                st_dave = json.loads(run_cli(dave_root, "status", "--key", "AP-1").stdout)
                self.assertEqual(st_dave["recommended_skill"], "team-qa")
                self.assertEqual(st_dave["responsible_person"], "Dave Ho")

                # Dave plans QA TCs and files a Bug on TC-002 (covering AC-002, which belongs to TASK-002 owned by Bob!) ->
                # Newly created Bug AP-999 MUST be assigned directly to Bob (`acc-bob-from-token`), with zh-TW Bug description & comment!
                self.assertEqual(
                    run_cli(
                        dave_root, "record-qa",
                        "--key", "AP-1",
                        "--mode", "plan",
                        "--qa-backend", "jira-comment",
                        "--tc", "TC-001:AC-001:automated:API 驗證",
                        "--tc", "TC-002:AC-002:automated:看板按鈕驗證",
                    ).returncode,
                    0,
                )
                MockJiraHandler.requests_log.clear()
                bug_res = run_cli(
                    dave_root, "create-bug",
                    "--key", "AP-1",
                    "--tc-id", "TC-002",
                    "--ac-id", "AC-002",
                    "--title", "前端看板按鈕點擊無反應",
                    "--severity", "major",
                    "--steps", "點擊按讚",
                    "--expected", "票數 +1",
                    "--actual", "無反應",
                )
                self.assertEqual(bug_res.returncode, 0, bug_res.stderr)
                self.assertEqual(
                    (MockJiraHandler.stored_issues["AP-999"]["fields"].get("assignee") or {}).get("accountId"),
                    "acc-bob-from-token",
                )
                bug_desc_str = json.dumps(MockJiraHandler.stored_issues["AP-999"]["fields"]["description"], ensure_ascii=False)
                self.assertIn("重現步驟", bug_desc_str)

                # S4b: Explicit `--language en` override produces English comment headers when requested
                self.assertEqual(
                    run_cli(
                        en_root, "init",
                        "--offline",
                        "--project-key", "ENP",
                        "--language", "en",
                        "--role", "pm,developer,reviewer,qa,release_manager",
                        "--name", "Alice",
                        "--jira-email", "alice@team.io",
                        "--jira-token", "tok",
                    ).returncode,
                    0,
                )
                cfg_en = json.loads((en_root / ".ai-workflow/team-config.json").read_text(encoding="utf-8"))
                self.assertEqual(cfg_en["language"], "en")
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_15_bidirectional_jira_ui_reconciliation_and_description_drift_guard(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            with tempfile.TemporaryDirectory() as tmp_root:
                root = Path(tmp_root)
                self.assertEqual(
                    run_cli(
                        root, "init",
                        "--project-key", "AP",
                        "--jira-url", f"http://127.0.0.1:{jira_port}",
                        "--roles-json", json.dumps({
                            "pm": {"name": "Alice", "email": "alice@team.io", "api_token": "tok-a"},
                            "tech_lead": {"name": "Alice", "email": "alice@team.io"},
                            "developer": {
                                "members": [
                                    {"name": "Alice", "email": "alice@team.io"},
                                    {"name": "Bob", "email": "bob@team.io", "api_token": "tok-b"},
                                ]
                            },
                            "reviewer": {
                                "members": [
                                    {"name": "Alice", "email": "alice@team.io"},
                                    {"name": "Bob", "email": "bob@team.io"},
                                ]
                            },
                            "qa": {"name": "Carol", "email": "carol@team.io", "api_token": "tok-c"},
                            "release_manager": {"name": "Alice", "email": "alice@team.io"},
                        }),
                    ).returncode,
                    0,
                )

                # Create & approve AP-1, create & complete TASK-001 (AP-102, owned by Alice)
                self.assertEqual(
                    run_cli(
                        root, "sync-story",
                        "--key", "AP-1",
                        "--title", "雙向同步驗證 Story",
                        "--ac", "AC-001:基礎匯出功能",
                        "--status", "approved",
                        "--create",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "create-subtasks",
                        "--key", "AP-1",
                        "--task", "TASK-001:實作匯出模組:AC-001:pytest",
                        "--task-assignee", "TASK-001=alice@team.io",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "complete-task",
                        "--key", "AP-1",
                        "--task-id", "TASK-001",
                        "--commit", "111aaaa",
                        "--files", "export.py",
                        "--test-result", "pytest ok",
                    ).returncode,
                    0,
                )

                # Check Peer Review auto-avoidance: since Alice implemented AP-1, `handoff` selects Bob (`self_reviewed_with_ai == False`)!
                ho_peer = run_cli(root, "handoff", "--key", "AP-1", "--review-status", "approved", "--commit", "111aaaa")
                self.assertEqual(ho_peer.returncode, 0, ho_peer.stderr)
                story_after_ho = json.loads((root / ".ai-workflow/active/AP-1.json").read_text(encoding="utf-8"))
                self.assertFalse(story_after_ho["review"]["self_reviewed_with_ai"])
                self.assertEqual(story_after_ho["review"]["reviewer_person"]["email"], "bob@team.io")

                # S3a: Human in Jira Web UI:
                # 1) Manually adds Sub-task AP-109 ("人工在 Jira 新增的報表權限子任務", To Do, assigned to acc-bob-from-token)
                # 2) Reopens AP-102 back to "In Progress"
                manual_sub = {
                    "key": "AP-109",
                    "fields": {
                        "summary": "人工在 Jira 新增的報表權限子任務",
                        "issuetype": {"name": "Subtask", "subtask": True},
                        "status": {"name": "To Do"},
                        "assignee": {"accountId": "acc-bob-from-token", "displayName": "Bob Lin"},
                        "parent": {"key": "AP-1"},
                    },
                }
                MockJiraHandler.stored_issues["AP-109"] = manual_sub
                MockJiraHandler.stored_issues["AP-1"]["fields"].setdefault("subtasks", []).append(manual_sub)
                MockJiraHandler.stored_issues["AP-102"]["fields"]["status"] = {"name": "In Progress"}

                # Even though local `.ai-workflow/active/AP-1.json` ALREADY exists, `status --key AP-1` reconciles with Jira!
                st_rec = run_cli(root, "status", "--key", "AP-1")
                self.assertEqual(st_rec.returncode, 0, st_rec.stderr)
                sr_out = json.loads(st_rec.stdout)
                self.assertEqual(sr_out["recommended_skill"], "team-dev")
                self.assertEqual(sr_out["recommended_mode"], "implement")
                reconciled_story = json.loads((root / ".ai-workflow/active/AP-1.json").read_text(encoding="utf-8"))
                by_sub_key = {t["jira_subtask_key"]: t for t in reconciled_story["tasks"]}
                self.assertIn("AP-109", by_sub_key)
                self.assertEqual(by_sub_key["AP-109"]["source"], "jira-ui")
                self.assertEqual(by_sub_key["AP-102"]["status"], "pending")

                # Human marks BOTH AP-102 and AP-109 "Done" directly in Jira Web UI -> `pull --key AP-1` marks both completed!
                MockJiraHandler.stored_issues["AP-102"]["fields"]["status"] = {"name": "Done"}
                MockJiraHandler.stored_issues["AP-109"]["fields"]["status"] = {"name": "Done"}
                pull_done = run_cli(root, "pull", "--key", "AP-1")
                self.assertEqual(pull_done.returncode, 0, pull_done.stderr)
                after_pull = json.loads((root / ".ai-workflow/active/AP-1.json").read_text(encoding="utf-8"))
                self.assertTrue(all(t["status"] == "completed" for t in after_pull["tasks"]))

                # Complete QA verify so AP-1 is otherwise ready for release
                self.assertEqual(
                    run_cli(
                        root, "record-qa",
                        "--key", "AP-1",
                        "--mode", "verify",
                        "--qa-backend", "jira-comment",
                        "--commit", "111aaaa",
                        "--all-pass",
                    ).returncode,
                    0,
                )

                # S3b: Human QA files & links Bug `AP-888` directly in Jira Web UI!
                manual_bug = {
                    "key": "AP-888",
                    "fields": {
                        "summary": "Jira 網頁人工新增的匯出亂碼 Bug",
                        "issuetype": {"name": "Bug", "subtask": False},
                        "status": {"name": "To Do"},
                    },
                }
                MockJiraHandler.stored_issues["AP-888"] = manual_bug
                MockJiraHandler.stored_issues["AP-1"]["fields"].setdefault("issuelinks", []).append({
                    "type": {"name": "Blocks"},
                    "inwardIssue": manual_bug,
                })

                # `close-story` MUST reconcile first and FAIL CLOSED because AP-888 is open on Jira!
                blocked_close = run_cli(root, "close-story", "--key", "AP-1", "--commit", "111aaaa")
                self.assertNotEqual(blocked_close.returncode, 0)
                self.assertIn("AP-888", blocked_close.stderr)

                # Human closes AP-888 in Jira Web UI -> `pull --key AP-1` updates AP-888 to completed!
                MockJiraHandler.stored_issues["AP-888"]["fields"]["status"] = {"name": "Done"}
                self.assertEqual(run_cli(root, "pull", "--key", "AP-1").returncode, 0)

                # S3c: Human edits AP-1 Story Description directly in Jira Web UI (adding AC-002 + human note)
                MockJiraHandler.stored_issues["AP-1"]["fields"]["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "- AC-001: 基礎匯出功能\n- AC-002: PM 在 Jira 網頁手動新增的 BOM 編碼要求\nPM 補充備註：請務必保留 UTF-8 BOM"}],
                        }
                    ],
                }
                pull_desc = run_cli(root, "pull", "--key", "AP-1")
                self.assertEqual(pull_desc.returncode, 0, pull_desc.stderr)
                pd_out = json.loads(pull_desc.stdout)
                self.assertTrue(pd_out["reconciled"]["description_modified"])
                desc_story = json.loads((root / ".ai-workflow/active/AP-1.json").read_text(encoding="utf-8"))
                ac_ids = [a["id"] for a in desc_story["acceptance_criteria"]]
                self.assertIn("AC-002", ac_ids)

                # Blind `sync-story` without `--accept-remote-description` or `--force-overwrite` MUST fail closed to protect human edits!
                clobber_attempt = run_cli(root, "sync-story", "--key", "AP-1", "--title", "試圖覆蓋")
                self.assertNotEqual(clobber_attempt.returncode, 0)
                self.assertIn("description", clobber_attempt.stderr.lower())

                # Passing `--accept-remote-description` succeeds cleanly!
                safe_sync = run_cli(root, "sync-story", "--key", "AP-1", "--accept-remote-description")
                self.assertEqual(safe_sync.returncode, 0, safe_sync.stderr)

                # S3d: PM creates a brand-new Story `AP-9` directly in Jira Web UI while `AP-1.json` already exists locally ->
                # `status` (without --key) discovers `AP-9` via JQL!
                MockJiraHandler.stored_issues["AP-9"] = {
                    "key": "AP-9",
                    "fields": {
                        "summary": "PM 在 Jira 網頁新建的第二張需求單",
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "To Do"},
                    },
                }
                st_multi = run_cli(root, "status")
                self.assertEqual(st_multi.returncode, 0, st_multi.stderr)
                sm_out = json.loads(st_multi.stdout)
                self.assertTrue(sm_out["ambiguous"])
                self.assertIn("AP-1", sm_out["active_stories"])
                self.assertIn("AP-9", sm_out["active_stories"])
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_16_qa_media_attachments_jira_and_qase_sync(self):
        MockJiraHandler.requests_log = []
        MockJiraHandler.stored_issues = {}
        MockJiraHandler.stored_properties = {}
        MockJiraHandler.stored_attachments = {}
        MockJiraHandler.simulate_put_404_keys = set()
        MockJiraHandler.reject_subtask_types = set()
        MockJiraHandler.reject_attachment_keys = set()

        MockQaseHandler.requests_log = []
        MockQaseHandler.uploaded_files_log = []
        MockQaseHandler.suites = {}
        MockQaseHandler.cases = {}
        MockQaseHandler.runs = {}
        MockQaseHandler.attachments = {}
        MockQaseHandler.defects = {}
        MockQaseHandler.next_suite_id = 10
        MockQaseHandler.next_case_id = 100
        MockQaseHandler.next_run_id = 500
        MockQaseHandler.next_defect_id = 700
        MockQaseHandler.fail_external_issue = False

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        qase_srv = HTTPServer(("127.0.0.1", 0), MockQaseHandler)
        jt = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        qt = threading.Thread(target=qase_srv.serve_forever, daemon=True)
        jt.start()
        qt.start()
        jira_url = f"http://127.0.0.1:{jira_srv.server_port}"
        qase_url = f"http://127.0.0.1:{qase_srv.server_port}/v1"

        try:
            with tempfile.TemporaryDirectory() as td_comment, tempfile.TemporaryDirectory() as td_qase:
                # ------------------------------------------------------------------
                # Part A: Backend == "jira-comment" + S1 Safety & S2 Jira Bug Attachments
                # ------------------------------------------------------------------
                root_c = Path(td_comment)
                subprocess.run(["git", "init", "-q"], cwd=str(root_c), check=True)
                init_c = run_cli(
                    root_c,
                    "init",
                    "--project-key", "JC",
                    "--jira-url", jira_url,
                    "--jira-email", "alice@team.io",
                    "--jira-token", "tok-alice",
                    "--role", "pm,developer,reviewer,qa,release_manager",
                    "--qa-backend", "jira-comment",
                )
                self.assertEqual(init_c.returncode, 0, init_c.stderr)

                self.assertEqual(
                    run_cli(
                        root_c, "sync-story", "--key", "JC-1",
                        "--title", "按讚動畫與面板",
                        "--status", "approved",
                        "--ac", "AC-001:按讚切換不閃爍",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root_c, "complete-task", "--key", "JC-1",
                        "--task-id", "TASK-001", "--commit", "abc1111",
                        "--files", "src/App.tsx", "--test-result", "1 passed",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root_c, "handoff", "--key", "JC-1",
                        "--from-role", "reviewer", "--to-role", "qa",
                        "--review-status", "approved", "--commit", "abc1111",
                        "--summary", "LGTM",
                    ).returncode,
                    0,
                )

                # Prepare binary PNG screenshot & console log + unsafe/invalid files
                ev_dir = root_c / "qa-evidence"
                ev_dir.mkdir(parents=True, exist_ok=True)
                png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\xff\xd9"
                shot_png = ev_dir / "broken-button.png"
                shot_png.write_bytes(png_bytes)
                console_log = ev_dir / "console.log"
                console_log.write_text("TypeError: Cannot read properties of null\n", encoding="utf-8")
                empty_file = ev_dir / "empty.png"
                empty_file.write_bytes(b"")

                # S1a: Missing file fails fast BEFORE creating any Jira Bug
                missing_res = run_cli(
                    root_c, "create-bug", "--key", "JC-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "按鈕破版", "--severity", "major",
                    "--steps", "點擊按讚", "--expected", "正常縮放", "--actual", "溢位破版",
                    "--attach", str(ev_dir / "does-not-exist.png"),
                )
                self.assertNotEqual(missing_res.returncode, 0)
                self.assertNotIn("JC-999", MockJiraHandler.stored_issues)

                # S1b: Empty (0-byte) file fails fast BEFORE creating any Jira Bug
                empty_res = run_cli(
                    root_c, "create-bug", "--key", "JC-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "按鈕破版", "--severity", "major",
                    "--steps", "點擊按讚", "--expected", "正常縮放", "--actual", "溢位破版",
                    "--attach", str(empty_file),
                )
                self.assertNotEqual(empty_res.returncode, 0)
                self.assertNotIn("JC-999", MockJiraHandler.stored_issues)

                # S1c: Sensitive secret file (`credentials.json` / `.env`) is blocked from upload!
                (root_c / ".ai-workflow/credentials.json").write_text('{"jira_api_token":"dummy"}\n', encoding="utf-8")
                secret_res = run_cli(
                    root_c, "create-bug", "--key", "JC-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "按鈕破版", "--severity", "major",
                    "--steps", "點擊按讚", "--expected", "正常縮放", "--actual", "溢位破版",
                    "--attach", str(root_c / ".ai-workflow/credentials.json"),
                )
                self.assertNotEqual(secret_res.returncode, 0)
                self.assertNotIn("JC-999", MockJiraHandler.stored_issues)

                # S2: `record-qa --mode verify --tc-attach TC-001=<shot>` records TC attachment, and `create-bug`
                # inherits `shot_png` + appends explicit `--attach console.log` -> uploads BOTH to Jira Bug `JC-999`,
                # with ZERO Qase requests!
                rqa_c = run_cli(
                    root_c, "record-qa", "--key", "JC-1", "--mode", "verify",
                    "--commit", "abc1111",
                    "--result", "TC-001=fail:按讚按鈕溢位",
                    "--tc-attach", f"TC-001={shot_png}",
                )
                self.assertEqual(rqa_c.returncode, 0, rqa_c.stderr)

                bug_c = run_cli(
                    root_c, "create-bug", "--key", "JC-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "按讚按鈕溢位破版", "--severity", "major",
                    "--steps", "1. 開啟首頁 2. 點擊按讚", "--expected", "按鈕寬度固定", "--actual", "右側文字溢位",
                    "--attach", str(console_log),
                )
                self.assertEqual(bug_c.returncode, 0, bug_c.stderr)
                bug_c_out = json.loads(bug_c.stdout)
                self.assertEqual(bug_c_out["bug_key"], "JC-999")
                self.assertEqual(len(MockQaseHandler.requests_log), 0, "jira-comment backend must never call Qase")

                # Verify Jira Bug `JC-999` received exact binary PNG + console.log via multipart/form-data (`field="file"`)
                jc_atts = MockJiraHandler.stored_attachments.get("JC-999") or []
                self.assertEqual([a["filename"] for a in jc_atts], ["broken-button.png", "console.log"])
                self.assertTrue(all(a["field"] == "file" for a in jc_atts))
                self.assertEqual(jc_atts[0]["sha256"], hashlib.sha256(png_bytes).hexdigest())

                # Verify Bug description mentions `附件證據` and filenames, and local active JSON stores attachment metadata
                jc_bug_adf = json.dumps(MockJiraHandler.stored_issues["JC-999"]["fields"]["description"], ensure_ascii=False)
                self.assertIn("broken-button.png", jc_bug_adf)
                self.assertIn("console.log", jc_bug_adf)
                jc_story = json.loads((root_c / ".ai-workflow/active/JC-1.json").read_text(encoding="utf-8"))
                self.assertEqual(len(jc_story["bugs"][0]["attachments"]), 2)

                # S4: Graceful degradation if Jira returns HTTP 403 on `/attachments` + Offline mode check
                MockJiraHandler.reject_attachment_keys.add("JC-998")
                bug_403 = run_cli(
                    root_c, "create-bug", "--key", "JC-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "權限受限附件降級測試", "--severity", "minor",
                    "--steps", "測試", "--expected", "正常", "--actual", "403 降級",
                    "--attach", str(shot_png),
                )
                self.assertEqual(bug_403.returncode, 0, bug_403.stderr)
                bug_403_out = json.loads(bug_403.stdout)
                self.assertEqual(bug_403_out["bug_key"], "JC-998")
                self.assertIn("attachment_warning", bug_403_out)

                off_bug = run_cli(
                    root_c, "--offline", "create-bug", "--key", "JC-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "離線附件驗證", "--severity", "minor",
                    "--steps", "離線", "--expected", "正常", "--actual", "記錄本地附件",
                    "--attach", str(shot_png),
                )
                self.assertEqual(off_bug.returncode, 0, off_bug.stderr)

                # ------------------------------------------------------------------
                # Part B: Backend == "qase" -> Dual Sync to Jira Bug AND Qase (Run Result + Defect + Resolve)
                # ------------------------------------------------------------------
                root_q = Path(td_qase)
                subprocess.run(["git", "init", "-q"], cwd=str(root_q), check=True)
                init_q = run_cli(
                    root_q,
                    "init",
                    "--project-key", "QS",
                    "--jira-url", jira_url,
                    "--jira-email", "alice@team.io",
                    "--jira-token", "tok-alice",
                    "--role", "pm,developer,reviewer,qa,release_manager",
                    "--qa-backend", "qase",
                    "--qase-project-code", "QS",
                    "--qase-token", "qase-secret-123",
                    "--qase-base-url", qase_url,
                )
                self.assertEqual(init_q.returncode, 0, init_q.stderr)

                self.assertEqual(
                    run_cli(
                        root_q, "sync-story", "--key", "QS-1",
                        "--title", "Roadmap 拖拉與投票",
                        "--status", "approved",
                        "--ac", "AC-001:投票成功",
                        "--ac", "AC-002:看板渲染正常",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root_q, "complete-task", "--key", "QS-1",
                        "--task-id", "TASK-001", "--commit", "def2222",
                        "--files", "src/Roadmap.tsx", "--test-result", "2 passed",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root_q, "handoff", "--key", "QS-1",
                        "--from-role", "reviewer", "--to-role", "qa",
                        "--review-status", "approved", "--commit", "def2222",
                        "--summary", "Ready for QA",
                    ).returncode,
                    0,
                )

                q_ev = root_q / "e2e-artifacts"
                q_ev.mkdir(parents=True, exist_ok=True)
                ui_png = q_ev / "roadmap-overlap.png"
                ui_png.write_bytes(png_bytes)
                trace_webm = q_ev / "roadmap-trace.webm"
                trace_webm.write_bytes(b"\x1a\x45\xdf\xa3WEBM_VIDEO_PAYLOAD_BYTES")

                # Step A: `record-qa --mode verify` with `--tc-attach TC-002=ui_png`
                rqa_q = run_cli(
                    root_q, "record-qa", "--key", "QS-1", "--mode", "verify",
                    "--commit", "def2222",
                    "--result", "TC-001=pass",
                    "--result", "TC-002=fail:卡片重疊",
                    "--tc-attach", f"TC-002={ui_png}",
                )
                self.assertEqual(rqa_q.returncode, 0, rqa_q.stderr)

                # Verify Qase Run #500 received `attachments: [<hash>]` on TC-002 (case_id 101)
                self.assertIn(500, MockQaseHandler.runs)
                run_results = MockQaseHandler.runs[500]["results"]
                tc2_res = [r for r in run_results if r.get("case_id") == 101][0]
                self.assertEqual(tc2_res["status"], "failed")
                self.assertEqual(len(tc2_res.get("attachments") or []), 1)
                ui_png_hash = tc2_res["attachments"][0]
                self.assertEqual(MockQaseHandler.attachments[ui_png_hash]["filename"], "roadmap-overlap.png")

                upload_calls_after_rqa = [
                    req for req in MockQaseHandler.requests_log if req[0] == "POST" and req[1].startswith("/v1/attachment/")
                ]
                self.assertEqual(len(upload_calls_after_rqa), 1)

                # Step B: `create-bug` on `QS-1` passing `--attach ui_png` (already uploaded -> deduplicated!)
                # AND a new `--attach trace_webm` -> uploads BOTH to Jira Bug `QS-999`, uploads ONLY `trace_webm`
                # anew to Qase, creates Qase Defect #700 with BOTH hashes, and updates Run #500 result!
                bug_q = run_cli(
                    root_q, "create-bug", "--key", "QS-1", "--tc-id", "TC-002", "--ac-id", "AC-002",
                    "--title", "Roadmap 卡片窄螢幕重疊", "--severity", "major",
                    "--steps", "調整視窗至 375px", "--expected", "垂直堆疊", "--actual", "卡片重疊遮擋",
                    "--attach", str(ui_png),
                    "--attach", str(trace_webm),
                )
                self.assertEqual(bug_q.returncode, 0, bug_q.stderr)
                bug_q_out = json.loads(bug_q.stdout)
                self.assertEqual(bug_q_out["bug_key"], "QS-999")
                self.assertEqual(bug_q_out["qase_defect"]["defect_id"], 700)

                # Jira Bug `QS-999` received BOTH files
                qs_jira_atts = MockJiraHandler.stored_attachments.get("QS-999") or []
                self.assertEqual([a["filename"] for a in qs_jira_atts], ["roadmap-overlap.png", "roadmap-trace.webm"])

                # Qase uploaded `ui_png` ONCE (cached hash reused!) and `trace_webm` ONCE -> exact 2 file parts uploaded
                self.assertEqual(
                    MockQaseHandler.uploaded_files_log,
                    ["roadmap-overlap.png", "roadmap-trace.webm"],
                    "Cached Qase hash for ui_png must not be re-uploaded",
                )

                # Qase Defect #700 has BOTH attachment hashes & references `QS-999`
                self.assertIn(700, MockQaseHandler.defects)
                defect_payload = MockQaseHandler.defects[700]["payload"]
                self.assertEqual(len(defect_payload.get("attachments") or []), 2)
                self.assertIn(ui_png_hash, defect_payload["attachments"])
                self.assertIn("QS-999", defect_payload.get("actual_result", ""))
                self.assertFalse(MockQaseHandler.defects[700]["resolved"])

                # Step C: Developer fixes `QS-999` via `complete-task --task-id QS-999` -> Qase Defect #700 is resolved!
                fix_q = run_cli(
                    root_q, "complete-task", "--key", "QS-1", "--task-id", "QS-999",
                    "--commit", "fed3333", "--files", "src/Roadmap.tsx", "--test-result", "2 passed",
                )
                self.assertEqual(fix_q.returncode, 0, fix_q.stderr)
                self.assertTrue(MockQaseHandler.defects[700]["resolved"])
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()
            qase_srv.shutdown()
            qase_srv.server_close()

    def test_17_qa_report_exploratory_story_bug_and_standalone_production_bug_lifecycle(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.stored_attachments.clear()
        MockJiraHandler.reject_attachment_keys.clear()

        MockQaseHandler.requests_log.clear()
        MockQaseHandler.uploaded_files_log.clear()
        MockQaseHandler.suites.clear()
        MockQaseHandler.cases.clear()
        MockQaseHandler.runs.clear()
        MockQaseHandler.attachments.clear()
        MockQaseHandler.defects.clear()
        MockQaseHandler.next_suite_id = 10
        MockQaseHandler.next_case_id = 100
        MockQaseHandler.next_run_id = 500
        MockQaseHandler.next_defect_id = 700
        MockQaseHandler.expected_token = "qase-secret-123"
        MockQaseHandler.fail_external_issue = False

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        qase_srv = HTTPServer(("127.0.0.1", 0), MockQaseHandler)
        qase_port = qase_srv.server_port
        t_qase = threading.Thread(target=qase_srv.serve_forever, daemon=True)
        t_qase.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                exp_png = root / "exploratory-crash.png"
                exp_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"exploratory-bug-shot" * 20)
                prod_png = root / "prod-500.png"
                prod_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"prod-bug-shot" * 20)
                retest_png = root / "prod-still-failing.png"
                retest_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"prod-retest-fail" * 20)

                roles_cfg = {
                    "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acct-alice"},
                    "tech_lead": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acct-alice"},
                    "developer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acct-bob"},
                    "reviewer": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acct-alice"},
                    "qa": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acct-carol"},
                    "release_manager": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acct-alice"},
                }
                self.assertEqual(
                    run_cli(
                        root, "init",
                        "--project-key", "AP",
                        "--jira-url", f"http://127.0.0.1:{jira_port}",
                        "--jira-email", "alice@team.io",
                        "--jira-token", "tok-alice",
                        "--qa-backend", "qase",
                        "--qase-project-code", "AP",
                        "--qase-token", "qase-secret-123",
                        "--qase-base-url", f"http://127.0.0.1:{qase_port}/v1",
                        "--qase-app-url", f"http://127.0.0.1:{qase_port}/app",
                        "--roles-json", json.dumps(roles_cfg),
                        "--provenance-audit-mode", "off",
                    ).returncode,
                    0,
                )

                # =====================================================================
                # PART A: Exploratory / Ad-Hoc Bug on Story `AP-1` WITHOUT `--tc-id`
                # =====================================================================
                self.assertEqual(
                    run_cli(
                        root, "sync-story", "--key", "AP-1", "--title", "訂單匯出",
                        "--ac", "AC-001:匯出 UTF-8 CSV", "--ac", "AC-002:無權限回傳 403",
                        "--status", "approved", "--create",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "complete-task", "--key", "AP-1", "--task-id", "TASK-001",
                        "--commit", "aaa1111", "--files", "src/export.py", "--test-result", "2 passed",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "handoff", "--key", "AP-1", "--review-status", "approved",
                        "--commit", "aaa1111", "--summary", "LGTM",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "record-qa", "--key", "AP-1", "--mode", "plan",
                        "--tc", "TC-001:AC-001:automated:驗證匯出 CSV",
                        "--tc", "TC-002:AC-002:automated:驗證 403 攔截",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "record-qa", "--key", "AP-1", "--mode", "verify",
                        "--commit", "aaa1111", "--all-pass",
                    ).returncode,
                    0,
                )

                # QA discovers an unscripted exploratory defect NOT in TC-001/TC-002 -> omits `--tc-id`!
                exp_bug = run_cli(
                    root, "create-bug", "--key", "AP-1",
                    "--title", "快速連點兩次匯出按鈕會產生重複下載請求",
                    "--severity", "major",
                    "--steps", "1. 進入訂單頁\n2. 連續雙擊匯出按鈕",
                    "--expected", "匯出中應防抖鎖定按鈕",
                    "--actual", "同時發出 2 個匯出請求",
                    "--attach", str(exp_png),
                )
                self.assertEqual(exp_bug.returncode, 0, exp_bug.stderr)
                exp_bug_out = json.loads(exp_bug.stdout)
                self.assertEqual(exp_bug_out["bug_key"], "AP-999")
                self.assertEqual(exp_bug_out["tc_id"], "TC-EXP-001")
                self.assertTrue(exp_bug_out["exploratory"])

                # CRITICAL INVARIANT: TC-001 & TC-002 MUST remain `pass` (unpolluted!), while TC-EXP-001 is `fail` and synced to Qase!
                ap1_state = json.loads((root / ".ai-workflow/active/AP-1.json").read_text(encoding="utf-8"))
                tcs_by_id = {c["id"]: c for c in ap1_state["qa"]["test_cases"]}
                self.assertEqual(tcs_by_id["TC-001"]["result"], "pass")
                self.assertEqual(tcs_by_id["TC-002"]["result"], "pass")
                self.assertIn("TC-EXP-001", tcs_by_id)
                self.assertEqual(tcs_by_id["TC-EXP-001"]["result"], "fail")
                self.assertEqual(tcs_by_id["TC-EXP-001"]["execution_type"], "manual")
                self.assertIn("qase_case_id", tcs_by_id["TC-EXP-001"])
                self.assertEqual(len(MockJiraHandler.stored_attachments.get("AP-999") or []), 1)
                self.assertIn(700, MockQaseHandler.defects)

                # Developer fixes `AP-999` + re-reviews -> `close-story` MUST still block until `TC-EXP-001` passes in verify!
                self.assertEqual(
                    run_cli(
                        root, "complete-task", "--key", "AP-1", "--task-id", "AP-999",
                        "--commit", "bbb2222", "--files", "src/export.py", "--test-result", "3 passed",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "handoff", "--key", "AP-1", "--review-status", "approved",
                        "--commit", "bbb2222", "--summary", "Debounce added",
                    ).returncode,
                    0,
                )
                # Verify with ONLY `--all-pass` -> `TC-EXP-001` is `manual` (still `fail`), so Anti-False-Pass refuses to pass it!
                ver_inc = run_cli(root, "record-qa", "--key", "AP-1", "--mode", "verify", "--commit", "bbb2222", "--all-pass")
                self.assertEqual(ver_inc.returncode, 0, ver_inc.stderr)
                self.assertEqual(json.loads(ver_inc.stdout)["qa_status"], "failed")
                self.assertNotEqual(run_cli(root, "close-story", "--key", "AP-1", "--commit", "bbb2222").returncode, 0)

                # Explicit `--result TC-EXP-001=pass` passes QA and unlocks `close-story`!
                ver_ok = run_cli(
                    root, "record-qa", "--key", "AP-1", "--mode", "verify",
                    "--commit", "bbb2222", "--all-pass", "--result", "TC-EXP-001=pass",
                )
                self.assertEqual(ver_ok.returncode, 0, ver_ok.stderr)
                self.assertEqual(json.loads(ver_ok.stdout)["qa_status"], "passed")

                # =====================================================================
                # PART B: Standalone Production Bug (`create-bug --standalone`) Full Lifecycle
                # =====================================================================
                prod_bug = run_cli(
                    root, "create-bug", "--standalone",
                    "--title", "線上報修：訂單金額為 0 時匯出報表回傳 HTTP 500",
                    "--severity", "blocker",
                    "--steps", "1. 篩選金額為 0 的免費體驗訂單\n2. 點擊匯出",
                    "--expected", "正常匯出金額為 0 的列",
                    "--actual", "除以零導致 HTTP 500",
                    "--attach", str(prod_png),
                )
                self.assertEqual(prod_bug.returncode, 0, prod_bug.stderr)
                pb_out = json.loads(prod_bug.stdout)
                prod_key = pb_out["bug_key"]
                self.assertEqual(prod_key, "AP-998")
                self.assertEqual(pb_out["issue_kind"], "standalone_bug")
                self.assertEqual(pb_out["qase_defect"]["defect_id"], 701)
                self.assertFalse(MockQaseHandler.defects[701]["resolved"])
                self.assertEqual(len(MockJiraHandler.stored_attachments.get("AP-998") or []), 1)

                # Verify NO Sub-task was created under AP-998 and NO issueLink was posted for AP-998
                self.assertEqual(MockJiraHandler.stored_issues["AP-998"]["fields"].get("subtasks") or [], [])
                link_calls_for_998 = [
                    req for req in MockJiraHandler.requests_log
                    if req[0] == "POST" and req[1] == "/rest/api/3/issueLink"
                    and "AP-998" in json.dumps(req[2])
                ]
                self.assertEqual(link_calls_for_998, [])

                # Router (`status --key AP-998`) routes directly to `team-dev` (`implement`), skipping `team-spec` & `plan`!
                st_998 = json.loads(run_cli(root, "status", "--key", "AP-998").stdout)
                self.assertEqual(st_998["recommended_skill"], "team-dev")
                self.assertEqual(st_998["recommended_mode"], "implement")

                # Station 2: RD fixes AP-998 via `complete-task --key AP-998 --task-id AP-998`
                # MUST NOT prematurely transition AP-998 to `Done`!
                fix_998 = run_cli(
                    root, "complete-task", "--key", "AP-998", "--task-id", "AP-998",
                    "--commit", "ccc3333", "--files", "src/calc.py", "--test-result", "pytest tests/test_calc.py (4 passed)",
                )
                self.assertEqual(fix_998.returncode, 0, fix_998.stderr)
                self.assertNotEqual(
                    MockJiraHandler.stored_issues["AP-998"]["fields"]["status"]["name"].lower(),
                    "done",
                    "Standalone Bug AP-998 must NOT be marked Done before Review, QA, and Release!",
                )

                # Station 3a: Code Review approves `ccc3333` -> moves AP-998 to `In QA`
                self.assertEqual(
                    run_cli(
                        root, "handoff", "--key", "AP-998", "--review-status", "approved",
                        "--commit", "ccc3333", "--summary", "Zero-division guard verified",
                    ).returncode,
                    0,
                )
                self.assertEqual(MockJiraHandler.stored_issues["AP-998"]["fields"]["status"]["name"], "In QA")

                # Station 3b (Round 1 — Regression check FAILS!): QA runs `verify` with `TC-001=fail` + `--tc-attach`
                # Must upload failure screenshot onto `AP-998`, reopen `TASK-001`, and transition `AP-998` back to `In Progress` WITHOUT creating nested Bug!
                bug_count_before = len([
                    k for k, v in MockJiraHandler.stored_issues.items()
                    if ((v.get("fields") or {}).get("issuetype") or {}).get("name") == "Bug"
                ])
                ver_fail_998 = run_cli(
                    root, "record-qa", "--key", "AP-998", "--mode", "verify",
                    "--commit", "ccc3333",
                    "--result", "TC-001=fail:負數折抵後金額為 0 仍觸發 500",
                    "--tc-attach", f"TC-001={retest_png}",
                )
                self.assertEqual(ver_fail_998.returncode, 0, ver_fail_998.stderr)
                bug_count_after = len([
                    k for k, v in MockJiraHandler.stored_issues.items()
                    if ((v.get("fields") or {}).get("issuetype") or {}).get("name") == "Bug"
                ])
                self.assertEqual(bug_count_after, bug_count_before, "Failed retest on standalone bug must not spawn nested Bug")
                self.assertEqual(len(MockJiraHandler.stored_attachments.get("AP-998") or []), 2)
                self.assertEqual(MockJiraHandler.stored_issues["AP-998"]["fields"]["status"]["name"], "In Progress")
                st_reopened = json.loads(run_cli(root, "status", "--key", "AP-998").stdout)
                self.assertEqual(st_reopened["recommended_skill"], "team-dev")
                self.assertEqual(st_reopened["recommended_mode"], "implement")

                # RD submits fix `ddd4444` -> Review approves -> QA verifies `TC-001=pass` -> Qase Defect #701 resolves!
                self.assertEqual(
                    run_cli(
                        root, "complete-task", "--key", "AP-998",
                        "--commit", "ddd4444", "--files", "src/calc.py", "--test-result", "5 passed",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "handoff", "--key", "AP-998", "--review-status", "approved",
                        "--commit", "ddd4444", "--summary", "Discount zero-amount path fixed",
                    ).returncode,
                    0,
                )
                ver_pass_998 = run_cli(
                    root, "record-qa", "--key", "AP-998", "--mode", "verify",
                    "--commit", "ddd4444", "--result", "TC-001=pass",
                )
                self.assertEqual(ver_pass_998.returncode, 0, ver_pass_998.stderr)
                self.assertTrue(
                    MockQaseHandler.defects[701]["resolved"],
                    "Passing QA verification on standalone bug must resolve its Qase Defect",
                )

                # Station 4: `close-story --key AP-998` transitions `AP-998` to `Done` and deletes active file!
                close_998 = run_cli(
                    root, "close-story", "--key", "AP-998", "--version", "v1.2.1", "--commit", "ddd4444",
                    "--notes", "修復零元訂單匯出 500 錯誤（Bug Fix，手冊無須變更）",
                )
                self.assertEqual(close_998.returncode, 0, close_998.stderr)
                self.assertEqual(MockJiraHandler.stored_issues["AP-998"]["fields"]["status"]["name"], "Done")
                self.assertFalse((root / ".ai-workflow/active/AP-998.json").exists())

                # Also close Story AP-1 so active directory is completely empty before Part C discovery test
                self.assertEqual(run_cli(root, "close-story", "--key", "AP-1", "--commit", "bbb2222").returncode, 0)

                # =====================================================================
                # PART C: Auto-Discovery of CS-Filed Standalone Bug (`AP-777`) vs Story-Linked Bug (`AP-888`)
                # =====================================================================
                cs_desc = {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "客服原始報修描述：Safari 登入後重整白屏"}]}],
                }
                MockJiraHandler.stored_issues["AP-777"] = {
                    "key": "AP-777",
                    "fields": {
                        "summary": "客服報修：Safari 登入白屏",
                        "description": cs_desc,
                        "issuetype": {"name": "Bug", "subtask": False},
                        "status": {"name": "To Do"},
                    },
                }
                # Story AP-2 (Done? No, In Progress) with a linked child Bug AP-888 -> AP-888 must NOT be discovered as standalone!
                MockJiraHandler.stored_issues["AP-888"] = {
                    "key": "AP-888",
                    "fields": {
                        "summary": "附屬於 AP-2 的測試缺陷",
                        "issuetype": {"name": "Bug", "subtask": False},
                        "status": {"name": "To Do"},
                    },
                }
                MockJiraHandler.stored_issues["AP-2"] = {
                    "key": "AP-2",
                    "fields": {
                        "summary": "新報表需求",
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "In Progress"},
                        "issuelinks": [{"type": {"name": "Blocks"}, "inwardIssue": MockJiraHandler.stored_issues["AP-888"]}],
                    },
                }

                disc_res = run_cli(root, "status")
                self.assertEqual(disc_res.returncode, 0, disc_res.stderr)
                disc_out = json.loads(disc_res.stdout)
                # Both Story `AP-2` AND standalone Bug `AP-777` are discovered, while child Bug `AP-888` is excluded!
                self.assertEqual(sorted(disc_out["active_stories"]), ["AP-2", "AP-777"])
                self.assertNotIn("AP-888", disc_out["active_stories"])

                # Check `AP-777` hydrated as `standalone_bug`, routes to `team-dev` (`implement`), and CS description is untouched!
                st_777 = json.loads(run_cli(root, "status", "--key", "AP-777").stdout)
                self.assertEqual(st_777["recommended_skill"], "team-dev")
                self.assertEqual(st_777["recommended_mode"], "implement")
                self.assertEqual(MockJiraHandler.stored_issues["AP-777"]["fields"]["description"], cs_desc)

                # Explicit `--standalone --adopt --key AP-777` enriches Qase Defect & attachments while STILL preserving CS description!
                adopt_777 = run_cli(
                    root, "create-bug", "--standalone", "--adopt", "--key", "AP-777",
                    "--attach", str(prod_png),
                )
                self.assertEqual(adopt_777.returncode, 0, adopt_777.stderr)
                ad_out = json.loads(adopt_777.stdout)
                self.assertTrue(ad_out["adopted"])
                self.assertEqual(ad_out["issue_kind"], "standalone_bug")
                self.assertEqual(MockJiraHandler.stored_issues["AP-777"]["fields"]["description"], cs_desc)
                self.assertEqual(len(MockJiraHandler.stored_attachments.get("AP-777") or []), 1)
                self.assertIn(702, MockQaseHandler.defects)
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()
            qase_srv.shutdown()
            qase_srv.server_close()

    def _load_jira_sync_module(self):
        spec = importlib.util.spec_from_file_location("jira_sync_mod", str(JIRA_SYNC))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _assert_valid_adf_invariants(self, node, path="root"):
        """Recursively verify Atlassian Document Format structural invariants:
        - No empty text nodes (Jira rejects {"type": "text", "text": ""})
        - `code` mark is NEVER combined with any other mark (e.g., `strong`)
        - Block nodes with `content` never have an empty content list
        """
        if isinstance(node, list):
            for idx, item in enumerate(node):
                self._assert_valid_adf_invariants(item, f"{path}[{idx}]")
            return
        if not isinstance(node, dict):
            return
        ntype = node.get("type")
        if ntype == "text":
            txt = node.get("text")
            self.assertIsInstance(txt, str, f"Non-string text at {path}")
            self.assertGreater(len(txt), 0, f"Empty text node at {path}: {node}")
            marks = node.get("marks") or []
            mark_types = [m.get("type") for m in marks if isinstance(m, dict)]
            if "code" in mark_types:
                self.assertEqual(
                    mark_types,
                    ["code"],
                    f"Illegal mark combination with `code` at {path}: {node}",
                )
        if "content" in node:
            self.assertIsInstance(node["content"], list, f"Non-list content at {path}")
            self.assertGreater(len(node["content"]), 0, f"Empty content list at {path}: {node}")
            self._assert_valid_adf_invariants(node["content"], f"{path}.{ntype}")

    def test_19_text_to_adf_inline_markdown_and_edge_cases(self):
        mod = self._load_jira_sync_module()
        JiraClient = mod.JiraClient

        # 1. Basic bold & inline code in paragraph
        adf_basic = JiraClient.text_to_adf("Hello **world** and `code`!")
        self._assert_valid_adf_invariants(adf_basic)
        self.assertEqual(
            adf_basic["content"][0]["content"],
            [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world", "marks": [{"type": "strong"}]},
                {"type": "text", "text": " and "},
                {"type": "text", "text": "code", "marks": [{"type": "code"}]},
                {"type": "text", "text": "!"},
            ],
        )

        # 2. Inline code precedence over bold: asterisks inside backticks stay literal inside `code`
        adf_prec = JiraClient.text_to_adf("Check `a**b**c` and `x**y` plus `z**w` and **real bold**")
        self._assert_valid_adf_invariants(adf_prec)
        self.assertEqual(
            adf_prec["content"][0]["content"],
            [
                {"type": "text", "text": "Check "},
                {"type": "text", "text": "a**b**c", "marks": [{"type": "code"}]},
                {"type": "text", "text": " and "},
                {"type": "text", "text": "x**y", "marks": [{"type": "code"}]},
                {"type": "text", "text": " plus "},
                {"type": "text", "text": "z**w", "marks": [{"type": "code"}]},
                {"type": "text", "text": " and "},
                {"type": "text", "text": "real bold", "marks": [{"type": "strong"}]},
            ],
        )

        # 3. Bold wrapping inline code MUST NEVER illegally combine `strong` + `code` marks
        adf_nested = JiraClient.text_to_adf("**prefix `inner` suffix** and **`only_code`**")
        self._assert_valid_adf_invariants(adf_nested)
        self.assertEqual(
            adf_nested["content"][0]["content"],
            [
                {"type": "text", "text": "prefix ", "marks": [{"type": "strong"}]},
                {"type": "text", "text": "inner", "marks": [{"type": "code"}]},
                {"type": "text", "text": " suffix", "marks": [{"type": "strong"}]},
                {"type": "text", "text": " and "},
                {"type": "text", "text": "only_code", "marks": [{"type": "code"}]},
            ],
        )

        # 4. Unclosed delimiters and degenerate empty markers fall back safely to literal text
        for raw, expected_nodes in [
            ("**abc", [{"type": "text", "text": "**abc"}]),
            ("`abc", [{"type": "text", "text": "`abc"}]),
            ("****", [{"type": "text", "text": "****"}]),
            ("``", [{"type": "text", "text": "``"}]),
            ("a **** b `` c", [{"type": "text", "text": "a **** b `` c"}]),
            (
                "**unclosed `code**` tail",
                [
                    {"type": "text", "text": "**unclosed "},
                    {"type": "text", "text": "code**", "marks": [{"type": "code"}]},
                    {"type": "text", "text": " tail"},
                ],
            ),
        ]:
            adf_edge = JiraClient.text_to_adf(raw)
            self._assert_valid_adf_invariants(adf_edge)
            self.assertEqual(adf_edge["content"][0]["content"], expected_nodes, f"Failed for input: {raw!r}")

        # 5. Headings, bulletList items, and tableHeader / tableCell (including empty cell)
        md_doc = "\n".join([
            "## 1. **範圍**與 `jira_sync.py`",
            "- **納入範圍 (In-Scope)：** 支援 `medium` 風險等級",
            "- **站點角色與負責人**：`reviewer` — **Alice** (alice@team.io)",
            "",
            "| **TC ID** | `AC` | Result | Empty |",
            "|---|---|---|---|",
            "| `TC-001` | AC-001 | **pass** | |",
        ])
        adf_doc = JiraClient.text_to_adf(md_doc)
        self._assert_valid_adf_invariants(adf_doc)

        # Heading
        h_node = adf_doc["content"][0]
        self.assertEqual(h_node["type"], "heading")
        self.assertEqual(
            h_node["content"],
            [
                {"type": "text", "text": "1. "},
                {"type": "text", "text": "範圍", "marks": [{"type": "strong"}]},
                {"type": "text", "text": "與 "},
                {"type": "text", "text": "jira_sync.py", "marks": [{"type": "code"}]},
            ],
        )

        # Bullet list
        bl_node = adf_doc["content"][1]
        self.assertEqual(bl_node["type"], "bulletList")
        item0_content = bl_node["content"][0]["content"][0]["content"]
        self.assertEqual(
            item0_content,
            [
                {"type": "text", "text": "納入範圍 (In-Scope)：", "marks": [{"type": "strong"}]},
                {"type": "text", "text": " 支援 "},
                {"type": "text", "text": "medium", "marks": [{"type": "code"}]},
                {"type": "text", "text": " 風險等級"},
            ],
        )

        # Table header and cells
        tbl_node = adf_doc["content"][2]
        self.assertEqual(tbl_node["type"], "table")
        hdr_cells = tbl_node["content"][0]["content"]
        self.assertEqual(hdr_cells[0]["content"][0]["content"], [{"type": "text", "text": "TC ID", "marks": [{"type": "strong"}]}])
        self.assertEqual(hdr_cells[1]["content"][0]["content"], [{"type": "text", "text": "AC", "marks": [{"type": "code"}]}])
        body_cells = tbl_node["content"][1]["content"]
        self.assertEqual(body_cells[0]["content"][0]["content"], [{"type": "text", "text": "TC-001", "marks": [{"type": "code"}]}])
        self.assertEqual(body_cells[2]["content"][0]["content"], [{"type": "text", "text": "pass", "marks": [{"type": "strong"}]}])
        self.assertEqual(body_cells[3]["content"][0]["content"], [{"type": "text", "text": " "}])

        # 6. _adf_to_plain_text and last_synced_description_hash stability
        sample_story = {
            "jira_key": "PROJ-6",
            "title": "Inline Markdown Test",
            "scope_in": "Parse **bold** and `code`",
            "scope_out": "N/A",
            "change_surface": {"has_ui": False, "has_api": False, "has_data": False},
            "risk_level": "medium",
            "acceptance_criteria": [{"id": "AC-001", "description": "渲染 `code` 與 **粗體**"}],
            "design": {"summary": "Use `_parse_inline_markdown` in `text_to_adf`."},
        }
        desc_md = mod.format_story_description(sample_story, {"language": "zh-TW"})
        desc_adf = JiraClient.text_to_adf(desc_md)
        self._assert_valid_adf_invariants(desc_adf)
        plain = mod._adf_to_plain_text(desc_adf).strip()
        self.assertIn("AC-001: 渲染 code 與 粗體", plain)
        self.assertEqual(
            mod._compute_description_hash(plain),
            mod._compute_description_hash(mod._adf_to_plain_text(JiraClient.text_to_adf(desc_md)).strip()),
        )

    def test_20_sod_handoff_governance_fields_and_qa_identity_guardrail(self):
        # Part A: Cross-person handoff (reviewer != qa)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles_cross = {
                "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "tech_lead": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "developer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acc-bob"},
                "reviewer": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "qa": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                "release_manager": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
            }
            self.assertEqual(
                run_cli(root, "init", "--project-key", "SOD", "--roles-json", json.dumps(roles_cross)).returncode,
                0,
            )
            s_out = json.loads(
                run_cli(
                    root, "sync-story", "--key", "SOD-1", "--title", "SoD Test",
                    "--ac", "AC-001:Verify SoD Clean Stop", "--status", "approved",
                ).stdout
            )
            # sync-story handoff (pm=Alice -> developer=Bob, cross-person)
            self.assertFalse(s_out["handoff"]["same_person"])
            self.assertFalse(s_out["handoff"]["can_auto_chain_next_station"])
            self.assertTrue(s_out["handoff"]["requires_user_confirmation_for_next_station"])
            self.assertTrue(s_out["handoff"]["cross_person_clean_stop"])
            self.assertFalse(s_out["handoff"]["next_station_prompt_allowed"])
            self.assertFalse(s_out["can_auto_chain_next_station"])
            self.assertTrue(s_out["cross_person_clean_stop"])
            self.assertFalse(s_out["next_station_prompt_allowed"])

            ct_out = json.loads(
                run_cli(
                    root, "complete-task", "--key", "SOD-1", "--task-id", "TASK-001",
                    "--commit", "aaa1111", "--files", "a.py", "--test-result", "ok",
                ).stdout
            )
            self.assertTrue(ct_out["ready_for_review"])
            self.assertFalse(ct_out["can_auto_chain_next_station"])
            self.assertTrue(ct_out["requires_user_confirmation_for_next_station"])
            self.assertTrue(ct_out["cross_person_clean_stop"])
            self.assertFalse(ct_out["next_station_prompt_allowed"])

            # handoff (reviewer=Alice -> qa=Carol, cross-person -> Clean Stop!)
            h_res = run_cli(root, "handoff", "--key", "SOD-1", "--review-status", "approved", "--commit", "aaa1111")
            self.assertEqual(h_res.returncode, 0, h_res.stderr)
            h_out = json.loads(h_res.stdout)
            for container in (h_out, h_out["handoff"]):
                self.assertFalse(container["same_person"])
                self.assertFalse(container["can_auto_chain_next_station"])
                self.assertTrue(container["requires_user_confirmation_for_next_station"])
                self.assertTrue(container["cross_person_clean_stop"])
                self.assertFalse(container["next_station_prompt_allowed"])
                self.assertEqual(container["next_station_owner"], "Carol")

            # Identity guardrail: Bob (developer) runs record-qa --inspect & status -> mismatch warning!
            bob_env = {"JIRA_EMAIL": "bob@team.io"}
            insp_bob = json.loads(run_cli(root, "record-qa", "--inspect", "--key", "SOD-1", env_override=bob_env).stdout)
            guard_bob = insp_bob["qa_identity_guardrail"]
            self.assertTrue(guard_bob["qa_role_claimed"])
            self.assertFalse(guard_bob["actor_matches_qa"])
            self.assertTrue(guard_bob["identity_mismatch"])
            self.assertTrue(guard_bob["requires_explicit_confirmation"])
            self.assertTrue(guard_bob["actor_is_story_developer"])
            self.assertIn("Carol", guard_bob["warning"])

            st_bob = json.loads(run_cli(root, "status", "--key", "SOD-1", env_override=bob_env).stdout)
            self.assertEqual(st_bob["recommended_skill"], "team-qa")
            self.assertTrue(st_bob["qa_identity_guardrail"]["identity_mismatch"])

            # Identity guardrail: Carol (configured QA) runs record-qa --inspect -> NO mismatch!
            carol_env = {"JIRA_EMAIL": "carol@team.io"}
            insp_carol = json.loads(run_cli(root, "record-qa", "--inspect", "--key", "SOD-1", env_override=carol_env).stdout)
            guard_carol = insp_carol["qa_identity_guardrail"]
            self.assertTrue(guard_carol["qa_role_claimed"])
            self.assertTrue(guard_carol["actor_matches_qa"])
            self.assertFalse(guard_carol["identity_mismatch"])
            self.assertFalse(guard_carol["requires_explicit_confirmation"])
            self.assertEqual(guard_carol["warning"], "")

            # record-qa verify (qa=Carol -> release_manager=Alice, cross-person)
            qa_out = json.loads(
                run_cli(
                    root, "record-qa", "--key", "SOD-1", "--mode", "verify",
                    "--qa-backend", "jira-comment", "--commit", "aaa1111", "--all-pass",
                    env_override=carol_env,
                ).stdout
            )
            self.assertFalse(qa_out["handoff"]["same_person"])
            self.assertFalse(qa_out["handoff"]["can_auto_chain_next_station"])
            self.assertTrue(qa_out["handoff"]["requires_user_confirmation_for_next_station"])
            self.assertTrue(qa_out["handoff"]["cross_person_clean_stop"])
            self.assertFalse(qa_out["handoff"]["next_station_prompt_allowed"])
            self.assertFalse(qa_out["qa_identity_guardrail"]["identity_mismatch"])

            # P1-1 regression: when qa_test_backend is already remembered AND identity_mismatch is True,
            # agent_instruction must NOT say "Proceed using" and MUST instruct STOP.
            insp_bob_after_backend = json.loads(
                run_cli(root, "record-qa", "--inspect", "--key", "SOD-1", env_override=bob_env).stdout
            )
            self.assertTrue(insp_bob_after_backend["qa_backend_configured"])
            self.assertTrue(insp_bob_after_backend["qa_identity_guardrail"]["identity_mismatch"])
            self.assertNotIn("Proceed using", insp_bob_after_backend["agent_instruction"])
            self.assertIn("STOP", insp_bob_after_backend["agent_instruction"])

            # P1-2 regression: stale story.role_owners.qa (Bob) must NOT widen authoritative team-config roles.qa (Carol)
            sod1_path = root / ".ai-workflow" / "active" / "SOD-1.json"
            sod1_data = json.loads(sod1_path.read_text(encoding="utf-8"))
            sod1_data.setdefault("role_owners", {})["qa"] = {
                "name": "Bob",
                "email": "bob@team.io",
                "jira_account_id": "acc-bob",
            }
            sod1_path.write_text(json.dumps(sod1_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            insp_bob_stale_ro = json.loads(
                run_cli(root, "record-qa", "--inspect", "--key", "SOD-1", env_override=bob_env).stdout
            )
            self.assertTrue(insp_bob_stale_ro["qa_identity_guardrail"]["identity_mismatch"])
            self.assertFalse(insp_bob_stale_ro["qa_identity_guardrail"]["actor_matches_qa"])

            # P1-4 regression: completing REV-001 while REV-002 (major, open) remains must report ready_for_review=False
            # without prematurely emitting next-station governance flags; completing REV-002 sets ready_for_review=True.
            findings_two = [
                {"id": "REV-001", "severity": "major", "axis": "code", "location": "a.py", "observation": "Fix 1", "status": "open"},
                {"id": "REV-002", "severity": "major", "axis": "security", "location": "a.py", "observation": "Fix 2", "status": "open"},
            ]
            ho_chg = run_cli(
                root, "handoff", "--key", "SOD-1",
                "--review-status", "changes-requested", "--commit", "aaa1111",
                "--summary", "Two major issues",
                "--findings-json", json.dumps(findings_two),
            )
            self.assertEqual(ho_chg.returncode, 0, ho_chg.stderr)

            ct_rev1 = json.loads(
                run_cli(
                    root, "complete-task", "--key", "SOD-1", "--task-id", "REV-001",
                    "--commit", "aaa2222", "--files", "a.py", "--test-result", "ok",
                ).stdout
            )
            self.assertFalse(ct_rev1["ready_for_review"])
            self.assertEqual(ct_rev1["remaining_review_findings"], ["REV-002"])
            self.assertNotIn("same_person", ct_rev1)
            self.assertNotIn("cross_person_clean_stop", ct_rev1)
            self.assertNotIn("next_station_prompt_allowed", ct_rev1)

            ct_rev2 = json.loads(
                run_cli(
                    root, "complete-task", "--key", "SOD-1", "--task-id", "REV-002",
                    "--commit", "aaa3333", "--files", "a.py", "--test-result", "ok",
                ).stdout
            )
            self.assertTrue(ct_rev2["ready_for_review"])
            self.assertEqual(ct_rev2["remaining_review_findings"], [])
            self.assertFalse(ct_rev2["same_person"])
            self.assertFalse(ct_rev2["can_auto_chain_next_station"])
            self.assertTrue(ct_rev2["requires_user_confirmation_for_next_station"])
            self.assertTrue(ct_rev2["cross_person_clean_stop"])
            self.assertFalse(ct_rev2["next_station_prompt_allowed"])
            self.assertEqual(ct_rev2["next_station_owner"], "Alice")

        # Part B: Same-person / Solo team (reviewer == qa == developer == Alice)
        with tempfile.TemporaryDirectory() as tmp2:
            root2 = Path(tmp2)
            roles_solo = {
                r: {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"}
                for r in ("pm", "tech_lead", "developer", "reviewer", "qa", "release_manager")
            }
            self.assertEqual(
                run_cli(root2, "init", "--project-key", "SOLO", "--roles-json", json.dumps(roles_solo)).returncode,
                0,
            )
            run_cli(
                root2, "sync-story", "--key", "SOLO-1", "--title", "Solo Story",
                "--ac", "AC-001:Solo flow", "--status", "approved",
            )
            run_cli(
                root2, "complete-task", "--key", "SOLO-1", "--task-id", "TASK-001",
                "--commit", "bbb2222", "--files", "b.py", "--test-result", "ok",
            )
            h_solo = json.loads(
                run_cli(root2, "handoff", "--key", "SOLO-1", "--review-status", "approved", "--commit", "bbb2222").stdout
            )
            for container in (h_solo, h_solo["handoff"]):
                self.assertTrue(container["same_person"])
                self.assertFalse(container["can_auto_chain_next_station"])
                self.assertTrue(container["requires_user_confirmation_for_next_station"])
                self.assertFalse(container["cross_person_clean_stop"])
                self.assertTrue(container["next_station_prompt_allowed"])
                self.assertEqual(container["next_station_owner"], "Alice")

            # Solo QA identity guardrail must NOT disrupt Alice!
            alice_env = {"JIRA_EMAIL": "alice@team.io"}
            insp_alice = json.loads(run_cli(root2, "record-qa", "--inspect", "--key", "SOLO-1", env_override=alice_env).stdout)
            guard_alice = insp_alice["qa_identity_guardrail"]
            self.assertTrue(guard_alice["qa_role_claimed"])
            self.assertTrue(guard_alice["actor_matches_qa"])
            self.assertFalse(guard_alice["identity_mismatch"])
            self.assertFalse(guard_alice["requires_explicit_confirmation"])

        # Part C: Unclaimed QA role -> no false identity mismatch
        with tempfile.TemporaryDirectory() as tmp3:
            root3 = Path(tmp3)
            self.assertEqual(
                run_cli(
                    root3, "init", "--project-key", "PART",
                    "--role", "pm,developer", "--name", "Alice", "--jira-email", "alice@team.io",
                ).returncode,
                0,
            )
            insp_unclaimed = json.loads(
                run_cli(root3, "record-qa", "--inspect", env_override={"JIRA_EMAIL": "alice@team.io"}).stdout
            )
            guard_u = insp_unclaimed["qa_identity_guardrail"]
            self.assertFalse(guard_u["qa_role_claimed"])
            self.assertFalse(guard_u["identity_mismatch"])

        # Part D (P1-3 regression): accountId-only roles.qa without email
        with tempfile.TemporaryDirectory() as tmp4:
            root4 = Path(tmp4)
            roles_acct_only_qa = {
                "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "tech_lead": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "developer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acc-bob"},
                "reviewer": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                "qa": {"name": "Carol", "email": "", "jira_account_id": "acc-carol"},
                "release_manager": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
            }
            self.assertEqual(
                run_cli(root4, "init", "--project-key", "ACCT", "--roles-json", json.dumps(roles_acct_only_qa)).returncode,
                0,
            )
            # Carol has email-only local credentials and holds no other configured role -> uncomparable offline
            insp_carol_uncomp = json.loads(
                run_cli(root4, "record-qa", "--inspect", env_override={"JIRA_EMAIL": "carol@team.io"}).stdout
            )
            guard_c_uncomp = insp_carol_uncomp["qa_identity_guardrail"]
            self.assertTrue(guard_c_uncomp["qa_role_claimed"])
            self.assertIsNone(guard_c_uncomp["actor_matches_qa"])
            self.assertFalse(guard_c_uncomp["identity_mismatch"])
            self.assertFalse(guard_c_uncomp["requires_explicit_confirmation"])
            self.assertIsNone(guard_c_uncomp["warning"])

            # Bob matches roles.developer with acc-bob -> comparable via accountId -> confirmed mismatch!
            insp_bob_comp = json.loads(
                run_cli(root4, "record-qa", "--inspect", env_override={"JIRA_EMAIL": "bob@team.io"}).stdout
            )
            guard_b_comp = insp_bob_comp["qa_identity_guardrail"]
            self.assertTrue(guard_b_comp["qa_role_claimed"])
            self.assertFalse(guard_b_comp["actor_matches_qa"])
            self.assertTrue(guard_b_comp["identity_mismatch"])
            self.assertTrue(guard_b_comp["requires_explicit_confirmation"])

    def test_21_governance_docs_no_auto_chain_and_sod_boundaries(self):
        forbidden_re = re.compile(
            r"proactively\s+(?:auto-?)?chain|auto-chaining\s+is\s+authorized|same-person\s+chaining|自動連貫推進|自動銜接|主動銜接",
            re.IGNORECASE,
        )
        files_to_scan = [REPO_ROOT / "README.md"]
        for s in EXPECTED_SKILLS:
            files_to_scan.append(SKILLS_DIR / s / "SKILL.md")
            files_to_scan.extend((SKILLS_DIR / s / "references").glob("*.md"))
            openai_yaml = SKILLS_DIR / s / "agents" / "openai.yaml"
            if openai_yaml.exists():
                files_to_scan.append(openai_yaml)

        for path in files_to_scan:
            text = path.read_text(encoding="utf-8")
            m = forbidden_re.search(text)
            self.assertIsNone(m, f"Forbidden auto-chaining wording found in {path}: {m.group(0) if m else ''}")

        gov_text = (SKILLS_DIR / "team-init" / "references" / "common-workflow-governance.md").read_text(encoding="utf-8")
        self.assertIn("Stop & Yield on Fix", gov_text)
        self.assertIn("Clean Stop", gov_text)
        self.assertIn("can_auto_chain_next_station", gov_text)

        review_md = (SKILLS_DIR / "team-review" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Clean Stop", review_md)
        self.assertIn("Stop & Yield on Fix", review_md)
        self.assertIn("cross_person_clean_stop", review_md)

        dev_md = (SKILLS_DIR / "team-dev" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Stop & Yield on Fix", dev_md)

        qa_md = (SKILLS_DIR / "team-qa" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Entry Identity Guardrail", qa_md)
        self.assertIn("qa_identity_guardrail", qa_md)
        self.assertIn("Stop & Yield on Fix", qa_md)
        self.assertIn("close-story", qa_md)

        release_md = (SKILLS_DIR / "team-release" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("explicit user authorization", release_md.lower())

    def test_22_extract_agents_md_git_commit_pr_standards_and_no_false_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents_md = root / "AGENTS.md"
            agents_md.write_text(
                "# Project Overview\nBuild command: `pytest`\n\n"
                "## Prerequisites\n- Python 3.11+\n\n"
                "## Build Process\n- Run `make build`.\n\n"
                "## Print Helpers\n- Use `logger.info` instead of raw print.\n\n"
                "## Prototype Notes\n- Prototype fixtures live in `fixtures/`.\n\n"
                "## Approval Flow\n- PM approves scope before dev.\n\n"
                "## PR and Commit Notes\n- Use project commit template `<Emoji>  <KEY> <summary>`.\n\n"
                "## PR Guidelines\n- Squash commits before merge.\n\n"
                "## Pull Request Guidelines\n- Keep PR descriptions focused on Why/How/Tests.\n\n"
                "## 提交與分支策略\n- 功能分支命名為 `feat/<key>-<slug>`。\n\n"
                "## Conventional Commits\n- Optional note for libraries.\n",
                encoding="utf-8",
            )
            res = run_cli(root, "init", "--project-key", "GIT")
            self.assertEqual(res.returncode, 0, res.stderr)

            std_text = (root / "docs" / "shared" / "engineering-standards.md").read_text(encoding="utf-8")
            trimmed_agents = agents_md.read_text(encoding="utf-8")

            # Git Commit & PR sections MUST be extracted into engineering-standards.md and removed from AGENTS.md
            for expected in (
                "## PR and Commit Notes",
                "Use project commit template",
                "## PR Guidelines",
                "Squash commits before merge",
                "## Pull Request Guidelines",
                "Keep PR descriptions focused",
                "## 提交與分支策略",
                "功能分支命名",
                "## Conventional Commits",
            ):
                self.assertIn(expected, std_text, f"Expected '{expected}' in engineering-standards.md")
                self.assertNotIn(expected, trimmed_agents, f"Expected '{expected}' to be trimmed from AGENTS.md")

            # Unrelated sections with 'pr' inside words MUST stay in AGENTS.md and NOT be extracted
            for kept in (
                "# Project Overview",
                "## Prerequisites",
                "## Build Process",
                "## Print Helpers",
                "## Prototype Notes",
                "## Approval Flow",
            ):
                self.assertIn(kept, trimmed_agents, f"False match trimmed '{kept}' from AGENTS.md")
                self.assertNotIn(kept, std_text, f"False match extracted '{kept}' into engineering-standards.md")

        # When AGENTS.md has no standards sections, seeded engineering-standards.md MUST include §4 Git Commit & PR Standards
        with tempfile.TemporaryDirectory() as tmp2:
            root2 = Path(tmp2)
            res2 = run_cli(root2, "init", "--project-key", "SEED")
            self.assertEqual(res2.returncode, 0, res2.stderr)
            seeded_std = (root2 / "docs" / "shared" / "engineering-standards.md").read_text(encoding="utf-8")
            self.assertIn("## 4. Git Commit & PR Standards", seeded_std)

    def test_23_create_subtasks_rich_description_and_backward_compatibility(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()

        server = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            jira_url = f"http://127.0.0.1:{port}"
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                roles = {
                    r: {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"}
                    for r in ("pm", "tech_lead", "developer", "reviewer", "qa", "release_manager")
                }
                self.assertEqual(
                    run_cli(
                        root, "init", "--project-key", "SUB", "--jira-url", jira_url,
                        "--jira-email", "alice@team.io", "--jira-token", "tok-1",
                        "--roles-json", json.dumps(roles),
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "sync-story", "--key", "SUB-1", "--title", "Rich Subtask Story",
                        "--ac", "AC-001:First AC", "--ac", "AC-002:Second AC", "--ac", "AC-003:Third AC",
                        "--status", "approved", "--create",
                    ).returncode,
                    0,
                )

                tasks_payload = [
                    {
                        "id": "TASK-001",
                        "title": "Implement exporter core",
                        "description": "Build **UTF-8** CSV streaming helper in `exporter.py`.",
                        "implementation_steps": [
                            "Create `CsvExporter` class with streaming writer",
                            "Add boundary validation for empty datasets",
                        ],
                        "ac_coverage": ["AC-001"],
                        "depends_on": [],
                        "files": ["src/export/exporter.py", "tests/test_exporter.py"],
                        "verification": "pytest tests/test_exporter.py",
                    },
                    {
                        "id": "TASK-002",
                        "title": "Wire export API route",
                        "implementation_steps": "- Add `/api/export` route\n* Enforce authz guard\nReturn 403 on unauthorized",
                        "ac_coverage": ["AC-002"],
                        "depends_on": ["TASK-001"],
                        "files": ["src/api/export.py"],
                        "verification": "pytest tests/test_api_export.py",
                    },
                    {
                        "id": "TASK-003",
                        "title": "Minimal backward-compatible task",
                        "ac_coverage": ["AC-003"],
                        "depends_on": ["TASK-002"],
                        "verification": "pytest tests/test_minimal.py",
                    },
                ]
                sub_res = run_cli(
                    root, "create-subtasks", "--key", "SUB-1",
                    "--tasks-json", json.dumps(tasks_payload, ensure_ascii=False),
                )
                self.assertEqual(sub_res.returncode, 0, sub_res.stderr)
                sub_out = json.loads(sub_res.stdout)
                self.assertEqual(sub_out["commit_policy"], "confirm_before_commit")

                # Verify JSON persistence
                story_data = json.loads((root / ".ai-workflow" / "active" / "SUB-1.json").read_text(encoding="utf-8"))
                t1, t2, t3 = story_data["tasks"]
                self.assertEqual(t1["description"], "Build **UTF-8** CSV streaming helper in `exporter.py`.")
                self.assertEqual(
                    t1["implementation_steps"],
                    ["Create `CsvExporter` class with streaming writer", "Add boundary validation for empty datasets"],
                )
                self.assertEqual(t1["files"], ["src/export/exporter.py", "tests/test_exporter.py"])
                self.assertEqual(
                    t2["implementation_steps"],
                    ["Add `/api/export` route", "Enforce authz guard", "Return 403 on unauthorized"],
                )
                self.assertEqual(t3.get("implementation_steps", []), [])
                self.assertEqual(t3.get("description", ""), "")
                self.assertEqual(t3.get("files", []), [])

                # Verify Jira Sub-task ADF descriptions
                mod = self._load_jira_sync_module()
                adf1 = MockJiraHandler.stored_issues[t1["jira_subtask_key"]]["fields"]["description"]
                adf2 = MockJiraHandler.stored_issues[t2["jira_subtask_key"]]["fields"]["description"]
                adf3 = MockJiraHandler.stored_issues[t3["jira_subtask_key"]]["fields"]["description"]
                for adf in (adf1, adf2, adf3):
                    self._assert_valid_adf_invariants(adf)

                plain1 = mod._adf_to_plain_text(adf1)
                self.assertIn("任務編號：TASK-001", plain1)
                self.assertIn("AC-001", plain1)
                self.assertIn("Build UTF-8 CSV streaming helper in exporter.py.", plain1)
                self.assertIn("Create CsvExporter class with streaming writer", plain1)
                self.assertIn("src/export/exporter.py", plain1)
                self.assertIn("tests/test_exporter.py", plain1)
                self.assertIn("pytest tests/test_exporter.py", plain1)
                # Verify inline code marks exist for target files
                adf1_json = json.dumps(adf1, ensure_ascii=False)
                self.assertIn('"text": "src/export/exporter.py", "marks": [{"type": "code"}]', adf1_json)

                # Verify string implementation_steps did NOT double-bullet
                plain2 = mod._adf_to_plain_text(adf2)
                self.assertIn("Add /api/export route", plain2)
                self.assertNotIn("- - ", plain2)
                self.assertNotIn("- * ", plain2)

                # Backward-compatible minimal task omits empty Implementation Steps / Target Files headings
                plain3 = mod._adf_to_plain_text(adf3)
                self.assertIn("任務編號：TASK-003", plain3)
                self.assertNotIn("Target Files", plain3)
                self.assertNotIn("Implementation Steps", plain3)

                # Verify English rendering via format_subtask_description
                en_md = mod.format_subtask_description(
                    t_id="TASK-009",
                    task_dev={"name": "Alice", "email": "alice@team.io"},
                    ac_cov=["AC-001"],
                    deps=["TASK-001"],
                    verif="pytest tests/test_en.py",
                    zh=False,
                    description="Implement `TokenGuard`.",
                    implementation_steps=["Validate JWT signature", "Return 401 on expiry"],
                    files=["src/auth/guard.py"],
                )
                en_adf = mod.JiraClient.text_to_adf(en_md)
                self._assert_valid_adf_invariants(en_adf)
                en_plain = mod._adf_to_plain_text(en_adf)
                self.assertIn("Implementation Details & Steps", en_plain)
                self.assertIn("Target Files", en_plain)
                self.assertIn("src/auth/guard.py", en_plain)
                self.assertIn("Validate JWT signature", en_plain)
        finally:
            server.shutdown()
            server.server_close()

    def test_24_complete_task_commit_message_warnings_and_commit_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Initialize a real git repository
            subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "dev@team.io"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Dev"], cwd=str(root), check=True, capture_output=True)

            roles = {
                r: {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"}
                for r in ("pm", "tech_lead", "developer", "reviewer", "qa", "release_manager")
            }
            self.assertEqual(
                run_cli(root, "init", "--project-key", "CMT", "--roles-json", json.dumps(roles)).returncode,
                0,
            )

            # Backward-compatibility: strip commit_policy from team-config.json to verify default is confirm_before_commit
            cfg_path = root / ".ai-workflow" / "team-config.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(cfg.get("commit_policy"), "confirm_before_commit")
            self.assertEqual(cfg.get("commit_message_pattern"), "")
            cfg.pop("commit_policy", None)
            cfg.pop("commit_message_pattern", None)
            cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

            self.assertEqual(
                run_cli(
                    root, "sync-story", "--key", "CMT-10", "--title", "Commit Governance",
                    "--ac", "AC-001:First", "--ac", "AC-002:Second", "--ac", "AC-003:Third",
                    "--ac", "AC-004:Fourth", "--status", "approved",
                ).returncode,
                0,
            )
            st_default = json.loads(run_cli(root, "status", "--key", "CMT-10").stdout)
            self.assertEqual(st_default.get("commit_policy"), "confirm_before_commit")
            self.assertTrue(st_default.get("requires_commit_confirmation"))

            tasks = [
                {"id": "TASK-001", "title": "T1", "ac_coverage": ["AC-001"], "verification": "pytest"},
                {"id": "TASK-002", "title": "T2", "ac_coverage": ["AC-002"], "verification": "pytest"},
                {"id": "TASK-003", "title": "T3", "ac_coverage": ["AC-003"], "verification": "pytest"},
                {"id": "TASK-004", "title": "T4", "ac_coverage": ["AC-004"], "verification": "pytest"},
            ]
            sub_out = json.loads(
                run_cli(root, "create-subtasks", "--key", "CMT-10", "--tasks-json", json.dumps(tasks)).stdout
            )
            self.assertEqual(sub_out.get("commit_policy"), "confirm_before_commit")
            sub1 = sub_out["subtasks"][0]["jira_subtask_key"]  # CMT-10-SUB-1
            sub2 = sub_out["subtasks"][1]["jira_subtask_key"]  # CMT-10-SUB-2
            sub3 = sub_out["subtasks"][2]["jira_subtask_key"]  # CMT-10-SUB-3

            # Configure commit_message_pattern & opt-in auto_on_test_pass
            cfg["commit_policy"] = "auto_on_test_pass"
            cfg["commit_message_pattern"] = r"^✨  [A-Z]+-\d+"
            cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

            st_auto = json.loads(run_cli(root, "status", "--key", "CMT-10").stdout)
            self.assertEqual(st_auto.get("commit_policy"), "auto_on_test_pass")
            self.assertFalse(st_auto.get("requires_commit_confirmation"))

            def _git_commit(filename: str, content: str, msg: str) -> str:
                (root / filename).write_text(content, encoding="utf-8")
                subprocess.run(["git", "add", filename], cwd=str(root), check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", msg], cwd=str(root), check=True, capture_output=True)
                return subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"], cwd=str(root), check=True, capture_output=True, text=True
                ).stdout.strip()

            # Case 1: Clean commit matching pattern AND containing both Story & Sub-task keys (case-insensitive check)
            sha1 = _git_commit("f1.py", "x = 1\n", f"✨  CMT-10 新增功能\n\nRefs: {sub1.lower()}")
            c1 = run_cli(
                root, "complete-task", "--key", "CMT-10", "--task-id", "TASK-001",
                "--commit", sha1, "--test-result", "pytest: 1 passed",
            )
            self.assertEqual(c1.returncode, 0, c1.stderr)
            c1_out = json.loads(c1.stdout)
            self.assertEqual(c1_out["commit_policy"], "auto_on_test_pass")
            self.assertEqual(c1_out["commit_message_warnings"], [])

            # Case 2: Commit missing jira_subtask_key -> non-blocking warning!
            sha2 = _git_commit("f2.py", "x = 2\n", "✨  CMT-10 只帶父票號")
            c2 = run_cli(
                root, "complete-task", "--key", "CMT-10", "--task-id", "TASK-002",
                "--commit", sha2, "--test-result", "pytest: 1 passed",
            )
            self.assertEqual(c2.returncode, 0, c2.stderr)
            c2_out = json.loads(c2.stdout)
            self.assertEqual(len(c2_out["commit_message_warnings"]), 1)
            self.assertIn(sub2, c2_out["commit_message_warnings"][0])

            # Case 3: Commit failing pattern AND missing both keys -> non-blocking warnings for all 3!
            sha3 = _git_commit("f3.py", "x = 3\n", "wip fix stuff")
            c3 = run_cli(
                root, "complete-task", "--key", "CMT-10", "--task-id", "TASK-003",
                "--commit", sha3, "--test-result", "pytest: 1 passed",
            )
            self.assertEqual(c3.returncode, 0, c3.stderr)
            c3_out = json.loads(c3.stdout)
            joined_w3 = "\n".join(c3_out["commit_message_warnings"])
            self.assertIn("commit_message_pattern", joined_w3)
            self.assertIn("CMT-10", joined_w3)
            self.assertIn(sub3, joined_w3)

            # Case 4: Invalid regex in team-config.json -> safe config warning, never crashes!
            cfg["commit_message_pattern"] = "[unclosed-regex("
            cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            sha4 = _git_commit("f4.py", "x = 4\n", f"CMT-10 {sub_out['subtasks'][3]['jira_subtask_key']} ok")
            c4 = run_cli(
                root, "complete-task", "--key", "CMT-10", "--task-id", "TASK-004",
                "--commit", sha4, "--test-result", "pytest: 1 passed",
            )
            self.assertEqual(c4.returncode, 0, c4.stderr)
            c4_out = json.loads(c4.stdout)
            self.assertTrue(
                any("invalid" in w.lower() or "regex" in w.lower() for w in c4_out["commit_message_warnings"]),
                f"Expected invalid regex warning, got: {c4_out['commit_message_warnings']}",
            )

        # Case 5: Synthetic SHA in a non-git temp dir -> never crashes, returns empty warnings
        with tempfile.TemporaryDirectory() as tmp_nogit:
            root_ng = Path(tmp_nogit)
            run_cli(root_ng, "init", "--project-key", "NG")
            run_cli(root_ng, "sync-story", "--key", "NG-1", "--title", "NoGit", "--ac", "AC-001:ok", "--status", "approved")
            c_ng = run_cli(
                root_ng, "complete-task", "--key", "NG-1", "--task-id", "TASK-001",
                "--commit", "synthetic999", "--files", "a.py", "--test-result", "ok",
            )
            self.assertEqual(c_ng.returncode, 0, c_ng.stderr)
            c_ng_out = json.loads(c_ng.stdout)
            self.assertEqual(c_ng_out["commit_message_warnings"], [])
            self.assertEqual(c_ng_out["commit_policy"], "confirm_before_commit")

    def test_25_v140_commit_and_subtask_governance_doc_consistency(self):
        # 1. engineering-standards-template.md has §4 Git Commit & PR Standards
        eng_tmpl = (SKILLS_DIR / "team-init" / "references" / "engineering-standards-template.md").read_text(encoding="utf-8")
        self.assertIn("## 4. Git Commit & PR Standards", eng_tmpl)
        self.assertIn("AGENTS.md", eng_tmpl)

        # 2. team-config-template.json has backward-compatible commit_policy & commit_message_pattern
        cfg_tmpl = json.loads((SKILLS_DIR / "team-init" / "references" / "team-config-template.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg_tmpl.get("commit_policy"), "confirm_before_commit")
        self.assertEqual(cfg_tmpl.get("commit_message_pattern"), "")

        # 3. common-workflow-governance.md documents commit_policy gate
        gov_md = (SKILLS_DIR / "team-init" / "references" / "common-workflow-governance.md").read_text(encoding="utf-8")
        self.assertIn("confirm_before_commit", gov_md)
        self.assertIn("auto_on_test_pass", gov_md)

        # 4. team-dev SKILL.md and dev-method.md document confirm_before_commit, implementation_steps, §4 & dual Jira keys
        dev_skill = (SKILLS_DIR / "team-dev" / "SKILL.md").read_text(encoding="utf-8")
        dev_method = (SKILLS_DIR / "team-dev" / "references" / "dev-method.md").read_text(encoding="utf-8")
        for doc_name, text in (("team-dev/SKILL.md", dev_skill), ("team-dev/references/dev-method.md", dev_method)):
            self.assertIn("confirm_before_commit", text, f"{doc_name} missing confirm_before_commit")
            self.assertIn("auto_on_test_pass", text, f"{doc_name} missing auto_on_test_pass")
            self.assertIn("implementation_steps", text, f"{doc_name} missing implementation_steps")
            self.assertIn("<SUBTASK-KEY>", text, f"{doc_name} missing <SUBTASK-KEY>")
            self.assertIn("<STORY-KEY>", text, f"{doc_name} missing <STORY-KEY>")
            self.assertIn("engineering-standards.md", text, f"{doc_name} missing engineering-standards.md")

        # 5. team-review SKILL.md and review-method.md include Axis 3 Commit Message check
        rev_skill = (SKILLS_DIR / "team-review" / "SKILL.md").read_text(encoding="utf-8")
        rev_method = (SKILLS_DIR / "team-review" / "references" / "review-method.md").read_text(encoding="utf-8")
        for doc_name, text in (("team-review/SKILL.md", rev_skill), ("team-review/references/review-method.md", rev_method)):
            self.assertIn("<SUBTASK-KEY>", text, f"{doc_name} missing <SUBTASK-KEY> commit check")
            self.assertIn("commit", text.lower(), f"{doc_name} missing commit message review")

        # 6. Crucial conflict-resolution guard: NEVER hardcode Conventional Commits or team-specific emoji template in generic skills
        hardcoded_format_re = re.compile(
            r"feat\(<scope>\):\s*\[<STORY-KEY>\]\[<SUBTASK-KEY>\]|🐛\s+AL-XXX|✨\s+AL-XXX",
            re.IGNORECASE,
        )
        for path in (
            SKILLS_DIR / "team-init" / "references" / "engineering-standards-template.md",
            SKILLS_DIR / "team-dev" / "SKILL.md",
            SKILLS_DIR / "team-dev" / "references" / "dev-method.md",
            SKILLS_DIR / "team-review" / "SKILL.md",
            SKILLS_DIR / "team-review" / "references" / "review-method.md",
            SKILLS_DIR / "team-init" / "references" / "common-workflow-governance.md",
        ):
            m = hardcoded_format_re.search(path.read_text(encoding="utf-8"))
            self.assertIsNone(m, f"Hardcoded conflicting commit format found in {path}: {m.group(0) if m else ''}")

    def test_26_round1_review_regressions_p1_and_p2(self):
        mod = self._load_jira_sync_module()

        # P1-1: Boundary-aware Jira key matching (no prefix/substring collisions)
        for pos in ("[AL-37]", "(AL-37)", "AL-37:", "AL-37,", "✨ AL-37 fix", "Refs: AL-37", "AL-37新增", "al-37"):
            self.assertTrue(mod._contains_jira_key(pos, "AL-37"), f"Expected match for {pos!r}")
        for neg in ("AL-371", "FOO-AL-37", "REAL-37", "AL-380", "AL-37-SUB-1"):
            self.assertFalse(mod._contains_jira_key(neg, "AL-37"), f"Expected NO match for {neg!r}")
        self.assertFalse(mod._contains_jira_key("AL-380", "AL-38"))
        # _validate_commit_message must flag missing AL-37 when commit only has AL-371 / AL-37-SUB-1
        w_coll = mod._validate_commit_message("✨ AL-371 Refs: AL-37-SUB-1", "AL-37", ["AL-38"], {})
        self.assertTrue(any("AL-37" in w for w in w_coll), f"Expected missing AL-37 warning, got {w_coll}")
        self.assertTrue(any("AL-38" in w for w in w_coll), f"Expected missing AL-38 warning, got {w_coll}")

        # P1-2: Multi-line subject bypass & CRLF normalization & unanchored trailer
        strict_cfg = {"commit_message_pattern": r"^(🐛|✨)\s+[A-Z]+-\d+\s+.+$"}
        w_bypass = mod._validate_commit_message(
            "bad subject AL-37 AL-38\n\n✨ AL-37 valid body line",
            "AL-37",
            ["AL-38"],
            strict_cfg,
        )
        self.assertTrue(
            any("commit_message_pattern" in w for w in w_bypass),
            f"Expected subject-line mismatch warning on multiline bypass, got {w_bypass}",
        )

        crlf_cfg = {"commit_message_pattern": r"^✨ AL-\d+ [a-z]+$"}
        w_crlf = mod._validate_commit_message("✨ AL-37 fix\r\n\r\nRefs: AL-38", "AL-37", ["AL-38"], crlf_cfg)
        self.assertEqual(w_crlf, [], f"Expected clean CRLF pass, got {w_crlf}")

        trailer_cfg = {"commit_message_pattern": r"Signed-off-by:"}
        w_trailer = mod._validate_commit_message(
            "✨ AL-37 AL-38 ok\n\nSigned-off-by: Alice <alice@team.io>",
            "AL-37",
            ["AL-38"],
            trailer_cfg,
        )
        self.assertEqual(w_trailer, [], f"Expected unanchored trailer match to pass, got {w_trailer}")

        # P2-R2-1: Scoped inline multiline/dotall flag (e.g. ^(?s:...)) evaluates against msg_norm
        scoped_cfg = {"commit_message_pattern": r"^(?s:feat:.*Signed-off-by:)"}
        w_scoped = mod._validate_commit_message(
            "feat: AL-37 AL-38 add feature\n\nSigned-off-by: Alice <alice@team.io>",
            "AL-37",
            ["AL-38"],
            scoped_cfg,
        )
        self.assertEqual(w_scoped, [], f"Expected scoped inline (?s:...) multiline match to pass, got {w_scoped}")

        # P1-3: description: null and files: [null, "", "src/a.py"] must never serialize literal "None"
        # P2-3 & P1-R2-1: bare "- "/"* "/"-"/"*" dropped, while "**Step 1**: ..." and "--dry-run flag" preserved intact
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_cli(root, "init", "--project-key", "NULL")
            run_cli(root, "sync-story", "--key", "NULL-1", "--title", "Null Safety", "--ac", "AC-001:ok", "--status", "approved")
            null_tasks = [
                {
                    "id": "TASK-001",
                    "title": "Null fields task",
                    "description": None,
                    "implementation_steps": [
                        "**Step 1**: Create `foo()`",
                        "- **Step 2**: Add tests",
                        "--dry-run flag",
                        "- ",
                        "* ",
                        "-",
                        "*",
                        None,
                    ],
                    "ac_coverage": ["AC-001"],
                    "files": [None, "", "src/a.py"],
                    "verification": "pytest",
                }
            ]
            sub_res = run_cli(root, "create-subtasks", "--key", "NULL-1", "--tasks-json", json.dumps(null_tasks))
            self.assertEqual(sub_res.returncode, 0, sub_res.stderr)
            st = json.loads((root / ".ai-workflow" / "active" / "NULL-1.json").read_text(encoding="utf-8"))
            t = st["tasks"][0]
            self.assertEqual(t["description"], "")
            self.assertEqual(t["files"], ["src/a.py"])
            self.assertEqual(
                t["implementation_steps"],
                ["**Step 1**: Create `foo()`", "**Step 2**: Add tests", "--dry-run flag"],
            )
            rendered_md = mod.format_subtask_description(
                t_id=t["id"],
                task_dev={"name": "Alice", "email": "alice@team.io"},
                ac_cov=t["ac_coverage"],
                deps=[],
                verif=t["verification"],
                zh=True,
                description=t["description"],
                implementation_steps=t["implementation_steps"],
                files=t["files"],
            )
            self.assertIn("- **Step 1**: Create `foo()`", rendered_md)
            self.assertIn("- **Step 2**: Add tests", rendered_md)
            self.assertIn("- --dry-run flag", rendered_md)
            rendered_adf = mod.JiraClient.text_to_adf(rendered_md)
            self._assert_valid_adf_invariants(rendered_adf)
            rendered_adf_json = json.dumps(rendered_adf, ensure_ascii=False)
            self.assertIn('"text": "Step 1", "marks": [{"type": "strong"}]', rendered_adf_json)
            self.assertIn('"text": "Step 2", "marks": [{"type": "strong"}]', rendered_adf_json)
            self.assertIn('"text": "foo()", "marks": [{"type": "code"}]', rendered_adf_json)
            self.assertIn('"text": "--dry-run flag"', rendered_adf_json)

        # P1-4: Non-string commit_message_pattern (123, True, ["foo"]) emits config warning via load_team_config + _validate_commit_message
        with tempfile.TemporaryDirectory() as tmp_cfg:
            root_c = Path(tmp_cfg)
            run_cli(root_c, "init", "--project-key", "TYP")
            cfg_path = root_c / ".ai-workflow" / "team-config.json"
            for bad_pat, expected_type in ((123, "int"), (True, "bool"), (["foo"], "list")):
                cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg_data["commit_message_pattern"] = bad_pat
                cfg_path.write_text(json.dumps(cfg_data, indent=2) + "\n", encoding="utf-8")
                loaded_cfg = mod.load_team_config(root_c)
                self.assertEqual(loaded_cfg["commit_message_pattern"], bad_pat)
                w_bad = mod._validate_commit_message("TYP-1 TYP-2 message", "TYP-1", ["TYP-2"], loaded_cfg)
                self.assertTrue(
                    any("expected string regex" in w and expected_type in w for w in w_bad),
                    f"Expected non-string config warning for {bad_pat!r}, got {w_bad}",
                )

        # P2-1: Dev Fast-Track auto-created sub-task must NOT emit unavoidable missing-subtask warning
        with tempfile.TemporaryDirectory() as tmp_ft:
            root_ft = Path(tmp_ft)
            subprocess.run(["git", "init"], cwd=str(root_ft), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "dev@team.io"], cwd=str(root_ft), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Dev"], cwd=str(root_ft), check=True, capture_output=True)
            run_cli(root_ft, "init", "--project-key", "FT")
            run_cli(root_ft, "sync-story", "--key", "FT-1", "--title", "Fast Track", "--ac", "AC-001:small", "--status", "approved")
            (root_ft / "a.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.py"], cwd=str(root_ft), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "FT-1 implement small story"], cwd=str(root_ft), check=True, capture_output=True)
            ft_res = run_cli(root_ft, "complete-task", "--key", "FT-1", "--task-id", "TASK-001", "--test-result", "pytest: 1 passed")
            self.assertEqual(ft_res.returncode, 0, ft_res.stderr)
            ft_out = json.loads(ft_res.stdout)
            self.assertTrue(ft_out["fast_track"])
            self.assertEqual(ft_out["commit_message_warnings"], [])

        # P2-2: Word-bounded commit matching must NOT falsely extract "## Committee Members"
        with tempfile.TemporaryDirectory() as tmp_cm:
            root_cm = Path(tmp_cm)
            agents_cm = root_cm / "AGENTS.md"
            agents_cm.write_text(
                "# Overview\n\n"
                "## Committee Members\n- Alice, Bob\n\n"
                "## Commit Format\n- Follow team rules.\n",
                encoding="utf-8",
            )
            self.assertEqual(run_cli(root_cm, "init", "--project-key", "CM").returncode, 0)
            std_cm = (root_cm / "docs" / "shared" / "engineering-standards.md").read_text(encoding="utf-8")
            trimmed_cm = agents_cm.read_text(encoding="utf-8")
            self.assertIn("## Committee Members", trimmed_cm)
            self.assertNotIn("## Committee Members", std_cm)
            self.assertIn("## Commit Format", std_cm)
            self.assertNotIn("## Commit Format", trimmed_cm)

        # P2-4: README.md and team-init/SKILL.md doc polish
        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("5-check", readme_text)
        self.assertIn("Git Commit/PR", readme_text)
        init_skill_text = (SKILLS_DIR / "team-init" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--commit-policy", init_skill_text)
        self.assertIn("--commit-message-pattern", init_skill_text)

    def test_27_qa_handoff_gated_by_deploy_and_promote_qa(self):
        mod = self._load_jira_sync_module()

        # 1. Fail-safe config validation: invalid / non-string / unknown qa_handoff_mode falls back to "immediate"
        for bad_mode in (None, "", "bogus", 123, True, ["gated_by_deploy"], {"mode": "gated_by_deploy"}):
            self.assertEqual(mod.get_effective_qa_handoff_mode({"qa_handoff_mode": bad_mode}), "immediate")
        self.assertEqual(mod.get_effective_qa_handoff_mode({"qa_handoff_mode": "GATED_BY_DEPLOY"}), "gated_by_deploy")
        self.assertEqual(mod.get_effective_qa_handoff_mode({"qa_handoff_mode": "immediate"}), "immediate")

        with tempfile.TemporaryDirectory() as tmp_cfg:
            root_c = Path(tmp_cfg)
            self.assertEqual(run_cli(root_c, "init", "--project-key", "GCFG").returncode, 0)
            cfg_p = root_c / ".ai-workflow" / "team-config.json"
            for bad_val in (123, True, ["x"], "unknown_mode", None):
                cfg_d = json.loads(cfg_p.read_text(encoding="utf-8"))
                cfg_d["qa_handoff_mode"] = bad_val
                cfg_p.write_text(json.dumps(cfg_d, indent=2) + "\n", encoding="utf-8")
                loaded = mod.load_team_config(root_c)
                self.assertEqual(loaded["qa_handoff_mode"], "immediate")

        # 2. Live Jira mock server: gated_by_deploy with dev_done, parallel QA plan, Scenario A (CI/CD sync),
        #    blank dev_done safeguard, and Scenario B (promote-qa + unapproved rejection)
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.reject_resolution_transition_keys.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                roles = {
                    "pm": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice", "api_token": "tok-a"},
                    "tech_lead": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob Lin", "email": "bob@team.io", "jira_account_id": "acc-bob", "api_token": "tok-b"},
                    "reviewer": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "qa": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol", "api_token": "tok-c"},
                    "release_manager": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                init_res = run_cli(
                    root, "init",
                    "--project-key", "DEP",
                    "--jira-url", f"http://127.0.0.1:{jira_port}",
                    "--roles-json", json.dumps(roles),
                    "--qa-handoff-mode", "gated_by_deploy",
                    "--status-dev-done", "Dev Completed",
                )
                self.assertEqual(init_res.returncode, 0, init_res.stderr)
                init_out = json.loads(init_res.stdout)
                self.assertEqual(init_out["qa_handoff_mode"], "gated_by_deploy")
                saved_cfg = json.loads((root / ".ai-workflow" / "team-config.json").read_text(encoding="utf-8"))
                self.assertEqual(saved_cfg["qa_handoff_mode"], "gated_by_deploy")
                self.assertEqual(saved_cfg["status_mapping"]["dev_done"], "Dev Completed")

                # Create story, complete task via fast-track
                self.assertEqual(
                    run_cli(root, "sync-story", "--key", "DEP-1", "--title", "Deploy Gated Story", "--ac", "AC-001:Works", "--status", "approved", "--create").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "complete-task", "--key", "DEP-1", "--task-id", "TASK-001", "--commit", "c0ffee1", "--files", "app.py", "--test-result", "pytest ok").returncode,
                    0,
                )

                # promote-qa before review approval MUST fail cleanly
                premature_promote = run_cli(root, "promote-qa", "--key", "DEP-1", "--tag", "qa-v0")
                self.assertNotEqual(premature_promote.returncode, 0)
                self.assertIn("approved", premature_promote.stderr.lower())

                # Clear request log before reviewer -> qa handoff
                MockJiraHandler.requests_log.clear()
                ho = run_cli(
                    root, "handoff",
                    "--key", "DEP-1",
                    "--from-role", "reviewer",
                    "--to-role", "qa",
                    "--review-status", "approved",
                    "--commit", "c0ffee1",
                    "--summary", "All ACs verified; ready for Dev deploy.",
                )
                self.assertEqual(ho.returncode, 0, ho.stderr)
                ho_out = json.loads(ho.stdout)
                self.assertEqual(ho_out["qa_handoff_mode"], "gated_by_deploy")
                self.assertEqual(ho_out["qa_deploy_status"], "awaiting_qa_deploy")
                self.assertFalse(ho_out["qa_notified"])
                self.assertFalse(ho_out["can_auto_chain_next_station"])
                self.assertTrue(ho_out["requires_user_confirmation_for_next_station"])
                self.assertTrue(ho_out["cross_person_clean_stop"])
                self.assertFalse(ho_out["next_station_prompt_allowed"])
                self.assertEqual(ho_out["next_station_owner"], "Carol Wu")

                # Verify Jira Story transitioned to Dev Completed (id=15), NOT In QA, NOT reassigned to Carol, NO @mention
                self.assertEqual(MockJiraHandler.stored_issues["DEP-1"]["fields"]["status"]["name"], "Dev Completed")
                assign_calls = [r for r in MockJiraHandler.requests_log if r[0] == "PUT" and r[1].endswith("/assignee")]
                self.assertEqual(assign_calls, [], f"QA must NOT be assigned during gated_by_deploy handoff: {assign_calls}")
                comment_calls = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(comment_calls), 1)
                comment_adf_str = json.dumps(comment_calls[0][2], ensure_ascii=False)
                self.assertNotIn('"type": "mention"', comment_adf_str)
                self.assertIn("gated_by_deploy", comment_adf_str)

                st_disk = json.loads((root / ".ai-workflow" / "active" / "DEP-1.json").read_text(encoding="utf-8"))
                self.assertEqual(st_disk["candidate_commit"], "c0ffee1")
                self.assertEqual(st_disk["qa_deploy_gate"]["status"], "awaiting_qa_deploy")

                # Status before QA plan: surfaces awaiting_qa_deploy=True AND still permits parallel team-qa (plan)
                st_before_plan = json.loads(run_cli(root, "status", "--key", "DEP-1").stdout)
                self.assertTrue(st_before_plan["awaiting_qa_deploy"])
                self.assertEqual(st_before_plan["recommended_skill"], "team-qa")
                self.assertEqual(st_before_plan["recommended_mode"], "plan")

                # Running record-qa --mode plan during awaiting_qa_deploy must NOT reassign Jira Story to QA
                MockJiraHandler.requests_log.clear()
                plan_res = run_cli(
                    root, "record-qa",
                    "--key", "DEP-1",
                    "--mode", "plan",
                    "--qa-backend", "jira-comment",
                    "--tc", "TC-001:AC-001:automated:Verify works",
                )
                self.assertEqual(plan_res.returncode, 0, plan_res.stderr)
                plan_assign_calls = [r for r in MockJiraHandler.requests_log if r[0] == "PUT" and r[1].endswith("/assignee")]
                self.assertEqual(plan_assign_calls, [])

                # Status after QA plan while still awaiting_qa_deploy: explicitly gates team-qa (verify)
                st_gated = json.loads(run_cli(root, "status", "--key", "DEP-1").stdout)
                self.assertTrue(st_gated["awaiting_qa_deploy"])
                self.assertIsNone(st_gated["recommended_skill"])
                self.assertEqual(st_gated["qa_deploy_status"], "awaiting_qa_deploy")

                # Scenario A: CI/CD pipeline transitions remote Jira Story to "  in qa  " -> status auto-unblocks!
                MockJiraHandler.stored_issues["DEP-1"]["fields"]["status"] = {"name": "  in qa  "}
                st_unblocked = json.loads(run_cli(root, "status", "--key", "DEP-1").stdout)
                self.assertFalse(st_unblocked.get("awaiting_qa_deploy", False))
                self.assertEqual(st_unblocked["recommended_skill"], "team-qa")
                self.assertEqual(st_unblocked["recommended_mode"], "verify")
                st_after_cicd = json.loads((root / ".ai-workflow" / "active" / "DEP-1.json").read_text(encoding="utf-8"))
                self.assertEqual(st_after_cicd["qa_deploy_gate"]["status"], "deployed")
                self.assertEqual(st_after_cicd["qa_deploy_gate"]["source"], "jira_status_sync")

            # 3. Blank dev_done safeguard + Scenario B: CLI promote-qa
            with tempfile.TemporaryDirectory() as tmp2:
                root2 = Path(tmp2)
                roles2 = {
                    "pm": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice", "api_token": "tok-a"},
                    "tech_lead": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob Lin", "email": "bob@team.io", "jira_account_id": "acc-bob", "api_token": "tok-b"},
                    "reviewer": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "qa": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol", "api_token": "tok-c"},
                    "release_manager": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                self.assertEqual(
                    run_cli(
                        root2, "init",
                        "--project-key", "PRM",
                        "--jira-url", f"http://127.0.0.1:{jira_port}",
                        "--roles-json", json.dumps(roles2),
                        "--qa-handoff-mode", "gated_by_deploy",
                        "--status-dev-done", "",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root2, "sync-story", "--key", "PRM-1", "--title", "Blank DevDone Story", "--ac", "AC-001:Ok", "--status", "approved", "--create").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root2, "complete-task", "--key", "PRM-1", "--task-id", "TASK-001", "--commit", "beef123", "--files", "b.py", "--test-result", "pytest ok").returncode,
                    0,
                )
                MockJiraHandler.requests_log.clear()
                ho_blank = run_cli(root2, "handoff", "--key", "PRM-1", "--review-status", "approved", "--commit", "beef123")
                self.assertEqual(ho_blank.returncode, 0, ho_blank.stderr)
                ho_blank_out = json.loads(ho_blank.stdout)
                self.assertIn("warning", ho_blank_out)
                self.assertIn("dev_done", ho_blank_out["warning"])
                # Must NOT transition to In QA prematurely
                trans_calls = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/transitions")]
                self.assertEqual(trans_calls, [])
                self.assertEqual(MockJiraHandler.stored_issues["PRM-1"]["fields"]["status"]["name"], "In Progress")

                # Scenario B: CLI promote-qa transitions to In QA, assigns & @mentions QA, emits SoD flags
                MockJiraHandler.requests_log.clear()
                prom = run_cli(
                    root2, "promote-qa",
                    "--key", "PRM-1",
                    "--tag", "qa-2026-11-23",
                    "--environment", "staging-qa",
                    "--note", "Deployed via manual QA pipeline",
                )
                self.assertEqual(prom.returncode, 0, prom.stderr)
                prom_out = json.loads(prom.stdout)
                self.assertEqual(prom_out["action"], "promote-qa")
                self.assertEqual(prom_out["qa_deploy_status"], "deployed")
                self.assertEqual(prom_out["qa_deploy_gate"]["source"], "cli_promote_qa")
                self.assertEqual(prom_out["qa_deploy_gate"]["tag"], "qa-2026-11-23")
                self.assertEqual(prom_out["qa_deploy_gate"]["environment"], "staging-qa")
                self.assertTrue(prom_out["qa_notified"])
                self.assertFalse(prom_out["can_auto_chain_next_station"])
                self.assertTrue(prom_out["requires_user_confirmation_for_next_station"])
                self.assertTrue(prom_out["cross_person_clean_stop"])
                self.assertFalse(prom_out["next_station_prompt_allowed"])
                self.assertEqual(prom_out["next_station_owner"], "Carol Wu")

                self.assertEqual(MockJiraHandler.stored_issues["PRM-1"]["fields"]["status"]["name"], "In QA")
                self.assertEqual(MockJiraHandler.stored_issues["PRM-1"]["fields"]["assignee"]["accountId"], "acc-carol")
                prom_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(prom_comments), 1)
                prom_adf_str = json.dumps(prom_comments[0][2], ensure_ascii=False)
                self.assertIn('"type": "mention"', prom_adf_str)
                self.assertIn("qa-2026-11-23", prom_adf_str)
                self.assertIn("staging-qa", prom_adf_str)
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_28_bug_triage_resolution_sync_and_controlled_release_gate(self):
        mod = self._load_jira_sync_module()

        # 1. Safe resolution extraction (None, non-dict, dict, str)
        self.assertEqual(mod._extract_jira_resolution({"resolution": None}), "")
        self.assertEqual(mod._extract_jira_resolution({"resolution": 123}), "")
        self.assertEqual(mod._extract_jira_resolution({"resolution": ["bad"]}), "")
        self.assertEqual(mod._extract_jira_resolution({"resolution": {"name": "Won't Fix"}}), "Won't Fix")
        self.assertEqual(mod._extract_jira_resolution({"resolution": "Duplicate"}), "Duplicate")

        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.reject_resolution_transition_keys.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                roles = {
                    "pm": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice", "api_token": "tok-a"},
                    "tech_lead": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob Lin", "email": "bob@team.io", "jira_account_id": "acc-bob", "api_token": "tok-b"},
                    "reviewer": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "qa": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol", "api_token": "tok-c"},
                    "release_manager": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                self.assertEqual(
                    run_cli(root, "init", "--project-key", "TRG", "--jira-url", f"http://127.0.0.1:{jira_port}", "--roles-json", json.dumps(roles), "--provenance-audit-mode", "off").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "sync-story",
                        "--key", "TRG-1",
                        "--title", "Triage Gate Story",
                        "--ac", "AC-001:Core pass",
                        "--ac", "AC-002:Low impact edge",
                        "--ac", "AC-003:Spec aligned behavior",
                        "--status", "approved",
                        "--create",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "complete-task", "--key", "TRG-1", "--task-id", "TASK-001", "--commit", "deadbe1", "--files", "t.py", "--test-result", "pytest ok").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "handoff", "--key", "TRG-1", "--review-status", "approved", "--commit", "deadbe1").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "record-qa",
                        "--key", "TRG-1",
                        "--mode", "plan",
                        "--qa-backend", "jira-comment",
                        "--tc", "TC-001:AC-001:automated:Core passes",
                        "--tc", "TC-002:AC-002:manual:Edge case",
                        "--tc", "TC-003:AC-003:manual:Spec behavior",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(
                        root, "record-qa",
                        "--key", "TRG-1",
                        "--mode", "verify",
                        "--commit", "deadbe1",
                        "--result", "TC-001=pass",
                        "--result", "TC-002=fail:Edge tooltip offset",
                        "--result", "TC-003=fail:Rounded to 2 decimals",
                    ).returncode,
                    0,
                )

                # File 4 bugs on TRG-1 to exercise all 5 triage resolutions + Jira resolution fallback + sync
                b1 = json.loads(run_cli(
                    root, "create-bug", "--key", "TRG-1", "--tc-id", "TC-002", "--ac-id", "AC-002",
                    "--title", "Tooltip offset 2px", "--severity", "minor",
                    "--steps", "Hover", "--expected", "0px", "--actual", "2px", "--commit", "deadbe1",
                ).stdout)["bug_key"]
                b2 = json.loads(run_cli(
                    root, "create-bug", "--key", "TRG-1", "--tc-id", "TC-003", "--ac-id", "AC-003",
                    "--title", "Price rounded to 2 decimals", "--severity", "minor",
                    "--steps", "View price", "--expected", "3 decimals", "--actual", "2 decimals", "--commit", "deadbe1",
                ).stdout)["bug_key"]
                b3 = json.loads(run_cli(
                    root, "create-bug", "--key", "TRG-1", "--tc-id", "TC-002", "--ac-id", "AC-002",
                    "--title", "Duplicate tooltip bug", "--severity", "minor",
                    "--steps", "Hover", "--expected", "0px", "--actual", "2px", "--commit", "deadbe1",
                ).stdout)["bug_key"]
                b4 = json.loads(run_cli(
                    root, "create-bug", "--key", "TRG-1", "--tc-id", "TC-002", "--ac-id", "AC-002",
                    "--title", "Flaky flicker", "--severity", "minor",
                    "--steps", "Refresh", "--expected", "Stable", "--actual", "Flickered once", "--commit", "deadbe1",
                ).stdout)["bug_key"]

                # Validation checks
                self.assertNotEqual(run_cli(root, "triage-bug", "--key", "TRG-1", "--bug-id", b1, "--resolution", "bogus", "--reason", "x", "--decided-by", "PO").returncode, 0)
                self.assertNotEqual(run_cli(root, "triage-bug", "--key", "TRG-1", "--bug-id", b1, "--resolution", "wont-fix", "--reason", "   ", "--decided-by", "PO").returncode, 0)
                self.assertNotEqual(run_cli(root, "triage-bug", "--key", "TRG-1", "--bug-id", b1, "--resolution", "wont-fix", "--reason", "ok", "--decided-by", "   ").returncode, 0)
                self.assertNotEqual(run_cli(root, "triage-bug", "--key", "TRG-1", "--bug-id", b3, "--resolution", "duplicate", "--reason", "dup", "--decided-by", "PO").returncode, 0)
                self.assertNotEqual(run_cli(root, "triage-bug", "--key", "TRG-1", "--resolution", "wont-fix", "--reason", "missing bug-id", "--decided-by", "PO").returncode, 0)
                self.assertNotEqual(run_cli(root, "triage-bug", "--key", "TRG-1", "--bug-id", b1, "--resolution", "wont-fix", "--reason", "bad tc", "--decided-by", "PO", "--waive-tcs", "TC-999").returncode, 0)
                self.assertNotEqual(run_cli(root, "triage-bug", "--key", "TRG-1", "--bug-id", b1, "--resolution", "fixed", "--reason", "needs fix", "--decided-by", "Dev", "--waive-tcs", "TC-002").returncode, 0)

                # Resolution 1: Fixed -> records triage note WITHOUT closing bug prematurely
                tr_fixed = run_cli(
                    root, "triage-bug", "--key", "TRG-1", "--bug-id", b1,
                    "--resolution", "Fixed", "--reason", "Initial check thought fix needed", "--decided-by", "Bob Lin",
                )
                self.assertEqual(tr_fixed.returncode, 0, tr_fixed.stderr)
                self.assertEqual(json.loads(tr_fixed.stdout)["bug_status"], "open")

                # Enable Jira resolution-rejection fallback on b1 to verify graceful retry!
                MockJiraHandler.reject_resolution_transition_keys.add(b1)

                # Resolution 2: Won't Fix (display alias) + waive TC-002 (NEVER sets pass!)
                tr_wf = run_cli(
                    root, "triage-bug", "--key", "TRG-1", "--bug-id", b1,
                    "--resolution", "Won't Fix",
                    "--reason", "Cosmetic 2px offset accepted for v1.4",
                    "--decided-by", "PO Alice",
                    "--waive-tcs", "TC-002",
                )
                self.assertEqual(tr_wf.returncode, 0, tr_wf.stderr)
                wf_out = json.loads(tr_wf.stdout)
                self.assertEqual(wf_out["resolution"], "wont-fix")
                self.assertEqual(wf_out["bug_status"], "closed")
                self.assertTrue(wf_out["transition"]["resolution_fallback"])
                self.assertEqual(MockJiraHandler.stored_issues[b1]["fields"]["status"]["name"], "Done")

                # Resolution 3: Not a Bug + TC-003 marked not-applicable
                tr_nab = run_cli(
                    root, "triage-bug", "--key", "TRG-1", "--bug-id", b2,
                    "--resolution", "Not a Bug",
                    "--reason", "Matches AC-003 2-decimal pricing spec",
                    "--decided-by", "PO Alice",
                    "--waive-tcs", "TC-003",
                    "--tc-disposition", "not-applicable",
                )
                self.assertEqual(tr_nab.returncode, 0, tr_nab.stderr)
                nab_out = json.loads(tr_nab.stdout)
                self.assertEqual(nab_out["resolution"], "not-a-bug")
                self.assertEqual(nab_out["waived_tcs"][0]["result"], "not-applicable")
                self.assertEqual(MockJiraHandler.stored_issues[b2]["fields"]["resolution"]["name"], "Not a Bug")

                # Resolution 4: Duplicate (requires --duplicate-of)
                tr_dup = run_cli(
                    root, "triage-bug", "--key", "TRG-1", "--bug-id", b3,
                    "--resolution", "Duplicate",
                    "--reason", "Same root issue as TRG-50",
                    "--decided-by", "Carol Wu",
                    "--duplicate-of", "TRG-50",
                )
                self.assertEqual(tr_dup.returncode, 0, tr_dup.stderr)
                self.assertEqual(json.loads(tr_dup.stdout)["duplicate_of"], "TRG-50")

                # While b4 is STILL OPEN, close-story MUST be blocked!
                blocked_open = run_cli(root, "close-story", "--key", "TRG-1", "--commit", "deadbe1")
                self.assertNotEqual(blocked_open.returncode, 0)
                self.assertIn(b4, blocked_open.stderr)

                # Resolution 5: Cannot Reproduce closes b4
                tr_cr = run_cli(
                    root, "triage-bug", "--key", "TRG-1", "--bug-id", b4,
                    "--resolution", "Cannot Reproduce",
                    "--reason", "Unable to reproduce after 5 attempts on staging",
                    "--decided-by", "Carol Wu",
                )
                self.assertEqual(tr_cr.returncode, 0, tr_cr.stderr)

                # Test remote resolution sync in reconcile_story_with_jira (including None / non-dict safety)
                MockJiraHandler.stored_issues["TRG-888"] = {
                    "key": "TRG-888",
                    "fields": {
                        "summary": "Remote triaged bug",
                        "issuetype": {"name": "Bug", "subtask": False},
                        "status": {"name": "Done"},
                        "resolution": {"name": "Won't Fix"},
                    },
                }
                MockJiraHandler.stored_issues["TRG-1"]["fields"].setdefault("issuelinks", []).append(
                    {"type": {"name": "Blocks"}, "inwardIssue": MockJiraHandler.stored_issues["TRG-888"]}
                )
                pull_res = run_cli(root, "pull", "--key", "TRG-1")
                self.assertEqual(pull_res.returncode, 0, pull_res.stderr)
                st_synced = json.loads((root / ".ai-workflow" / "active" / "TRG-1.json").read_text(encoding="utf-8"))
                synced_b888 = next(b for b in st_synced["bugs"] if b["bug_key"] == "TRG-888")
                self.assertEqual(synced_b888["jira_resolution"], "Won't Fix")
                self.assertEqual(synced_b888["jira_status"], "Done")

                # Verify TC-002 is 'waived' (NOT 'pass') and TC-003 is 'not-applicable' (NOT 'pass')
                tcs_by_id = {c["id"]: c for c in st_synced["qa"]["test_cases"]}
                self.assertEqual(tcs_by_id["TC-002"]["result"], "waived")
                self.assertEqual(tcs_by_id["TC-002"]["waived_bug_id"], b1)
                self.assertEqual(tcs_by_id["TC-003"]["result"], "not-applicable")
                self.assertEqual(tcs_by_id["TC-003"]["waived_bug_id"], b2)

                # Tamper test: forged 'waived' TC missing audit metadata MUST STILL BLOCK close-story!
                forged_story = json.loads(json.dumps(st_synced))
                forged_story["qa"]["test_cases"].append({
                    "id": "TC-004",
                    "ac_id": "AC-001",
                    "title": "Forged waiver",
                    "execution_type": "manual",
                    "result": "waived",
                })
                (root / ".ai-workflow" / "active" / "TRG-1.json").write_text(json.dumps(forged_story, indent=2), encoding="utf-8")
                MockJiraHandler.stored_properties["TRG-1"] = forged_story
                forged_close = run_cli(root, "close-story", "--key", "TRG-1", "--commit", "deadbe1")
                self.assertNotEqual(forged_close.returncode, 0)
                self.assertIn("TC-004", forged_close.stderr)

                # Restore valid audited story -> close-story succeeds WITH Bug Triage & Risk Acceptance Disclosure!
                st_synced["updated_at"] = "2099-01-01T00:00:00+00:00"
                (root / ".ai-workflow" / "active" / "TRG-1.json").write_text(json.dumps(st_synced, indent=2), encoding="utf-8")
                MockJiraHandler.stored_properties["TRG-1"] = st_synced
                ok_close = run_cli(root, "close-story", "--key", "TRG-1", "--commit", "deadbe1", "--version", "v1.4.0")
                self.assertEqual(ok_close.returncode, 0, ok_close.stderr)
                close_out = json.loads(ok_close.stdout)
                self.assertTrue(close_out["triage_disclosure"]["has_risk_acceptance"])
                self.assertEqual(len(close_out["triage_disclosure"]["waived_tcs"]), 2)
                self.assertIn("Bug Triage & Risk Acceptance Disclosure", close_out["release_summary"])
                self.assertIn("PO Alice", close_out["release_summary"])
                self.assertIn("Cosmetic 2px offset accepted for v1.4", close_out["release_summary"])
                self.assertIn("TC-002", close_out["release_summary"])
                self.assertIn("TC-003", close_out["release_summary"])

                # Standalone --adopt Bug direct triage closure without forcing team-dev implement!
                MockJiraHandler.stored_issues["TRG-555"] = {
                    "key": "TRG-555",
                    "fields": {
                        "summary": "CS reported timezone display",
                        "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Shows UTC+8"}]}]},
                        "issuetype": {"name": "Bug", "subtask": False},
                        "status": {"name": "To Do"},
                    },
                }
                adopt_res = run_cli(root, "create-bug", "--standalone", "--adopt", "--key", "TRG-555")
                self.assertEqual(adopt_res.returncode, 0, adopt_res.stderr)
                tr_stand = run_cli(
                    root, "triage-bug",
                    "--key", "TRG-555",
                    "--resolution", "Not a Bug",
                    "--reason", "Intentional UTC+8 display per product spec",
                    "--decided-by", "PO Alice",
                )
                self.assertEqual(tr_stand.returncode, 0, tr_stand.stderr)
                tr_stand_out = json.loads(tr_stand.stdout)
                self.assertTrue(tr_stand_out["triage_closed"])
                self.assertEqual(tr_stand_out["bug_status"], "closed")
                self.assertEqual(MockJiraHandler.stored_issues["TRG-555"]["fields"]["status"]["name"], "Done")
                # Status recognizes closure cleanly without forcing team-dev implement!
                st_stand = json.loads(run_cli(root, "status", "--key", "TRG-555").stdout)
                self.assertTrue(st_stand["triage_closed"])
                self.assertIsNone(st_stand["recommended_skill"])
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_29_bug_fix_root_cause_solution_backfill_and_warnings(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                roles = {
                    "pm": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice", "api_token": "tok-a"},
                    "tech_lead": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob Lin", "email": "bob@team.io", "jira_account_id": "acc-bob", "api_token": "tok-b"},
                    "reviewer": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "qa": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol", "api_token": "tok-c"},
                    "release_manager": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                self.assertEqual(
                    run_cli(root, "init", "--project-key", "RC", "--jira-url", f"http://127.0.0.1:{jira_port}", "--roles-json", json.dumps(roles)).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "sync-story", "--key", "RC-1", "--title", "Avatar Upload", "--ac", "AC-001:Upload 512px", "--status", "approved", "--create").returncode,
                    0,
                )

                # 1. Normal Story TASK-* without --root-cause / --solution -> bug_fix_warnings MUST be []
                comp_task = run_cli(
                    root, "complete-task",
                    "--key", "RC-1",
                    "--task-id", "TASK-001",
                    "--commit", "aaa111",
                    "--files", "avatar.ts",
                    "--test-result", "vitest 4 passed",
                )
                self.assertEqual(comp_task.returncode, 0, comp_task.stderr)
                comp_task_out = json.loads(comp_task.stdout)
                self.assertEqual(comp_task_out["bug_fix_warnings"], [])
                self.assertEqual(comp_task_out["root_cause"], "")
                self.assertEqual(comp_task_out["solution"], "")

                # File two Story bugs on RC-1
                bug_a = json.loads(run_cli(
                    root, "create-bug", "--key", "RC-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "Avatar cropped to 320px", "--severity", "major",
                    "--steps", "Upload 512px", "--expected", "512px", "--actual", "320px", "--commit", "aaa111",
                ).stdout)["bug_key"]
                bug_b = json.loads(run_cli(
                    root, "create-bug", "--key", "RC-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "Missing retry on upload", "--severity", "minor",
                    "--steps", "Drop net", "--expected", "Retry", "--actual", "No retry", "--commit", "aaa111",
                ).stdout)["bug_key"]

                # 2. Complete Story bug_a WITH --root-cause and --solution -> clean JSON, ADF comment, bug_fix_warnings=[]
                MockJiraHandler.requests_log.clear()
                comp_bug_a = run_cli(
                    root, "complete-task",
                    "--key", "RC-1",
                    "--task-id", bug_a,
                    "--commit", "bbb222",
                    "--files", "avatar.ts",
                    "--test-result", "vitest 6 passed",
                    "--root-cause", "Desktop uploader hardcoded `size: 320`, causing secondary crop.",
                    "--solution", "Added `resolveAvatarUploadSize()` to preserve 512px crop bounds.",
                )
                self.assertEqual(comp_bug_a.returncode, 0, comp_bug_a.stderr)
                comp_bug_a_out = json.loads(comp_bug_a.stdout)
                self.assertEqual(comp_bug_a_out["bug_fix_warnings"], [])
                self.assertIn("size: 320", comp_bug_a_out["root_cause"])
                self.assertIn("resolveAvatarUploadSize", comp_bug_a_out["solution"])

                st_rc1 = json.loads((root / ".ai-workflow" / "active" / "RC-1.json").read_text(encoding="utf-8"))
                bug_a_rec = next(b for b in st_rc1["bugs"] if b["bug_key"] == bug_a)
                self.assertIn("size: 320", bug_a_rec["root_cause"])
                self.assertIn("resolveAvatarUploadSize", bug_a_rec["solution"])

                bug_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1] == f"/rest/api/3/issue/{bug_a}/comment"]
                self.assertEqual(len(bug_comments), 1)
                self._assert_valid_adf_invariants(bug_comments[0][2]["body"])
                bug_adf_str = json.dumps(bug_comments[0][2]["body"], ensure_ascii=False)
                self.assertIn("問題原因 (Root Cause)", bug_adf_str)
                self.assertIn("解決辦法 (Solution)", bug_adf_str)
                self.assertIn('"text": "size: 320", "marks": [{"type": "code"}]', bug_adf_str)

                # 3. Complete Story bug_b WITHOUT --root-cause / --solution -> non-blocking warning, exit 0, never "None"
                comp_bug_b = run_cli(
                    root, "complete-task",
                    "--key", "RC-1",
                    "--task-id", bug_b,
                    "--commit", "ccc333",
                    "--files", "retry.ts",
                    "--test-result", "vitest 7 passed",
                )
                self.assertEqual(comp_bug_b.returncode, 0, comp_bug_b.stderr)
                comp_bug_b_out = json.loads(comp_bug_b.stdout)
                self.assertEqual(len(comp_bug_b_out["bug_fix_warnings"]), 2)
                self.assertTrue(any("--root-cause" in w for w in comp_bug_b_out["bug_fix_warnings"]))
                self.assertTrue(any("--solution" in w for w in comp_bug_b_out["bug_fix_warnings"]))

                # 4. Standalone Bug with --root-cause & --solution persists on task, bug_report, and bug_fix_summary;
                #    original Jira Description is NEVER modified (no --update-template)
                sb_out = json.loads(run_cli(
                    root, "create-bug", "--standalone",
                    "--title", "Prod avatar crop", "--severity", "major",
                    "--steps", "Upload", "--expected", "512", "--actual", "320",
                ).stdout)
                sb_key = sb_out["bug_key"]
                orig_sb_desc = json.dumps(MockJiraHandler.stored_issues[sb_key]["fields"]["description"])

                MockJiraHandler.requests_log.clear()
                comp_sb = run_cli(
                    root, "complete-task",
                    "--key", sb_key,
                    "--commit", "ddd444",
                    "--files", "avatar.ts",
                    "--test-result", "vitest 8 passed",
                    "--root-cause", "Hardcoded 320px constant",
                    "--solution", "Dynamic crop resolution",
                )
                self.assertEqual(comp_sb.returncode, 0, comp_sb.stderr)
                comp_sb_out = json.loads(comp_sb.stdout)
                self.assertEqual(comp_sb_out["bug_fix_warnings"], [])
                sb_json = json.loads((root / ".ai-workflow" / "active" / f"{sb_key}.json").read_text(encoding="utf-8"))
                self.assertEqual(sb_json["tasks"][0]["root_cause"], "Hardcoded 320px constant")
                self.assertEqual(sb_json["tasks"][0]["solution"], "Dynamic crop resolution")
                self.assertEqual(sb_json["bug_report"]["root_cause"], "Hardcoded 320px constant")
                self.assertEqual(sb_json["bug_report"]["solution"], "Dynamic crop resolution")
                self.assertEqual(sb_json["bug_fix_summary"]["root_cause"], "Hardcoded 320px constant")
                self.assertEqual(sb_json["bug_fix_summary"]["solution"], "Dynamic crop resolution")
                # Description untouched (no PUT /rest/api/3/issue/{sb_key})
                put_issue_calls = [
                    r for r in MockJiraHandler.requests_log
                    if r[0] == "PUT" and r[1] == f"/rest/api/3/issue/{sb_key}"
                ]
                self.assertEqual(put_issue_calls, [])
                self.assertEqual(json.dumps(MockJiraHandler.stored_issues[sb_key]["fields"]["description"]), orig_sb_desc)

                # Confirm --update-template is explicitly NOT supported
                bad_flag = run_cli(root, "complete-task", "--key", sb_key, "--test-result", "ok", "--update-template")
                self.assertNotEqual(bad_flag.returncode, 0)
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_30_v140b_jira_bug_lifecycle_doc_consistency(self):
        # 1. team-config-template.json has qa_handoff_mode & status_mapping.dev_done
        cfg_tmpl = json.loads((SKILLS_DIR / "team-init" / "references" / "team-config-template.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg_tmpl.get("qa_handoff_mode"), "immediate")
        self.assertEqual((cfg_tmpl.get("status_mapping") or {}).get("dev_done"), "")

        # 2. #4 docs: team-init, team-review, team-qa, team-next-step, common-workflow-governance
        for rel_path in (
            "team-init/SKILL.md",
            "team-review/SKILL.md",
            "team-qa/SKILL.md",
            "team-next-step/SKILL.md",
            "team-init/references/common-workflow-governance.md",
        ):
            txt = (SKILLS_DIR / rel_path).read_text(encoding="utf-8")
            self.assertIn("gated_by_deploy", txt, f"{rel_path} missing gated_by_deploy")
        self.assertIn("promote-qa", (SKILLS_DIR / "team-review" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("awaiting_qa_deploy", (SKILLS_DIR / "team-next-step" / "SKILL.md").read_text(encoding="utf-8"))

        # 3. #11 docs: Mode 4 Bug Triage + HITL Red Flag + Release Disclosure
        qa_skill = (SKILLS_DIR / "team-qa" / "SKILL.md").read_text(encoding="utf-8")
        qa_method = (SKILLS_DIR / "team-qa" / "references" / "qa-method.md").read_text(encoding="utf-8")
        rel_skill = (SKILLS_DIR / "team-release" / "SKILL.md").read_text(encoding="utf-8")
        gov_md = (SKILLS_DIR / "team-init" / "references" / "common-workflow-governance.md").read_text(encoding="utf-8")
        for name, txt in (("team-qa/SKILL.md", qa_skill), ("team-qa/references/qa-method.md", qa_method)):
            self.assertIn("Mode 4", txt, f"{name} missing Mode 4 Bug Triage")
            self.assertIn("triage-bug", txt, f"{name} missing triage-bug")
            self.assertIn("--decided-by", txt, f"{name} missing --decided-by HITL gate")
            self.assertIn("not-applicable", txt, f"{name} missing not-applicable")
            self.assertIn("waived", txt, f"{name} missing waived")
        self.assertIn("Bug Triage & Risk Acceptance Disclosure", rel_skill)
        self.assertIn("triage-bug", gov_md)
        self.assertIn("--decided-by", gov_md)

        # 4. #12 docs: team-dev SKILL.md & dev-method.md
        for rel_path in ("team-dev/SKILL.md", "team-dev/references/dev-method.md"):
            txt = (SKILLS_DIR / rel_path).read_text(encoding="utf-8")
            self.assertIn("--root-cause", txt, f"{rel_path} missing --root-cause")
            self.assertIn("--solution", txt, f"{rel_path} missing --solution")
            self.assertIn("bug_fix_warnings", txt, f"{rel_path} missing bug_fix_warnings")

    def test_31_round1_review_regressions_p1_and_p2(self):
        mod = self._load_jira_sync_module()

        # P1-4d: load_team_config sanitizes every status_mapping entry (non-string or blank required keys -> defaults)
        with tempfile.TemporaryDirectory() as tmp_sm:
            root_sm = Path(tmp_sm)
            self.assertEqual(run_cli(root_sm, "init", "--project-key", "SM").returncode, 0)
            cfg_p = root_sm / ".ai-workflow" / "team-config.json"
            for bad_in_qa in (123, None, "", "   ", True, ["In QA"]):
                cfg_d = json.loads(cfg_p.read_text(encoding="utf-8"))
                cfg_d["status_mapping"]["in_qa"] = bad_in_qa
                cfg_d["status_mapping"]["done"] = None
                cfg_d["status_mapping"]["dev_done"] = "   "
                cfg_p.write_text(json.dumps(cfg_d, indent=2) + "\n", encoding="utf-8")
                loaded = mod.load_team_config(root_sm)
                self.assertEqual(loaded["status_mapping"]["in_qa"], "In QA")
                self.assertEqual(loaded["status_mapping"]["done"], "Done")
                self.assertEqual(loaded["status_mapping"]["dev_done"], "")

        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.reject_resolution_transition_keys.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            # P1-1 (Probe 1.1) + P1-2 (blank & unmapped dev_done while Jira at In QA) + P1-3 (verify CLI gate) + P1-4 + P2-4
            with tempfile.TemporaryDirectory() as tmp1:
                root = Path(tmp1)
                # Notice P2-4 setup: reviewer == qa (Carol), developer == Bob (distinct!)
                roles = {
                    "pm": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice", "api_token": "tok-a"},
                    "tech_lead": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob Lin", "email": "bob@team.io", "jira_account_id": "acc-bob", "api_token": "tok-b"},
                    "reviewer": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol", "api_token": "tok-c"},
                    "qa": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                    "release_manager": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                self.assertEqual(
                    run_cli(
                        root, "init",
                        "--project-key", "R1",
                        "--jira-url", f"http://127.0.0.1:{jira_port}",
                        "--roles-json", json.dumps(roles),
                        "--qa-handoff-mode", "gated_by_deploy",
                        "--status-dev-done", "Dev Completed",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "sync-story", "--key", "R1-1", "--title", "Re-deploy Gate", "--ac", "AC-001:Works", "--status", "approved", "--create").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "complete-task", "--key", "R1-1", "--task-id", "TASK-001", "--commit", "commit1", "--files", "a.py", "--test-result", "ok").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "handoff", "--key", "R1-1", "--review-status", "approved", "--commit", "commit1").returncode,
                    0,
                )
                # P1-3: plan allowed, verify rejected while awaiting_qa_deploy
                self.assertEqual(
                    run_cli(root, "record-qa", "--key", "R1-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Check").returncode,
                    0,
                )
                blocked_verify = run_cli(root, "record-qa", "--key", "R1-1", "--mode", "verify", "--commit", "commit1", "--all-pass")
                self.assertNotEqual(blocked_verify.returncode, 0)
                self.assertIn("gated", blocked_verify.stderr.lower())

                # P2-4: promote-qa defaults --from-role developer (Bob) -> qa (Carol) so qa_notified=True even though reviewer==qa!
                MockJiraHandler.requests_log.clear()
                prom1 = run_cli(root, "promote-qa", "--key", "R1-1", "--tag", "qa-c1")
                self.assertEqual(prom1.returncode, 0, prom1.stderr)
                prom1_out = json.loads(prom1.stdout)
                self.assertFalse(prom1_out["already_deployed"])
                self.assertTrue(prom1_out["qa_notified"])
                self.assertFalse(prom1_out["same_person"])
                self.assertTrue(prom1_out["cross_person_clean_stop"])
                self.assertEqual(prom1_out["qa_deploy_gate"]["commit"], "commit1")

                # P1-4b: Duplicate promote-qa on commit1 is idempotent no-op with ZERO Jira comments/transitions
                MockJiraHandler.requests_log.clear()
                prom_dup = run_cli(root, "promote-qa", "--key", "R1-1", "--tag", "qa-c1-dup")
                self.assertEqual(prom_dup.returncode, 0, prom_dup.stderr)
                prom_dup_out = json.loads(prom_dup.stdout)
                self.assertTrue(prom_dup_out["ok"])
                self.assertTrue(prom_dup_out["already_deployed"])
                self.assertFalse(prom_dup_out["qa_notified"])
                self.assertFalse(prom_dup_out["can_auto_chain_next_station"])
                self.assertTrue(prom_dup_out["requires_user_confirmation_for_next_station"])
                dup_mutations = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and (r[1].endswith("/comment") or r[1].endswith("/transitions"))]
                self.assertEqual(dup_mutations, [])

                # P1-3: verify now succeeds on commit1 -> qa.status == "passed", qa.tested_commit == "commit1"
                ver1 = run_cli(root, "record-qa", "--key", "R1-1", "--mode", "verify", "--commit", "commit1", "--all-pass")
                self.assertEqual(ver1.returncode, 0, ver1.stderr)

                # Probe 1.1 (P1-1): commit2 fix & re-approve -> status MUST report awaiting_qa_deploy=True & recommended_skill=None!
                self.assertEqual(
                    run_cli(root, "complete-task", "--key", "R1-1", "--task-id", "TASK-001", "--commit", "commit2", "--files", "a.py", "--test-result", "ok2").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "handoff", "--key", "R1-1", "--review-status", "approved", "--commit", "commit2").returncode,
                    0,
                )
                st_c2 = json.loads(run_cli(root, "status", "--key", "R1-1").stdout)
                self.assertTrue(st_c2["awaiting_qa_deploy"])
                self.assertIsNone(st_c2["recommended_skill"])
                # Verify on commit2 is gated until commit2 is deployed
                self.assertNotEqual(
                    run_cli(root, "record-qa", "--key", "R1-1", "--mode", "verify", "--commit", "commit2", "--all-pass").returncode,
                    0,
                )

                # P1-2a: Blank dev_done when Jira remained at "In QA" from commit1 -> MUST NOT auto-unblock on stale "In QA"!
                cfg_p = root / ".ai-workflow" / "team-config.json"
                cfg_d = json.loads(cfg_p.read_text(encoding="utf-8"))
                cfg_d["status_mapping"]["dev_done"] = ""
                cfg_p.write_text(json.dumps(cfg_d, indent=2) + "\n", encoding="utf-8")
                MockJiraHandler.stored_issues["R1-1"]["fields"]["status"] = {"name": "In QA"}
                self.assertEqual(
                    run_cli(root, "complete-task", "--key", "R1-1", "--task-id", "TASK-001", "--commit", "commit3", "--files", "a.py", "--test-result", "ok3").returncode,
                    0,
                )
                ho_c3 = run_cli(root, "handoff", "--key", "R1-1", "--review-status", "approved", "--commit", "commit3")
                self.assertEqual(ho_c3.returncode, 0, ho_c3.stderr)
                st_c3_disk = json.loads((root / ".ai-workflow" / "active" / "R1-1.json").read_text(encoding="utf-8"))
                self.assertTrue(st_c3_disk["qa_deploy_gate"]["awaiting_in_qa_cycle"])

                # Status while Jira is STILL at stale "In QA" MUST stay gated!
                st_c3_stale = json.loads(run_cli(root, "status", "--key", "R1-1").stdout)
                self.assertTrue(st_c3_stale["awaiting_qa_deploy"])
                self.assertIsNone(st_c3_stale["recommended_skill"])

                # Once Jira leaves "In QA" -> clears awaiting_in_qa_cycle; subsequent "In QA" -> auto-unblocks!
                MockJiraHandler.stored_issues["R1-1"]["fields"]["status"] = {"name": "In Progress"}
                st_c3_left = json.loads(run_cli(root, "status", "--key", "R1-1").stdout)
                self.assertTrue(st_c3_left["awaiting_qa_deploy"])
                st_c3_left_disk = json.loads((root / ".ai-workflow" / "active" / "R1-1.json").read_text(encoding="utf-8"))
                self.assertFalse(st_c3_left_disk["qa_deploy_gate"]["awaiting_in_qa_cycle"])

                MockJiraHandler.stored_issues["R1-1"]["fields"]["status"] = {"name": "In QA"}
                st_c3_reentered = json.loads(run_cli(root, "status", "--key", "R1-1").stdout)
                self.assertFalse(st_c3_reentered.get("awaiting_qa_deploy", False))
                self.assertEqual(st_c3_reentered["recommended_skill"], "team-qa")
                self.assertEqual(st_c3_reentered["recommended_mode"], "verify")

                # P1-2b: Unmapped/failed dev_done transition when Jira remained at "In QA" -> preserves "In QA", warns, stays gated!
                cfg_d["status_mapping"]["dev_done"] = "Nonexistent Dev Done"
                cfg_p.write_text(json.dumps(cfg_d, indent=2) + "\n", encoding="utf-8")
                self.assertEqual(
                    run_cli(root, "complete-task", "--key", "R1-1", "--task-id", "TASK-001", "--commit", "commit4", "--files", "a.py", "--test-result", "ok4").returncode,
                    0,
                )
                ho_c4 = run_cli(root, "handoff", "--key", "R1-1", "--review-status", "approved", "--commit", "commit4")
                self.assertEqual(ho_c4.returncode, 0, ho_c4.stderr)
                ho_c4_out = json.loads(ho_c4.stdout)
                self.assertIn("warning", ho_c4_out)
                st_c4 = json.loads(run_cli(root, "status", "--key", "R1-1").stdout)
                self.assertTrue(st_c4["awaiting_qa_deploy"])
                self.assertIsNone(st_c4["recommended_skill"])
                # Explicit promote-qa clears awaiting_in_qa_cycle and unblocks!
                prom_c4 = run_cli(root, "promote-qa", "--key", "R1-1", "--tag", "qa-c4")
                self.assertEqual(prom_c4.returncode, 0, prom_c4.stderr)
                self.assertFalse(json.loads(prom_c4.stdout)["qa_deploy_gate"]["awaiting_in_qa_cycle"])

                # P1-4a: promote-qa rejects open bugs & incomplete tasks
                bug_open = json.loads(run_cli(
                    root, "create-bug", "--key", "R1-1", "--tc-id", "TC-001", "--ac-id", "AC-001",
                    "--title", "Blocking bug", "--severity", "major",
                    "--steps", "s", "--expected", "e", "--actual", "a", "--commit", "commit4",
                ).stdout)["bug_key"]
                prom_open_bug = run_cli(root, "promote-qa", "--key", "R1-1")
                self.assertNotEqual(prom_open_bug.returncode, 0)
                self.assertIn(bug_open, prom_open_bug.stderr)

            # P1-4c: immediate-mode promote-qa is an idempotent no-op with ZERO duplicate comments/transitions
            with tempfile.TemporaryDirectory() as tmp_imm:
                root_i = Path(tmp_imm)
                self.assertEqual(
                    run_cli(root_i, "init", "--project-key", "IMM", "--jira-url", f"http://127.0.0.1:{jira_port}", "--jira-email", "alice@team.io", "--jira-token", "tok-a").returncode,
                    0,
                )
                self.assertEqual(run_cli(root_i, "sync-story", "--key", "IMM-1", "--title", "Immediate Story", "--ac", "AC-001:ok", "--status", "approved", "--create").returncode, 0)
                self.assertEqual(run_cli(root_i, "complete-task", "--key", "IMM-1", "--task-id", "TASK-001", "--commit", "imm1", "--files", "i.py", "--test-result", "ok").returncode, 0)
                self.assertEqual(run_cli(root_i, "handoff", "--key", "IMM-1", "--review-status", "approved", "--commit", "imm1").returncode, 0)
                MockJiraHandler.requests_log.clear()
                prom_imm = run_cli(root_i, "promote-qa", "--key", "IMM-1")
                self.assertEqual(prom_imm.returncode, 0, prom_imm.stderr)
                prom_imm_out = json.loads(prom_imm.stdout)
                self.assertTrue(prom_imm_out["already_deployed"])
                self.assertFalse(prom_imm_out["qa_notified"])
                self.assertEqual(prom_imm_out["qa_handoff_mode"], "immediate")
                imm_mutations = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and (r[1].endswith("/comment") or r[1].endswith("/transitions"))]
                self.assertEqual(imm_mutations, [])

            # P1-5 + P2-1 + P2-2 + P2-5: passing-TC waiver rejection, fixed reopen, mismatch resolution check, stale waiver clear, multi-story skip
            with tempfile.TemporaryDirectory() as tmp2:
                root2 = Path(tmp2)
                self.assertEqual(run_cli(root2, "init", "--project-key", "T5").returncode, 0)
                self.assertEqual(run_cli(root2, "sync-story", "--key", "T5-1", "--title", "Triage Hardening", "--ac", "AC-001:A", "--ac", "AC-002:B", "--status", "approved").returncode, 0)
                self.assertEqual(run_cli(root2, "complete-task", "--key", "T5-1", "--task-id", "TASK-001", "--commit", "t5c1", "--files", "x.py", "--test-result", "ok").returncode, 0)
                self.assertEqual(run_cli(root2, "handoff", "--key", "T5-1", "--review-status", "approved", "--commit", "t5c1").returncode, 0)
                self.assertEqual(run_cli(root2, "record-qa", "--key", "T5-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:A", "--tc", "TC-002:AC-002:manual:B").returncode, 0)
                self.assertEqual(run_cli(root2, "record-qa", "--key", "T5-1", "--mode", "verify", "--commit", "t5c1", "--result", "TC-001=pass", "--result", "TC-002=fail:bad").returncode, 0)
                b_key = json.loads(run_cli(root2, "create-bug", "--key", "T5-1", "--tc-id", "TC-002", "--ac-id", "AC-002", "--title", "Bug B", "--severity", "minor", "--steps", "s", "--expected", "e", "--actual", "a", "--commit", "t5c1").stdout)["bug_key"]

                # P1-5a: Reject waiving an already-passing TC (TC-001)
                waive_pass = run_cli(root2, "triage-bug", "--key", "T5-1", "--bug-id", b_key, "--resolution", "wont-fix", "--reason", "try waive pass", "--decided-by", "PO", "--waive-tcs", "TC-001")
                self.assertNotEqual(waive_pass.returncode, 0)
                self.assertIn("TC-001", waive_pass.stderr)

                # Waive failing TC-002 properly
                self.assertEqual(run_cli(root2, "triage-bug", "--key", "T5-1", "--bug-id", b_key, "--resolution", "wont-fix", "--reason", "accepted", "--decided-by", "PO", "--waive-tcs", "TC-002").returncode, 0)

                # P2-1: _is_tc_waiver_valid rejects resolution mismatch between TC and bug
                st_t5 = json.loads((root2 / ".ai-workflow" / "active" / "T5-1.json").read_text(encoding="utf-8"))
                tc2 = next(c for c in st_t5["qa"]["test_cases"] if c["id"] == "TC-002")
                self.assertTrue(mod._is_tc_waiver_valid(tc2, st_t5))
                tampered_tc2 = dict(tc2, triage_resolution="not-a-bug")
                self.assertFalse(mod._is_tc_waiver_valid(tampered_tc2, st_t5))

                # P2-2: record-qa verify updating TC-002 to pass strips stale waiver metadata
                self.assertEqual(run_cli(root2, "record-qa", "--key", "T5-1", "--mode", "verify", "--commit", "t5c1", "--result", "TC-002=pass").returncode, 0)
                st_t5_cleared = json.loads((root2 / ".ai-workflow" / "active" / "T5-1.json").read_text(encoding="utf-8"))
                tc2_cleared = next(c for c in st_t5_cleared["qa"]["test_cases"] if c["id"] == "TC-002")
                self.assertEqual(tc2_cleared["result"], "pass")
                for stale_k in ("waived_bug_id", "triage_resolution", "waived_by", "waived_reason", "waived_at"):
                    self.assertNotIn(stale_k, tc2_cleared)

                # P1-5b + P2-5: Standalone bug closed via triage -> skipped by multi-story status without --key,
                #               and re-triaging as "fixed" reopens it cleanly back to team-dev!
                sb_k = json.loads(run_cli(root2, "create-bug", "--standalone", "--title", "Stand bug", "--severity", "minor", "--steps", "s", "--expected", "e", "--actual", "a").stdout)["bug_key"]
                self.assertEqual(run_cli(root2, "triage-bug", "--key", sb_k, "--resolution", "not-a-bug", "--reason", "spec", "--decided-by", "PO").returncode, 0)

                # P2-5: status without --key ignores triage_closed sb_k and routes cleanly to T5-1 (NOT ambiguous!)
                st_no_key = json.loads(run_cli(root2, "status").stdout)
                self.assertFalse(st_no_key.get("ambiguous", False))
                self.assertEqual(st_no_key["jira_key"], "T5-1")
                # Explicit --key lookup on sb_k still works
                st_sb_closed = json.loads(run_cli(root2, "status", "--key", sb_k).stdout)
                self.assertTrue(st_sb_closed["triage_closed"])

                # P1-5b: Re-triage closed standalone bug as "fixed" -> reopens & routes back to team-dev!
                retriage = run_cli(root2, "triage-bug", "--key", sb_k, "--resolution", "fixed", "--reason", "Actually a bug", "--decided-by", "PO")
                self.assertEqual(retriage.returncode, 0, retriage.stderr)
                retriage_out = json.loads(retriage.stdout)
                self.assertEqual(retriage_out["bug_status"], "open")
                self.assertFalse(retriage_out["triage_closed"])
                st_sb_reopened = json.loads(run_cli(root2, "status", "--key", sb_k).stdout)
                self.assertFalse(st_sb_reopened.get("triage_closed", False))
                self.assertEqual(st_sb_reopened["recommended_skill"], "team-dev")
                self.assertEqual(st_sb_reopened["recommended_mode"], "implement")

            # P1-6 (Probe 3.3) + P2-3 (English labels when language == "en")
            with tempfile.TemporaryDirectory() as tmp3:
                root3 = Path(tmp3)
                self.assertEqual(
                    run_cli(root3, "init", "--project-key", "EN", "--language", "en", "--jira-url", f"http://127.0.0.1:{jira_port}", "--jira-email", "alice@team.io", "--jira-token", "tok-a").returncode,
                    0,
                )
                sb_en = json.loads(run_cli(root3, "create-bug", "--standalone", "--title", "EN prod bug", "--severity", "major", "--steps", "s", "--expected", "e", "--actual", "a").stdout)["bug_key"]

                MockJiraHandler.requests_log.clear()
                c1 = run_cli(
                    root3, "complete-task",
                    "--key", sb_en,
                    "--commit", "en111",
                    "--files", "fix.py",
                    "--test-result", "pytest ok",
                    "--root-cause", "Null pointer on empty payload",
                    "--solution", "Added guard before dereference",
                )
                self.assertEqual(c1.returncode, 0, c1.stderr)
                self.assertEqual(json.loads(c1.stdout)["bug_fix_warnings"], [])
                # P2-3: English comment uses **Root Cause** and **Solution** without Chinese labels
                en_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1] == f"/rest/api/3/issue/{sb_en}/comment"]
                self.assertEqual(len(en_comments), 1)
                en_adf_str = json.dumps(en_comments[0][2]["body"], ensure_ascii=False)
                self.assertIn("Root Cause", en_adf_str)
                self.assertIn("Solution", en_adf_str)
                self.assertNotIn("問題原因", en_adf_str)
                self.assertNotIn("解決辦法", en_adf_str)

                # Probe 3.3 (P1-6): review requests changes -> complete-task REV-001 without re-passing args -> bug_fix_warnings == []
                self.assertEqual(
                    run_cli(root3, "handoff", "--key", sb_en, "--review-status", "changes-requested", "--commit", "en111", "--summary", "Add edge test").returncode,
                    0,
                )
                c_rev = run_cli(
                    root3, "complete-task",
                    "--key", sb_en,
                    "--task-id", "REV-001",
                    "--commit", "en222",
                    "--files", "test_fix.py",
                    "--test-result", "pytest 2 passed",
                )
                self.assertEqual(c_rev.returncode, 0, c_rev.stderr)
                self.assertEqual(json.loads(c_rev.stdout)["bug_fix_warnings"], [])

            # P1-R2-1 (live Jira reopen on fixed re-triage for Story-linked & Standalone bugs)
            # + P2-R2-1 (creds_missing triggers team-init auth when only triage_closed file exists)
            with tempfile.TemporaryDirectory() as tmp4:
                root4 = Path(tmp4)
                roles4 = {
                    "pm": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice", "api_token": "tok-a"},
                    "tech_lead": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob Lin", "email": "bob@team.io", "jira_account_id": "acc-bob", "api_token": "tok-b"},
                    "reviewer": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "qa": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol", "api_token": "tok-c"},
                    "release_manager": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                self.assertEqual(
                    run_cli(root4, "init", "--project-key", "R2", "--jira-url", f"http://127.0.0.1:{jira_port}", "--roles-json", json.dumps(roles4)).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root4, "sync-story", "--key", "R2-1", "--title", "Live Reopen Story", "--ac", "AC-001:A", "--ac", "AC-002:B", "--status", "approved", "--create").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root4, "complete-task", "--key", "R2-1", "--task-id", "TASK-001", "--commit", "r2c1", "--files", "r2.py", "--test-result", "ok").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root4, "handoff", "--key", "R2-1", "--review-status", "approved", "--commit", "r2c1").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root4, "record-qa", "--key", "R2-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:A", "--tc", "TC-002:AC-002:manual:B").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root4, "record-qa", "--key", "R2-1", "--mode", "verify", "--commit", "r2c1", "--result", "TC-001=pass", "--result", "TC-002=fail:broken").returncode,
                    0,
                )
                story_bug = json.loads(run_cli(
                    root4, "create-bug", "--key", "R2-1", "--tc-id", "TC-002", "--ac-id", "AC-002",
                    "--title", "Live Story Bug", "--severity", "major",
                    "--steps", "s", "--expected", "e", "--actual", "a", "--commit", "r2c1",
                ).stdout)["bug_key"]

                # 1) Triage Story-linked bug as wont-fix -> Jira transitions bug to Done
                tr_sb_close = run_cli(
                    root4, "triage-bug", "--key", "R2-1", "--bug-id", story_bug,
                    "--resolution", "wont-fix", "--reason", "Defer", "--decided-by", "PO Alice", "--waive-tcs", "TC-002",
                )
                self.assertEqual(tr_sb_close.returncode, 0, tr_sb_close.stderr)
                self.assertEqual(MockJiraHandler.stored_issues[story_bug]["fields"]["status"]["name"], "Done")

                # Re-triage as fixed -> remote Jira bug transitions OUT of Done, live status keeps it open & routes to team-dev!
                tr_sb_reopen = run_cli(
                    root4, "triage-bug", "--key", "R2-1", "--bug-id", story_bug,
                    "--resolution", "fixed", "--reason", "Customer escalated; must fix", "--decided-by", "PO Alice",
                )
                self.assertEqual(tr_sb_reopen.returncode, 0, tr_sb_reopen.stderr)
                tr_sb_reopen_out = json.loads(tr_sb_reopen.stdout)
                self.assertEqual(tr_sb_reopen_out["bug_status"], "open")
                self.assertEqual(tr_sb_reopen_out["transition"]["transitioned_to"], "In Progress")
                self.assertEqual(MockJiraHandler.stored_issues[story_bug]["fields"]["status"]["name"], "In Progress")

                st_r2_live = json.loads(run_cli(root4, "status", "--key", "R2-1").stdout)
                self.assertEqual(st_r2_live["recommended_skill"], "team-dev")
                self.assertEqual(st_r2_live["recommended_mode"], "implement")
                st_r2_disk = json.loads((root4 / ".ai-workflow" / "active" / "R2-1.json").read_text(encoding="utf-8"))
                reopened_b = next(b for b in st_r2_disk["bugs"] if b["bug_key"] == story_bug)
                self.assertEqual(reopened_b["status"], "open")
                self.assertEqual(reopened_b["jira_status"], "In Progress")
                self.assertEqual(reopened_b["jira_resolution"], "")

                # 2) Standalone adopted bug: triage as not-a-bug (Done) -> re-triage as fixed -> Jira reopened, live status -> team-dev!
                MockJiraHandler.stored_issues["R2-777"] = {
                    "key": "R2-777",
                    "fields": {
                        "summary": "Adopted prod bug",
                        "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Prod issue"}]}]},
                        "issuetype": {"name": "Bug", "subtask": False},
                        "status": {"name": "To Do"},
                    },
                }
                self.assertEqual(run_cli(root4, "create-bug", "--standalone", "--adopt", "--key", "R2-777").returncode, 0)
                self.assertEqual(
                    run_cli(root4, "triage-bug", "--key", "R2-777", "--resolution", "not-a-bug", "--reason", "Misread", "--decided-by", "PO Alice").returncode,
                    0,
                )
                self.assertEqual(MockJiraHandler.stored_issues["R2-777"]["fields"]["status"]["name"], "Done")
                self.assertEqual(MockJiraHandler.stored_issues["R2-777"]["fields"]["resolution"]["name"], "Not a Bug")

                tr_sa_reopen = run_cli(
                    root4, "triage-bug", "--key", "R2-777",
                    "--resolution", "fixed", "--reason", "Confirmed regression", "--decided-by", "PO Alice",
                )
                self.assertEqual(tr_sa_reopen.returncode, 0, tr_sa_reopen.stderr)
                tr_sa_out = json.loads(tr_sa_reopen.stdout)
                self.assertFalse(tr_sa_out["triage_closed"])
                self.assertEqual(tr_sa_out["transition"]["transitioned_to"], "In Progress")
                self.assertEqual(MockJiraHandler.stored_issues["R2-777"]["fields"]["status"]["name"], "In Progress")

                st_sa_live = json.loads(run_cli(root4, "status", "--key", "R2-777").stdout)
                self.assertFalse(st_sa_live.get("triage_closed", False))
                self.assertEqual(st_sa_live["recommended_skill"], "team-dev")
                self.assertEqual(st_sa_live["recommended_mode"], "implement")
                sa_disk = json.loads((root4 / ".ai-workflow" / "active" / "R2-777.json").read_text(encoding="utf-8"))
                self.assertEqual(sa_disk["jira_status"], "In Progress")
                self.assertEqual(sa_disk["jira_resolution"], "")

                # 3) P2-R2-1: when .ai-workflow/active/ contains ONLY a triage_closed=True file and credentials.json is missing,
                #    `status` MUST return credentials_missing=True / team-init (auth) rather than skipping auth!
                self.assertEqual(
                    run_cli(root4, "triage-bug", "--key", "R2-777", "--resolution", "not-a-bug", "--reason", "Closed again", "--decided-by", "PO Alice").returncode,
                    0,
                )
                (root4 / ".ai-workflow" / "active" / "R2-1.json").unlink()
                user_cred_r4 = Path(json.loads(run_cli(root4, "init", "--inspect").stdout)["user_credentials_path"])
                if user_cred_r4.exists():
                    user_cred_r4.unlink()
                legacy_cred_r4 = root4 / ".ai-workflow" / "credentials.json"
                if legacy_cred_r4.exists():
                    legacy_cred_r4.unlink()
                st_need_auth = json.loads(run_cli(root4, "status").stdout)
                self.assertTrue(st_need_auth.get("credentials_missing"))
                self.assertTrue(st_need_auth.get("requires_member_auth"))
                self.assertEqual(st_need_auth["recommended_skill"], "team-init")
                self.assertEqual(st_need_auth["recommended_mode"], "auth")
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_32_team_spec_backfill_preserve_owner_risks_and_open_questions(self):
        mod = self._load_jira_sync_module()
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.reject_parent_on_create_epics.clear()
        MockJiraHandler.reject_parent_jql_epics.clear()
        MockJiraHandler.fail_create_story_titles.clear()
        MockJiraHandler.simulate_get_404_keys.clear()
        MockJiraHandler.reject_customfield_on_create_epics.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                roles = {
                    "pm": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice", "api_token": "tok-a"},
                    "developer": {"name": "Bob Lin", "email": "bob@team.io", "jira_account_id": "acc-bob", "api_token": "tok-b"},
                    "reviewer": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol", "api_token": "tok-c"},
                    "qa": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                    "release_manager": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                self.assertEqual(
                    run_cli(root, "init", "--project-key", "BF", "--jira-url", f"http://127.0.0.1:{jira_port}", "--roles-json", json.dumps(roles)).returncode,
                    0,
                )

                # 1. Tracked Story already in progress under Developer Bob with tasks/review/qa/bugs
                self.assertEqual(
                    run_cli(
                        root, "sync-story",
                        "--key", "BF-1",
                        "--title", "Tracked Order Export",
                        "--scope-in", "Initial export",
                        "--ac", "AC-001:Export orders to CSV",
                        "--status", "approved",
                        "--create",
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "create-subtasks", "--key", "BF-1", "--task", "TASK-001:Implement exporter:AC-001:pytest").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "complete-task", "--key", "BF-1", "--task-id", "TASK-001", "--commit", "bf111", "--files", "exp.py", "--test-result", "pytest ok").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "handoff", "--key", "BF-1", "--review-status", "approved", "--commit", "bf111").returncode,
                    0,
                )
                self.assertEqual(
                    run_cli(root, "record-qa", "--key", "BF-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Check CSV").returncode,
                    0,
                )
                before_disk = json.loads((root / ".ai-workflow" / "active" / "BF-1.json").read_text(encoding="utf-8"))
                self.assertEqual(before_disk["current_owner_role"], "qa")
                self.assertEqual(before_disk["jira_status"], "In QA")

                # 2. Reject --backfill + --status approved with exit 1 and ZERO Jira writes
                MockJiraHandler.requests_log.clear()
                bad_bf = run_cli(root, "sync-story", "--key", "BF-1", "--backfill", "--status", "approved")
                self.assertNotEqual(bad_bf.returncode, 0)
                self.assertIn("--backfill", bad_bf.stderr)
                self.assertEqual(MockJiraHandler.requests_log, [])

                # 3. Reject malformed / non-list --open-questions-json
                bad_oq1 = run_cli(root, "sync-story", "--key", "BF-1", "--backfill", "--open-questions-json", "{malformed")
                self.assertNotEqual(bad_oq1.returncode, 0)
                self.assertIn("open-questions-json", bad_oq1.stderr.lower())

                bad_oq2 = run_cli(root, "sync-story", "--key", "BF-1", "--backfill", "--open-questions-json", '{"id":"Q-001"}')
                self.assertNotEqual(bad_oq2.returncode, 0)

                # 4. --backfill with --risks & --open-questions-json (@file) preserves owner/status/tasks/review/qa/bugs
                oq_file = root / "oqs.json"
                oq_file.write_text(json.dumps([
                    None,
                    "   ",
                    {"id": "Q-005", "question": "Confirm tenant isolation rule", "owner": "pm", "proposed_default": "Scope by tenant_id", "impacted_acs": ["AC-001", ""], "blocking": True},
                    "Clarify CSV row cap above 10k",
                ]), encoding="utf-8")

                MockJiraHandler.requests_log.clear()
                bf_res = run_cli(
                    root, "sync-story",
                    "--key", "BF-1",
                    "--backfill",
                    "--scope-in", "Initial export + tenant boundary check",
                    "--ac", "AC-002:Reject cross-tenant query with 403",
                    "--design-summary", "Observed code currently filters by user_id; tenant_id filter not yet verified.",
                    "--risks", "Line 1: Tenant boundary gap\n- Line 2: Rate limiting missing",
                    "--open-questions-json", f"@{oq_file}",
                    "--spec-json", json.dumps({"current_owner_role": "pm", "jira_status": "To Do", "tasks": [], "qa": {}}),
                )
                self.assertEqual(bf_res.returncode, 0, bf_res.stderr)
                bf_out = json.loads(bf_res.stdout)
                self.assertTrue(bf_out["ok"])
                self.assertTrue(bf_out["backfill_mode"])
                self.assertTrue(bf_out["owner_preserved"])
                self.assertTrue(bf_out["status_preserved"])
                self.assertEqual(bf_out["spec_status"], "approved")  # no status rollback on already-approved tracked story
                self.assertEqual(bf_out["open_blocking_questions"], ["Q-005"])
                self.assertEqual(bf_out["backfill_stage"], "spec_pending_confirmation")
                self.assertTrue(bf_out["description_verified"])
                self.assertFalse(bf_out["can_auto_chain_next_station"])
                self.assertTrue(bf_out["requires_user_confirmation_for_next_station"])
                self.assertIsNone(bf_out["handoff"])

                # Zero assignee/transition/comment calls during backfill
                mutating_ops = [
                    r for r in MockJiraHandler.requests_log
                    if r[1].endswith("/assignee") or r[1].endswith("/transitions") or r[1].endswith("/comment")
                ]
                self.assertEqual(mutating_ops, [])

                # Active JSON preserved owner/status/tasks/review/qa and stored normalized Q-### (Q-005 + auto Q-001)
                after_disk = json.loads((root / ".ai-workflow" / "active" / "BF-1.json").read_text(encoding="utf-8"))
                self.assertEqual(after_disk["current_owner_role"], "qa")
                self.assertEqual(after_disk["jira_status"], "In QA")
                self.assertEqual(len(after_disk["tasks"]), 1)
                self.assertEqual(after_disk["review"]["status"], "approved")
                self.assertEqual(len(after_disk["qa"]["test_cases"]), 1)
                self.assertEqual([a["id"] for a in after_disk["acceptance_criteria"]], ["AC-001", "AC-002"])
                oqs_saved = after_disk["spec"]["open_questions"]
                self.assertEqual([q["id"] for q in oqs_saved], ["Q-005", "Q-001"])
                self.assertTrue(oqs_saved[0]["blocking"])
                self.assertFalse(oqs_saved[1]["blocking"])

                # Description includes Risks & Open Questions, zero Verification Scenarios table, valid ADF invariants, and no analysis.md
                desc_adf = MockJiraHandler.stored_issues["BF-1"]["fields"]["description"]
                self._assert_valid_adf_invariants(desc_adf)
                desc_plain = mod._adf_to_plain_text(desc_adf)
                self.assertIn("風險與待確認問題", desc_plain)
                self.assertIn("Q-005", desc_plain)
                self.assertIn("Q-001", desc_plain)
                self.assertNotIn("驗證場景", desc_plain)
                self.assertNotIn("Verification Scenarios", desc_plain)
                self.assertFalse((root / "analysis.md").exists())

                # Resolving blocking Qs -> backfill_stage becomes "material_backfilled"; empty/whitespace risks omitted cleanly
                bf_clear = run_cli(
                    root, "sync-story",
                    "--key", "BF-1",
                    "--backfill",
                    "--risks", "   ",
                    "--open-questions-json", "[]",
                )
                self.assertEqual(bf_clear.returncode, 0, bf_clear.stderr)
                bf_clear_out = json.loads(bf_clear.stdout)
                self.assertEqual(bf_clear_out["open_blocking_questions"], [])
                self.assertEqual(bf_clear_out["backfill_stage"], "material_backfilled")
                desc_after_clear = mod._adf_to_plain_text(MockJiraHandler.stored_issues["BF-1"]["fields"]["description"])
                self.assertNotIn("Risks & Open Questions", desc_after_clear)
                self.assertNotIn("風險與待確認問題", desc_after_clear)

                # 5. Adopting an UNTRACKED existing Jira Story via --key <EXISTING-KEY> --backfill
                MockJiraHandler.stored_issues["BF-99"] = {
                    "key": "BF-99",
                    "fields": {
                        "summary": "Legacy Payment Refund Flow",
                        "description": {
                            "type": "doc",
                            "version": 1,
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "HUMAN PROSE: Keep legal note.\n- AC-001: Issue full refund within 5 minutes"}]}],
                        },
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "In Review"},
                        "assignee": {"accountId": "acc-bob", "displayName": "Bob Lin", "emailAddress": "bob@team.io"},
                        "subtasks": [
                            {
                                "key": "BF-100",
                                "fields": {
                                    "summary": "TASK-001: Implement refund service",
                                    "issuetype": {"name": "Sub-task", "subtask": True},
                                    "status": {"name": "Done"},
                                    "assignee": {"accountId": "acc-bob", "displayName": "Bob Lin"},
                                },
                            }
                        ],
                    },
                }
                # P1-2: First-adoption on untracked Story with non-empty Jira description MUST block unless --accept-remote-description or --force-overwrite is passed!
                MockJiraHandler.requests_log.clear()
                blind_adopt = run_cli(
                    root, "sync-story",
                    "--key", "BF-99",
                    "--backfill",
                    "--ac", "AC-002:Prevent duplicate refund on retry",
                    "--risks", "Idempotency key enforcement inferred from controller only",
                )
                self.assertNotEqual(blind_adopt.returncode, 0)
                self.assertIn("description", blind_adopt.stderr.lower())
                self.assertFalse((root / ".ai-workflow" / "active" / "BF-99.json").exists())
                self.assertFalse(any(m in ("PUT", "POST") and "/issue/BF-99" in p for m, p, _, _ in MockJiraHandler.requests_log))
                self.assertIn("HUMAN PROSE", mod._adf_to_plain_text(MockJiraHandler.stored_issues["BF-99"]["fields"]["description"]))

                # Explicit --accept-remote-description adopts cleanly and preserves Jira status/assignee/subtasks
                MockJiraHandler.requests_log.clear()
                adopt_res = run_cli(
                    root, "sync-story",
                    "--key", "BF-99",
                    "--backfill",
                    "--accept-remote-description",
                    "--ac", "AC-002:Prevent duplicate refund on retry",
                    "--risks", "Idempotency key enforcement inferred from controller only",
                    "--open-questions-json", json.dumps([{"question": "Verify refund webhook retry TTL", "blocking": True}]),
                )
                self.assertEqual(adopt_res.returncode, 0, adopt_res.stderr)
                adopt_out = json.loads(adopt_res.stdout)
                self.assertEqual(adopt_out["spec_status"], "draft")  # NEVER auto-approves adopted raw Jira Story!
                self.assertEqual(adopt_out["backfill_stage"], "spec_pending_confirmation")
                self.assertEqual(adopt_out["open_blocking_questions"], ["Q-001"])
                self.assertTrue(adopt_out["description_verified"])
                self.assertEqual(MockJiraHandler.stored_issues["BF-99"]["fields"]["status"]["name"], "In Review")
                self.assertEqual(MockJiraHandler.stored_issues["BF-99"]["fields"]["assignee"]["accountId"], "acc-bob")
                adopted_disk = json.loads((root / ".ai-workflow" / "active" / "BF-99.json").read_text(encoding="utf-8"))
                self.assertEqual([a["id"] for a in adopted_disk["acceptance_criteria"]], ["AC-001", "AC-002"])
                self.assertEqual(len(adopted_disk["tasks"]), 1)
                self.assertTrue(adopted_disk.get("last_synced_description_hash"))

                # Repeat --backfill on BF-99 succeeds without false drift
                repeat_bf = run_cli(root, "sync-story", "--key", "BF-99", "--backfill", "--risks", "Updated risk without UI edit")
                self.assertEqual(repeat_bf.returncode, 0, repeat_bf.stderr)

                # Description Drift Guard blocks subsequent backfill if Jira description was edited in Web UI, recovers with --force-overwrite
                MockJiraHandler.stored_issues["BF-99"]["fields"]["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Human edited in Jira Web UI\n- AC-003: Manual AC"}]}],
                }
                drift_blocked = run_cli(root, "sync-story", "--key", "BF-99", "--backfill", "--risks", "Updated risk")
                self.assertNotEqual(drift_blocked.returncode, 0)
                self.assertIn("description", drift_blocked.stderr.lower())

                drift_ok = run_cli(root, "sync-story", "--key", "BF-99", "--backfill", "--force-overwrite", "--risks", "Updated risk")
                self.assertEqual(drift_ok.returncode, 0, drift_ok.stderr)

                # Untracked existing Jira Story with EMPTY description stays unblocked on first --backfill
                MockJiraHandler.stored_issues["BF-98"] = {
                    "key": "BF-98",
                    "fields": {
                        "summary": "Blank Description Story",
                        "description": None,
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "In Progress"},
                    },
                }
                empty_adopt = run_cli(root, "sync-story", "--key", "BF-98", "--backfill", "--ac", "AC-001:Works")
                self.assertEqual(empty_adopt.returncode, 0, empty_adopt.stderr)

                # 6. --preserve-owner standalone with English labels and no None string leakage
                self.assertEqual(run_cli(root, "init", "--project-key", "BF", "--language", "en").returncode, 0)
                en_md = mod.format_story_description({
                    "jira_key": "BF-EN",
                    "title": "English Backfill",
                    "scope_in": "Scope",
                    "scope_out": "None",
                    "change_surface": {"has_ui": False, "has_api": False, "has_data": False},
                    "acceptance_criteria": [{"id": "AC-001", "description": "Works"}],
                    "spec": {
                        "risks": "Missing index",
                        "open_questions": [{"id": "Q-001", "question": "Confirm SLA", "owner": "pm", "blocking": True}],
                    },
                }, {"language": "en"})
                self.assertIn("### Risks & Open Questions", en_md)
                self.assertIn("[Blocking]", en_md)
                self.assertNotIn("風險與待確認問題", en_md)
                self._assert_valid_adf_invariants(mod.JiraClient.text_to_adf(en_md))

                # Simple story without risks/open_questions keeps 5-section format unchanged
                simple_md = mod.format_story_description({
                    "jira_key": "BF-SIMPLE",
                    "title": "Simple Story",
                    "scope_in": "Simple",
                    "scope_out": "N/A",
                    "change_surface": {"has_ui": False, "has_api": False, "has_data": False},
                    "acceptance_criteria": [{"id": "AC-001", "description": "Done"}],
                }, {"language": "en"})
                self.assertNotIn("Risks & Open Questions", simple_md)
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_33_epic_decompose_validation_preview_apply_fallbacks_and_partial_failure(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.reject_parent_on_create_epics.clear()
        MockJiraHandler.reject_parent_jql_epics.clear()
        MockJiraHandler.fail_create_story_titles.clear()
        MockJiraHandler.simulate_get_404_keys.clear()
        MockJiraHandler.reject_customfield_on_create_epics.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            # Part A: Live mode validation, preview, apply, duplicate skip, fallbacks, partial failure
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.assertEqual(
                    run_cli(
                        root, "init",
                        "--project-key", "EP",
                        "--jira-url", f"http://127.0.0.1:{jira_port}",
                        "--jira-email", "alice@team.io",
                        "--jira-token", "tok-a",
                    ).returncode,
                    0,
                )
                MockJiraHandler.stored_issues["EP-10"] = {
                    "key": "EP-10",
                    "fields": {"summary": "Multi-tenant Auth Epic", "issuetype": {"name": "Epic"}, "status": {"name": "To Do"}},
                }

                # 1. Input & Graph validation failures (exit 1, zero Jira writes)
                invalid_cases = [
                    "[]",
                    "{}",
                    "{bad-json",
                    json.dumps([{"id": "S1"}]),
                    json.dumps([{"id": "S1", "title": None}]),
                    json.dumps([{"id": "S1", "title": "   "}]),
                    json.dumps([{"id": "S1", "title": "A"}, {"id": "S1", "title": "B"}]),
                    json.dumps([{"id": "S1", "title": "Self", "depends_on": ["S1"]}]),
                    json.dumps([{"id": "S1", "title": "Unknown", "depends_on": ["NOPE"]}]),
                    json.dumps([
                        {"id": "S1", "title": "A", "depends_on": ["S3"]},
                        {"id": "S2", "title": "B", "depends_on": ["S1"]},
                        {"id": "S3", "title": "C", "depends_on": ["S2"]},
                    ]),
                ]
                MockJiraHandler.requests_log.clear()
                for bad_json in invalid_cases:
                    res_bad = run_cli(root, "epic-decompose", "--epic", "EP-10", "--stories-json", bad_json)
                    self.assertNotEqual(res_bad.returncode, 0, f"Expected failure for {bad_json}")
                self.assertEqual(MockJiraHandler.requests_log, [])

                # 2. Preview mode (default dry_run=True, @file input, title duplicate warning, waves, ZERO writes)
                MockJiraHandler.stored_issues["EP-50"] = {
                    "key": "EP-50",
                    "fields": {
                        "summary": "Core Auth Schema",
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "In Progress"},
                        "parent": {"key": "EP-10"},
                    },
                }
                stories_file = root / "epic_stories.json"
                stories_payload = [
                    {
                        "id": "S2",
                        "title": "Login & JWT Endpoint",
                        "scope": "POST /login issuing JWT",
                        "acceptance_criteria": ["AC-001:Valid login returns JWT", "AC-002:Invalid credentials return 401"],
                        "depends_on": ["S1"],
                        "shared_contracts": ["AuthUser schema", "JWT token payload"],
                        "priority": "High",
                        "story_points": 5,
                    },
                    {
                        "id": "S1",
                        "title": "Core Auth Schema",
                        "summary": "User and tenant tables",
                        "acceptance_criteria": ["AC-001:Users table exists"],
                        "depends_on": [],
                        "shared_contracts": ["AuthUser schema"],
                        "story_points": 3,
                    },
                    {
                        "id": "S3",
                        "title": "RBAC Middleware",
                        "acceptance_criteria": ["AC-001:Enforces role permissions"],
                        "depends_on": ["S2"],
                        "shared_contracts": ["JWT token payload"],
                    },
                ]
                stories_file.write_text(json.dumps(stories_payload), encoding="utf-8")

                MockJiraHandler.requests_log.clear()
                prev = run_cli(root, "epic-decompose", "--epic", "EP-10", "--stories-json", f"@{stories_file}")
                self.assertEqual(prev.returncode, 0, prev.stderr)
                prev_out = json.loads(prev.stdout)
                self.assertEqual(prev_out["mode"], "preview")
                self.assertTrue(prev_out["dry_run"])
                self.assertFalse(prev_out["applied"])
                self.assertEqual(prev_out["epic_summary"], "Multi-tenant Auth Epic")
                self.assertEqual(prev_out["total_to_create"], 3)
                self.assertEqual(
                    prev_out["execution_waves"],
                    [
                        {"wave": 1, "label": "Wave 1", "story_ids": ["S1"]},
                        {"wave": 2, "label": "Wave 2", "story_ids": ["S2"]},
                        {"wave": 3, "label": "Wave 3", "story_ids": ["S3"]},
                    ],
                )
                self.assertEqual(prev_out["shared_contracts_summary"], ["AuthUser schema", "JWT token payload"])
                self.assertEqual(len(prev_out["duplicate_warnings"]), 1)
                self.assertEqual(prev_out["duplicate_warnings"][0]["existing_key"], "EP-50")
                self.assertTrue(prev_out["requires_user_confirmation"])
                self.assertFalse(prev_out["can_auto_chain_next_station"])
                # Zero POST /issue writes and zero local active files created in preview mode
                self.assertFalse(any(m == "POST" and p == "/rest/api/3/issue" for m, p, _, _ in MockJiraHandler.requests_log))
                self.assertEqual(list((root / ".ai-workflow" / "active").glob("*.json")) if (root / ".ai-workflow" / "active").exists() else [], [])

                # 3. Apply mode (--apply): creates Stories in Wave order with real upstream depends_on_keys!
                MockJiraHandler.requests_log.clear()
                MockJiraHandler.stored_issues.pop("EP-50", None)
                app_res = run_cli(root, "epic-decompose", "--epic", "EP-10", "--stories-json", f"@{stories_file}", "--apply")
                self.assertEqual(app_res.returncode, 0, app_res.stderr)
                app_out = json.loads(app_res.stdout)
                self.assertTrue(app_out["ok"])
                self.assertTrue(app_out["applied"])
                self.assertFalse(app_out["dry_run"])
                self.assertEqual(app_out["total_created"], 3)
                created = app_out["created_stories"]
                self.assertEqual([c["id"] for c in created], ["S1", "S2", "S3"])
                k1, k2, k3 = created[0]["jira_key"], created[1]["jira_key"], created[2]["jira_key"]
                self.assertEqual(created[1]["depends_on_keys"], [k1])
                self.assertEqual(created[2]["depends_on_keys"], [k2])

                for c_item in created:
                    st_disk = json.loads((root / c_item["active_file"]).read_text(encoding="utf-8"))
                    self.assertEqual(st_disk["epic_key"], "EP-10")
                    self.assertEqual(st_disk["epic_decompose_id"], c_item["id"])
                    self.assertEqual(st_disk["wave"], c_item["wave"])
                    self.assertEqual(st_disk["depends_on_keys"], c_item["depends_on_keys"])
                    self.assertEqual(st_disk["shared_contracts"], c_item["shared_contracts"])
                    self.assertEqual(st_disk["spec_status"], "draft")
                    self.assertEqual(MockJiraHandler.stored_issues[c_item["jira_key"]]["fields"]["parent"]["key"], "EP-10")

                # 4. Re-running --apply with same epic_decompose_ids skips existing without creating duplicates
                MockJiraHandler.requests_log.clear()
                reapp = run_cli(root, "epic-decompose", "--epic", "EP-10", "--stories-json", f"@{stories_file}", "--apply")
                self.assertEqual(reapp.returncode, 0, reapp.stderr)
                reapp_out = json.loads(reapp.stdout)
                self.assertEqual(reapp_out["total_created"], 0)
                self.assertEqual(len(reapp_out["skipped_stories"]), 3)
                self.assertFalse(any(m == "POST" and p == "/rest/api/3/issue" for m, p, _, _ in MockJiraHandler.requests_log))

                # 5. Live Epic linking HTTP 400 fallback: unconfigured epic_link_field -> unlinked + warning;
                #    configured epic_link_field -> customfield_10014!
                MockJiraHandler.stored_issues["EP-20"] = {
                    "key": "EP-20",
                    "fields": {"summary": "Fallback Epic", "issuetype": {"name": "Epic"}, "status": {"name": "To Do"}},
                }
                MockJiraHandler.reject_parent_on_create_epics.add("EP-20")
                fb1 = run_cli(
                    root, "epic-decompose", "--epic", "EP-20",
                    "--stories-json", json.dumps([{"id": "FB1", "title": "Unlinked Fallback Story"}]),
                    "--apply",
                )
                self.assertEqual(fb1.returncode, 0, fb1.stderr)
                fb1_out = json.loads(fb1.stdout)
                self.assertEqual(fb1_out["created_stories"][0]["epic_link_mode"], "unlinked")
                self.assertTrue(any("HTTP 400" in w for w in fb1_out["warnings"]))

                cfg_p = root / ".ai-workflow" / "team-config.json"
                cfg_d = json.loads(cfg_p.read_text(encoding="utf-8"))
                cfg_d["epic_link_field"] = "customfield_10014"
                cfg_p.write_text(json.dumps(cfg_d, indent=2) + "\n", encoding="utf-8")

                fb2 = run_cli(
                    root, "epic-decompose", "--epic", "EP-20",
                    "--stories-json", json.dumps([{"id": "FB2", "title": "Custom Field Linked Story"}]),
                    "--apply",
                )
                self.assertEqual(fb2.returncode, 0, fb2.stderr)
                fb2_out = json.loads(fb2.stdout)
                self.assertEqual(fb2_out["created_stories"][0]["epic_link_mode"], "epic_link_field")
                fb2_key = fb2_out["created_stories"][0]["jira_key"]
                self.assertEqual(MockJiraHandler.stored_issues[fb2_key]["fields"]["customfield_10014"], "EP-20")

                # 6. Partial failure mid-batch preserves created Wave 1 story, skips dependent Wave 2, exits 1
                MockJiraHandler.fail_create_story_titles.add("Failing Story")
                part_stories = [
                    {"id": "P1", "title": "Good Wave 1 Story"},
                    {"id": "P2", "title": "Failing Story"},
                    {"id": "P3", "title": "Dependent Wave 2 Story", "depends_on": ["P2"]},
                ]
                MockJiraHandler.stored_issues["EP-30"] = {
                    "key": "EP-30",
                    "fields": {"summary": "Partial Fail Epic", "issuetype": {"name": "Epic"}, "status": {"name": "To Do"}},
                }
                part_res = run_cli(root, "epic-decompose", "--epic", "EP-30", "--stories-json", json.dumps(part_stories), "--apply")
                self.assertNotEqual(part_res.returncode, 0)
                part_out = json.loads(part_res.stdout)
                self.assertTrue(part_out["partial_failure"])
                self.assertEqual(len(part_out["created_stories"]), 1)
                self.assertEqual(part_out["created_stories"][0]["id"], "P1")
                self.assertEqual([f["id"] for f in part_out["failed_stories"]], ["P2", "P3"])
                self.assertTrue((root / part_out["created_stories"][0]["active_file"]).exists())

            # Part B: Offline deterministic local keys avoiding collisions
            with tempfile.TemporaryDirectory() as tmp_off:
                root_off = Path(tmp_off)
                self.assertEqual(run_cli(root_off, "init", "--project-key", "OFF").returncode, 0)
                # Pre-existing collision file OFF-99-S1.json
                active_off = root_off / ".ai-workflow" / "active"
                active_off.mkdir(parents=True, exist_ok=True)
                (active_off / "OFF-99-S1.json").write_text(json.dumps({"jira_key": "OFF-99-S1"}), encoding="utf-8")

                off_stories = [
                    {"title": "Offline Base"},
                    {"title": "Offline Followup", "depends_on": ["S-1"]},
                ]
                off_res = run_cli(root_off, "epic-decompose", "--epic", "OFF-99", "--stories-json", json.dumps(off_stories), "--apply")
                self.assertEqual(off_res.returncode, 0, off_res.stderr)
                off_out = json.loads(off_res.stdout)
                self.assertEqual([c["jira_key"] for c in off_out["created_stories"]], ["OFF-99-S2", "OFF-99-S3"])
                self.assertEqual(off_out["created_stories"][1]["depends_on_keys"], ["OFF-99-S2"])
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_34_status_epic_progress_aggregation_live_and_offline(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.reject_parent_on_create_epics.clear()
        MockJiraHandler.reject_parent_jql_epics.clear()
        MockJiraHandler.fail_create_story_titles.clear()
        MockJiraHandler.simulate_get_404_keys.clear()
        MockJiraHandler.reject_customfield_on_create_epics.clear()

        # Part A: Offline mode rollup & empty Epic safety
        with tempfile.TemporaryDirectory() as tmp_off:
            root_off = Path(tmp_off)
            self.assertEqual(run_cli(root_off, "init", "--project-key", "OE").returncode, 0)

            # Mutually exclusive --key + --epic -> exit 1
            both = run_cli(root_off, "status", "--key", "OE-1", "--epic", "OE-10")
            self.assertNotEqual(both.returncode, 0)

            # Empty / missing Epic returns total_stories=0, progress_pct=0.0 without ZeroDivisionError
            empty_ep = run_cli(root_off, "status", "--epic", "OE-999")
            self.assertEqual(empty_ep.returncode, 0, empty_ep.stderr)
            empty_out = json.loads(empty_ep.stdout)
            self.assertEqual(empty_out["total_stories"], 0)
            self.assertEqual(empty_out["progress_pct"], 0.0)
            self.assertIsNone(empty_out["next_recommended_story"])

            active_dir = root_off / ".ai-workflow" / "active"
            active_dir.mkdir(parents=True, exist_ok=True)
            # Story 1: Done
            (active_dir / "OE-10-S1.json").write_text(json.dumps({
                "jira_key": "OE-10-S1",
                "epic_key": "OE-10",
                "wave": 1,
                "title": "Done Wave 1 Story",
                "jira_status": "Done",
                "spec_status": "approved",
                "tasks": [{"id": "TASK-001", "status": "completed"}],
                "bugs": [],
            }), encoding="utf-8")
            # Story 2: In Progress (Station 2: Dev Implement), Wave 1
            (active_dir / "OE-10-S2.json").write_text(json.dumps({
                "jira_key": "OE-10-S2",
                "epic_key": "OE-10",
                "wave": 1,
                "title": "In Progress Wave 1 Story",
                "jira_status": "In Progress",
                "spec_status": "approved",
                "tasks": [{"id": "TASK-001", "status": "pending"}],
                "bugs": [],
            }), encoding="utf-8")
            # Story 3: Blocked by dependency on OE-10-S2, Wave 2
            (active_dir / "OE-10-S3.json").write_text(json.dumps({
                "jira_key": "OE-10-S3",
                "epic_key": "OE-10",
                "wave": 2,
                "title": "Blocked Wave 2 Story",
                "jira_status": "To Do",
                "spec_status": "draft",
                "depends_on_keys": ["OE-10-S2"],
                "tasks": [],
                "bugs": [],
            }), encoding="utf-8")
            # Story 4: Ready to start (draft, To Do, unblocked), Wave 2
            (active_dir / "OE-10-S4.json").write_text(json.dumps({
                "jira_key": "OE-10-S4",
                "epic_key": "OE-10",
                "wave": 2,
                "title": "Ready Wave 2 Story",
                "jira_status": "To Do",
                "spec_status": "draft",
                "depends_on_keys": ["OE-10-S1"],
                "tasks": [],
                "bugs": [],
            }), encoding="utf-8")

            ep_off = run_cli(root_off, "status", "--epic", "OE-10")
            self.assertEqual(ep_off.returncode, 0, ep_off.stderr)
            ep_out = json.loads(ep_off.stdout)
            self.assertEqual(ep_out["total_stories"], 4)
            self.assertEqual(ep_out["completed_stories"], 1)
            self.assertEqual(ep_out["in_progress_stories"], 1)
            self.assertEqual(ep_out["blocked_stories"], 1)
            self.assertEqual(ep_out["ready_to_start_stories"], 1)
            self.assertEqual(ep_out["progress_pct"], 25.0)
            self.assertEqual(ep_out["station_breakdown"]["Done"], 1)
            self.assertEqual(ep_out["station_breakdown"]["Station 2: Dev Implement"], 1)
            self.assertEqual(ep_out["next_recommended_story"]["key"], "OE-10-S2")
            self.assertFalse(ep_out["can_auto_chain_next_station"])
            self.assertTrue(ep_out["requires_user_confirmation"])
            by_k = {row["key"]: row for row in ep_out["stories"]}
            self.assertEqual(by_k["OE-10-S3"]["blocked_by_dependencies"], ["OE-10-S2"])
            self.assertEqual(by_k["OE-10-S4"]["blocked_by_dependencies"], [])

        # Part B: Live mode merging remote Done & remote active Stories + parent->Epic Link JQL fallback
        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()
        try:
            with tempfile.TemporaryDirectory() as tmp_live:
                root_l = Path(tmp_live)
                self.assertEqual(
                    run_cli(
                        root_l, "init",
                        "--project-key", "LE",
                        "--jira-url", f"http://127.0.0.1:{jira_port}",
                        "--jira-email", "alice@team.io",
                        "--jira-token", "tok-a",
                    ).returncode,
                    0,
                )
                MockJiraHandler.stored_issues["LE-10"] = {
                    "key": "LE-10",
                    "fields": {"summary": "Live Reporting Epic", "issuetype": {"name": "Epic"}, "status": {"name": "In Progress"}},
                }
                # Remote closed Story (deleted from local active dir)
                MockJiraHandler.stored_issues["LE-101"] = {
                    "key": "LE-101",
                    "fields": {
                        "summary": "Remote Done Story",
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "Done"},
                        "parent": {"key": "LE-10"},
                    },
                }
                # Remote active Story created in Jira Web UI (not in local active dir)
                MockJiraHandler.stored_issues["LE-102"] = {
                    "key": "LE-102",
                    "fields": {
                        "summary": "Remote Web UI Story",
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "To Do"},
                        "customfield_10014": "LE-10",
                    },
                }
                # Local active Story linked to LE-10
                MockJiraHandler.stored_issues["LE-103"] = {
                    "key": "LE-103",
                    "fields": {
                        "summary": "Local Active Story",
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "In Progress"},
                        "parent": {"key": "LE-10"},
                    },
                }
                active_l = root_l / ".ai-workflow" / "active"
                active_l.mkdir(parents=True, exist_ok=True)
                (active_l / "LE-103.json").write_text(json.dumps({
                    "jira_key": "LE-103",
                    "epic_key": "LE-10",
                    "wave": 2,
                    "title": "Local Active Story",
                    "jira_status": "In Progress",
                    "spec_status": "approved",
                    "depends_on_keys": ["LE-101"],
                    "tasks": [],
                    "bugs": [],
                }), encoding="utf-8")

                # 1) Normal parent JQL returns LE-101 & LE-103; Done LE-101 is NOT recreated on disk!
                st_live1 = run_cli(root_l, "status", "--epic", "LE-10")
                self.assertEqual(st_live1.returncode, 0, st_live1.stderr)
                l1_out = json.loads(st_live1.stdout)
                self.assertEqual(l1_out["epic_summary"], "Live Reporting Epic")
                self.assertEqual(l1_out["total_stories"], 2)
                self.assertEqual(l1_out["completed_stories"], 1)
                self.assertEqual(l1_out["progress_pct"], 50.0)
                self.assertFalse((active_l / "LE-101.json").exists())

                # 2) Simulate Jira rejecting `parent = "LE-10"` (HTTP 400) -> falls back to `"Epic Link" = "LE-10"` discovering LE-102 as well!
                MockJiraHandler.reject_parent_jql_epics.add("LE-10")
                st_live2 = run_cli(root_l, "status", "--epic", "LE-10")
                self.assertEqual(st_live2.returncode, 0, st_live2.stderr)
                l2_out = json.loads(st_live2.stdout)
                self.assertEqual(l2_out["query_mode"], "epic_link")
                self.assertEqual(l2_out["total_stories"], 3)
                self.assertEqual({s["key"] for s in l2_out["stories"]}, {"LE-101", "LE-102", "LE-103"})
                self.assertFalse((active_l / "LE-101.json").exists())
                self.assertFalse((active_l / "LE-102.json").exists())
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_35_v150_spec_backfill_and_epic_decompose_doc_consistency(self):
        # 1. team-config-template.json includes epic_link_field
        cfg_tmpl = json.loads((SKILLS_DIR / "team-init" / "references" / "team-config-template.json").read_text(encoding="utf-8"))
        self.assertIn("epic_link_field", cfg_tmpl)

        # 2. team-spec SKILL.md & spec-method.md cover has_data clarification, Backfill Mode, Q-###, evidence discipline, and doc boundaries
        spec_skill = (SKILLS_DIR / "team-spec" / "SKILL.md").read_text(encoding="utf-8")
        spec_method = (SKILLS_DIR / "team-spec" / "references" / "spec-method.md").read_text(encoding="utf-8")
        for name, txt in (("team-spec/SKILL.md", spec_skill), ("team-spec/references/spec-method.md", spec_method)):
            self.assertIn("--backfill", txt, f"{name} missing --backfill")
            self.assertIn("--preserve-owner", txt, f"{name} missing --preserve-owner")
            self.assertIn("Q-###", txt, f"{name} missing Q-###")
            self.assertIn("material_backfilled", txt, f"{name} missing material_backfilled")
            self.assertIn("spec_pending_confirmation", txt, f"{name} missing spec_pending_confirmation")
            self.assertIn("analysis.md", txt, f"{name} missing analysis.md boundary")
            self.assertIn("persistence", txt.lower(), f"{name} missing has_data persistence API clarification")

        # 3. team-plan SKILL.md covers epic-decompose preview -> confirm -> apply and keeps Phase 1 platform-neutral
        plan_skill = (SKILLS_DIR / "team-plan" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("epic-decompose", plan_skill)
        self.assertIn("dry_run", plan_skill)
        self.assertIn("--apply", plan_skill)
        self.assertIn("run-workflow", plan_skill)

        # 4. team-next-step SKILL.md documents status --epic
        next_skill = (SKILLS_DIR / "team-next-step" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("status --epic", next_skill)
        self.assertIn("blocked_by_dependencies", next_skill)

        # 5. common-workflow-governance.md references epic-decompose, status --epic, and backfill
        gov_md = (SKILLS_DIR / "team-init" / "references" / "common-workflow-governance.md").read_text(encoding="utf-8")
        self.assertIn("epic-decompose", gov_md)
        self.assertIn("status [--epic", gov_md)
        self.assertIn("--backfill", gov_md)

    def test_36_batch4_round1_review_regressions(self):
        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()
        MockJiraHandler.reject_parent_on_create_epics.clear()
        MockJiraHandler.reject_parent_jql_epics.clear()
        MockJiraHandler.fail_create_story_titles.clear()
        MockJiraHandler.simulate_get_404_keys.clear()
        MockJiraHandler.simulate_put_404_keys.clear()
        MockJiraHandler.reject_customfield_on_create_epics.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()

        try:
            # --- Part A: Live Jira regressions (P1-1, P1-2, P1-3, P1-4, P1-5 live, P1-6, P2-1, P2-3, P2-6, P2-8) ---
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                roles = {
                    "pm": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice", "api_token": "tok-a"},
                    "developer": {"name": "Bob Lin", "email": "bob@team.io", "jira_account_id": "acc-bob", "api_token": "tok-b"},
                    "reviewer": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol", "api_token": "tok-c"},
                    "qa": {"name": "Carol Wu", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                    "release_manager": {"name": "Alice Chen", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                self.assertEqual(
                    run_cli(
                        root, "init",
                        "--project-key", "PRB",
                        "--jira-url", f"http://127.0.0.1:{jira_port}",
                        "--roles-json", json.dumps(roles),
                    ).returncode,
                    0,
                )

                # Seed PRB-1 as an approved, review-approved gated Story with candidate_commit & epic metadata
                MockJiraHandler.stored_issues["PRB-1"] = {
                    "key": "PRB-1",
                    "fields": {"summary": "Protected Lifecycle Story", "issuetype": {"name": "Story", "subtask": False}, "status": {"name": "In Progress"}},
                }
                active_dir = root / ".ai-workflow" / "active"
                active_dir.mkdir(parents=True, exist_ok=True)
                prb1_initial = {
                    "jira_key": "PRB-1",
                    "title": "Protected Lifecycle Story",
                    "scope_in": "Core scope",
                    "scope_out": "N/A",
                    "change_surface": {"has_ui": False, "has_api": False, "has_data": False},
                    "risk_level": "medium",
                    "acceptance_criteria": [{"id": "AC-001", "description": "Works"}],
                    "design": {"summary": "Initial"},
                    "spec_status": "approved",
                    "approved_by": "Alice Chen",
                    "approved_at": "2026-11-20T00:00:00+00:00",
                    "current_owner_role": "developer",
                    "jira_status": "In Progress",
                    "candidate_commit": "abc1234",
                    "qa_deploy_gate": {"status": "awaiting_qa_deploy", "commit": "abc1234"},
                    "epic_key": "PRB-EPIC",
                    "epic_decompose_id": "S1",
                    "wave": 2,
                    "depends_on": ["S0"],
                    "depends_on_keys": ["PRB-0"],
                    "shared_contracts": ["AuthContract"],
                    "tasks": [{"id": "TASK-001", "status": "completed"}],
                    "review": {"status": "approved", "reviewed_commit": "abc1234"},
                    "qa": {},
                    "bugs": [],
                }
                (active_dir / "PRB-1.json").write_text(json.dumps(prb1_initial, indent=2), encoding="utf-8")

                # P1-1: --backfill --status draft NEVER rolls back spec_status="approved" -> "draft"
                r_no_rb = run_cli(root, "sync-story", "--key", "PRB-1", "--backfill", "--status", "draft", "--risks", "Some risk")
                self.assertEqual(r_no_rb.returncode, 0, r_no_rb.stderr)
                self.assertEqual(json.loads(r_no_rb.stdout)["spec_status"], "approved")
                st_after = json.loads(run_cli(root, "status", "--key", "PRB-1").stdout)
                self.assertNotEqual(st_after.get("recommended_skill"), "team-spec")

                # P1-1: --preserve-owner --status draft also NEVER rolls back spec_status="approved"
                r_po_no_rb = run_cli(root, "sync-story", "--key", "PRB-1", "--preserve-owner", "--status", "draft", "--risks", "Note")
                self.assertEqual(r_po_no_rb.returncode, 0, r_po_no_rb.stderr)
                self.assertEqual(json.loads(r_po_no_rb.stdout)["spec_status"], "approved")

                # P1-1: --backfill --spec-json cannot mutate qa_deploy_gate, candidate_commit, spec_status, or epic metadata
                r_shield = run_cli(
                    root, "sync-story", "--key", "PRB-1", "--backfill",
                    "--spec-json", json.dumps({
                        "qa_deploy_gate": {"status": "deployed"},
                        "candidate_commit": "wiped",
                        "spec_status": "draft",
                        "epic_key": "EVIL-1",
                        "wave": 99,
                        "depends_on_keys": [],
                    }),
                )
                self.assertEqual(r_shield.returncode, 0, r_shield.stderr)
                prb1_disk = json.loads((active_dir / "PRB-1.json").read_text(encoding="utf-8"))
                self.assertEqual(prb1_disk["spec_status"], "approved")
                self.assertEqual(prb1_disk["candidate_commit"], "abc1234")
                self.assertEqual(prb1_disk["qa_deploy_gate"], {"status": "awaiting_qa_deploy", "commit": "abc1234"})
                self.assertEqual(prb1_disk["epic_key"], "PRB-EPIC")
                self.assertEqual(prb1_disk["wave"], 2)
                self.assertEqual(prb1_disk["depends_on_keys"], ["PRB-0"])

                # P1-1: Tracked draft PRB-2 rejects --backfill --spec-json '{"spec_status":"approved"}' with exit 1 & zero writes
                (active_dir / "PRB-2.json").write_text(json.dumps({
                    "jira_key": "PRB-2",
                    "title": "Draft Story",
                    "spec_status": "draft",
                    "acceptance_criteria": [],
                    "tasks": [],
                    "bugs": [],
                }), encoding="utf-8")
                MockJiraHandler.requests_log.clear()
                r_ban_spec = run_cli(root, "sync-story", "--key", "PRB-2", "--backfill", "--spec-json", '{"spec_status":"approved"}')
                self.assertNotEqual(r_ban_spec.returncode, 0)
                self.assertEqual(MockJiraHandler.requests_log, [])
                self.assertEqual(json.loads((active_dir / "PRB-2.json").read_text(encoding="utf-8"))["spec_status"], "draft")

                # P1-1: --backfill --spec-json cannot inject qa_deploy_gate into draft PRB-2
                r_no_inject = run_cli(root, "sync-story", "--key", "PRB-2", "--backfill", "--spec-json", '{"qa_deploy_gate":{"status":"deployed"}}')
                self.assertEqual(r_no_inject.returncode, 0, r_no_inject.stderr)
                self.assertNotIn("qa_deploy_gate", json.loads((active_dir / "PRB-2.json").read_text(encoding="utf-8")))

                # P1-R2-1: Nested spec.spec_status="approved" / "APPROVED" rejected with exit 1 & zero writes;
                # nested spec.spec_status="draft" stripped from story["spec"] and preserves top-level approved status
                for bad_nested in ('{"spec":{"spec_status":"approved"}}', '{"spec":{"spec_status":"APPROVED"}}'):
                    MockJiraHandler.requests_log.clear()
                    before_prb2 = (active_dir / "PRB-2.json").read_text(encoding="utf-8")
                    r_bad_nested = run_cli(root, "sync-story", "--key", "PRB-2", "--backfill", "--spec-json", bad_nested)
                    self.assertNotEqual(r_bad_nested.returncode, 0)
                    self.assertEqual(MockJiraHandler.requests_log, [])
                    self.assertEqual((active_dir / "PRB-2.json").read_text(encoding="utf-8"), before_prb2)

                r_nested_draft = run_cli(
                    root, "sync-story", "--key", "PRB-1", "--backfill",
                    "--spec-json", '{"spec":{"spec_status":"draft","risks":"Nested draft test"}}',
                )
                self.assertEqual(r_nested_draft.returncode, 0, r_nested_draft.stderr)
                prb1_after_nested = json.loads((active_dir / "PRB-1.json").read_text(encoding="utf-8"))
                self.assertEqual(prb1_after_nested["spec_status"], "approved")
                self.assertNotIn("spec_status", prb1_after_nested.get("spec") or {})
                self.assertEqual((prb1_after_nested.get("spec") or {}).get("risks"), "Nested draft test")

                # P2-6: Standalone --preserve-owner --status approved emits backfill_stage="spec_approved"
                (active_dir / "PRB-PO.json").write_text(json.dumps({
                    "jira_key": "PRB-PO",
                    "title": "Preserve Owner Approve",
                    "spec_status": "draft",
                    "current_owner_role": "developer",
                    "jira_status": "In Progress",
                    "acceptance_criteria": [{"id": "AC-001", "description": "Valid"}],
                    "tasks": [],
                    "bugs": [],
                }), encoding="utf-8")
                MockJiraHandler.stored_issues["PRB-PO"] = {
                    "key": "PRB-PO",
                    "fields": {"summary": "Preserve Owner Approve", "issuetype": {"name": "Story", "subtask": False}, "status": {"name": "In Progress"}},
                }
                r_po_app = run_cli(root, "sync-story", "--key", "PRB-PO", "--preserve-owner", "--status", "approved")
                self.assertEqual(r_po_app.returncode, 0, r_po_app.stderr)
                po_out = json.loads(r_po_app.stdout)
                self.assertFalse(po_out["backfill_mode"])
                self.assertEqual(po_out["spec_status"], "approved")
                self.assertEqual(po_out["backfill_stage"], "spec_approved")

                # P1-2: --backfill --create rejected cleanly with zero writes; non-existent Jira key HTTP 404 rejected cleanly with zero POST /issue
                MockJiraHandler.requests_log.clear()
                r_bf_create = run_cli(root, "sync-story", "--key", "PRB-4041", "--backfill", "--create", "--title", "Bad")
                self.assertNotEqual(r_bf_create.returncode, 0)
                self.assertEqual(MockJiraHandler.requests_log, [])

                MockJiraHandler.simulate_get_404_keys.add("PRB-4040")
                MockJiraHandler.simulate_put_404_keys.add("PRB-4040")
                MockJiraHandler.requests_log.clear()
                r_bf_404 = run_cli(root, "sync-story", "--key", "PRB-4040", "--backfill", "--title", "Typo Key")
                self.assertNotEqual(r_bf_404.returncode, 0)
                self.assertFalse(any(m == "POST" and p == "/rest/api/3/issue" for m, p, _, _ in MockJiraHandler.requests_log))
                self.assertFalse((active_dir / "PRB-4040.json").exists())

                # P1-3: Duplicate explicit Q-### IDs rejected, blank question doesn't reserve ID, --spec-json boolean normalization & dup rejection
                MockJiraHandler.requests_log.clear()
                r_dup_q = run_cli(
                    root, "sync-story", "--key", "PRB-1", "--backfill",
                    "--open-questions-json", json.dumps([
                        {"id": "Q-001", "question": "First", "blocking": True},
                        {"id": "q-001", "question": "Second", "blocking": True},
                    ]),
                )
                self.assertNotEqual(r_dup_q.returncode, 0)
                self.assertIn("duplicate", r_dup_q.stderr.lower())
                self.assertEqual(MockJiraHandler.requests_log, [])

                r_blank_q = run_cli(
                    root, "sync-story", "--key", "PRB-1", "--backfill",
                    "--open-questions-json", json.dumps([{"id": "Q-001", "question": "   "}, "Actual question"]),
                )
                self.assertEqual(r_blank_q.returncode, 0, r_blank_q.stderr)
                self.assertEqual(
                    json.loads((active_dir / "PRB-1.json").read_text(encoding="utf-8"))["spec"]["open_questions"][0]["id"],
                    "Q-001",
                )

                r_spec_oq = run_cli(
                    root, "sync-story", "--key", "PRB-1", "--backfill",
                    "--spec-json", json.dumps({"open_questions": [{"id": "Q-010", "question": "Via spec-json", "blocking": "false"}]}),
                )
                self.assertEqual(r_spec_oq.returncode, 0, r_spec_oq.stderr)
                spec_oq_out = json.loads(r_spec_oq.stdout)
                self.assertEqual(spec_oq_out["open_blocking_questions"], [])
                self.assertIs(
                    json.loads((active_dir / "PRB-1.json").read_text(encoding="utf-8"))["spec"]["open_questions"][0]["blocking"],
                    False,
                )

                r_nested_oq = run_cli(
                    root, "sync-story", "--key", "PRB-1", "--backfill",
                    "--spec-json", json.dumps({"spec": {"open_questions": [{"id": "Q-011", "question": "Nested", "blocking": "0"}]}}),
                )
                self.assertEqual(r_nested_oq.returncode, 0, r_nested_oq.stderr)
                self.assertEqual(json.loads(r_nested_oq.stdout)["open_blocking_questions"], [])

                r_spec_dup = run_cli(
                    root, "sync-story", "--key", "PRB-1", "--backfill",
                    "--spec-json", json.dumps({"open_questions": [{"id": "Q-010", "question": "A"}, {"id": "q-010", "question": "B"}]}),
                )
                self.assertNotEqual(r_spec_dup.returncode, 0)

                # P1-4: status --epic "" and status --key PRB-1 --epic "" both fail with exit 1
                self.assertNotEqual(run_cli(root, "status", "--epic", "").returncode, 0)
                self.assertNotEqual(run_cli(root, "status", "--key", "PRB-1", "--epic", "").returncode, 0)

                # P2-5: Non-finite & negative story_points rejected cleanly
                for bad_sp in ("nan", "inf", "-inf", -2):
                    r_bad_sp = run_cli(
                        root, "epic-decompose", "--epic", "PRB-EPIC",
                        "--stories-json", json.dumps([{"id": "SP1", "title": "Bad SP", "story_points": bad_sp}]),
                    )
                    self.assertNotEqual(r_bad_sp.returncode, 0, f"Expected failure for story_points={bad_sp!r}")

                # P2-8: Live epic-decompose --apply on HTTP 404 non-existent Epic fails fast with zero Stories created
                MockJiraHandler.simulate_get_404_keys.add("PRB-4044")
                MockJiraHandler.requests_log.clear()
                r_ep_404 = run_cli(
                    root, "epic-decompose", "--epic", "PRB-4044",
                    "--stories-json", json.dumps([{"id": "S1", "title": "Should Not Create"}]),
                    "--apply",
                )
                self.assertNotEqual(r_ep_404.returncode, 0)
                self.assertFalse(any(m == "POST" and p == "/rest/api/3/issue" for m, p, _, _ in MockJiraHandler.requests_log))

                # P2-3: Configured epic_link_field double-400 warning mentions both fields.parent and epic_link_field
                cfg_p = root / ".ai-workflow" / "team-config.json"
                cfg_d = json.loads(cfg_p.read_text(encoding="utf-8"))
                cfg_d["epic_link_field"] = "customfield_10014"
                cfg_p.write_text(json.dumps(cfg_d, indent=2) + "\n", encoding="utf-8")
                MockJiraHandler.stored_issues["PRB-D400"] = {
                    "key": "PRB-D400",
                    "fields": {"summary": "Double 400 Epic", "issuetype": {"name": "Epic"}, "status": {"name": "To Do"}},
                }
                MockJiraHandler.reject_parent_on_create_epics.add("PRB-D400")
                MockJiraHandler.reject_customfield_on_create_epics.add("PRB-D400")
                r_d400 = run_cli(
                    root, "epic-decompose", "--epic", "PRB-D400",
                    "--stories-json", json.dumps([{"id": "D1", "title": "Double 400 Story"}]),
                    "--apply",
                )
                self.assertEqual(r_d400.returncode, 0, r_d400.stderr)
                d400_out = json.loads(r_d400.stdout)
                self.assertEqual(d400_out["created_stories"][0]["epic_link_mode"], "unlinked")
                self.assertTrue(any("fields.parent" in w and "customfield_10014" in w for w in d400_out["warnings"]))

                # P1-5 live unlinked Done dependency resolution in status --epic
                MockJiraHandler.stored_issues["UL-10"] = {
                    "key": "UL-10",
                    "fields": {"summary": "Unlinked Dep Epic", "issuetype": {"name": "Epic"}, "status": {"name": "In Progress"}},
                }
                MockJiraHandler.stored_issues["UL-1"] = {
                    "key": "UL-1",
                    "fields": {"summary": "Closed Unlinked Upstream", "issuetype": {"name": "Story", "subtask": False}, "status": {"name": "Done"}},
                }
                MockJiraHandler.stored_issues["UL-2"] = {
                    "key": "UL-2",
                    "fields": {
                        "summary": "Downstream Story",
                        "issuetype": {"name": "Story", "subtask": False},
                        "status": {"name": "To Do"},
                        "parent": {"key": "UL-10"},
                    },
                }
                (active_dir / "UL-2.json").write_text(json.dumps({
                    "jira_key": "UL-2",
                    "epic_key": "UL-10",
                    "wave": 2,
                    "title": "Downstream Story",
                    "jira_status": "To Do",
                    "spec_status": "draft",
                    "depends_on_keys": ["UL-1"],
                    "tasks": [],
                    "bugs": [],
                }), encoding="utf-8")
                r_ul = run_cli(root, "status", "--epic", "UL-10")
                self.assertEqual(r_ul.returncode, 0, r_ul.stderr)
                ul_out = json.loads(r_ul.stdout)
                self.assertEqual(ul_out["stories"][0]["blocked_by_dependencies"], [])
                self.assertEqual(ul_out["blocked_stories"], 0)

                # P1-6 & P2-1: Cross-machine normal sync-story preserves epic metadata, and cross-machine epic-decompose --apply is idempotent
                MockJiraHandler.stored_issues["XM-10"] = {
                    "key": "XM-10",
                    "fields": {"summary": "Cross-Machine Epic", "issuetype": {"name": "Epic"}, "status": {"name": "In Progress"}},
                }
                xm_stories = [
                    {"id": "S1", "title": "XM Base", "acceptance_criteria": ["AC-001:Base ready"], "shared_contracts": ["Shared DTO"]},
                    {"id": "S2", "title": "XM Child", "acceptance_criteria": ["AC-001:Child ready"], "depends_on": ["S1"], "shared_contracts": ["Shared DTO"]},
                ]
                xm_app = run_cli(root, "epic-decompose", "--epic", "XM-10", "--stories-json", json.dumps(xm_stories), "--apply")
                self.assertEqual(xm_app.returncode, 0, xm_app.stderr)
                xm_created = json.loads(xm_app.stdout)["created_stories"]
                xm_k1, xm_k2 = xm_created[0]["jira_key"], xm_created[1]["jira_key"]

                with tempfile.TemporaryDirectory() as tmp_b:
                    root_b = Path(tmp_b)
                    self.assertEqual(
                        run_cli(
                            root_b, "init",
                            "--project-key", "PRB",
                            "--jira-url", f"http://127.0.0.1:{jira_port}",
                            "--roles-json", json.dumps(roles),
                        ).returncode,
                        0,
                    )
                    # P1-6: Machine B runs normal sync-story --key xm_k2 --title ... --ac ... --status approved without losing epic metadata
                    xm_sync_b = run_cli(
                        root_b, "sync-story",
                        "--key", xm_k2,
                        "--title", "Cross-Machine Refined",
                        "--ac", "AC-001:Refined AC",
                        "--status", "approved",
                    )
                    self.assertEqual(xm_sync_b.returncode, 0, xm_sync_b.stderr)
                    xm_k2_disk = json.loads((root_b / ".ai-workflow" / "active" / f"{xm_k2}.json").read_text(encoding="utf-8"))
                    self.assertEqual(xm_k2_disk["epic_key"], "XM-10")
                    self.assertEqual(xm_k2_disk["epic_decompose_id"], "S2")
                    self.assertEqual(xm_k2_disk["wave"], 2)
                    self.assertEqual(xm_k2_disk["depends_on_keys"], [xm_k1])
                    self.assertEqual(xm_k2_disk["shared_contracts"], ["Shared DTO"])

                    # P2-1: Machine B (which lacks xm_k1 locally) re-runs epic-decompose --apply with S1, S2, and new S3 depending on S1
                    xm_stories_ext = xm_stories + [
                        {"id": "S3", "title": "XM Wave 2 Extension", "depends_on": ["S1"]},
                    ]
                    xm_re = run_cli(root_b, "epic-decompose", "--epic", "XM-10", "--stories-json", json.dumps(xm_stories_ext), "--apply")
                    self.assertEqual(xm_re.returncode, 0, xm_re.stderr)
                    xm_re_out = json.loads(xm_re.stdout)
                    self.assertEqual([s["id"] for s in xm_re_out["skipped_stories"]], ["S1", "S2"])
                    self.assertEqual(xm_re_out["total_created"], 1)
                    self.assertEqual(xm_re_out["created_stories"][0]["id"], "S3")
                    self.assertEqual(xm_re_out["created_stories"][0]["depends_on_keys"], [xm_k1])

            # --- Part B: Offline & Secondary Git Worktree regressions (P1-5 localized todo + close-story deadlock, P2-4, P2-7) ---
            with tempfile.TemporaryDirectory() as tmp_off:
                root_off = Path(tmp_off)
                self.assertEqual(
                    run_cli(
                        root_off, "init",
                        "--project-key", "BS",
                        "--status-mapping-json", json.dumps({"todo": "待辦"}),
                        "--provenance-audit-mode", "off",
                    ).returncode,
                    0,
                )
                bs_stories = [
                    {"id": "S1", "title": "Offline Wave 1", "acceptance_criteria": ["AC-001:First"]},
                    {"id": "S2", "title": "Offline Wave 2", "acceptance_criteria": ["AC-001:Second"], "depends_on": ["S1"]},
                ]
                self.assertEqual(run_cli(root_off, "epic-decompose", "--epic", "BS-10", "--stories-json", json.dumps(bs_stories), "--apply").returncode, 0)
                # Localized status_mapping.todo = "待辦" -> ready_to_start_stories
                s1_p = root_off / ".ai-workflow" / "active" / "BS-10-S1.json"
                s1_d = json.loads(s1_p.read_text(encoding="utf-8"))
                s1_d["jira_status"] = "待辦"
                s1_p.write_text(json.dumps(s1_d, indent=2), encoding="utf-8")
                st_loc = json.loads(run_cli(root_off, "status", "--epic", "BS-10").stdout)
                self.assertEqual(st_loc["ready_to_start_stories"], 1)
                self.assertEqual(st_loc["in_progress_stories"], 0)
                self.assertEqual(st_loc["blocked_stories"], 1)

                # Close BS-10-S1 through full lifecycle -> BS-10-S2 becomes unblocked via resolved_depends_on_keys!
                self.assertEqual(run_cli(root_off, "sync-story", "--key", "BS-10-S1", "--status", "approved").returncode, 0)
                self.assertEqual(run_cli(root_off, "complete-task", "--key", "BS-10-S1", "--task-id", "TASK-001", "--commit", "c1", "--files", "a.py", "--test-result", "ok").returncode, 0)
                self.assertEqual(run_cli(root_off, "handoff", "--key", "BS-10-S1", "--review-status", "approved", "--commit", "c1").returncode, 0)
                self.assertEqual(run_cli(root_off, "record-qa", "--key", "BS-10-S1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Ok").returncode, 0)
                self.assertEqual(run_cli(root_off, "record-qa", "--key", "BS-10-S1", "--mode", "verify", "--commit", "c1", "--all-pass").returncode, 0)
                cl_res = run_cli(root_off, "close-story", "--key", "BS-10-S1", "--commit", "c1")
                self.assertEqual(cl_res.returncode, 0, cl_res.stderr)
                self.assertIn("BS-10-S2", json.loads(cl_res.stdout)["resolved_dependent_stories"])

                s2_disk = json.loads((root_off / ".ai-workflow" / "active" / "BS-10-S2.json").read_text(encoding="utf-8"))
                self.assertEqual(s2_disk["resolved_depends_on_keys"], ["BS-10-S1"])
                st_unblocked = json.loads(run_cli(root_off, "status", "--epic", "BS-10").stdout)
                self.assertEqual(st_unblocked["blocked_stories"], 0)
                self.assertEqual(st_unblocked["ready_to_start_stories"], 1)
                self.assertEqual(st_unblocked["stories"][0]["blocked_by_dependencies"], [])
                self.assertEqual(st_unblocked["next_recommended_story"]["key"], "BS-10-S2")

            # P2-4: gated_by_deploy awaiting_qa_deploy bucket alignment (in_progress while team-qa plan is actionable; blocked once TCs planned)
            with tempfile.TemporaryDirectory() as tmp_p24:
                root_p24 = Path(tmp_p24)
                self.assertEqual(run_cli(root_p24, "init", "--project-key", "GD", "--qa-handoff-mode", "gated_by_deploy", "--status-dev-done", "Dev Completed").returncode, 0)
                self.assertEqual(run_cli(root_p24, "sync-story", "--key", "GD-1", "--title", "Gated Story", "--ac", "AC-001:Ok", "--status", "approved").returncode, 0)
                gd_p = root_p24 / ".ai-workflow" / "active" / "GD-1.json"
                gd_d = json.loads(gd_p.read_text(encoding="utf-8"))
                gd_d["epic_key"] = "GD-10"
                gd_p.write_text(json.dumps(gd_d, indent=2), encoding="utf-8")
                self.assertEqual(run_cli(root_p24, "complete-task", "--key", "GD-1", "--task-id", "TASK-001", "--commit", "g1", "--files", "g.py", "--test-result", "ok").returncode, 0)
                self.assertEqual(run_cli(root_p24, "handoff", "--key", "GD-1", "--review-status", "approved", "--commit", "g1").returncode, 0)

                st_plan_actionable = json.loads(run_cli(root_p24, "status", "--epic", "GD-10").stdout)
                self.assertEqual(st_plan_actionable["in_progress_stories"], 1)
                self.assertEqual(st_plan_actionable["blocked_stories"], 0)
                self.assertEqual(st_plan_actionable["next_recommended_story"]["recommended_skill"], "team-qa")
                self.assertEqual(st_plan_actionable["next_recommended_story"]["recommended_mode"], "plan")

                self.assertEqual(run_cli(root_p24, "record-qa", "--key", "GD-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Check").returncode, 0)
                st_deploy_blocked = json.loads(run_cli(root_p24, "status", "--epic", "GD-10").stdout)
                self.assertEqual(st_deploy_blocked["blocked_stories"], 1)
                self.assertEqual(st_deploy_blocked["in_progress_stories"], 0)
                self.assertIsNone(st_deploy_blocked["next_recommended_story"])

            # P2-7: Secondary Git worktree merges missing Epic stories from main worktree
            with tempfile.TemporaryDirectory() as tmp_wt_base:
                base_p = Path(tmp_wt_base)
                main_repo = base_p / "main_repo"
                wt_repo = base_p / "sec_wt"
                main_repo.mkdir()
                subprocess.run(["git", "init"], cwd=str(main_repo), check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "t@team.io"], cwd=str(main_repo), check=True, capture_output=True)
                subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(main_repo), check=True, capture_output=True)
                (main_repo / "README.md").write_text("# WT\n", encoding="utf-8")
                self.assertEqual(run_cli(main_repo, "init", "--project-key", "WT").returncode, 0)
                subprocess.run(["git", "add", "."], cwd=str(main_repo), check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "init"], cwd=str(main_repo), check=True, capture_output=True)
                subprocess.run(["git", "worktree", "add", "-b", "feat/sec", str(wt_repo)], cwd=str(main_repo), check=True, capture_output=True)
                try:
                    main_active = main_repo / ".ai-workflow" / "active"
                    main_active.mkdir(parents=True, exist_ok=True)
                    (main_active / "WT-10-S1.json").write_text(json.dumps({
                        "jira_key": "WT-10-S1", "epic_key": "WT-10", "wave": 1, "title": "S1", "jira_status": "To Do", "spec_status": "draft", "tasks": [], "bugs": [],
                    }), encoding="utf-8")
                    (main_active / "WT-10-S2.json").write_text(json.dumps({
                        "jira_key": "WT-10-S2", "epic_key": "WT-10", "wave": 2, "title": "S2", "jira_status": "To Do", "spec_status": "draft", "tasks": [], "bugs": [],
                    }), encoding="utf-8")
                    wt_active = wt_repo / ".ai-workflow" / "active"
                    wt_active.mkdir(parents=True, exist_ok=True)
                    (wt_active / "WT-10-S1.json").write_text((main_active / "WT-10-S1.json").read_text(encoding="utf-8"), encoding="utf-8")

                    st_wt = json.loads(run_cli(wt_repo, "status", "--epic", "WT-10").stdout)
                    self.assertEqual(st_wt["total_stories"], 2)
                    self.assertEqual({s["key"] for s in st_wt["stories"]}, {"WT-10-S1", "WT-10-S2"})
                finally:
                    subprocess.run(["git", "worktree", "remove", "--force", str(wt_repo)], cwd=str(main_repo), capture_output=True)
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_37_v160_ci_delivery_pipeline_phase_a(self):
        """
        v1.6.0 Phase A end-to-end & unit verification:
        1. delivery_pipeline schema & sanitization (malformed inputs, unsafe git refs, strict bool 'false').
        2. Pure-stdlib CI auto-detection (.gitlab-ci.yml, .github/workflows/*.yml, binary/corrupt resilience, init flags).
        3. integration_branch base-branch resolution (_resolve_base_branch & status exposure).
        4. promote-qa --deploy-ref/--environment -> record-qa --mode verify -> stale reset on new commit, ADF validity, qa_deploy_hint.
        5. Non-blocking release_checklist_reminders on status & close-story (never blocks on UAT).
        6. Doc & template consistency + 100% backward compatibility when delivery_pipeline is unconfigured.
        """
        mod = self._load_jira_sync_module()

        def _assert_valid_adf(adf_doc):
            self.assertIsInstance(adf_doc, dict)
            self.assertEqual(adf_doc.get("type"), "doc")
            def _walk(node):
                if isinstance(node, dict):
                    if node.get("type") == "text":
                        self.assertTrue(len(node.get("text") or "") > 0, f"Empty ADF text node: {node}")
                        mark_types = {m.get("type") for m in (node.get("marks") or []) if isinstance(m, dict)}
                        if "code" in mark_types:
                            self.assertEqual(mark_types, {"code"}, f"Illegal combined ADF code marks: {node}")
                    for v in node.values():
                        _walk(v)
                elif isinstance(node, list):
                    for item in node:
                        _walk(item)
            _walk(adf_doc)

        # 1. Template & _sanitize_delivery_pipeline
        cfg_tmpl = json.loads((SKILLS_DIR / "team-init" / "references" / "team-config-template.json").read_text(encoding="utf-8"))
        self.assertEqual(
            cfg_tmpl["delivery_pipeline"],
            {"integration_branch": "", "production_branch": "", "environments": {}, "jira_deployment_sync": False},
        )
        for bad_raw in (None, "bad", 123, ["x"]):
            self.assertEqual(
                mod._sanitize_delivery_pipeline(bad_raw),
                {"integration_branch": "", "production_branch": "", "environments": {}, "jira_deployment_sync": False},
            )
        for falsy_str in ("false", "FALSE", "0", "no", "off", "", False, None, []):
            self.assertIs(mod._sanitize_delivery_pipeline({"jira_deployment_sync": falsy_str})["jira_deployment_sync"], False)
        for truthy_val in (True, "true", "TRUE", "1", "yes", 1):
            self.assertIs(mod._sanitize_delivery_pipeline({"jira_deployment_sync": truthy_val})["jira_deployment_sync"], True)

        san = mod._sanitize_delivery_pipeline({
            "integration_branch": "  release  ",
            "production_branch": "bad branch; rm -rf /",
            "jira_deployment_sync": "false",
            "environments": {
                "QA": {"trigger": "TAG", "ref": None, "prefix": " qa- ", "deployment_tier": "Testing"},
                "bad env": {"trigger": "branch", "ref": "qa", "prefix": "", "deployment_tier": "testing"},
                "uat": {"trigger": "webhook", "ref": "../evil", "prefix": None, "deployment_tier": "staging"},
                "dev": "not-a-dict",
            },
        })
        self.assertEqual(san["integration_branch"], "release")
        self.assertEqual(san["production_branch"], "")
        self.assertIs(san["jira_deployment_sync"], False)
        self.assertIn("qa", san["environments"])
        self.assertEqual(san["environments"]["qa"], {"trigger": "tag", "ref": "", "prefix": "qa-", "deployment_tier": "testing"})
        self.assertNotIn("bad env", san["environments"])
        self.assertNotIn("dev", san["environments"])
        self.assertEqual(san["environments"]["uat"], {"trigger": "", "ref": "", "prefix": "", "deployment_tier": "staging"})
        # Never store literal "None"
        self.assertNotIn("None", json.dumps(san))

        # 2. Pure-stdlib CI auto-detection (GitLab CI, GitHub Actions, binary/corrupt files)
        with tempfile.TemporaryDirectory() as tmp_gl:
            gl_root = Path(tmp_gl)
            (gl_root / ".gitlab-ci.yml").write_text(
                "\n".join([
                    "stages: [lint, test, deploy]",
                    "lint_job:",
                    "  stage: lint",
                    "  script:",
                    "    - swiftlint",
                    "    - fastlane test",
                    "    - swiftlint",
                    "deploy_dev:",
                    "  stage: deploy",
                    "  environment:",
                    "    name: dev",
                    "    deployment_tier: development",
                    "  rules:",
                    "    - if: '$CI_COMMIT_BRANCH == \"release\"'",
                    "  script:",
                    "    - npm run buildios",
                    "deploy_qa:",
                    "  stage: deploy",
                    "  environment:",
                    "    name: qa",
                    "    deployment_tier: testing",
                    "  rules:",
                    "    - if: '$CI_COMMIT_TAG =~ /^qa-.*/'",
                    "  script:",
                    "    - pytest -q",
                    "deploy_uat:",
                    "  stage: deploy",
                    "  environment:",
                    "    name: uat",
                    "    deployment_tier: staging",
                    "  rules:",
                    "    - if: '$CI_COMMIT_TAG =~ /^uat-/'",
                    "  script:",
                    "    - echo deploy uat",
                    "deploy_prod:",
                    "  stage: deploy",
                    "  environment:",
                    "    name: prod",
                    "    deployment_tier: production",
                    "  rules:",
                    "    - if: '$CI_COMMIT_BRANCH == \"main\"'",
                    "  script:",
                    "    - echo deploy prod",
                ]) + "\n",
                encoding="utf-8",
            )
            insp = json.loads(run_cli(gl_root, "init", "--inspect").stdout)
            self.assertEqual(insp["suggested_qa_handoff_mode"], "gated_by_deploy")
            dp_gl = insp["detected_delivery_pipeline"]
            self.assertEqual(dp_gl["integration_branch"], "release")
            self.assertEqual(dp_gl["production_branch"], "main")
            self.assertTrue(dp_gl["jira_deployment_sync"])
            self.assertEqual(dp_gl["environments"]["qa"]["trigger"], "tag")
            self.assertEqual(dp_gl["environments"]["qa"]["prefix"], "qa-")
            self.assertEqual(dp_gl["environments"]["qa"]["deployment_tier"], "testing")
            self.assertEqual(dp_gl["environments"]["uat"]["prefix"], "uat-")
            self.assertEqual(dp_gl["environments"]["uat"]["deployment_tier"], "staging")
            self.assertEqual(insp["detected_ci_checks"], ["swiftlint", "fastlane test", "npm run buildios", "pytest -q"])

            # init with explicit delivery_pipeline flags persists cleanly
            r_init_gl = run_cli(
                gl_root, "init",
                "--project-key", "CI",
                "--qa-handoff-mode", "gated_by_deploy",
                "--status-dev-done", "Dev Completed",
                "--integration-branch", "release",
                "--production-branch", "main",
                "--qa-tag-prefix", "qa-",
                "--uat-tag-prefix", "uat-",
                "--jira-deployment-sync",
            )
            self.assertEqual(r_init_gl.returncode, 0, r_init_gl.stderr)
            init_gl_out = json.loads(r_init_gl.stdout)
            self.assertEqual(init_gl_out["suggested_qa_handoff_mode"], "gated_by_deploy")
            self.assertEqual(init_gl_out["delivery_pipeline"]["integration_branch"], "release")
            self.assertEqual(init_gl_out["delivery_pipeline"]["production_branch"], "main")
            self.assertTrue(init_gl_out["delivery_pipeline"]["jira_deployment_sync"])
            self.assertEqual(init_gl_out["delivery_pipeline"]["environments"]["qa"]["prefix"], "qa-")
            self.assertEqual(init_gl_out["delivery_pipeline"]["environments"]["uat"]["prefix"], "uat-")
            disk_cfg = json.loads((gl_root / ".ai-workflow" / "team-config.json").read_text(encoding="utf-8"))
            self.assertEqual(disk_cfg["delivery_pipeline"]["integration_branch"], "release")
            self.assertTrue(disk_cfg["delivery_pipeline"]["jira_deployment_sync"])

            # Explicit --jira-deployment-sync false sets False
            r_sync_false = run_cli(gl_root, "init", "--jira-deployment-sync", "false")
            self.assertEqual(r_sync_false.returncode, 0, r_sync_false.stderr)
            self.assertIs(json.loads(r_sync_false.stdout)["delivery_pipeline"]["jira_deployment_sync"], False)

        with tempfile.TemporaryDirectory() as tmp_gh:
            gh_root = Path(tmp_gh)
            wf_dir = gh_root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "deploy.yml").write_text(
                "\n".join([
                    "name: Deploy",
                    "on:",
                    "  push:",
                    "    branches: [develop]",
                    "    tags:",
                    "      - 'qa-*'",
                    "      - 'uat-*'",
                    "jobs:",
                    "  test_and_deploy:",
                    "    runs-on: ubuntu-latest",
                    "    environment: development",
                    "    steps:",
                    "      - run: npm test",
                    "      - run: |",
                    "          pytest",
                    "          npm run lint",
                ]) + "\n",
                encoding="utf-8",
            )
            (wf_dir / "prod.yaml").write_text(
                "\n".join([
                    "on:",
                    "  push:",
                    "    branches: [main]",
                    "jobs:",
                    "  prod:",
                    "    environment:",
                    "      name: production",
                ]) + "\n",
                encoding="utf-8",
            )
            # Binary / invalid UTF-8 file must never crash detector
            (wf_dir / "corrupt.yml").write_bytes(b"\x80\x81\x00\xffbinary-garbage")
            gh_det = mod._detect_ci_delivery_pipeline(gh_root)
            self.assertEqual(gh_det["suggested_qa_handoff_mode"], "gated_by_deploy")
            self.assertEqual(gh_det["delivery_pipeline"]["integration_branch"], "develop")
            self.assertEqual(gh_det["delivery_pipeline"]["production_branch"], "main")
            self.assertEqual(gh_det["delivery_pipeline"]["environments"]["qa"]["prefix"], "qa-")
            self.assertEqual(gh_det["delivery_pipeline"]["environments"]["uat"]["prefix"], "uat-")
            self.assertEqual(gh_det["detected_ci_checks"], ["npm test", "pytest", "npm run lint"])

        with tempfile.TemporaryDirectory() as tmp_corrupt:
            corrupt_root = Path(tmp_corrupt)
            (corrupt_root / ".gitlab-ci.yml").write_bytes(b"\x00\xff\xfeinvalid")
            corrupt_det = mod._detect_ci_delivery_pipeline(corrupt_root)
            self.assertEqual(corrupt_det["suggested_qa_handoff_mode"], "immediate")
            self.assertEqual(corrupt_det["detected_ci_checks"], [])

        # 3. _resolve_base_branch in real git repo + graceful fallback + status exposure
        with tempfile.TemporaryDirectory() as tmp_git:
            git_root = Path(tmp_git)
            subprocess.run(["git", "init", "-b", "main"], cwd=str(git_root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@team.io"], cwd=str(git_root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(git_root), check=True, capture_output=True)
            (git_root / "app.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=str(git_root), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=str(git_root), check=True, capture_output=True)

            # Unconfigured -> main
            self.assertEqual(mod._resolve_base_branch(git_root, {}), "main")
            # Configured to missing branch -> graceful fallback to main
            self.assertEqual(mod._resolve_base_branch(git_root, {"delivery_pipeline": {"integration_branch": "release"}}), "main")
            # Create release branch -> resolves to release
            subprocess.run(["git", "checkout", "-b", "release"], cwd=str(git_root), check=True, capture_output=True)
            self.assertEqual(mod._resolve_base_branch(git_root, {"delivery_pipeline": {"integration_branch": "release"}}), "release")

            # Verify status omits integration_branch when unconfigured, exposes when configured
            self.assertEqual(run_cli(git_root, "init", "--project-key", "BR").returncode, 0)
            st_unconf = json.loads(run_cli(git_root, "status").stdout)
            self.assertNotIn("integration_branch", st_unconf)
            self.assertNotIn("base_branch", st_unconf)

            self.assertEqual(run_cli(git_root, "init", "--integration-branch", "release").returncode, 0)
            st_conf = json.loads(run_cli(git_root, "status").stdout)
            self.assertEqual(st_conf["integration_branch"], "release")
            self.assertEqual(st_conf["base_branch"], "release")

        # 4 & 5. Live Mock Jira E2E: promote-qa --deploy-ref/--environment -> record-qa --mode verify ->
        #        stale reset on commit2 -> qa_deploy_hint (tag & branch) -> non-blocking release_checklist_reminders
        MockJiraHandler.requests_log = []
        MockJiraHandler.stored_issues = {}
        MockJiraHandler.stored_properties = {}
        MockJiraHandler.stored_attachments = {}
        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_address[1]
        threading.Thread(target=jira_srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp_live:
                root = Path(tmp_live)
                roles = {
                    "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "tech_lead": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acc-bob"},
                    "reviewer": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "qa": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                    "release_manager": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }
                self.assertEqual(
                    run_cli(
                        root, "init",
                        "--project-key", "DP",
                        "--jira-url", f"http://127.0.0.1:{jira_port}",
                        "--jira-email", "alice@team.io",
                        "--jira-token", "tok-alice",
                        "--roles-json", json.dumps(roles),
                        "--qa-handoff-mode", "gated_by_deploy",
                        "--status-dev-done", "Dev Completed",
                        "--integration-branch", "release",
                        "--production-branch", "main",
                        "--qa-tag-prefix", "qa-",
                        "--uat-tag-prefix", "uat-",
                        "--jira-deployment-sync",
                        "--provenance-audit-mode", "off",
                    ).returncode,
                    0,
                )
                MockJiraHandler.stored_issues["DP-1"] = {
                    "key": "DP-1",
                    "fields": {"summary": "Pipeline Story", "issuetype": {"name": "Story", "subtask": False}, "status": {"name": "To Do"}},
                }
                self.assertEqual(run_cli(root, "sync-story", "--key", "DP-1", "--title", "Pipeline Story", "--ac", "AC-001:Works", "--status", "approved").returncode, 0)
                self.assertEqual(run_cli(root, "complete-task", "--key", "DP-1", "--task-id", "TASK-001", "--commit", "c1", "--files", "a.py", "--test-result", "ok").returncode, 0)
                self.assertEqual(run_cli(root, "handoff", "--key", "DP-1", "--review-status", "approved", "--commit", "c1").returncode, 0)
                self.assertEqual(run_cli(root, "record-qa", "--key", "DP-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Verify").returncode, 0)

                # Tag-based qa_deploy_hint
                st_wait = json.loads(run_cli(root, "status", "--key", "DP-1").stdout)
                self.assertTrue(st_wait["awaiting_qa_deploy"])
                self.assertEqual(st_wait["integration_branch"], "release")
                self.assertIn("qa_deploy_hint", st_wait)
                self.assertIn("release", st_wait["qa_deploy_hint"])
                self.assertIn("qa-*", st_wait["qa_deploy_hint"])
                self.assertIn("deployment_tier: testing", st_wait["qa_deploy_hint"])
                self.assertFalse(st_wait["can_auto_chain_next_station"])
                self.assertTrue(st_wait["requires_user_confirmation_for_next_station"])

                # Branch-based qa_deploy_hint check via evaluate_story_next_step
                cfg_branch_qa = mod.load_team_config(root)
                cfg_branch_qa["delivery_pipeline"]["environments"]["qa"] = {"trigger": "branch", "ref": "qa", "prefix": "", "deployment_tier": "testing"}
                st_disk_wait = json.loads((root / ".ai-workflow" / "active" / "DP-1.json").read_text(encoding="utf-8"))
                rec_branch_qa = mod.evaluate_story_next_step(st_disk_wait, cfg_branch_qa, True)
                self.assertIn("`qa` branch", rec_branch_qa["qa_deploy_hint"])
                self.assertFalse(rec_branch_qa["can_auto_chain_next_station"])
                self.assertTrue(rec_branch_qa["requires_user_confirmation_for_next_station"])

                # Backward compat: no delivery_pipeline -> NO qa_deploy_hint
                cfg_no_dp = mod.load_team_config(root)
                cfg_no_dp["delivery_pipeline"] = mod._sanitize_delivery_pipeline(None)
                rec_no_dp = mod.evaluate_story_next_step(st_disk_wait, cfg_no_dp, True)
                self.assertNotIn("qa_deploy_hint", rec_no_dp)

                # promote-qa with --deploy-ref & --environment
                MockJiraHandler.requests_log.clear()
                prom = run_cli(
                    root, "promote-qa",
                    "--key", "DP-1",
                    "--deploy-ref", "qa-2026-11-23-b42",
                    "--environment", "qa-testflight",
                )
                self.assertEqual(prom.returncode, 0, prom.stderr)
                prom_out = json.loads(prom.stdout)
                self.assertEqual(prom_out["deploy_ref"], "qa-2026-11-23-b42")
                self.assertEqual(prom_out["environment"], "qa-testflight")
                self.assertEqual(prom_out["qa_deploy_gate"]["deploy_ref"], "qa-2026-11-23-b42")
                self.assertEqual(prom_out["qa_deploy_gate"]["environment"], "qa-testflight")
                prom_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(prom_comments), 1)
                _assert_valid_adf(prom_comments[0][2]["body"])
                self.assertIn("qa-2026-11-23-b42", json.dumps(prom_comments[0][2], ensure_ascii=False))

                # Idempotent promote-qa without flags preserves deploy_ref/environment with ZERO Jira comments/transitions
                MockJiraHandler.requests_log.clear()
                prom_idem_keep = json.loads(run_cli(root, "promote-qa", "--key", "DP-1").stdout)
                self.assertTrue(prom_idem_keep["already_deployed"])
                self.assertEqual(prom_idem_keep["deploy_ref"], "qa-2026-11-23-b42")
                self.assertEqual(prom_idem_keep["environment"], "qa-testflight")
                self.assertEqual([r for r in MockJiraHandler.requests_log if r[0] == "POST" and (r[1].endswith("/comment") or r[1].endswith("/transitions"))], [])

                # Idempotent promote-qa WITH new metadata updates deploy_ref/environment cleanly with ZERO Jira comments/transitions
                MockJiraHandler.requests_log.clear()
                prom_idem_upd = json.loads(run_cli(root, "promote-qa", "--key", "DP-1", "--deploy-ref", "qa-2026-11-23-b43", "--environment", "qa-staging").stdout)
                self.assertTrue(prom_idem_upd["already_deployed"])
                self.assertEqual(prom_idem_upd["deploy_ref"], "qa-2026-11-23-b43")
                self.assertEqual(prom_idem_upd["environment"], "qa-staging")
                self.assertEqual([r for r in MockJiraHandler.requests_log if r[0] == "POST" and (r[1].endswith("/comment") or r[1].endswith("/transitions"))], [])

                # record-qa --mode verify inherits deploy_ref from qa_deploy_gate and renders valid ADF
                MockJiraHandler.requests_log.clear()
                ver1 = run_cli(root, "record-qa", "--key", "DP-1", "--mode", "verify", "--commit", "c1", "--all-pass")
                self.assertEqual(ver1.returncode, 0, ver1.stderr)
                ver1_out = json.loads(ver1.stdout)
                self.assertEqual(ver1_out["verified_deploy_ref"], "qa-2026-11-23-b43")
                st_after_v1 = json.loads((root / ".ai-workflow" / "active" / "DP-1.json").read_text(encoding="utf-8"))
                self.assertEqual(st_after_v1["qa"]["verified_deploy_ref"], "qa-2026-11-23-b43")
                qa_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(qa_comments), 1)
                _assert_valid_adf(qa_comments[0][2]["body"])
                self.assertIn("qa-2026-11-23-b43", json.dumps(qa_comments[0][2], ensure_ascii=False))

                # Explicit --deploy-ref override on record-qa --mode verify
                ver1_ovr = json.loads(run_cli(root, "record-qa", "--key", "DP-1", "--mode", "verify", "--commit", "c1", "--deploy-ref", "qa-explicit-99", "--all-pass").stdout)
                self.assertEqual(ver1_ovr["verified_deploy_ref"], "qa-explicit-99")

                # New commit c2 fix cycle -> handoff MUST clear stale deploy_ref & environment!
                self.assertEqual(run_cli(root, "complete-task", "--key", "DP-1", "--task-id", "TASK-001", "--commit", "c2", "--files", "a.py", "--test-result", "ok2").returncode, 0)
                self.assertEqual(run_cli(root, "handoff", "--key", "DP-1", "--review-status", "approved", "--commit", "c2").returncode, 0)
                st_c2_disk = json.loads((root / ".ai-workflow" / "active" / "DP-1.json").read_text(encoding="utf-8"))
                self.assertEqual(st_c2_disk["qa_deploy_gate"]["deploy_ref"], "")
                self.assertEqual(st_c2_disk["qa_deploy_gate"]["environment"], "")

                # Promote c2 without --deploy-ref and verify c2 without --deploy-ref -> stale ref NEVER leaks!
                prom_c2 = json.loads(run_cli(root, "promote-qa", "--key", "DP-1").stdout)
                self.assertEqual(prom_c2["deploy_ref"], "")
                self.assertEqual(prom_c2["environment"], "")
                ver_c2 = json.loads(run_cli(root, "record-qa", "--key", "DP-1", "--mode", "verify", "--commit", "c2", "--all-pass").stdout)
                self.assertEqual(ver_c2["verified_deploy_ref"], "")
                st_c2_ver = json.loads((root / ".ai-workflow" / "active" / "DP-1.json").read_text(encoding="utf-8"))
                self.assertNotIn("verified_deploy_ref", st_c2_ver["qa"])

                # Station 4 status & close-story emit non-blocking release_checklist_reminders (UAT + distinct production_branch)
                st_rel = json.loads(run_cli(root, "status", "--key", "DP-1").stdout)
                self.assertEqual(st_rel["recommended_skill"], "team-release")
                self.assertEqual(len(st_rel["release_checklist_reminders"]), 2)
                self.assertTrue(any("uat-" in r for r in st_rel["release_checklist_reminders"]))
                self.assertTrue(any("main" in r and "release" in r for r in st_rel["release_checklist_reminders"]))

                # close-story MUST NOT block on UAT and returns release_checklist_reminders
                cl_res = run_cli(root, "close-story", "--key", "DP-1", "--commit", "c2", "--version", "v1.6.0")
                self.assertEqual(cl_res.returncode, 0, cl_res.stderr)
                cl_out = json.loads(cl_res.stdout)
                self.assertTrue(cl_out["ok"])
                self.assertEqual(len(cl_out["release_checklist_reminders"]), 2)
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

        # 6. Offline backward compatibility (no delivery_pipeline -> empty release_checklist_reminders)
        with tempfile.TemporaryDirectory() as tmp_bc:
            bc_root = Path(tmp_bc)
            self.assertEqual(run_cli(bc_root, "init", "--project-key", "BC", "--provenance-audit-mode", "off").returncode, 0)
            self.assertEqual(run_cli(bc_root, "sync-story", "--key", "BC-1", "--title", "Single Branch", "--ac", "AC-001:Ok", "--status", "approved").returncode, 0)
            self.assertEqual(run_cli(bc_root, "complete-task", "--key", "BC-1", "--task-id", "TASK-001", "--commit", "b1", "--files", "b.py", "--test-result", "ok").returncode, 0)
            self.assertEqual(run_cli(bc_root, "handoff", "--key", "BC-1", "--review-status", "approved", "--commit", "b1").returncode, 0)
            self.assertEqual(run_cli(bc_root, "record-qa", "--key", "BC-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Ok").returncode, 0)
            self.assertEqual(run_cli(bc_root, "record-qa", "--key", "BC-1", "--mode", "verify", "--commit", "b1", "--all-pass").returncode, 0)
            st_bc = json.loads(run_cli(bc_root, "status", "--key", "BC-1").stdout)
            self.assertEqual(st_bc["release_checklist_reminders"], [])
            self.assertNotIn("integration_branch", st_bc)
            cl_bc = json.loads(run_cli(bc_root, "close-story", "--key", "BC-1", "--commit", "b1").stdout)
            self.assertTrue(cl_bc["ok"])
            self.assertEqual(cl_bc["release_checklist_reminders"], [])

        # 7. Skill doc consistency checks for v1.6.0 Phase A
        self.assertIn("delivery_pipeline", (SKILLS_DIR / "team-init" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("suggested_qa_handoff_mode", (SKILLS_DIR / "team-init" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("integration_branch", (SKILLS_DIR / "team-dev" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("integration_branch", (SKILLS_DIR / "team-review" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("--deploy-ref", (SKILLS_DIR / "team-review" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("--deploy-ref", (SKILLS_DIR / "team-qa" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("qa_deploy_hint", (SKILLS_DIR / "team-next-step" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("release_checklist_reminders", (SKILLS_DIR / "team-release" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("delivery_pipeline", (SKILLS_DIR / "team-init" / "references" / "common-workflow-governance.md").read_text(encoding="utf-8"))

    def test_38_v160_round1_review_regressions(self):
        """
        Round 1 adversarial review regressions (5 P1 + 4 P2):
        - P1-1: YAML comment stripping in GitLab CI & GitHub Actions auto-detector.
        - P1-2: Token-boundary & explicit deploy-signal guard (evaluate_model / noqa_check must not false-positive).
        - P1-3: Strict allowlist & git-check-ref-format hardening on _sanitize_git_ref / _sanitize_tag_prefix.
        - P1-4: _build_release_checklist_reminders skips default main/master when integration_branch is empty.
        - P1-5: Idempotent promote-qa --tag <NEW_TAG> updates both tag and deploy_ref without Jira comment/transition.
        - P2-1: _build_qa_deploy_hint returns '' when QA prefix/ref is empty (no fabricated 'qa-*'/'qa').
        - P2-2: evaluate_story_next_step base_done_when references --deploy-ref (not legacy --tag).
        - P2-3: GitLab colon-separated & quoted top-level job names (testflight:qa:, testflight:uat:, testflight:production:, "deploy qa":).
        - P2-4: Strict non-empty commit match for qa_deploy_gate deploy_ref inheritance in record-qa --mode verify.
        """
        mod = self._load_jira_sync_module()

        # P1-3: Hardened _sanitize_git_ref and _sanitize_tag_prefix
        for bad_ref in (
            "", " ", None, 123, "@", "-lead", "/lead", "trail/", "trail.",
            "a..b", "a//b", "a@{0}", "a/.b", ".hidden", "a.lock/b", "foo.lock",
            "main;rm", "main$(id)", "main`id`", "main|cat", "main&bg",
            "main(1)", "main<in", "main>out", "main'q", 'main"q', "a b", "a\nb",
        ):
            self.assertEqual(mod._sanitize_git_ref(bad_ref), "", f"Expected _sanitize_git_ref({bad_ref!r}) == ''")
        for good_ref in ("main", "release", "feat/v1.6.0", "release-2026_11", "v1+build"):
            self.assertEqual(mod._sanitize_git_ref(good_ref), good_ref)

        for bad_pfx in (
            "", " ", None, "@", "-qa", "/qa-", "a..b", "a//b", "a@{0}",
            "a/.b", "qa/.", ".hidden-", "a.lock/b", "a.lock/", "foo.lock",
            "qa-;rm", "qa-$(id)", "qa-`id`", "qa-|cat", "qa-&bg",
            "qa-(1)", "qa-<x", "qa->x", "qa-'q", 'qa-"q', "qa -",
        ):
            self.assertEqual(mod._sanitize_tag_prefix(bad_pfx), "", f"Expected _sanitize_tag_prefix({bad_pfx!r}) == ''")
        for good_pfx in ("qa-", "qa/", "qa_", "uat-", "release/qa-", "v1."):
            self.assertEqual(mod._sanitize_tag_prefix(good_pfx), good_pfx)

        # P1-1 & P1-2: Commented GitLab rules + evaluate_model/noqa_check non-deploy jobs -> zero false positives
        with tempfile.TemporaryDirectory() as tmp_gl_fp:
            gl_fp = Path(tmp_gl_fp)
            (gl_fp / ".gitlab-ci.yml").write_text(
                "\n".join([
                    "stages: [lint, test]",
                    "evaluate_model:",
                    "  stage: test",
                    "  # environment:",
                    "  #   name: uat",
                    "  #   deployment_tier: staging",
                    "  rules:",
                    "    # - if: '$CI_COMMIT_TAG =~ /^qa-/'",
                    "    - if: '$CI_COMMIT_BRANCH == \"main\"' # run on main",
                    "  script:",
                    "    - pytest # unit tests",
                    "noqa_check:",
                    "  stage: lint",
                    "  rules:",
                    "    - if: '$CI_COMMIT_BRANCH == \"release\"'",
                    "  script:",
                    "    - ruff check .",
                ]) + "\n",
                encoding="utf-8",
            )
            det_fp = mod._detect_ci_delivery_pipeline(gl_fp)
            self.assertEqual(det_fp["delivery_pipeline"]["environments"], {})
            self.assertEqual(det_fp["delivery_pipeline"]["integration_branch"], "")
            self.assertEqual(det_fp["delivery_pipeline"]["production_branch"], "")
            self.assertIs(det_fp["delivery_pipeline"]["jira_deployment_sync"], False)
            self.assertEqual(det_fp["suggested_qa_handoff_mode"], "immediate")
            self.assertEqual(det_fp["detected_ci_checks"], ["pytest", "ruff check ."])

        # P2-3: GitLab colon-separated & quoted top-level job names (testflight:qa:, testflight:uat:, testflight:production:, "deploy dev":)
        with tempfile.TemporaryDirectory() as tmp_gl_col:
            gl_col = Path(tmp_gl_col)
            (gl_col / ".gitlab-ci.yml").write_text(
                "\n".join([
                    '"deploy dev": # dev integration deploy',
                    "  rules:",
                    "    - if: '$CI_COMMIT_BRANCH == \"release\"' # integration branch",
                    "  script:",
                    "    - swiftlint",
                    "testflight:qa:",
                    "  rules:",
                    "    - if: '$CI_COMMIT_TAG =~ /^qa-/' # QA tag",
                    "  script:",
                    "    - fastlane test",
                    "testflight:uat:",
                    "  rules:",
                    "    - if: '$CI_COMMIT_TAG =~ /^uat-/'",
                    "  script:",
                    "    - echo uat",
                    "testflight:production:",
                    "  rules:",
                    "    - if: '$CI_COMMIT_BRANCH == \"main\"'",
                    "  script:",
                    "    - echo prod",
                ]) + "\n",
                encoding="utf-8",
            )
            det_col = mod._detect_ci_delivery_pipeline(gl_col)
            dp_col = det_col["delivery_pipeline"]
            self.assertEqual(det_col["suggested_qa_handoff_mode"], "gated_by_deploy")
            self.assertEqual(dp_col["integration_branch"], "release")
            self.assertEqual(dp_col["production_branch"], "main")
            self.assertEqual(dp_col["environments"]["qa"]["trigger"], "tag")
            self.assertEqual(dp_col["environments"]["qa"]["prefix"], "qa-")
            self.assertEqual(dp_col["environments"]["uat"]["trigger"], "tag")
            self.assertEqual(dp_col["environments"]["uat"]["prefix"], "uat-")
            self.assertEqual(dp_col["environments"]["prod"]["ref"], "main")
            self.assertEqual(det_col["detected_ci_checks"], ["swiftlint", "fastlane test"])

        # P1-1: GitHub Actions trailing comments on branches:/tags: headers & list items
        with tempfile.TemporaryDirectory() as tmp_gh_cmt:
            gh_cmt = Path(tmp_gh_cmt)
            wf_dir = gh_cmt / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "deploy.yml").write_text(
                "\n".join([
                    "name: Deploy with Comments",
                    "on:",
                    "  push: # push trigger",
                    "    branches: # integration branch list",
                    "      - release # dev integration",
                    "    tags: # deploy tag list",
                    '      - "qa-*" # QA deploy trigger',
                    "      - 'uat-*' # UAT deploy trigger",
                    "jobs:",
                    "  deploy:",
                    "    environment: development # env comment",
                    "    steps:",
                    "      - run: npm test # run tests",
                ]) + "\n",
                encoding="utf-8",
            )
            det_gh_cmt = mod._detect_ci_delivery_pipeline(gh_cmt)
            self.assertEqual(det_gh_cmt["suggested_qa_handoff_mode"], "gated_by_deploy")
            self.assertEqual(det_gh_cmt["delivery_pipeline"]["integration_branch"], "release")
            self.assertEqual(det_gh_cmt["delivery_pipeline"]["environments"]["qa"]["prefix"], "qa-")
            self.assertEqual(det_gh_cmt["delivery_pipeline"]["environments"]["uat"]["prefix"], "uat-")
            self.assertEqual(det_gh_cmt["detected_ci_checks"], ["npm test"])

        # P1-4: _build_release_checklist_reminders skips default main/master when integration_branch is empty
        self.assertEqual(
            mod._build_release_checklist_reminders({"delivery_pipeline": {"integration_branch": "", "production_branch": "main", "environments": {}}}),
            [],
        )
        self.assertEqual(
            mod._build_release_checklist_reminders({"delivery_pipeline": {"integration_branch": "", "production_branch": "master", "environments": {}}}),
            [],
        )
        rem_custom_prod = mod._build_release_checklist_reminders(
            {"delivery_pipeline": {"integration_branch": "", "production_branch": "production", "environments": {}}}
        )
        self.assertEqual(len(rem_custom_prod), 1)
        self.assertIn("production", rem_custom_prod[0])
        rem_distinct = mod._build_release_checklist_reminders(
            {"delivery_pipeline": {"integration_branch": "release", "production_branch": "main", "environments": {}}}
        )
        self.assertEqual(len(rem_distinct), 1)
        self.assertIn("release", rem_distinct[0])
        self.assertIn("main", rem_distinct[0])

        # P2-1: _build_qa_deploy_hint returns '' when sanitized prefix/ref is empty (no fabricated 'qa-*' or 'qa')
        self.assertEqual(
            mod._build_qa_deploy_hint(
                {"delivery_pipeline": {"environments": {"qa": {"trigger": "tag", "prefix": "", "ref": "", "deployment_tier": "testing"}}}},
                "PROJ-1",
            ),
            "",
        )
        self.assertEqual(
            mod._build_qa_deploy_hint(
                {"delivery_pipeline": {"environments": {"qa": {"trigger": "tag", "prefix": "bad;prefix", "ref": "", "deployment_tier": "testing"}}}},
                "PROJ-1",
            ),
            "",
        )
        self.assertEqual(
            mod._build_qa_deploy_hint(
                {"delivery_pipeline": {"environments": {"qa": {"trigger": "branch", "prefix": "", "ref": "", "deployment_tier": "testing"}}}},
                "PROJ-1",
            ),
            "",
        )
        self.assertEqual(
            mod._build_qa_deploy_hint(
                {"delivery_pipeline": {"environments": {"qa": {"trigger": "branch", "prefix": "", "ref": "a/.b", "deployment_tier": "testing"}}}},
                "PROJ-1",
            ),
            "",
        )

        # P2-2, P1-5, P2-4: End-to-end offline workflow checks
        with tempfile.TemporaryDirectory() as tmp_e2e:
            e2e_root = Path(tmp_e2e)
            self.assertEqual(
                run_cli(
                    e2e_root, "init",
                    "--project-key", "R16",
                    "--qa-handoff-mode", "gated_by_deploy",
                    "--status-dev-done", "Dev Completed",
                ).returncode,
                0,
            )
            self.assertEqual(run_cli(e2e_root, "sync-story", "--key", "R16-1", "--title", "R16", "--ac", "AC-001:Ok", "--status", "approved").returncode, 0)
            self.assertEqual(run_cli(e2e_root, "complete-task", "--key", "R16-1", "--task-id", "TASK-001", "--commit", "c1", "--files", "a.py", "--test-result", "ok").returncode, 0)
            self.assertEqual(run_cli(e2e_root, "handoff", "--key", "R16-1", "--review-status", "approved", "--commit", "c1").returncode, 0)
            self.assertEqual(run_cli(e2e_root, "record-qa", "--key", "R16-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Ok").returncode, 0)

            # P2-2: base_done_when uses --deploy-ref (not legacy --tag)
            st_r16 = json.loads(run_cli(e2e_root, "status", "--key", "R16-1").stdout)
            self.assertIn("--deploy-ref", st_r16["done_when"])
            self.assertNotIn("--tag <QA-TAG>", st_r16["done_when"])

            # P1-5: Idempotent promote-qa --tag <NEW_TAG> updates both tag and deploy_ref; re-run without flags preserves both
            p1 = json.loads(run_cli(e2e_root, "promote-qa", "--key", "R16-1", "--tag", "qa-1").stdout)
            self.assertEqual(p1["deploy_ref"], "qa-1")
            self.assertEqual(p1["qa_deploy_gate"]["tag"], "qa-1")
            p2 = json.loads(run_cli(e2e_root, "promote-qa", "--key", "R16-1", "--tag", "qa-2").stdout)
            self.assertTrue(p2["already_deployed"])
            self.assertEqual(p2["deploy_ref"], "qa-2")
            self.assertEqual(p2["qa_deploy_gate"]["tag"], "qa-2")
            self.assertEqual(p2["qa_deploy_gate"]["deploy_ref"], "qa-2")
            p3 = json.loads(run_cli(e2e_root, "promote-qa", "--key", "R16-1").stdout)
            self.assertEqual(p3["deploy_ref"], "qa-2")
            self.assertEqual(p3["qa_deploy_gate"]["tag"], "qa-2")

            # P2-4: Empty gate commit MUST NOT be inherited by record-qa --mode verify on tested_commit='c1'
            st_file = e2e_root / ".ai-workflow" / "active" / "R16-1.json"
            st_data = json.loads(st_file.read_text(encoding="utf-8"))
            st_data["qa_deploy_gate"]["commit"] = ""
            st_data["qa_deploy_gate"]["deploy_ref"] = "qa-orphan"
            st_file.write_text(json.dumps(st_data, indent=2), encoding="utf-8")

            ver_no_inh = json.loads(run_cli(e2e_root, "record-qa", "--key", "R16-1", "--mode", "verify", "--commit", "c1", "--all-pass").stdout)
            self.assertEqual(ver_no_inh["verified_deploy_ref"], "")
            st_after = json.loads(st_file.read_text(encoding="utf-8"))
            self.assertNotIn("verified_deploy_ref", st_after["qa"])

    def test_39_issue21_sast_remediation(self):
        """Issue #21 SAST remediation: safe stdlib JUnit XML parser & http.client _http_request."""
        import ast
        import ssl
        from unittest import mock

        spec = importlib.util.spec_from_file_location("jira_sync_issue21", str(JIRA_SYNC))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # ------------------------------------------------------------------
        # 1. Pure-stdlib AST & static security invariants on jira_sync.py
        # ------------------------------------------------------------------
        src_text = JIRA_SYNC.read_text(encoding="utf-8")
        tree = ast.parse(src_text)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        for forbidden in ("xml", "xml.etree", "xml.etree.ElementTree", "xml.dom", "xml.sax", "xml.parsers", "urllib.request", "urllib.error"):
            self.assertNotIn(forbidden, imported_modules, f"Forbidden import remains: {forbidden}")
        for mod_name in imported_modules:
            top = mod_name.split(".")[0]
            self.assertNotEqual(top, "xml", f"Any xml.* import is forbidden: {mod_name}")
            if hasattr(sys, "stdlib_module_names"):
                self.assertIn(top, sys.stdlib_module_names, f"Non-stdlib import detected: {mod_name}")
        self.assertNotIn("urlopen(", src_text)
        self.assertNotIn("urlretrieve(", src_text)

        # ------------------------------------------------------------------
        # 2. Hardened pure-stdlib JUnit XML parser (parse_junit_xml_results)
        # ------------------------------------------------------------------
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)

            # 2a. Full functional parity: pass, fail, error, skipped, self-closing,
            #     multi-TC, precedence, comments, CDATA, '>' in attrs, namespaces, entities, BOM
            rich_xml = (
                "\ufeff"
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<!-- <testcase name='TC-999'><failure/></testcase> -->\n"
                '<ns:testsuites xmlns:ns="urn:junit">\n'
                '  <ns:testsuite name="suite1">\n'
                '    <ns:testcase classname="pkg.mod" name="test_pass_TC-001"/>\n'
                '    <ns:testcase classname="pkg.mod" name="test_pass_comments_TC-002">\n'
                '      <!-- <failure message="fake comment failure"/> -->\n'
                '      <system-out><![CDATA[<failure>fake cdata failure</failure><error/>]]></system-out>\n'
                '      <system-err><![CDATA[<skipped/>]]></system-err>\n'
                '      <properties><failure message="non-direct child"/></properties>\n'
                '    </ns:testcase>\n'
                '    <ns:testcase classname="pkg.mod" name="test_fail_cdata_TC-003">\n'
                '      <ns:failure message="expected a &gt; b &amp; c &lt; d"><![CDATA[AssertionError: <a> != <b>]]></ns:failure>\n'
                '    </ns:testcase>\n'
                '    <ns:testcase classname="pkg.mod" name="test_error_TC-004">\n'
                '      <error message="x > y"/>\n'
                '    </ns:testcase>\n'
                '    <ns:testcase classname=\'pkg.mod\' name=\'test_skip_TC-005\'>\n'
                '      <skipped message="env missing"/>\n'
                '    </ns:testcase>\n'
                '    <ns:testcase classname="TC-006" name="multi TC-007 &amp; &#65;&#x42; &#9999999999999999999999999; &quot;q&quot; &apos;s&apos; x > y"/>\n'
                '    <ns:testcase classname="pkg" name="TC-008[1]"><skipped/></ns:testcase>\n'
                '    <ns:testcase classname="pkg" name="TC-008[2]"/>\n'
                '    <ns:testcase classname="pkg" name="TC-009[1]"/>\n'
                '    <ns:testcase classname="pkg" name="TC-009[2]"><skipped/></ns:testcase>\n'
                '    <ns:testcase classname="pkg" name="TC-009[3]"><failure message="boom"/></ns:testcase>\n'
                '    <ns:testcase classname="pkg" name="TC-010"><skipped/><failure/></ns:testcase>\n'
                '    <ns:testcase classname="pkg" name="no_tc_id_here"/>\n'
                '  </ns:testsuite>\n'
                '</ns:testsuites>\n'
            )
            rich_path = tdir / "rich.xml"
            rich_path.write_text(rich_xml, encoding="utf-8")
            parsed = mod.parse_junit_xml_results(rich_path)
            self.assertEqual(
                parsed,
                {
                    "TC-001": "pass",
                    "TC-002": "pass",
                    "TC-003": "fail",
                    "TC-004": "fail",
                    "TC-005": "not-executed",
                    "TC-006": "pass",
                    "TC-007": "pass",
                    "TC-008": "not-executed",
                    "TC-009": "fail",
                    "TC-010": "fail",
                },
            )

            # 2b. XXE / DOCTYPE / Billion Laughs payloads MUST be rejected (fail-closed)
            xxe_payloads = [
                '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><testsuite><testcase name="TC-001 &xxe;"/></testsuite>',
                '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]><testsuite><testcase name="TC-001 &lol2;"/></testsuite>',
                '<!DOCTYPE testsuite SYSTEM "http://127.0.0.1/evil.dtd"><testsuite><testcase name="TC-001"/></testsuite>',
            ]
            for idx, payload in enumerate(xxe_payloads):
                p = tdir / f"xxe_{idx}.xml"
                p.write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError, msg=f"XXE payload #{idx} must be rejected"):
                    mod.parse_junit_xml_results(p)

            # 2c. Malformed / truncated / empty / non-UTF-8 files raise clean ValueError
            malformed_samples = [
                b"",
                b"   \n\t  ",
                b"not xml at all",
                b'<testsuite><testcase name="TC-001"',
                b'<testsuite><testcase name="TC-001"></testsuite>',
                b'<testsuite><testcase name="TC-001"></testcase><extra/>',
                b'<testsuite><testcase name="TC-001"><system-out><![CDATA[unterminated</system-out></testcase></testsuite>',
                b'<testsuite><testcase name="TC-001" name="TC-002"/></testsuite>',
                b'<testsuite><testcase name="TC-001\x00"/></testsuite>',
                b"\xff\xfe\x00\x01",
            ]
            for idx, raw in enumerate(malformed_samples):
                p = tdir / f"bad_{idx}.xml"
                p.write_bytes(raw)
                with self.assertRaises(ValueError, msg=f"Malformed XML #{idx} must raise ValueError"):
                    mod.parse_junit_xml_results(p)

            # 2d. CLI record-qa --junit-xml fails closed on XXE XML
            cli_root = tdir / "cli_repo"
            cli_root.mkdir()
            self.assertEqual(run_cli(cli_root, "init", "--project-key", "SEC").returncode, 0)
            self.assertEqual(run_cli(cli_root, "sync-story", "--key", "SEC-1", "--title", "Sec", "--ac", "AC-001:Ok", "--status", "approved").returncode, 0)
            self.assertEqual(run_cli(cli_root, "complete-task", "--key", "SEC-1", "--task-id", "TASK-001", "--commit", "c1", "--files", "a.py", "--test-result", "ok").returncode, 0)
            self.assertEqual(run_cli(cli_root, "handoff", "--key", "SEC-1", "--review-status", "approved", "--commit", "c1").returncode, 0)
            self.assertEqual(run_cli(cli_root, "record-qa", "--key", "SEC-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Ok").returncode, 0)
            xxe_cli = run_cli(cli_root, "record-qa", "--key", "SEC-1", "--mode", "verify", "--commit", "c1", "--junit-xml", str(tdir / "xxe_0.xml"))
            self.assertNotEqual(xxe_cli.returncode, 0)
            self.assertIn("ERROR:", xxe_cli.stderr)

        # ------------------------------------------------------------------
        # 3. Hardened pure-stdlib _http_request validation & redirect safety
        # ------------------------------------------------------------------
        # 3a. Disallowed schemes, missing hostname, invalid port, CR/LF/NUL injection
        for bad_url in (
            "file:///etc/passwd",
            "FILE:///etc/passwd",
            "ftp://example.com/file.txt",
            "gopher://127.0.0.1:70/_test",
            "data:text/plain;base64,SGVsbG8=",
            "//example.com/no-scheme",
            "http:///missing-host",
            "https://:443/missing-host",
            "http://example.com:99999/bad-port",
            "http://example.com/path\r\nX-Injected: yes",
            "http://example.com/path\nX-Injected: yes",
            "http://example.com/path\x00null",
            "http://example.com/has space",
            "http://user:SUPER_SECRET_PASS@example.com/path",
            "http://example.com\\@evil.com/x",
            "http://example.com\\evil.com/x",
            "http://127.0.0.1:80/path/中文",
            "",
        ):
            with self.assertRaises(ValueError, msg=f"Bad URL must be rejected: {bad_url!r}") as url_ctx:
                mod._http_request(bad_url)
            self.assertNotIn("SUPER_SECRET_PASS", str(url_ctx.exception))

        for bad_method in ("", "GET\n", "GET\r\nEvil: 1", "POST /x HTTP/1.1", "GET\x00"):
            with self.assertRaises(ValueError, msg=f"Bad method must be rejected: {bad_method!r}") as meth_ctx:
                mod._http_request("http://127.0.0.1/ok", method=bad_method)
            self.assertEqual(str(meth_ctx.exception), "Invalid HTTP method.")

        secret_val = "SUPER_SECRET_TOKEN_XYZ\r\nInjected: 1"
        with self.assertRaises(ValueError) as hdr_ctx:
            mod._http_request("http://127.0.0.1/ok", headers={"Authorization": secret_val})
        self.assertNotIn("SUPER_SECRET_TOKEN_XYZ", str(hdr_ctx.exception))

        for bad_hdr_name, bad_hdr_val in (
            ("Bad\r\nKey", "val"),
            ("X-Test\n", "1"),
            ("Authorization\n", "Bearer SUPER_SECRET_HDR_VAL"),
        ):
            with self.assertRaises(ValueError) as hname_ctx:
                mod._http_request("http://127.0.0.1/ok", headers={bad_hdr_name: bad_hdr_val})
            self.assertEqual(str(hname_ctx.exception), "Invalid HTTP header name.")
            self.assertNotIn("SUPER_SECRET_HDR_VAL", str(hname_ctx.exception))

        with self.assertRaises(ValueError) as non_latin_ctx:
            mod._http_request("http://127.0.0.1/ok", headers={"X-Test": "SUPER_SECRET_值"})
        self.assertEqual(str(non_latin_ctx.exception), "Invalid HTTP header value.")
        self.assertNotIn("SUPER_SECRET", str(non_latin_ctx.exception))

        # 3b. HTTPS uses ssl.create_default_context() with certificate & hostname verification
        captured_ssl = {}

        class _DummyResp:
            status = 200

            def getheaders(self):
                return [("Content-Type", "application/json")]

            def getheader(self, name, default=None):
                return "application/json" if name.lower() == "content-type" else default

            def read(self):
                return b'{"ok":true}'

        class _FakeHTTPSConn(mod._VerifiedTLSConnection):
            def __init__(self, host, port=None, timeout=None, context=None):
                super().__init__(host, port=port, timeout=timeout, context=context)
                captured_ssl["host"] = self.host
                captured_ssl["port"] = self.port
                captured_ssl["timeout"] = self.timeout
                captured_ssl["context"] = self._context
                captured_ssl["closed"] = False

            def request(self, method, target, body=None, headers=None):
                captured_ssl["method"] = method
                captured_ssl["target"] = target

            def getresponse(self):
                return _DummyResp()

            def close(self):
                captured_ssl["closed"] = True

        with mock.patch.object(mod, "_VerifiedTLSConnection", _FakeHTTPSConn):
            st, hdrs, body_b = mod._http_request("https://api.example.com/v1/ping?x=1", method="GET", timeout=12)
        self.assertEqual(st, 200)
        self.assertEqual(body_b, b'{"ok":true}')
        self.assertEqual(captured_ssl["host"], "api.example.com")
        self.assertEqual(captured_ssl["target"], "/v1/ping?x=1")
        self.assertEqual(captured_ssl["timeout"], 12)
        self.assertTrue(captured_ssl["closed"])
        self.assertIsInstance(captured_ssl["context"], ssl.SSLContext)
        self.assertEqual(captured_ssl["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(captured_ssl["context"].check_hostname)

        # 3c. Real local HTTP servers for GET/POST/PUT/204/404/500/multipart + redirects
        received_b = []

        class ServerBHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                received_b.append({
                    "method": "GET",
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                })
                payload = json.dumps({"server": "B", "path": self.path}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        srv_b = HTTPServer(("127.0.0.1", 0), ServerBHandler)
        port_b = srv_b.server_address[1]
        tb = threading.Thread(target=srv_b.serve_forever, daemon=True)
        tb.start()

        received_a = []

        class ServerAHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _read_body(self):
                clen = int(self.headers.get("Content-Length") or "0")
                return self.rfile.read(clen) if clen else b""

            def _send_json(self, code, obj, extra_headers=None):
                data = json.dumps(obj).encode("utf-8") if obj is not None else b""
                self.send_response(code)
                if extra_headers:
                    for hk, hv in extra_headers.items():
                        self.send_header(hk, hv)
                if obj is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def do_GET(self):
                received_a.append({
                    "method": "GET",
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                })
                if self.path == "/same-origin-redir":
                    self._send_json(302, None, {"Location": "/same-origin-final"})
                elif self.path == "/same-origin-final":
                    self._send_json(200, {"ok": True, "auth": self.headers.get("Authorization", ""), "token": self.headers.get("Token", "")})
                elif self.path == "/cross-origin-redir":
                    self._send_json(302, None, {"Location": f"http://127.0.0.1:{port_b}/landed"})
                elif self.path == "/evil-file-redir":
                    self._send_json(302, None, {"Location": "file:///etc/passwd"})
                elif self.path == "/evil-ftp-redir":
                    self._send_json(302, None, {"Location": "ftp://attacker.example/secret"})
                elif self.path == "/loop-a":
                    self._send_json(302, None, {"Location": "/loop-b"})
                elif self.path == "/loop-b":
                    self._send_json(302, None, {"Location": "/loop-a"})
                elif self.path.startswith("/chain/"):
                    hop = int(self.path.rsplit("/", 1)[-1])
                    self._send_json(302, None, {"Location": f"/chain/{hop + 1}"})
                elif self.path == "/no-content":
                    self.send_response(204)
                    self.end_headers()
                elif self.path == "/rest/api/3/issue/MISS-1":
                    self._send_json(404, {"errorMessages": ["Issue Does Not Exist"]})
                elif self.path == "/rest/api/3/myself":
                    self._send_json(500, {"error": "Internal Error"})
                elif self.path == "/v1/case/PROJ":
                    self._send_json(200, {"status": False, "errorMessage": "Invalid project"})
                else:
                    self._send_json(200, {"path": self.path})

            def do_POST(self):
                raw = self._read_body()
                received_a.append({
                    "method": "POST",
                    "path": self.path,
                    "body": raw,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                })
                if self.path == "/post-302":
                    self._send_json(302, None, {"Location": "/same-origin-final"})
                elif self.path == "/post-307":
                    self._send_json(307, None, {"Location": "/post-307-target"})
                elif self.path == "/post-307-target":
                    self._send_json(200, {"echo": raw.decode("utf-8", errors="replace"), "method": "POST"})
                elif self.path == "/rest/api/3/issue/DEMO-1/attachments":
                    self._send_json(200, [{"id": "att-1", "filename": "shot.png", "size": 10, "mimeType": "image/png", "content": "http://example/att-1"}])
                elif self.path == "/v1/attachment/QA":
                    self._send_json(200, {"status": True, "result": [{"hash": "h123", "url": "http://example/h123"}]})
                else:
                    self._send_json(201, {"created": True})

            def do_PUT(self):
                raw = self._read_body()
                received_a.append({"method": "PUT", "path": self.path, "body": raw})
                self.send_response(204)
                self.end_headers()

        srv_a = HTTPServer(("127.0.0.1", 0), ServerAHandler)
        port_a = srv_a.server_address[1]
        ta = threading.Thread(target=srv_a.serve_forever, daemon=True)
        ta.start()

        try:
            base_a = f"http://127.0.0.1:{port_a}"

            # 204 No Content
            st_204, _, body_204 = mod._http_request(f"{base_a}/no-content", method="GET")
            self.assertEqual(st_204, 204)
            self.assertEqual(body_204, b"")

            # Same-origin redirect preserves Authorization & Token
            st_same, _, body_same = mod._http_request(
                f"{base_a}/same-origin-redir",
                headers={"Authorization": "Basic abc", "Token": "tok123"},
            )
            self.assertEqual(st_same, 200)
            same_json = json.loads(body_same.decode("utf-8"))
            self.assertEqual(same_json["auth"], "Basic abc")
            self.assertEqual(same_json["token"], "tok123")

            # POST -> 302 downgrades to GET with no body
            st_p302, _, body_p302 = mod._http_request(
                f"{base_a}/post-302",
                method="POST",
                headers={"Content-Type": "application/json", "Authorization": "Basic abc"},
                body=b'{"x":1}',
            )
            self.assertEqual(st_p302, 200)
            self.assertEqual(received_a[-1]["method"], "GET")
            self.assertNotIn("content-type", received_a[-1]["headers"])

            # POST -> 307 preserves POST method & body
            st_p307, _, body_p307 = mod._http_request(
                f"{base_a}/post-307",
                method="POST",
                headers={"Content-Type": "application/json"},
                body=b'{"keep":true}',
            )
            self.assertEqual(st_p307, 200)
            self.assertEqual(json.loads(body_p307.decode("utf-8")), {"echo": '{"keep":true}', "method": "POST"})

            # Cross-origin redirect MUST strip Authorization, Token, Cookie, Proxy-Authorization
            st_cross, _, _ = mod._http_request(
                f"{base_a}/cross-origin-redir",
                headers={
                    "Authorization": "Basic secret",
                    "Token": "qase-secret",
                    "Cookie": "sid=secret",
                    "Proxy-Authorization": "Basic proxy-secret",
                    "X-Custom-Safe": "keep-me",
                },
            )
            self.assertEqual(st_cross, 200)
            self.assertEqual(len(received_b), 1)
            b_hdrs = received_b[0]["headers"]
            for stripped in ("authorization", "token", "cookie", "proxy-authorization"):
                self.assertNotIn(stripped, b_hdrs, f"Sensitive header {stripped} leaked across origin!")
            self.assertEqual(b_hdrs.get("x-custom-safe"), "keep-me")

            # Redirect to file:// or ftp:// MUST raise ValueError
            with self.assertRaises(ValueError):
                mod._http_request(f"{base_a}/evil-file-redir")
            with self.assertRaises(ValueError):
                mod._http_request(f"{base_a}/evil-ftp-redir")

            # Redirect loop and max_redirects cap MUST raise RuntimeError
            with self.assertRaises(RuntimeError):
                mod._http_request(f"{base_a}/loop-a")
            with self.assertRaises(RuntimeError):
                mod._http_request(f"{base_a}/chain/0", max_redirects=3)

            # JiraClient._request 404 / 500 / 204 & upload_attachments via _http_request
            jc = mod.JiraClient({"jira_base_url": base_a}, offline=False)
            jc.email = "dev@team.io"
            jc.api_token = "tok"
            jc.offline = False

            with self.assertRaises(RuntimeError) as ctx_404:
                jc._request("GET", "/rest/api/3/issue/MISS-1")
            self.assertIn("HTTP 404", str(ctx_404.exception))

            with self.assertRaises(RuntimeError) as ctx_500:
                jc._request("GET", "/rest/api/3/myself")
            self.assertIn("HTTP 500", str(ctx_500.exception))

            self.assertEqual(jc._request("PUT", "/rest/api/3/issue/DEMO-1", {"fields": {}}), {})

            with tempfile.TemporaryDirectory() as att_tmp:
                shot = Path(att_tmp) / "shot.png"
                shot.write_bytes(b"\x89PNG\r\n\x1a\n00")
                vfs = mod.validate_attachment_files([str(shot)])
                jira_atts = jc.add_attachments("DEMO-1", vfs)
                self.assertEqual(jira_atts[0]["id"], "att-1")

                # QaseClient._request & upload_attachments via _http_request
                qc = mod.QaseClient({"qase": {"project_code": "QA", "base_url": f"{base_a}/v1"}}, offline=False)
                qc.api_token = "qtok"
                qc.has_credentials = True
                with self.assertRaises(RuntimeError) as ctx_qase:
                    qc._request("GET", "/case/PROJ")
                self.assertIn("status=false", str(ctx_qase.exception))

                hashes, recs, _ = qc.upload_attachments(vfs)
                self.assertEqual(hashes, ["h123"])
                self.assertEqual(recs[0]["hash"], "h123")
        finally:
            srv_a.shutdown()
            srv_a.server_close()
            srv_b.shutdown()
            srv_b.server_close()

    def test_40_issue23_verified_tls_connection(self):
        """Issue #23 (Part 1): pure-stdlib _VerifiedTLSConnection replaces HTTPSConnection with leak-free verified TLS."""
        import ast
        import http.client
        import shutil
        import socket
        import ssl
        import warnings
        from unittest import mock

        spec = importlib.util.spec_from_file_location("jira_sync_issue23", str(JIRA_SYNC))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 1. Static invariants: pure stdlib, zero HTTPSConnection, zero nosemgrep
        src_text = JIRA_SYNC.read_text(encoding="utf-8")
        self.assertNotIn("HTTPSConnection", src_text)
        self.assertNotIn("nosemgrep", src_text)
        tree = ast.parse(src_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if hasattr(sys, "stdlib_module_names"):
                        self.assertIn(top, sys.stdlib_module_names, f"Non-stdlib import: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if hasattr(sys, "stdlib_module_names"):
                    self.assertIn(top, sys.stdlib_module_names, f"Non-stdlib import: {node.module}")

        # 2. Class contract: subclasses HTTPConnection, default_port == 443, verified default SSLContext >= TLS 1.2
        self.assertTrue(issubclass(mod._VerifiedTLSConnection, http.client.HTTPConnection))
        self.assertEqual(mod._VerifiedTLSConnection.default_port, 443)
        self.assertEqual(mod._VerifiedTLSConnection.default_port, http.client.HTTPS_PORT)

        default_conn = mod._VerifiedTLSConnection("example.com")
        self.assertEqual(default_conn.port, 443)
        self.assertIsInstance(default_conn._context, ssl.SSLContext)
        self.assertEqual(default_conn._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(default_conn._context.check_hostname)
        if hasattr(ssl, "TLSVersion"):
            self.assertGreaterEqual(default_conn._context.minimum_version, ssl.TLSVersion.TLSv1_2)

        # 2b. Reject weakened ssl.SSLContext instances (CERT_NONE, CERT_OPTIONAL, check_hostname=False, TLS < 1.2)
        unverified_ctx = ssl._create_unverified_context()
        with self.assertRaises(ValueError):
            mod._VerifiedTLSConnection("example.com", context=unverified_ctx)

        cert_none_ctx = ssl.create_default_context()
        cert_none_ctx.check_hostname = False
        cert_none_ctx.verify_mode = ssl.CERT_NONE
        with self.assertRaises(ValueError):
            mod._VerifiedTLSConnection("example.com", context=cert_none_ctx)

        cert_opt_ctx = ssl.create_default_context()
        cert_opt_ctx.check_hostname = False
        cert_opt_ctx.verify_mode = ssl.CERT_OPTIONAL
        with self.assertRaises(ValueError):
            mod._VerifiedTLSConnection("example.com", context=cert_opt_ctx)

        no_host_ctx = ssl.create_default_context()
        no_host_ctx.check_hostname = False
        with self.assertRaises(ValueError):
            mod._VerifiedTLSConnection("example.com", context=no_host_ctx)

        if hasattr(ssl, "TLSVersion"):
            for weak_ver in (
                ssl.TLSVersion.MINIMUM_SUPPORTED,
                ssl.TLSVersion.TLSv1,
                ssl.TLSVersion.TLSv1_1,
            ):
                weak_tls_ctx = ssl.create_default_context()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    weak_tls_ctx.minimum_version = weak_ver
                with self.assertRaises(ValueError):
                    mod._VerifiedTLSConnection("example.com", context=weak_tls_ctx)

        # 2c. Default context ALPN http/1.1 fallback when set_alpn_protocols raises NotImplementedError
        alpn_calls = []

        class _NoAlpnContext:
            minimum_version = ssl.TLSVersion.TLSv1_2 if hasattr(ssl, "TLSVersion") else 0

            def set_alpn_protocols(self, protocols):
                alpn_calls.append(list(protocols))
                raise NotImplementedError("ALPN not supported")

        no_alpn_ctx = _NoAlpnContext()
        with mock.patch.object(mod.ssl, "create_default_context", return_value=no_alpn_ctx):
            alpn_conn = mod._VerifiedTLSConnection("example.com")
        self.assertIs(alpn_conn._context, no_alpn_ctx)
        self.assertEqual(alpn_calls, [["http/1.1"]])

        # 3. connect() wraps socket with server_hostname == self.host (and self._tunnel_host when tunneled)
        class _FakeRawSocket:
            def __init__(self, fail_close=False):
                self.closed = False
                self.close_calls = 0
                self.fail_close = fail_close

            def setsockopt(self, *args, **kwargs):
                pass

            def close(self):
                self.close_calls += 1
                self.closed = True
                if self.fail_close:
                    raise OSError("close failed")

        wrapped_sentinel = object()
        wrap_calls = []

        class _FakeContext:
            def __init__(self, fail_exc=None):
                self.fail_exc = fail_exc

            def wrap_socket(self, sock, server_hostname=None):
                wrap_calls.append((sock, server_hostname))
                if self.fail_exc is not None:
                    raise self.fail_exc
                return wrapped_sentinel

        # 3a. Direct host connect
        raw_direct = _FakeRawSocket()
        conn_direct = mod._VerifiedTLSConnection("jira.example.com", context=_FakeContext())
        conn_direct._create_connection = lambda addr, timeout, src: raw_direct
        conn_direct.connect()
        self.assertIs(conn_direct.sock, wrapped_sentinel)
        self.assertEqual(wrap_calls[-1], (raw_direct, "jira.example.com"))
        self.assertFalse(raw_direct.closed)

        # 3b. Tunneled host connect uses _tunnel_host as SNI server_hostname
        raw_tunnel = _FakeRawSocket()
        conn_tunnel = mod._VerifiedTLSConnection("proxy.internal", port=8080, context=_FakeContext())
        conn_tunnel._create_connection = lambda addr, timeout, src: raw_tunnel
        conn_tunnel._tunnel_host = "target.example.com"
        conn_tunnel._tunnel = lambda: None
        conn_tunnel.connect()
        self.assertIs(conn_tunnel.sock, wrapped_sentinel)
        self.assertEqual(wrap_calls[-1], (raw_tunnel, "target.example.com"))
        self.assertFalse(raw_tunnel.closed)

        # 4. Zero socket leak when wrap_socket raises ssl.SSLError or KeyboardInterrupt (BaseException)
        for fail_close in (False, True):
            raw_fail = _FakeRawSocket(fail_close=fail_close)
            conn_fail = mod._VerifiedTLSConnection(
                "badcert.example.com",
                context=_FakeContext(fail_exc=ssl.SSLError("CERTIFICATE_VERIFY_FAILED")),
            )
            conn_fail._create_connection = lambda addr, timeout, src, s=raw_fail: s
            with self.assertRaises(ssl.SSLError) as ssl_ctx:
                conn_fail.connect()
            self.assertIn("CERTIFICATE_VERIFY_FAILED", str(ssl_ctx.exception))
            self.assertIsNone(conn_fail.sock)
            self.assertTrue(raw_fail.closed)
            self.assertEqual(raw_fail.close_calls, 1)

        raw_kbi = _FakeRawSocket()
        conn_kbi = mod._VerifiedTLSConnection(
            "interrupt.example.com",
            context=_FakeContext(fail_exc=KeyboardInterrupt("aborted handshake")),
        )
        conn_kbi._create_connection = lambda addr, timeout, src: raw_kbi
        with self.assertRaises(KeyboardInterrupt):
            conn_kbi.connect()
        self.assertIsNone(conn_kbi.sock)
        self.assertTrue(raw_kbi.closed)
        self.assertEqual(raw_kbi.close_calls, 1)

        # 5. _http_request("https://example.com/rest/api") uses _VerifiedTLSConnection with verified
        #    default SSL context, effective_port, IPv6 safety, and preserves scheme/host/CRLF/redirect checks
        recorded_hops = []

        class _StubResp:
            def __init__(self, status, headers, body):
                self.status = status
                self._headers = headers
                self._body = body

            def getheaders(self):
                return list(self._headers)

            def getheader(self, name, default=None):
                for k, v in self._headers:
                    if k.lower() == name.lower():
                        return v
                return default

            def read(self):
                return self._body

        class _RecordingTLSConn(mod._VerifiedTLSConnection):
            def __init__(self, host, port=None, timeout=None, context=None):
                super().__init__(host, port=port, timeout=timeout, context=context)
                self.entry = {
                    "host": self.host,
                    "port": self.port,
                    "timeout": self.timeout,
                    "context": self._context,
                    "closed": False,
                }
                recorded_hops.append(self.entry)

            def request(self, method, target, body=None, headers=None):
                self.entry["method"] = method
                self.entry["target"] = target
                self.entry["headers"] = dict(headers or {})

            def getresponse(self):
                if self.entry["host"] == "example.com" and self.entry["target"] == "/rest/redir":
                    return _StubResp(302, [("Location", "https://other.example.com/final")], b"")
                return _StubResp(200, [("Content-Type", "application/json")], b'{"ok":true}')

            def close(self):
                self.entry["closed"] = True

        with mock.patch.object(mod, "_VerifiedTLSConnection", _RecordingTLSConn):
            # 5a. Direct HTTPS call uses effective_port 443 and verified default SSLContext >= TLS 1.2
            st, _, body = mod._http_request("https://example.com/rest/api", method="GET", timeout=9)
            self.assertEqual(st, 200)
            self.assertEqual(body, b'{"ok":true}')
            hop0 = recorded_hops[-1]
            self.assertEqual(hop0["host"], "example.com")
            self.assertEqual(hop0["port"], 443)
            self.assertEqual(hop0["target"], "/rest/api")
            self.assertEqual(hop0["timeout"], 9)
            self.assertTrue(hop0["closed"])
            self.assertIsInstance(hop0["context"], ssl.SSLContext)
            self.assertEqual(hop0["context"].verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(hop0["context"].check_hostname)
            if hasattr(ssl, "TLSVersion"):
                self.assertGreaterEqual(hop0["context"].minimum_version, ssl.TLSVersion.TLSv1_2)

            # 5b. Cross-origin HTTPS redirect strips credentials and keeps verified TLS on every hop
            recorded_hops.clear()
            st_r, _, body_r = mod._http_request(
                "https://example.com/rest/redir",
                headers={
                    "Authorization": "Basic secret-cred",
                    "Token": "qase-secret",
                    "X-Safe": "keep",
                },
            )
            self.assertEqual(st_r, 200)
            self.assertEqual(body_r, b'{"ok":true}')
            self.assertEqual(len(recorded_hops), 2)
            self.assertIn("Authorization", recorded_hops[0]["headers"])
            self.assertNotIn("Authorization", recorded_hops[1]["headers"])
            self.assertNotIn("Token", recorded_hops[1]["headers"])
            self.assertEqual(recorded_hops[1]["headers"].get("X-Safe"), "keep")
            for hop in recorded_hops:
                self.assertTrue(hop["closed"])
                self.assertEqual(hop["port"], 443)
                self.assertIsInstance(hop["context"], ssl.SSLContext)
                self.assertEqual(hop["context"].verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(hop["context"].check_hostname)

            # 5c. Default-port IPv6 HTTPS URLs preserve unbracketed host, port 443, and SNI server_hostname
            for ipv6_url, expected_host in (
                ("https://[::1]/rest/api", "::1"),
                ("https://[2001:db8::a]/rest/api", "2001:db8::a"),
            ):
                recorded_hops.clear()
                st_v6, _, _ = mod._http_request(ipv6_url, method="GET", timeout=7)
                self.assertEqual(st_v6, 200)
                self.assertEqual(len(recorded_hops), 1)
                self.assertEqual(recorded_hops[0]["host"], expected_host)
                self.assertEqual(recorded_hops[0]["port"], 443)
                self.assertEqual(recorded_hops[0]["target"], "/rest/api")

                raw_v6 = _FakeRawSocket()
                v6_conn = mod._VerifiedTLSConnection(expected_host, port=443, context=_FakeContext())
                v6_conn._create_connection = lambda addr, timeout, src, s=raw_v6: s
                v6_conn.connect()
                self.assertEqual(v6_conn.host, expected_host)
                self.assertEqual(v6_conn.port, 443)
                self.assertEqual(wrap_calls[-1], (raw_v6, expected_host))

            # 5c-ii. Emitted Host headers for IPv6 default (443) vs non-default (8443) ports
            v6_default_hdr_conn = mod._VerifiedTLSConnection("::1", port=443, context=_FakeContext())
            v6_default_hdr_conn.putrequest("GET", "/rest/api")
            self.assertIn(b"Host: [::1]", v6_default_hdr_conn._buffer)

            v6_custom_hdr_conn = mod._VerifiedTLSConnection("::1", port=8443, context=_FakeContext())
            v6_custom_hdr_conn.putrequest("GET", "/rest/api")
            self.assertIn(b"Host: [::1]:8443", v6_custom_hdr_conn._buffer)

            # 5d. Scheme / host / CRLF guards still reject before opening any TLS connection
            recorded_hops.clear()
            for bad_url in (
                "file:///etc/passwd",
                "ftp://example.com/a",
                "https:///missing-host",
                "https://example.com/rest/api\r\nX-Evil: 1",
            ):
                with self.assertRaises(ValueError):
                    mod._http_request(bad_url)
            with self.assertRaises(ValueError):
                mod._http_request("https://example.com/rest/api", headers={"X-Test": "bad\r\nInject: 1"})
            self.assertEqual(recorded_hops, [])

        # 5e. Default-port IPv6 HTTP URL preserves host "::1" and port 80
        recorded_http_v6 = []

        class _RecordingHTTPConn(http.client.HTTPConnection):
            def __init__(self, host, port=None, timeout=None):
                super().__init__(host, port=port, timeout=timeout)
                self.entry = {"host": self.host, "port": self.port, "closed": False}
                recorded_http_v6.append(self.entry)

            def request(self, method, target, body=None, headers=None):
                self.entry["target"] = target

            def getresponse(self):
                return _StubResp(200, [("Content-Type", "application/json")], b'{"v6":true}')

            def close(self):
                self.entry["closed"] = True

        with mock.patch("http.client.HTTPConnection", _RecordingHTTPConn):
            st_hv6, _, body_hv6 = mod._http_request("http://[::1]/rest/api", method="GET", timeout=7)
        self.assertEqual(st_hv6, 200)
        self.assertEqual(body_hv6, b'{"v6":true}')
        self.assertEqual(len(recorded_http_v6), 1)
        self.assertEqual(recorded_http_v6[0]["host"], "::1")
        self.assertEqual(recorded_http_v6[0]["port"], 80)
        self.assertEqual(recorded_http_v6[0]["target"], "/rest/api")
        self.assertTrue(recorded_http_v6[0]["closed"])

        # 6. Real loopback TLS rejection test against an untrusted self-signed certificate
        if shutil.which("openssl"):
            with tempfile.TemporaryDirectory() as tls_tmp:
                cert_file = Path(tls_tmp) / "selfsigned.crt"
                key_file = Path(tls_tmp) / "selfsigned.key"
                gen = subprocess.run(
                    [
                        "openssl",
                        "req",
                        "-x509",
                        "-newkey",
                        "rsa:2048",
                        "-keyout",
                        str(key_file),
                        "-out",
                        str(cert_file),
                        "-days",
                        "1",
                        "-nodes",
                        "-subj",
                        "/CN=127.0.0.1",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(gen.returncode, 0)

                srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                srv_ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))

                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                listener.settimeout(5)
                tls_port = listener.getsockname()[1]

                def _serve_once():
                    try:
                        client_sock, _ = listener.accept()
                        try:
                            with srv_ctx.wrap_socket(client_sock, server_side=True) as tls_sock:
                                tls_sock.recv(1024)
                        except (ssl.SSLError, OSError):
                            pass
                        finally:
                            try:
                                client_sock.close()
                            except OSError:
                                pass
                    except OSError:
                        pass

                t_srv = threading.Thread(target=_serve_once, daemon=True)
                t_srv.start()
                try:
                    live_conn = mod._VerifiedTLSConnection("127.0.0.1", port=tls_port, timeout=5)
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        live_conn.connect()
                    self.assertIsNone(live_conn.sock)
                finally:
                    listener.close()
                    t_srv.join(timeout=5)

    def test_41_issue23_out_of_repo_credentials(self):
        """Issue #23 Part 2: Out-of-repo default credential storage, pure env-var auth, precedence, symlink refusal & scanner guidance."""
        import shutil as _shutil
        import stat
        from unittest import mock

        spec = importlib.util.spec_from_file_location("jira_sync_issue23_p2", str(JIRA_SYNC))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        MockJiraHandler.requests_log.clear()
        MockJiraHandler.stored_issues.clear()
        MockJiraHandler.stored_properties.clear()

        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_port
        t_jira = threading.Thread(target=jira_srv.serve_forever, daemon=True)
        t_jira.start()
        jira_url = f"http://127.0.0.1:{jira_port}"

        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                creds_dir = base / "user-creds"
                env_iso = {"AI_WORKFLOW_CREDENTIALS_DIR": str(creds_dir)}

                # 1. Default cmd_auth writes ONLY to out-of-repo user config dir (0700 dir, 0600 file), ZERO in-repo credentials.json
                repo1 = base / "my-app"
                repo1.mkdir()
                init1 = run_cli(
                    repo1, "init",
                    "--project-key", "SEC",
                    "--jira-url", jira_url,
                    env_override=env_iso,
                )
                self.assertEqual(init1.returncode, 0, init1.stderr)
                self.assertFalse((repo1 / ".ai-workflow/credentials.json").exists())
                self.assertFalse((repo1 / ".semgrepignore").exists())
                self.assertFalse((repo1 / ".gitleaksignore").exists())

                secret_tok = "tok-super-secret-alice-23"
                qase_tok = "qase-super-secret-23"
                auth1 = run_cli(
                    repo1, "auth",
                    "--role", "pm,qa",
                    "--jira-email", "alice@team.io",
                    "--jira-token", secret_tok,
                    "--qase-token", qase_tok,
                    env_override=env_iso,
                )
                self.assertEqual(auth1.returncode, 0, auth1.stderr)
                self.assertNotIn(secret_tok, auth1.stdout)
                self.assertNotIn(secret_tok, auth1.stderr)
                self.assertNotIn(qase_tok, auth1.stdout)
                self.assertNotIn(qase_tok, auth1.stderr)
                a1_out = json.loads(auth1.stdout)
                self.assertTrue(a1_out["ok"])
                self.assertEqual(a1_out["credential_store"], "user")
                self.assertEqual(a1_out["credential_source"], "user_config")
                self.assertFalse((repo1 / ".ai-workflow/credentials.json").exists())
                self.assertFalse((repo1 / ".semgrepignore").exists())
                self.assertFalse((repo1 / ".gitleaksignore").exists())

                user_cred_file = Path(a1_out["credentials_file"])
                self.assertTrue(user_cred_file.exists())
                self.assertEqual(user_cred_file.resolve().parent, creds_dir.resolve())
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(creds_dir.stat().st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE(user_cred_file.stat().st_mode), 0o600)
                saved_json = json.loads(user_cred_file.read_text(encoding="utf-8"))
                self.assertEqual(saved_json["jira_api_token"], secret_tok)
                self.assertEqual(saved_json["qase_api_token"], qase_tok)

                # Attachment validator must ALSO refuse uploading out-of-repo credential file
                with mock.patch.dict(os.environ, env_iso):
                    with self.assertRaises(ValueError):
                        mod.validate_attachment_files([str(user_cred_file)], repo_root=repo1)

                # 2. XDG_CONFIG_HOME resolution + control char / NUL rejection + relative XDG fallback
                xdg_base = base / "xdg-home"
                with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg_base)}, clear=False):
                    os.environ.pop("AI_WORKFLOW_CREDENTIALS_DIR", None)
                    p_xdg = mod.get_user_credentials_path(repo1)
                    self.assertEqual(p_xdg.parent, (xdg_base / "ai-workflow" / "credentials").resolve())
                for bad_dir in ("/tmp/bad\x00dir", "/tmp/bad\ndir", "/tmp/bad\rdir"):
                    with mock.patch.object(mod.os, "environ", {"AI_WORKFLOW_CREDENTIALS_DIR": bad_dir}):
                        with self.assertRaises(ValueError) as ctx_bad:
                            mod.get_user_credentials_path(repo1)
                        self.assertNotIn("\x00", str(ctx_bad.exception))
                        self.assertNotIn("bad", str(ctx_bad.exception))
                for bad_xdg in ("/tmp/xdg\x00bad", "/tmp/xdg\nbad"):
                    with mock.patch.object(mod.os, "environ", {"XDG_CONFIG_HOME": bad_xdg}):
                        with self.assertRaises(ValueError) as ctx_xdg:
                            mod.get_user_credentials_path(repo1)
                        self.assertNotIn("bad", str(ctx_xdg.exception))
                with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative/path"}, clear=False):
                    os.environ.pop("AI_WORKFLOW_CREDENTIALS_DIR", None)
                    p_rel = mod.get_user_credentials_path(repo1)
                    self.assertEqual(p_rel.parent, (Path.home() / ".config" / "ai-workflow" / "credentials").resolve())
                # Restore isolated dir
                os.environ["AI_WORKFLOW_CREDENTIALS_DIR"] = str(creds_dir)

                # 3. Git worktree + main repo share EXACT same out-of-repo path; distinct repos (even same basename) differ!
                git_main = base / "ws-a" / "shared-repo"
                git_main.mkdir(parents=True)
                subprocess.run(["git", "init"], cwd=str(git_main), check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "dev@team.io"], cwd=str(git_main), check=True, capture_output=True)
                subprocess.run(["git", "config", "user.name", "Dev"], cwd=str(git_main), check=True, capture_output=True)
                (git_main / "README.md").write_text("# Main\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=str(git_main), check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "chore: init"], cwd=str(git_main), check=True, capture_output=True)

                wt_dir = base / "ws-wt" / "wt-branch-1"
                wt_dir.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "worktree", "add", "-b", "feat/wt1", str(wt_dir)],
                    cwd=str(git_main),
                    check=True,
                    capture_output=True,
                )
                other_same_name = base / "ws-b" / "shared-repo"
                other_same_name.mkdir(parents=True)
                try:
                    with mock.patch.dict(os.environ, env_iso):
                        p_main = mod.get_user_credentials_path(git_main)
                        p_wt = mod.get_user_credentials_path(wt_dir)
                        p_other = mod.get_user_credentials_path(other_same_name)
                        self.assertEqual(p_main, p_wt)
                        self.assertNotEqual(p_main, p_other)
                        self.assertTrue(p_main.name.startswith("shared-repo-"))

                        # Also verify pure-Python .git file / commondir fallback when git subprocess fails
                        with mock.patch("subprocess.run", side_effect=FileNotFoundError("git missing")):
                            p_main_fb = mod.get_user_credentials_path(git_main)
                            p_wt_fb = mod.get_user_credentials_path(wt_dir)
                            self.assertEqual(p_main_fb, p_main)
                            self.assertEqual(p_wt_fb, p_main)
                finally:
                    subprocess.run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=str(git_main), capture_output=True)

                # 4. Pure env-var auth (--credential-store env AND implicit env-var auth) writes ZERO files!
                repo_env = base / "env-only-repo"
                repo_env.mkdir()
                fresh_creds_dir = base / "should-not-exist-creds"
                env_base = {
                    "AI_WORKFLOW_CREDENTIALS_DIR": str(fresh_creds_dir),
                    "JIRA_URL": jira_url,  # Also tests JIRA_URL fallback when JIRA_BASE_URL is unset!
                    "JIRA_EMAIL": "alice@team.io",
                    "JIRA_API_TOKEN": "env-secret-alice-23",
                }
                init_env = run_cli(
                    repo_env, "init",
                    "--project-key", "ENV",
                    "--jira-url", jira_url,
                    "--credential-store", "env",
                    env_override=env_base,
                )
                self.assertEqual(init_env.returncode, 0, init_env.stderr)
                ie_out = json.loads(init_env.stdout)
                self.assertEqual(ie_out["credential_store"], "env")
                self.assertEqual(ie_out["credential_source"], "env")
                self.assertEqual(ie_out["credentials_file"], "")
                self.assertTrue(ie_out["jira_verification"]["verified"])
                self.assertFalse(fresh_creds_dir.exists())
                self.assertFalse((repo_env / ".ai-workflow/credentials.json").exists())

                # Explicit --credential-store env in auth claims role & verifies live with ZERO files written
                auth_env = run_cli(
                    repo_env, "auth",
                    "--role", "pm",
                    "--credential-store", "env",
                    env_override=env_base,
                )
                self.assertEqual(auth_env.returncode, 0, auth_env.stderr)
                self.assertNotIn("env-secret-alice-23", auth_env.stdout)
                ae_out = json.loads(auth_env.stdout)
                self.assertTrue(ae_out["ok"])
                self.assertEqual(ae_out["credential_store"], "env")
                self.assertEqual(ae_out["credentials_file"], "")
                self.assertEqual(ae_out["credentials_path"], "")
                self.assertIn("pm", ae_out["claimed_roles_updated"])
                self.assertTrue(ae_out["jira_verification"]["verified"])
                self.assertFalse(fresh_creds_dir.exists())
                self.assertFalse((repo_env / ".ai-workflow/credentials.json").exists())

                # Implicit env-var auth (no --credential-store, no --jira-token, role-specific env var JIRA_EMAIL_QA + JIRA_API_TOKEN_QA)
                env_role_only = {
                    "AI_WORKFLOW_CREDENTIALS_DIR": str(fresh_creds_dir),
                    "JIRA_EMAIL": None,
                    "JIRA_API_TOKEN": None,
                    "JIRA_EMAIL_QA": "carol@team.io",
                    "JIRA_API_TOKEN_QA": "qa-token-carol",
                }
                auth_implicit = run_cli(
                    repo_env, "auth",
                    "--role", "qa",
                    env_override=env_role_only,
                )
                self.assertEqual(auth_implicit.returncode, 0, auth_implicit.stderr)
                self.assertNotIn("qa-token-carol", auth_implicit.stdout)
                ai_impl = json.loads(auth_implicit.stdout)
                self.assertEqual(ai_impl["credential_store"], "env")
                self.assertEqual(ai_impl["credentials_file"], "")
                self.assertIn("qa", ai_impl["claimed_roles_updated"])
                self.assertTrue(ai_impl["jira_verification"]["verified"])
                self.assertFalse(fresh_creds_dir.exists())

                # --credential-store env rejects CLI --jira-token (fail closed) and fails when no env vars exist
                bad_env_combo = run_cli(
                    repo_env, "auth",
                    "--credential-store", "env",
                    "--jira-email", "alice@team.io",
                    "--jira-token", "should-not-accept",
                    env_override=env_base,
                )
                self.assertNotEqual(bad_env_combo.returncode, 0)
                self.assertNotIn("should-not-accept", bad_env_combo.stderr)

                empty_env_auth = run_cli(
                    repo_env, "auth",
                    "--credential-store", "env",
                    env_override={"AI_WORKFLOW_CREDENTIALS_DIR": str(fresh_creds_dir), "JIRA_EMAIL": None, "JIRA_API_TOKEN": None, "QASE_API_TOKEN": None},
                )
                self.assertNotEqual(empty_env_auth.returncode, 0)

                # 5. Precedence: env > user_config > legacy credentials.json > legacy .env
                repo_prec = base / "prec-repo"
                repo_prec.mkdir()
                self.assertEqual(run_cli(repo_prec, "init", "--project-key", "PRC", "--jira-url", jira_url, env_override=env_iso).returncode, 0)

                # 5a. Legacy .env alone -> credential_source == "legacy_repo"
                (repo_prec / ".ai-workflow/.env").write_text(
                    'JIRA_EMAIL="dotenv@team.io"\nJIRA_API_TOKEN="tok-dotenv"\nQASE_API_TOKEN="qase-dotenv"\n',
                    encoding="utf-8",
                )
                insp_dotenv = json.loads(run_cli(repo_prec, "init", "--inspect", env_override=env_iso).stdout)
                self.assertEqual(insp_dotenv["credential_source"], "legacy_repo")
                with mock.patch.dict(os.environ, env_iso):
                    c_dotenv = mod.load_jira_credentials(repo_prec)
                    self.assertEqual(c_dotenv["jira_api_token"], "tok-dotenv")
                    self.assertEqual(c_dotenv["qase_api_token"], "qase-dotenv")

                # 5b. Legacy credentials.json overrides .env (explicit --credential-store repo)
                auth_repo = run_cli(
                    repo_prec, "auth",
                    "--credential-store", "repo",
                    "--jira-email", "bob@team.io",
                    "--jira-token", "dev-token-bob",
                    env_override=env_iso,
                )
                self.assertEqual(auth_repo.returncode, 0, auth_repo.stderr)
                ar_out = json.loads(auth_repo.stdout)
                self.assertEqual(ar_out["credential_store"], "repo")
                self.assertEqual(ar_out["credentials_path"], ".ai-workflow/credentials.json")
                self.assertTrue((repo_prec / ".ai-workflow/credentials.json").exists())
                with mock.patch.dict(os.environ, env_iso):
                    c_leg = mod.load_jira_credentials(repo_prec)
                    self.assertEqual(c_leg["jira_api_token"], "dev-token-bob")
                    self.assertEqual(c_leg["qase_api_token"], "qase-dotenv")

                # 5c. User store overrides legacy credentials.json AND emits legacy_credentials_warning!
                auth_user = run_cli(
                    repo_prec, "auth",
                    "--role", "qa",
                    "--jira-email", "carol@team.io",
                    "--jira-token", "qa-token-carol",
                    env_override=env_iso,
                )
                self.assertEqual(auth_user.returncode, 0, auth_user.stderr)
                au_out = json.loads(auth_user.stdout)
                self.assertEqual(au_out["credential_store"], "user")
                self.assertEqual(au_out["credential_source"], "user_config")
                self.assertIn("legacy_credentials_warning", au_out)
                with mock.patch.dict(os.environ, env_iso):
                    c_usr = mod.load_jira_credentials(repo_prec)
                    self.assertEqual(c_usr["jira_api_token"], "qa-token-carol")
                    self.assertEqual(c_usr["qase_api_token"], "qase-dotenv")
                    # Runtime env beats user_config AND role_credentials!
                    with mock.patch.dict(os.environ, {"JIRA_EMAIL": "env@team.io", "JIRA_API_TOKEN": "tok-runtime-env", "QASE_API_TOKEN": "qase-runtime-env"}):
                        jc_env = mod.JiraClient(mod.load_team_config(repo_prec), repo_root=repo_prec, role="qa")
                        self.assertEqual(jc_env.email, "env@team.io")
                        self.assertEqual(jc_env.api_token, "tok-runtime-env")
                        qc_env = mod.QaseClient(mod.load_team_config(repo_prec), repo_root=repo_prec)
                        self.assertEqual(qc_env.api_token, "qase-runtime-env")
                        insp_env = mod._inspect_init_state(repo_prec)
                        self.assertEqual(insp_env["credential_source"], "env")

                # 6. Symlink refusal (both user store target & legacy repo target & symlinked creds dir)
                victim = base / "victim.txt"
                victim.write_text("DO_NOT_OVERWRITE", encoding="utf-8")

                repo_sym = base / "sym-repo"
                repo_sym.mkdir()
                self.assertEqual(run_cli(repo_sym, "init", "--project-key", "SYM", "--jira-url", jira_url, env_override=env_iso).returncode, 0)
                with mock.patch.dict(os.environ, env_iso):
                    sym_target = mod.get_user_credentials_path(repo_sym)
                    sym_target.parent.mkdir(parents=True, exist_ok=True)
                    sym_target.symlink_to(victim)
                    with self.assertRaises(RuntimeError):
                        mod.save_jira_credentials(repo_sym, jira_email="a@b.c", jira_token="secret-sym", store="user")
                    self.assertEqual(victim.read_text(encoding="utf-8"), "DO_NOT_OVERWRITE")
                    # Loading must also refuse symlink without reading victim
                    import io as _io
                    with mock.patch("sys.stderr", _io.StringIO()):
                        loaded_sym = mod.load_jira_credentials(repo_sym)
                    self.assertFalse(loaded_sym.get("jira_api_token"))

                # Symlinked credentials directory refusal
                real_dir = base / "real-target-dir"
                real_dir.mkdir()
                sym_creds_dir = base / "sym-creds-dir"
                sym_creds_dir.symlink_to(real_dir)
                with mock.patch.dict(os.environ, {"AI_WORKFLOW_CREDENTIALS_DIR": str(sym_creds_dir)}):
                    with self.assertRaises(RuntimeError):
                        mod.save_jira_credentials(repo_sym, jira_email="a@b.c", jira_token="secret-symdir", store="user")
                self.assertEqual(list(real_dir.iterdir()), [])

                sym_auth_cli = run_cli(
                    repo_sym, "auth",
                    "--jira-email", "alice@team.io",
                    "--jira-token", "tok-alice",
                    env_override=env_iso,
                )
                self.assertNotEqual(sym_auth_cli.returncode, 0)
                self.assertEqual(victim.read_text(encoding="utf-8"), "DO_NOT_OVERWRITE")
                self.assertNotIn("tok-alice", sym_auth_cli.stderr)

                # Legacy repo symlink refusal
                leg_sym = repo_sym / ".ai-workflow/credentials.json"
                leg_sym.symlink_to(victim)
                with mock.patch.dict(os.environ, env_iso):
                    with self.assertRaises(RuntimeError):
                        mod.save_jira_credentials(repo_sym, jira_email="a@b.c", jira_token="secret-leg", store="repo")
                self.assertEqual(victim.read_text(encoding="utf-8"), "DO_NOT_OVERWRITE")

                # 7. Malformed user JSON fails/warns cleanly without leaking secret content
                repo_badjson = base / "badjson-repo"
                repo_badjson.mkdir()
                with mock.patch.dict(os.environ, env_iso):
                    bad_p = mod.get_user_credentials_path(repo_badjson)
                    bad_p.parent.mkdir(parents=True, exist_ok=True)
                    bad_p.write_text('{"jira_api_token": "LEAK_GUARD_SECRET_999", broken', encoding="utf-8")
                    import io
                    stderr_buf = io.StringIO()
                    with mock.patch("sys.stderr", stderr_buf):
                        res_bad = mod.load_jira_credentials(repo_badjson)
                    self.assertFalse(res_bad.get("jira_api_token"))
                    self.assertNotIn("LEAK_GUARD_SECRET_999", stderr_buf.getvalue())

                # 8. Documentation checks: README, team-init SKILL.md, and common-workflow-governance.md
                readme_txt = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
                init_skill_txt = (SKILLS_DIR / "team-init" / "SKILL.md").read_text(encoding="utf-8")
                gov_txt = (SKILLS_DIR / "team-init" / "references" / "common-workflow-governance.md").read_text(encoding="utf-8")
                for label, txt in (("README.md", readme_txt), ("team-init/SKILL.md", init_skill_txt)):
                    self.assertIn(".config/ai-workflow/credentials", txt, f"{label} missing out-of-repo credentials path")
                    self.assertIn(".semgrepignore", txt, f"{label} missing opt-in Semgrep guidance")
                    self.assertIn("JIRA_API_TOKEN", txt, f"{label} missing env-var auth guidance")
                self.assertIn(".config/ai-workflow/credentials", gov_txt)
                self.assertIn("42-test", readme_txt)

                # ------------------------------------------------------------------
                # 9. Round 1 Adversarial Security Review Regressions (P1-1..P1-5, P2-1..P2-5)
                # ------------------------------------------------------------------
                import io as _io_r1

                # 9a. [P1-1] Intermediate $XDG_CONFIG_HOME/ai-workflow symlink write refusal + read refusal on AI_WORKFLOW_CREDENTIALS_DIR & .ai-workflow
                xdg_sym_root = base / "xdg-sym-test"
                xdg_sym_root.mkdir()
                attacker_dir = base / "attacker-target"
                attacker_dir.mkdir()
                (attacker_dir / "credentials").mkdir()
                (xdg_sym_root / "ai-workflow").symlink_to(attacker_dir)
                repo_r1 = base / "r1-sym-repo"
                repo_r1.mkdir()
                with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg_sym_root)}, clear=False):
                    os.environ.pop("AI_WORKFLOW_CREDENTIALS_DIR", None)
                    with self.assertRaises(RuntimeError):
                        mod.save_jira_credentials(repo_r1, jira_email="a@team.io", jira_token="tok-r1", store="user")
                    self.assertEqual(list((attacker_dir / "credentials").iterdir()), [])
                    # Even if attacker pre-populates a credential file inside the symlink target, read MUST refuse it!
                    raw_target_r1, _, fname_r1 = mod._get_user_credentials_target_spec(repo_r1)
                    (attacker_dir / "credentials" / fname_r1).write_text(
                        json.dumps({"jira_email": "evil@attacker.io", "jira_api_token": "evil-tok"}),
                        encoding="utf-8",
                    )
                    with mock.patch("sys.stderr", _io_r1.StringIO()):
                        loaded_xdg_sym = mod.load_jira_credentials(repo_r1)
                    self.assertFalse(loaded_xdg_sym.get("jira_api_token"))
                    self.assertEqual(mod._detect_credential_source(repo_r1), "none")
                os.environ["AI_WORKFLOW_CREDENTIALS_DIR"] = str(creds_dir)

                # Symlinked AI_WORKFLOW_CREDENTIALS_DIR read refusal
                attacker_override = base / "attacker-override-dir"
                attacker_override.mkdir()
                sym_override = base / "sym-override-dir"
                sym_override.symlink_to(attacker_override)
                with mock.patch.dict(os.environ, {"AI_WORKFLOW_CREDENTIALS_DIR": str(sym_override)}):
                    _, _, ov_fname = mod._get_user_credentials_target_spec(repo_r1)
                    (attacker_override / ov_fname).write_text(
                        json.dumps({"jira_email": "evil@attacker.io", "jira_api_token": "evil-ov-tok"}),
                        encoding="utf-8",
                    )
                    with mock.patch("sys.stderr", _io_r1.StringIO()):
                        loaded_ov_sym = mod.load_jira_credentials(repo_r1)
                    self.assertFalse(loaded_ov_sym.get("jira_api_token"))
                    self.assertEqual(mod._detect_credential_source(repo_r1), "none")

                # Symlinked legacy .ai-workflow dir write & read refusal
                repo_leg_sym = base / "repo-leg-sym"
                repo_leg_sym.mkdir()
                attacker_wf = base / "attacker-wf"
                attacker_wf.mkdir()
                (attacker_wf / "credentials.json").write_text(
                    json.dumps({"jira_email": "evil@attacker.io", "jira_api_token": "evil-leg-tok"}),
                    encoding="utf-8",
                )
                (attacker_wf / ".env").write_text("JIRA_API_TOKEN=evil-env-tok\n", encoding="utf-8")
                (repo_leg_sym / ".ai-workflow").symlink_to(attacker_wf)
                with mock.patch.dict(os.environ, env_iso):
                    with mock.patch("sys.stderr", _io_r1.StringIO()):
                        loaded_leg_sym = mod.load_jira_credentials(repo_leg_sym)
                    self.assertFalse(loaded_leg_sym.get("jira_api_token"))
                    self.assertEqual(mod._detect_credential_source(repo_leg_sym), "none")
                    with self.assertRaises(RuntimeError):
                        mod.save_jira_credentials(repo_leg_sym, jira_email="a@b.c", jira_token="tok", store="repo")

                # 9b. [P1-2] Repo directory names containing '..' must NOT crash get_user_credentials_path
                for tricky_name in ("app..v2", "..foo..bar", "..."):
                    tricky_repo = base / tricky_name
                    tricky_repo.mkdir()
                    with mock.patch.dict(os.environ, env_iso):
                        p_tricky = mod.get_user_credentials_path(tricky_repo)
                        self.assertNotIn("..", p_tricky.name)
                        self.assertEqual(p_tricky.parent, creds_dir.resolve())
                        saved_tricky = mod.save_jira_credentials(
                            tricky_repo, jira_email="alice@team.io", jira_token="tok-tricky", store="user"
                        )
                        self.assertTrue(saved_tricky.is_file())

                # 9c. [P1-3] Unrelated JIRA_API_TOKEN_QA must NOT hijack explicit `--role developer` when default JIRA_EMAIL/JIRA_API_TOKEN exist
                repo_p13 = base / "repo-p13"
                repo_p13.mkdir()
                self.assertEqual(
                    run_cli(repo_p13, "init", "--project-key", "P13", "--jira-url", jira_url, env_override=env_iso).returncode,
                    0,
                )
                p13_env = {
                    "AI_WORKFLOW_CREDENTIALS_DIR": str(base / "p13-no-creds"),
                    "JIRA_EMAIL": "bob@team.io",
                    "JIRA_API_TOKEN": "dev-token-bob",
                    "JIRA_EMAIL_QA": "carol@team.io",
                    "JIRA_API_TOKEN_QA": "qa-token-carol",
                }
                p13_auth = run_cli(
                    repo_p13, "auth",
                    "--role", "developer",
                    "--credential-store", "env",
                    env_override=p13_env,
                )
                self.assertEqual(p13_auth.returncode, 0, p13_auth.stderr)
                p13_cfg = json.loads((repo_p13 / ".ai-workflow/team-config.json").read_text(encoding="utf-8"))
                self.assertEqual(p13_cfg["roles"]["developer"]["email"], "bob@team.io")
                self.assertNotEqual(p13_cfg["roles"]["developer"]["email"], "carol@team.io")

                # 9d. [P1-4] Corrupt .git files must NOT collide or escape; fallback safely to repo_resolved
                corrupt_a = base / "corrupt-a"
                corrupt_b = base / "corrupt-b"
                outside_dir = base / "outside-not-git"
                corrupt_a.mkdir()
                corrupt_b.mkdir()
                outside_dir.mkdir()
                (corrupt_a / ".git").write_text("gitdir: /nonexistent/xyz\n", encoding="utf-8")
                (corrupt_b / ".git").write_text("gitdir: /nonexistent/xyz\n", encoding="utf-8")
                corrupt_c = base / "corrupt-c"
                corrupt_c.mkdir()
                (corrupt_c / ".git").write_text(f"gitdir: {outside_dir}\n", encoding="utf-8")
                with mock.patch.dict(os.environ, env_iso):
                    # Normal mode (git binary runs and rejects corrupt .git)
                    self.assertIsNone(mod._resolve_git_common_dir(corrupt_a))
                    self.assertIsNone(mod._resolve_git_common_dir(corrupt_c))
                    pa_norm = mod.get_user_credentials_path(corrupt_a)
                    pb_norm = mod.get_user_credentials_path(corrupt_b)
                    self.assertNotEqual(pa_norm, pb_norm)
                    # Fallback mode (git binary unavailable -> FileNotFoundError): must STILL reject corrupt/non-git dirs!
                    with mock.patch("subprocess.run", side_effect=FileNotFoundError("git missing")):
                        self.assertIsNone(mod._resolve_git_common_dir(corrupt_a))
                        self.assertIsNone(mod._resolve_git_common_dir(corrupt_c))
                        self.assertNotEqual(
                            mod.get_user_credentials_path(corrupt_a),
                            mod.get_user_credentials_path(corrupt_b),
                        )

                # 9e. [P1-5] Failed save_jira_credentials (symlink target) MUST leave team-config.json untouched!
                repo_p15 = base / "repo-p15"
                repo_p15.mkdir()
                self.assertEqual(
                    run_cli(repo_p15, "init", "--project-key", "P15", "--jira-url", jira_url, env_override=env_iso).returncode,
                    0,
                )
                cfg_before_p15 = (repo_p15 / ".ai-workflow/team-config.json").read_bytes()
                with mock.patch.dict(os.environ, env_iso):
                    p15_target = mod.get_user_credentials_path(repo_p15)
                    p15_target.parent.mkdir(parents=True, exist_ok=True)
                    p15_target.symlink_to(victim)
                p15_auth = run_cli(
                    repo_p15, "auth",
                    "--role", "qa",
                    "--jira-email", "carol@team.io",
                    "--jira-token", "qa-token-carol",
                    env_override=env_iso,
                )
                self.assertNotEqual(p15_auth.returncode, 0)
                self.assertEqual((repo_p15 / ".ai-workflow/team-config.json").read_bytes(), cfg_before_p15)
                self.assertEqual(victim.read_text(encoding="utf-8"), "DO_NOT_OVERWRITE")

                # 9f. [P2-1] Enforce 0o700 on BOTH ai-workflow AND ai-workflow/credentials under umask 0o000
                if os.name == "posix":
                    xdg_umask = base / "xdg-umask-zero"
                    xdg_umask.mkdir()
                    repo_umask = base / "repo-umask"
                    repo_umask.mkdir()
                    old_umask = os.umask(0o000)
                    try:
                        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg_umask)}, clear=False):
                            os.environ.pop("AI_WORKFLOW_CREDENTIALS_DIR", None)
                            saved_um = mod.save_jira_credentials(
                                repo_umask, jira_email="alice@team.io", jira_token="tok-um", store="user"
                            )
                        self.assertEqual(stat.S_IMODE((xdg_umask / "ai-workflow").stat().st_mode), 0o700)
                        self.assertEqual(stat.S_IMODE((xdg_umask / "ai-workflow" / "credentials").stat().st_mode), 0o700)
                        self.assertEqual(stat.S_IMODE(saved_um.stat().st_mode), 0o600)
                    finally:
                        os.umask(old_umask)
                        os.environ["AI_WORKFLOW_CREDENTIALS_DIR"] = str(creds_dir)

                # 9g. [P2-2] Role-only env vars (JIRA_EMAIL_PM / JIRA_API_TOKEN_PM) report credentials_configured: true in cmd_init
                repo_p22 = base / "repo-p22"
                repo_p22.mkdir()
                p22_env = {
                    "AI_WORKFLOW_CREDENTIALS_DIR": str(base / "p22-no-creds"),
                    "JIRA_EMAIL": None,
                    "JIRA_API_TOKEN": None,
                    "JIRA_EMAIL_PM": "alice@team.io",
                    "JIRA_API_TOKEN_PM": "tok-pm-only",
                }
                p22_init = run_cli(
                    repo_p22, "init",
                    "--project-key", "P22",
                    "--jira-url", jira_url,
                    "--role", "pm",
                    "--credential-store", "env",
                    env_override=p22_env,
                )
                self.assertEqual(p22_init.returncode, 0, p22_init.stderr)
                p22_out = json.loads(p22_init.stdout)
                self.assertTrue(p22_out["credentials_configured"])
                self.assertTrue(p22_out["role_has_api_token"]["pm"])
                self.assertEqual(p22_out["credential_source"], "env")

                # 9h. [P2-3] cmd_auth --credential-store env MUST NOT create .gitignore, and MUST reject Qase-only on live-Jira role claim
                repo_p23 = base / "repo-p23"
                (repo_p23 / ".ai-workflow").mkdir(parents=True)
                (repo_p23 / ".ai-workflow/team-config.json").write_text(
                    json.dumps({
                        "project_key": "P23",
                        "jira_base_url": jira_url,
                        "roles": {
                            "pm": {"name": "PM Owner", "email": "", "jira_account_id": ""},
                            "developer": {"name": "Developer", "email": "", "jira_account_id": ""},
                        },
                    }),
                    encoding="utf-8",
                )
                self.assertFalse((repo_p23 / ".gitignore").exists())
                p23_env_ok = {
                    "AI_WORKFLOW_CREDENTIALS_DIR": str(base / "p23-no-creds"),
                    "JIRA_EMAIL": "alice@team.io",
                    "JIRA_API_TOKEN": "tok-alice",
                }
                p23_auth_ok = run_cli(
                    repo_p23, "auth",
                    "--credential-store", "env",
                    env_override=p23_env_ok,
                )
                self.assertEqual(p23_auth_ok.returncode, 0, p23_auth_ok.stderr)
                p23_out = json.loads(p23_auth_ok.stdout)
                self.assertFalse(p23_out["gitignore_updated"])
                self.assertFalse((repo_p23 / ".gitignore").exists())

                # Qase-only env var on live-Jira role claim MUST fail closed
                p23_qase_only = run_cli(
                    repo_p23, "auth",
                    "--role", "pm",
                    "--credential-store", "env",
                    env_override={
                        "AI_WORKFLOW_CREDENTIALS_DIR": str(base / "p23-no-creds"),
                        "JIRA_EMAIL": None,
                        "JIRA_API_TOKEN": None,
                        "QASE_API_TOKEN": "qase-only-tok",
                    },
                )
                self.assertNotEqual(p23_qase_only.returncode, 0)
                self.assertNotIn("qase-only-tok", p23_qase_only.stderr)

                # 9i. [P2-4] cmd_init --credential-store env --jira-token secret MUST fail fast BEFORE creating .ai-workflow/active/ without leaking secret
                repo_p24 = base / "repo-p24"
                repo_p24.mkdir()
                p24_bad = run_cli(
                    repo_p24, "init",
                    "--project-key", "P24",
                    "--jira-url", jira_url,
                    "--credential-store", "env",
                    "--jira-token", "SUPER_SECRET_P24_LEAK_CHECK",
                    env_override=env_iso,
                )
                self.assertNotEqual(p24_bad.returncode, 0)
                self.assertNotIn("SUPER_SECRET_P24_LEAK_CHECK", p24_bad.stderr)
                self.assertNotIn("SUPER_SECRET_P24_LEAK_CHECK", p24_bad.stdout)
                self.assertFalse((repo_p24 / ".ai-workflow/active").exists())
                self.assertFalse((repo_p24 / ".ai-workflow").exists())
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()

    def test_42_team_provenance_audit_mandatory_gate(self):
        """Verify mandatory default-on team-provenance-audit gate in init, status, and close-story."""
        mod = self._load_jira_sync_module()
        pa_scripts = SKILLS_DIR / "team-provenance-audit" / "scripts"
        pa_policy = SKILLS_DIR / "team-provenance-audit" / "assets" / "policy.example.json"

        # 1. Sanitizer unit checks: default-on, legacy missing block, strict bool, control char rejection
        self.assertEqual(
            mod._sanitize_compliance_gates(None),
            {"provenance_audit": {"enabled": True, "mode": "blocking", "policy_path": "", "output_dir": ""}},
        )
        self.assertEqual(
            mod._sanitize_compliance_gates({}),
            {"provenance_audit": {"enabled": True, "mode": "blocking", "policy_path": "", "output_dir": ""}},
        )
        # Strict boolean: string "false" MUST disable (never Python raw bool("false") == True)
        self.assertEqual(
            mod._sanitize_compliance_gates({"provenance_audit": {"enabled": "false", "mode": "blocking"}})["provenance_audit"],
            {"enabled": False, "mode": "off", "policy_path": "", "output_dir": ""},
        )
        self.assertEqual(
            mod._sanitize_compliance_gates({"provenance_audit": {"mode": "off"}})["provenance_audit"],
            {"enabled": False, "mode": "off", "policy_path": "", "output_dir": ""},
        )
        self.assertEqual(
            mod._sanitize_compliance_gates({"provenance_audit": {"mode": "invalid-mode"}})["provenance_audit"],
            {"enabled": True, "mode": "blocking", "policy_path": "", "output_dir": ""},
        )
        self.assertEqual(
            mod._sanitize_compliance_gates(
                {"provenance_audit": {"mode": "blocking", "policy_path": "bad\x00p", "output_dir": "bad\ndir"}}
            )["provenance_audit"],
            {"enabled": True, "mode": "blocking", "policy_path": "", "output_dir": ""},
        )

        # Start mock Jira server for full E2E ADF & fail-closed mutation checks
        MockJiraHandler.requests_log = []
        MockJiraHandler.stored_issues = {}
        MockJiraHandler.stored_properties = {}
        MockJiraHandler.stored_attachments = {}
        jira_srv = HTTPServer(("127.0.0.1", 0), MockJiraHandler)
        jira_port = jira_srv.server_address[1]
        jira_url = f"http://127.0.0.1:{jira_port}"
        threading.Thread(target=jira_srv.serve_forever, daemon=True).start()

        try:
            with tempfile.TemporaryDirectory(prefix="aiwf-prov-e2e-") as tmp:
                base = Path(tmp)
                repo = base / "demo-repo"
                repo.mkdir()
                subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "dev@team.io"], cwd=str(repo), check=True, capture_output=True)
                subprocess.run(["git", "config", "user.name", "Dev"], cwd=str(repo), check=True, capture_output=True)
                (repo / "app.py").write_text("print('hello world')\n", encoding="utf-8")
                subprocess.run(["git", "add", "app.py"], cwd=str(repo), check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo), check=True, capture_output=True)
                head1 = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True
                ).stdout.strip().lower()
                head1_short = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True
                ).stdout.strip()

                roles = {
                    "pm": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "tech_lead": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "developer": {"name": "Bob", "email": "bob@team.io", "jira_account_id": "acc-bob"},
                    "reviewer": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                    "qa": {"name": "Carol", "email": "carol@team.io", "jira_account_id": "acc-carol"},
                    "release_manager": {"name": "Alice", "email": "alice@team.io", "jira_account_id": "acc-alice"},
                }

                # 2. Default cmd_init sets enabled=True, mode="blocking" and exposes provenance_audit_dir
                init_res = run_cli(
                    repo, "init",
                    "--project-key", "PRV",
                    "--jira-url", jira_url,
                    "--jira-email", "alice@team.io",
                    "--jira-token", "tok-alice",
                    "--roles-json", json.dumps(roles),
                )
                self.assertEqual(init_res.returncode, 0, init_res.stderr)
                init_out = json.loads(init_res.stdout)
                self.assertEqual(
                    init_out["compliance_gates"]["provenance_audit"],
                    {"enabled": True, "mode": "blocking", "policy_path": "", "output_dir": ""},
                )
                audit_dir = Path(init_out["provenance_audit_dir"])
                # Must be strictly inside our isolated AI_WORKFLOW_PROVENANCE_DIR and outside repo
                self.assertTrue(str(audit_dir).startswith(os.environ["AI_WORKFLOW_PROVENANCE_DIR"]))
                self.assertNotIn(str(repo.resolve()), str(audit_dir))

                # init --inspect also exposes compliance_gates & provenance_audit_dir
                insp_out = json.loads(run_cli(repo, "init", "--inspect").stdout)
                self.assertEqual(insp_out["compliance_gates"]["provenance_audit"]["mode"], "blocking")
                self.assertEqual(insp_out["provenance_audit_dir"], str(audit_dir))

                # Verify upgraded legacy team-config.json (missing compliance_gates) still enforces blocking!
                cfg_path = repo / ".ai-workflow" / "team-config.json"
                raw_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                raw_cfg.pop("compliance_gates", None)
                cfg_path.write_text(json.dumps(raw_cfg, indent=2), encoding="utf-8")
                legacy_loaded = mod.load_team_config(repo)
                self.assertTrue(legacy_loaded["compliance_gates"]["provenance_audit"]["enabled"])
                self.assertEqual(legacy_loaded["compliance_gates"]["provenance_audit"]["mode"], "blocking")

                # Advance Story PRV-1 through Spec -> Dev -> Review -> QA
                MockJiraHandler.stored_issues["PRV-1"] = {
                    "key": "PRV-1",
                    "fields": {"summary": "Provenance Gated Feature", "issuetype": {"name": "Story", "subtask": False}, "status": {"name": "To Do"}},
                }
                self.assertEqual(run_cli(repo, "sync-story", "--key", "PRV-1", "--title", "Provenance Gated Feature", "--ac", "AC-001:Works", "--status", "approved").returncode, 0)
                self.assertEqual(run_cli(repo, "complete-task", "--key", "PRV-1", "--task-id", "TASK-001", "--commit", head1_short, "--files", "app.py", "--test-result", "pytest ok").returncode, 0)
                self.assertEqual(run_cli(repo, "handoff", "--key", "PRV-1", "--review-status", "approved", "--commit", head1_short).returncode, 0)
                self.assertEqual(run_cli(repo, "record-qa", "--key", "PRV-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Works").returncode, 0)
                self.assertEqual(run_cli(repo, "record-qa", "--key", "PRV-1", "--mode", "verify", "--commit", head1_short, "--all-pass").returncode, 0)

                def _assert_close_blocked(expected_status, extra_args=()):
                    MockJiraHandler.requests_log.clear()
                    res = run_cli(repo, "close-story", "--key", "PRV-1", "--commit", head1_short, *extra_args)
                    self.assertNotEqual(res.returncode, 0, f"expected block for {expected_status}, got stdout={res.stdout}")
                    err = json.loads(res.stderr)
                    self.assertTrue(err["blocked"])
                    self.assertEqual(err["provenance_audit_gate"]["status"], expected_status)
                    # ZERO Jira comment / transition mutations and active file MUST remain intact!
                    mut_reqs = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and (r[1].endswith("/comment") or r[1].endswith("/transitions"))]
                    self.assertEqual(mut_reqs, [], f"unexpected Jira mutations on blocked close: {mut_reqs}")
                    self.assertTrue((repo / ".ai-workflow" / "active" / "PRV-1.json").exists())

                # 3a. status blocks release readiness until decision.json PASS exists
                st_missing = json.loads(run_cli(repo, "status", "--key", "PRV-1").stdout)
                self.assertEqual(st_missing["recommended_skill"], "team-provenance-audit")
                self.assertTrue(st_missing["release_blocked_by_provenance_audit"])
                self.assertFalse(st_missing["can_auto_chain_next_station"])
                self.assertTrue(st_missing["requires_user_confirmation_for_next_station"])
                self.assertEqual(st_missing["provenance_audit"]["status"], "missing")

                # 3b. Block on missing decision.json
                _assert_close_blocked("missing")

                # 3c. Block on malformed JSON
                audit_dir.mkdir(parents=True, exist_ok=True)
                dec_path = audit_dir / "decision.json"
                dec_path.write_text("{not valid json", encoding="utf-8")
                _assert_close_blocked("invalid_json")

                # 3d. Block on symlinked decision.json
                real_dec = base / "real-decision.json"
                checks_all = [{"id": f"{i:02}", "status": "Completed", "method": "verified"} for i in range(1, 15)]
                valid_doc = {
                    "verdict": "PASS",
                    "confirmed_failures": [],
                    "unresolved_reasons": [],
                    "checked_controls": [f"{i:02}" for i in range(1, 15)],
                    "required_controls": [f"{i:02}" for i in range(1, 15)],
                    "audited_commit": head1,
                    "generated_utc": "2026-11-26T00:00:00+00:00",
                }
                real_dec.write_text(json.dumps(valid_doc), encoding="utf-8")
                dec_path.unlink()
                dec_path.symlink_to(real_dec)
                _assert_close_blocked("symlink_rejected")
                dec_path.unlink()

                # 3e. Block on symlinked parent directory AND intermediate ancestor symlink via --provenance-decision & config.output_dir
                real_dir = base / "real-dir"
                real_sub = real_dir / "sub"
                real_sub.mkdir(parents=True)
                (real_dir / "decision.json").write_text(json.dumps(valid_doc), encoding="utf-8")
                (real_sub / "decision.json").write_text(json.dumps(valid_doc), encoding="utf-8")
                sym_dir = base / "sym-dir"
                sym_dir.symlink_to(real_dir)
                _assert_close_blocked("symlink_rejected", extra_args=("--provenance-decision", str(sym_dir / "decision.json")))
                # Intermediate ancestor symlink: <base>/sym-dir/sub/decision.json (sym-dir -> real_dir, sub is real dir)
                _assert_close_blocked("symlink_rejected", extra_args=("--provenance-decision", str(sym_dir / "sub" / "decision.json")))
                # Intermediate ancestor symlink in config.compliance_gates.provenance_audit.output_dir
                cfg_sym_out = {
                    "compliance_gates": {
                        "provenance_audit": {
                            "enabled": True,
                            "mode": "blocking",
                            "policy_path": "",
                            "output_dir": str(sym_dir / "sub"),
                        }
                    }
                }
                ev_sym_out = mod._evaluate_provenance_audit_gate(repo, cfg_sym_out)
                self.assertFalse(ev_sym_out["passed"])
                self.assertEqual(ev_sym_out["status"], "symlink_rejected")
                # Normal paths under /var/folders/... (tempdir) and /tmp/... without intermediate symlinks MUST PASS
                ev_normal_var = mod._evaluate_provenance_audit_gate(
                    repo,
                    {"compliance_gates": {"provenance_audit": {"enabled": True, "mode": "blocking", "policy_path": "", "output_dir": str(real_sub)}}},
                )
                self.assertTrue(ev_normal_var["passed"], ev_normal_var)
                self.assertEqual(ev_normal_var["status"], "pass")
                with tempfile.TemporaryDirectory(prefix="aiwf-tmp-prov-", dir="/tmp") as tmp_slash:
                    tmp_sub = Path(tmp_slash) / "sub"
                    tmp_sub.mkdir()
                    tmp_dec = tmp_sub / "decision.json"
                    tmp_dec.write_text(json.dumps(valid_doc), encoding="utf-8")
                    ev_normal_tmp = mod._evaluate_provenance_audit_gate(
                        repo,
                        {"compliance_gates": {"provenance_audit": {"enabled": True, "mode": "blocking", "policy_path": "", "output_dir": ""}}},
                        decision_path_override=str(tmp_dec),
                    )
                    self.assertTrue(ev_normal_tmp["passed"], ev_normal_tmp)
                    self.assertEqual(ev_normal_tmp["status"], "pass")

                # 3f. Block on verdict == FAIL
                dec_path.write_text(json.dumps({**valid_doc, "verdict": "FAIL", "confirmed_failures": ["FIND-01: bad"]}), encoding="utf-8")
                _assert_close_blocked("failed")

                # 3g. Block on verdict == REVIEW REQUIRED
                dec_path.write_text(json.dumps({**valid_doc, "verdict": "REVIEW REQUIRED", "unresolved_reasons": ["check 05 incomplete"]}), encoding="utf-8")
                _assert_close_blocked("review_required")

                # 3h. Block on tampered PASS with non-empty confirmed_failures
                dec_path.write_text(json.dumps({**valid_doc, "confirmed_failures": ["tampered"]}), encoding="utf-8")
                _assert_close_blocked("failed")

                # 3i. Block on tampered PASS with non-empty unresolved_reasons
                dec_path.write_text(json.dumps({**valid_doc, "unresolved_reasons": ["tampered"]}), encoding="utf-8")
                _assert_close_blocked("review_required")

                # 3j. Block on tampered PASS with missing required controls
                dec_path.write_text(json.dumps({**valid_doc, "checked_controls": ["01", "02"]}), encoding="utf-8")
                _assert_close_blocked("incomplete_controls")

                # 4. Real end-to-end provenance scan + commit-bound gate.py --repo
                dec_path.unlink()
                scan_res = subprocess.run(
                    [sys.executable, str(pa_scripts / "static_scan.py"), "--repo", str(repo), "--out", str(audit_dir)],
                    capture_output=True, text=True, check=True,
                )
                self.assertGreaterEqual(json.loads(scan_res.stdout)["files"], 1)
                cov_file = audit_dir / "coverage.json"
                find_file = audit_dir / "findings.json"
                cov_file.write_text(json.dumps({"checks": checks_all, "scan_errors": 0, "scan_skipped": 0}), encoding="utf-8")
                find_file.write_text("[]", encoding="utf-8")
                gate_res = subprocess.run(
                    [
                        sys.executable, str(pa_scripts / "gate.py"),
                        "--repo", str(repo),
                        "--coverage", str(cov_file),
                        "--findings", str(find_file),
                        "--policy", str(pa_policy),
                        "--out", str(dec_path),
                    ],
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(json.loads(gate_res.stdout)["verdict"], "PASS")
                self.assertEqual(json.loads(dec_path.read_text(encoding="utf-8"))["audited_commit"], head1)

                # Now status unblocks team-release!
                st_ready = json.loads(run_cli(repo, "status", "--key", "PRV-1").stdout)
                self.assertEqual(st_ready["recommended_skill"], "team-release")
                self.assertEqual(st_ready["provenance_audit"]["status"], "pass")

                # 5. New commit makes decision.json STALE -> MUST BLOCK with stale_commit!
                (repo / "app.py").write_text("print('hello v2')\n", encoding="utf-8")
                subprocess.run(["git", "add", "app.py"], cwd=str(repo), check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "second commit"], cwd=str(repo), check=True, capture_output=True)
                head2 = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True
                ).stdout.strip().lower()
                self.assertNotEqual(head1, head2)
                _assert_close_blocked("stale_commit")
                st_stale = json.loads(run_cli(repo, "status", "--key", "PRV-1").stdout)
                self.assertEqual(st_stale["recommended_skill"], "team-provenance-audit")
                self.assertEqual(st_stale["provenance_audit"]["status"], "stale_commit")

                # Re-run gate.py on new HEAD -> fresh PASS unblocks close-story!
                subprocess.run(
                    [
                        sys.executable, str(pa_scripts / "gate.py"),
                        "--repo", str(repo),
                        "--coverage", str(cov_file),
                        "--findings", str(find_file),
                        "--policy", str(pa_policy),
                        "--out", str(dec_path),
                    ],
                    capture_output=True, text=True, check=True,
                )

                MockJiraHandler.requests_log.clear()
                ok_close = run_cli(repo, "close-story", "--key", "PRV-1", "--commit", head1_short, "--version", "v1.7.0")
                self.assertEqual(ok_close.returncode, 0, ok_close.stderr)
                ok_out = json.loads(ok_close.stdout)
                self.assertTrue(ok_out["ok"])
                self.assertEqual(ok_out["provenance_audit"]["verdict"], "PASS")
                self.assertEqual(ok_out["provenance_audit"]["audited_commit"], head2)
                self.assertEqual(ok_out["provenance_audit"]["checked_controls_count"], 14)

                # Verify Jira closing ADF comment is valid AND contains zero local paths or secrets!
                close_comments = [r for r in MockJiraHandler.requests_log if r[0] == "POST" and r[1].endswith("/comment")]
                self.assertEqual(len(close_comments), 1)
                adf_body = close_comments[0][2]["body"]
                self._assert_valid_adf_invariants(adf_body)
                adf_text = json.dumps(adf_body, ensure_ascii=False)
                self.assertIn("team-provenance-audit", adf_text)
                self.assertIn("PASS", adf_text)
                self.assertIn(head2, adf_text)
                self.assertNotIn(str(audit_dir), adf_text)
                self.assertNotIn(str(base), adf_text)
                self.assertNotIn("tok-alice", adf_text)

                # 6. Advisory mode warns without blocking close-story
                adv_repo = base / "adv-repo"
                adv_repo.mkdir()
                self.assertEqual(run_cli(adv_repo, "init", "--project-key", "ADV", "--provenance-audit-mode", "advisory").returncode, 0)
                self.assertEqual(run_cli(adv_repo, "sync-story", "--key", "ADV-1", "--title", "Adv", "--ac", "AC-001:Ok", "--status", "approved").returncode, 0)
                self.assertEqual(run_cli(adv_repo, "complete-task", "--key", "ADV-1", "--task-id", "TASK-001", "--commit", "a1", "--files", "a.py", "--test-result", "ok").returncode, 0)
                self.assertEqual(run_cli(adv_repo, "handoff", "--key", "ADV-1", "--review-status", "approved", "--commit", "a1").returncode, 0)
                self.assertEqual(run_cli(adv_repo, "record-qa", "--key", "ADV-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Ok").returncode, 0)
                self.assertEqual(run_cli(adv_repo, "record-qa", "--key", "ADV-1", "--mode", "verify", "--commit", "a1", "--all-pass").returncode, 0)
                adv_close = run_cli(adv_repo, "close-story", "--key", "ADV-1", "--commit", "a1")
                self.assertEqual(adv_close.returncode, 0, adv_close.stderr)
                adv_out = json.loads(adv_close.stdout)
                self.assertTrue(adv_out["ok"])
                self.assertEqual(adv_out["provenance_audit_warning"]["status"], "missing")
                self.assertEqual(adv_out["provenance_audit_warning"]["mode"], "advisory")

                # 7. Off mode skips gate completely
                off_repo = base / "off-repo"
                off_repo.mkdir()
                self.assertEqual(run_cli(off_repo, "init", "--project-key", "OFF", "--provenance-audit-mode", "off").returncode, 0)
                self.assertEqual(run_cli(off_repo, "sync-story", "--key", "OFF-1", "--title", "Off", "--ac", "AC-001:Ok", "--status", "approved").returncode, 0)
                self.assertEqual(run_cli(off_repo, "complete-task", "--key", "OFF-1", "--task-id", "TASK-001", "--commit", "o1", "--files", "o.py", "--test-result", "ok").returncode, 0)
                self.assertEqual(run_cli(off_repo, "handoff", "--key", "OFF-1", "--review-status", "approved", "--commit", "o1").returncode, 0)
                self.assertEqual(run_cli(off_repo, "record-qa", "--key", "OFF-1", "--mode", "plan", "--qa-backend", "jira-comment", "--tc", "TC-001:AC-001:automated:Ok").returncode, 0)
                self.assertEqual(run_cli(off_repo, "record-qa", "--key", "OFF-1", "--mode", "verify", "--commit", "o1", "--all-pass").returncode, 0)
                off_close = run_cli(off_repo, "close-story", "--key", "OFF-1", "--commit", "o1")
                self.assertEqual(off_close.returncode, 0, off_close.stderr)
                off_out = json.loads(off_close.stdout)
                self.assertTrue(off_out["ok"])
                self.assertNotIn("provenance_audit", off_out)
                self.assertNotIn("provenance_audit_warning", off_out)
        finally:
            jira_srv.shutdown()
            jira_srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
