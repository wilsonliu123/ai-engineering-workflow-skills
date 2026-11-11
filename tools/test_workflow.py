#!/usr/bin/env python3
"""
End-to-end and unit test suite for the 7-skill, 4-station Jira-centered workflow.
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
"""

import hashlib
import json
import os
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
}


def run_cli(repo_root: Path, *args, env_override: dict = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_override is None:
        env.pop("JIRA_BASE_URL", None)
        env.pop("JIRA_EMAIL", None)
        env.pop("JIRA_API_TOKEN", None)
    else:
        env.update(env_override)
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
    simulate_put_404_keys = set()
    reject_subtask_types = set()
    reject_attachment_keys = set()

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
            if key in self.stored_issues:
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
        if self.path == "/rest/api/3/search/jql":
            jql = data.get("jql") or ""
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
                if "404" in (fields.get("summary") or ""):
                    key = f"{proj_key}-404"
                elif proj_key != "PROJ":
                    key = f"{proj_key}-1"
                else:
                    key = "PROJ-101"
                self.stored_issues[key] = {
                    "key": key,
                    "fields": {
                        "summary": fields.get("summary") or key,
                        "description": fields.get("description"),
                        "issuetype": {"name": itype or "Story", "subtask": False},
                        "status": {"name": "In Progress"},
                    },
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
            tid = str(((data.get("transition") or {}).get("id")) or "")
            id_to_status = {"11": "In Progress", "21": "In QA", "31": "Ready for Release", "41": "Done"}
            if key in self.stored_issues and tid in id_to_status:
                self.stored_issues[key].setdefault("fields", {})["status"] = {"name": id_to_status[tid]}
            self._send_json(204, {})
        else:
            self._send_json(200, {"ok": True})

    def do_PUT(self):
        data = self._read_json()
        self.requests_log.append(("PUT", self.path, data, dict(self.headers)))
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

    def test_01_skill_bundle_structure_and_shared_files(self):
        actual_skills = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and p.name != "system"}
        self.assertEqual(actual_skills, EXPECTED_SKILLS)

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

        # Verify check_shared.sh passes
        res = subprocess.run([str(REPO_ROOT / "tools" / "check_shared.sh")], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

        # Verify evals.json files are valid JSON
        for eval_skill in ("team-next-step", "team-qa"):
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
            run_cli(root, "init", "--project-key", "PROJ", "--roles-json", json.dumps(roles))

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

            init_res = run_cli(root, "init", "--project-key", "PROJ")
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
            run_cli(root, "init", "--project-key", "PROJ")

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
                self.assertEqual(init_out["credentials_path"], ".ai-workflow/credentials.json")
                self.assertTrue(init_out["jira_verification"]["verified"])
                self.assertEqual(init_out["jira_verification"]["account_id"], "acc-alice")
                self.assertTrue(init_out["role_has_api_token"]["developer"])
                self.assertTrue(init_out["role_has_api_token"]["qa"])
                self.assertEqual(init_out["role_verifications"]["developer"]["account_id"], "acc-bob-from-token")
                self.assertEqual(init_out["role_verifications"]["qa"]["account_id"], "acc-carol-from-token")

                # Verify auto-resolved jira_account_id from role token /rest/api/3/myself
                self.assertEqual(init_out["auto_resolved_account_ids"]["developer"], "acc-bob-from-token")
                self.assertEqual(init_out["auto_resolved_account_ids"]["qa"], "acc-carol-from-token")

                # Verify .ai-workflow/credentials.json exists (0600) and .ai-workflow/team-config.json NEVER contains ANY tokens
                cred_path = root / ".ai-workflow/credentials.json"
                self.assertTrue(cred_path.exists())
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

                # Secret hygiene: qase-secret-123 must NEVER appear in team-config.json, ONLY in credentials.json
                cfg_raw = (root / ".ai-workflow/team-config.json").read_text(encoding="utf-8")
                self.assertNotIn("qase-secret-123", cfg_raw)
                cfg_qase = json.loads(cfg_raw)
                self.assertEqual(cfg_qase["qa_test_backend"], "qase")
                self.assertEqual(cfg_qase["qase"]["project_code"], "AP")
                cred_qase = json.loads((root / ".ai-workflow/credentials.json").read_text(encoding="utf-8"))
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
                # Verify team-config.json is byte-for-byte untouched!
                self.assertEqual((carol_root / ".ai-workflow/team-config.json").read_bytes(), cfg_bytes)
                carol_cred = json.loads((carol_root / ".ai-workflow/credentials.json").read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
