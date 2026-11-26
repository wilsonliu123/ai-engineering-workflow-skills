#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
ASSETS = SCRIPTS.parent / "assets"


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], text=True, capture_output=True, check=True)


class SkillSmokeTests(unittest.TestCase):
    def test_static_resume_and_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "repo"
            out = home / "audit"
            repo.mkdir()
            (repo / ".npmrc").write_text("registry=https://registry.npmmirror.com\n", encoding="utf-8")
            (repo / "code.js").write_text('const x = "配置环境变量";\nconst origin="https://api.example.cn/v1";\neval(text);\n', encoding="utf-8")
            with zipfile.ZipFile(repo / "library.jar", "w") as z:
                z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nImplementation-Vendor: Tencent\n")
                z.writestr("BOOT-INF/lib/nested.jar", b"dummy")
            r1 = json.loads(run(SCRIPTS / "static_scan.py", "--repo", repo, "--out", out).stdout)
            self.assertEqual(r1["files"], 3)
            self.assertEqual(r1["reused"], 0)
            leads = [json.loads(line) for line in (out / "candidates.jsonl").read_text().splitlines()]
            self.assertTrue(any(x["category"] == "simplified_character_candidate" and x["path"] == "code.js" for x in leads))
            self.assertTrue(any(x["path"].endswith("!/META-INF/MANIFEST.MF") for x in leads))
            self.assertTrue(any(x["category"] == "javascript_obfuscation_candidate" for x in leads))
            r2 = json.loads(run(SCRIPTS / "static_scan.py", "--repo", repo, "--out", out).stdout)
            self.assertEqual(r2["reused"], 3)
            (repo / "code.js").write_text('const q="bytedance";\n', encoding="utf-8")
            r3 = json.loads(run(SCRIPTS / "static_scan.py", "--repo", repo, "--out", out).stdout)
            self.assertEqual(r3["reused"], 2)
            self.assertEqual(r3["rescanned"], 1)
            new_leads = (out / "candidates.jsonl").read_text()
            self.assertIn("bytedance", new_leads)
            self.assertNotIn("api.example.cn", new_leads)

    def test_static_archive_paths_and_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "repo"
            out = home / "audit"
            repo.mkdir()
            (repo / "requirements-dev.txt").write_text("requests==2.32.0\n", encoding="utf-8")
            (repo / "real.txt").write_text("hello world\n", encoding="utf-8")
            (repo / "big.txt").write_text("a" * 2048, encoding="utf-8")
            (repo / "link.txt").symlink_to(repo / "real.txt")
            with zipfile.ZipFile(repo / "sample.jar", "w") as z:
                z.writestr("../escape.txt", "blocked")
                z.writestr("com/alibaba/fastjson/JSON.class", b"\xca\xfe\xba\xbe")
                z.writestr("BOOT-INF/lib/nested-bytedance.jar", b"dummy")
            res = json.loads(run(SCRIPTS / "static_scan.py", "--repo", repo, "--out", out, "--max-text-bytes", "512").stdout)
            self.assertEqual(res["statuses"].get("skipped_symlink"), 1)
            self.assertEqual(res["statuses"].get("skipped_size"), 1)
            inv = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
            self.assertIn("requirements-dev.txt", inv["manifest_paths"])
            jar_entry = next(f for f in inv["files"] if f["path"] == "sample.jar")
            self.assertIn("unsafe_archive_member_path", jar_entry["details"]["warnings"])
            leads = [json.loads(line) for line in (out / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(x["category"] == "archive_java_namespace_candidate" for x in leads))
            self.assertTrue(any(x["category"] == "archive_enterprise_path_candidate" for x in leads))

    def test_fingerprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            a, b = p / "a.jar", p / "b.jar"
            with zipfile.ZipFile(a, "w") as z:
                z.writestr("a/Foo.class", b"class1")
                z.writestr("foo.txt", b"different")
            with zipfile.ZipFile(b, "w") as z:
                z.writestr("renamed/Bar.class", b"class1")
                z.writestr("foo.txt", b"other")
            output = p / "fingerprint.json"
            run(SCRIPTS / "fingerprint.py", "--target", a, "--reference", b, "--out", output)
            parsed = json.loads(output.read_text())
            self.assertFalse(parsed["exact_file_match"])
            self.assertEqual(parsed["archive_member_comparison"]["exact_shared_member_hash_count"], 1)
            link_a = p / "link_a.jar"
            link_a.symlink_to(a)
            sym_res = subprocess.run([sys.executable, str(SCRIPTS / "fingerprint.py"), "--target", str(link_a), "--reference", str(b), "--out", str(p / "sym.json")], text=True, capture_output=True)
            self.assertNotEqual(sym_res.returncode, 0)
            self.assertIn("symlinks not supported", sym_res.stderr)

    def test_gate_pass_review_fail_and_mirror(self):
        spec = importlib.util.spec_from_file_location("gate", SCRIPTS / "gate.py")
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        policy = json.loads((ASSETS / "policy.example.json").read_text())
        checks = [{"id": f"{i:02}", "status": "Completed", "method": "measured check"} for i in range(1, 15)]
        coverage = {"checks": checks, "scan_errors": 0, "scan_skipped": 0, "critical_unverified_components": [], "material_blind_spots": []}
        self.assertEqual(gate.decide(coverage, [], policy)["verdict"], "PASS")
        findings = [{"id": "FIND-01", "association": "A", "policy_basis": "component_developed_by_mainland_enterprise", "evidence_status": "verified_primary", "evidence": [{"kind":"official_repo", "ref":"https://example.org"}], "location": "go.mod:2"}]
        self.assertEqual(gate.decide(coverage, findings, policy)["verdict"], "FAIL")
        mirror = [{"id": "FIND-02", "association": "A", "policy_basis": "service_or_mirror", "evidence_status": "verified_primary", "location":".npmrc:1", "evidence": [{"kind":"service_provider"}]}]
        self.assertEqual(gate.decide(coverage, mirror, policy)["verdict"], "REVIEW REQUIRED")
        self.assertEqual(gate.decide(coverage, mirror, dict(policy, service_and_mirror_handling="fail"))["verdict"], "FAIL")
        self.assertEqual(gate.decide(coverage, mirror, dict(policy, service_and_mirror_handling="informational"))["verdict"], "PASS")
        cov_unverified = dict(coverage, critical_unverified_components=["libfoo.so"])
        self.assertEqual(gate.decide(cov_unverified, [], policy)["verdict"], "REVIEW REQUIRED")
        self.assertEqual(gate.decide(cov_unverified, [], dict(policy, pass_requires_no_critical_unverified=False))["verdict"], "PASS")
        checks[4]["status"] = "Partially Completed"
        self.assertEqual(gate.decide(coverage, [], policy)["verdict"], "REVIEW REQUIRED")
        self.assertEqual(gate.decide(coverage, [], dict(policy, pass_requires_complete_coverage=False))["verdict"], "PASS")
        checks[4]["status"] = "Not Applicable"
        checks[4]["reason"] = "No dependencies"
        self.assertEqual(gate.decide(coverage, [], policy)["verdict"], "PASS")

    def test_static_scan_explicit_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "repo"
            out = home / "audit"
            (repo / "keep").mkdir(parents=True)
            (repo / "skipme").mkdir(parents=True)
            (repo / "keep" / "ok.txt").write_text("hello\n", encoding="utf-8")
            (repo / "skipme" / "noise.txt").write_text("alibaba bytedance\n", encoding="utf-8")
            res = json.loads(run(SCRIPTS / "static_scan.py", "--repo", repo, "--out", out, "--exclude", "skipme").stdout)
            self.assertEqual(res["files"], 1)
            inv = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
            self.assertTrue(any(p["path"] == "skipme" and p["reason"] == "explicitly_excluded" for p in inv["pruned_directories"]))

    def test_check_env_and_stub_detection(self):
        spec = importlib.util.spec_from_file_location("check_env", SCRIPTS / "check_env.py")
        check_env = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check_env)
        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "env.json"
            res = json.loads(run(SCRIPTS / "check_env.py", "--out", out_json).stdout)
            self.assertTrue(out_json.exists())
            self.assertIn("tools", res)
            self.assertIn("jar", res["tools"])
            self.assertIn("readelf", res["tools"])
            self.assertIn("syft", res["tools"])
            # Negative control: fake macOS Java stub must be flagged as stub_unusable
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            stub = fake_bin / "fakejar"
            stub.write_text("#!/bin/sh\necho 'Unable to locate a Java Runtime.' >&2\nexit 1\n", encoding="utf-8")
            stub.chmod(0o755)
            probed = check_env.probe_tool(["fakejar"], ["--version"], {"PATH": str(fake_bin)})
            self.assertEqual(probed["status"], "stub_unusable")
            missing = check_env.probe_tool(["definitely_nonexistent_tool_xyz"], ["--version"], {"PATH": str(fake_bin)})
            self.assertEqual(missing["status"], "missing")

    def test_fetch_reference_allowlist_and_corpus_fingerprint(self):
        import hashlib
        import http.server
        import threading

        spec = importlib.util.spec_from_file_location("fetch_reference", SCRIPTS / "fetch_reference.py")
        fr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fr)

        # Negative controls: untrusted host & external plain HTTP must fail closed
        with self.assertRaises(ValueError):
            fr.validate_url("https://evil.example.com/foo.jar")
        with self.assertRaises(ValueError):
            fr.validate_url("http://repo1.maven.org/maven2/foo.jar")

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            # Create a realistic synthetic JAR with pom.properties
            target_jar = p / "demo-1.0.0.jar"
            with zipfile.ZipFile(target_jar, "w") as z:
                z.writestr("META-INF/maven/org.example/demo/pom.properties", "groupId=org.example\nartifactId=demo\nversion=1.0.0\n")
                z.writestr("org/example/Demo.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x34")
            jar_bytes = target_jar.read_bytes()
            jar_sha1 = hashlib.sha1(jar_bytes).hexdigest()

            # Verify pom.properties extraction
            self.assertEqual(fr.extract_maven_specs_from_jar(target_jar), ["maven:org.example:demo:1.0.0"])

            # Serve synthetic Maven Central locally on 127.0.0.1
            srv_root = p / "m2"
            art_dir = srv_root / "org" / "example" / "demo" / "1.0.0"
            art_dir.mkdir(parents=True)
            (art_dir / "demo-1.0.0.jar").write_bytes(jar_bytes)
            (art_dir / "demo-1.0.0.jar.sha1").write_text(jar_sha1 + "\n", encoding="utf-8")

            class QuietHandler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *a, **kw):
                    super().__init__(*a, directory=str(srv_root), **kw)
                def log_message(self, *a):
                    pass

            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                corpus_dir = p / "corpus"
                parsed = fr.parse_spec("maven:org.example:demo:1.0.0")
                entry = fr.resolve_maven(
                    parsed,
                    mode="download",
                    corpus_dir=corpus_dir,
                    target_hashes=fr.hash_file(target_jar),
                    base_url=f"http://127.0.0.1:{port}",
                    allowed_hosts={"127.0.0.1"},
                )
                self.assertTrue(entry["registry_checksum_verified"])
                self.assertTrue(entry["target_sha1_match"])
                self.assertEqual(entry["reference_origin_status"], "VERIFIED_PUBLIC_REGISTRY")

                manifest_path = corpus_dir / "corpus_manifest.json"
                manifest_path.write_text(json.dumps({"entries": [entry]}), encoding="utf-8")
                fp_out = p / "fp_corpus.json"
                summary = json.loads(run(SCRIPTS / "fingerprint.py", "--target", target_jar, "--corpus-manifest", manifest_path, "--out", fp_out).stdout)
                self.assertTrue(summary["any_exact_file_match"])
                fp_doc = json.loads(fp_out.read_text(encoding="utf-8"))
                self.assertEqual(fp_doc["comparisons"][0]["reference_origin_status"], "VERIFIED_PUBLIC_REGISTRY")
                self.assertEqual(fp_doc["comparisons"][0]["archive_member_comparison"]["target_member_match_ratio"], 1.0)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_fetch_reference_security_hardening(self):
        import http.server
        import ssl
        import threading

        spec = importlib.util.spec_from_file_location("fetch_reference_sec", SCRIPTS / "fetch_reference.py")
        fr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fr)

        # URL validation negative controls
        for bad_url in (
            "file:///etc/passwd",
            "https://user:pass@repo1.maven.org/maven2/a.jar",
            "https://repo1.maven.org\\@evil.example.com/a.jar",
            "https://repo1.maven.org/maven2/bad\r\nheader",
            "https://repo1.maven.org/maven2/nonascii-\u00e9",
            "ftp://repo1.maven.org/maven2/a.jar",
        ):
            with self.assertRaises(ValueError, msg=f"expected rejection for {bad_url!r}"):
                fr.validate_url(bad_url)

        # CR/LF method & header guards
        with self.assertRaises(ValueError):
            fr._http_request("https://repo1.maven.org/maven2/a.jar", method="GET\r\n")
        with self.assertRaises(ValueError):
            fr._http_request(
                "https://repo1.maven.org/maven2/a.jar",
                headers={"X-Test": "ok\r\nInjected: 1"},
            )

        # Weak custom SSLContext must be rejected
        weak_no_verify = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        weak_no_verify.check_hostname = False
        weak_no_verify.verify_mode = ssl.CERT_NONE
        with self.assertRaises(ValueError):
            fr._VerifiedTLSConnection("repo1.maven.org", port=443, context=weak_no_verify)

        weak_no_hostname = ssl.create_default_context()
        weak_no_hostname.check_hostname = False
        with self.assertRaises(ValueError):
            fr._VerifiedTLSConnection("repo1.maven.org", port=443, context=weak_no_hostname)

        weak_min_ver = ssl.create_default_context()
        weak_min_ver.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
        with self.assertRaises(ValueError):
            fr._VerifiedTLSConnection("repo1.maven.org", port=443, context=weak_min_ver)

        weak_tls11 = ssl.create_default_context()
        weak_tls11.minimum_version = ssl.TLSVersion.TLSv1_1
        with self.assertRaises(ValueError):
            fr._VerifiedTLSConnection("repo1.maven.org", port=443, context=weak_tls11)

        # Redirect attacks: file:// and non-allowlisted host must be blocked
        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/to-file":
                    self.send_response(302)
                    self.send_header("Location", "file:///etc/passwd")
                    self.end_headers()
                elif self.path == "/to-evil":
                    self.send_response(302)
                    self.send_header("Location", "https://evil.example.com/payload.jar")
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *a):
                pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with self.assertRaises(ValueError):
                fr.http_get_bytes(f"http://127.0.0.1:{port}/to-file", allowed_hosts={"127.0.0.1"})
            with self.assertRaises(ValueError):
                fr.http_get_bytes(f"http://127.0.0.1:{port}/to-evil", allowed_hosts={"127.0.0.1"})
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_gate_repo_and_commit_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            repo = p / "sample-repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
            (repo / "README.md").write_text("# Sample\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip().lower()

            checks = [{"id": f"{i:02}", "status": "Completed", "method": "measured"} for i in range(1, 15)]
            cov_path = p / "coverage.json"
            find_path = p / "findings.json"
            cov_path.write_text(json.dumps({"checks": checks, "scan_errors": 0, "scan_skipped": 0}), encoding="utf-8")
            find_path.write_text("[]", encoding="utf-8")

            # 1. --repo binds HEAD commit, branch, and repo_name
            dec_repo = p / "decision_repo.json"
            run(SCRIPTS / "gate.py", "--coverage", cov_path, "--findings", find_path, "--policy", ASSETS / "policy.example.json", "--repo", repo, "--out", dec_repo)
            doc_repo = json.loads(dec_repo.read_text(encoding="utf-8"))
            self.assertEqual(doc_repo["verdict"], "PASS")
            self.assertEqual(doc_repo["audited_commit"], head_sha)
            self.assertEqual(doc_repo["audited_branch"], "main")
            self.assertEqual(doc_repo["repo_name"], "sample-repo")

            # 2. --commit normalizes uppercase hex to lowercase
            explicit_sha = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"
            dec_commit = p / "decision_commit.json"
            run(SCRIPTS / "gate.py", "--coverage", cov_path, "--findings", find_path, "--policy", ASSETS / "policy.example.json", "--commit", explicit_sha, "--out", dec_commit)
            doc_commit = json.loads(dec_commit.read_text(encoding="utf-8"))
            self.assertEqual(doc_commit["audited_commit"], explicit_sha.lower())

            # 3. Invalid --commit rejected
            bad_res = subprocess.run(
                [sys.executable, str(SCRIPTS / "gate.py"), "--coverage", str(cov_path), "--findings", str(find_path), "--policy", str(ASSETS / "policy.example.json"), "--commit", "not-a-sha!", "--out", str(p / "bad.json")],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(bad_res.returncode, 0)

    def test_static_scan_self_excludes_team_provenance_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / "repo"
            out = home / "audit"
            nested_skill = repo / "skills" / "team-provenance-audit" / "scripts"
            nested_skill.mkdir(parents=True)
            (repo / "skills" / "team-provenance-audit" / "SKILL.md").write_text("name: team-provenance-audit\n", encoding="utf-8")
            (nested_skill / "static_scan.py").write_text("BRANDS = ['alibaba', 'bytedance']\n", encoding="utf-8")
            (repo / "app.py").write_text("print('clean')\n", encoding="utf-8")
            res = json.loads(run(SCRIPTS / "static_scan.py", "--repo", repo, "--out", out).stdout)
            self.assertEqual(res["files"], 1)
            inv = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
            self.assertTrue(
                any(
                    d["path"] == "skills/team-provenance-audit" and d["reason"] == "audit_skill_self_excluded"
                    for d in inv["pruned_directories"]
                )
            )


if __name__ == "__main__":
    unittest.main()
