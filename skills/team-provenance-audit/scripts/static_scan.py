#!/usr/bin/env python3
"""Deterministic, read-only, resumable local candidate scanner.

Candidates are investigation leads, never enterprise ownership determinations.
Writes outside the target; uses only Python standard library. Never executes targets.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import zipfile

REV = "1.2.0"
SKILL_ROOT = Path(__file__).resolve().parents[1]
CACHED_DIRS = {".git", "__pycache__", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
ARCHIVES = {".jar", ".war", ".ear", ".zip", ".aar", ".apk", ".whl"}
BINARY_EXT = {".so", ".dll", ".dylib", ".a", ".exe", ".bin", ".class", ".wasm", ".pyc", ".node", ".safetensors", ".gguf", ".onnx", ".pt", ".ipa"}
MANIFEST_NAMES = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock",
    ".npmrc", ".yarnrc", ".yarnrc.yml",
    "go.mod", "go.sum", "go.work",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile", "settings.xml",
    "requirements.txt", "poetry.lock", "uv.lock", "pyproject.toml", "Pipfile.lock", "pip.conf",
    "Cargo.toml", "Cargo.lock",
    "Gemfile", "Gemfile.lock", "composer.json", "composer.lock",
    "Podfile", "Podfile.lock", "Package.swift", "Package.resolved", "pubspec.yaml", "pubspec.lock",
    "NuGet.Config", "Directory.Packages.props",
    "vcpkg.json", "conanfile.py", "conanfile.txt", "CMakeLists.txt",
    "Dockerfile", "Jenkinsfile",
}
# Curated unambiguous Simplified-only Chinese characters (deduplicated; excludes Traditional-shared characters)
SIMP_CHARS = "".join(dict.fromkeys(
    "这们为发个开关网说话点现资认权务虑读写办检测软属类买卖产码参观态结图连转应变对动规实总简响间时会处统负许证"
    "国体学机车长门仅从无与专业东风云电数据库录输请问题报错误败设备环组织级号页标签键盘链删创账选择项确调试运启终异历"
    "进线缓执编译构协议华腾讯节团宁苏广汉庆计算频视语隐私审获归档还让给将经过来么样种称义则条单双两万亿"
))
BRANDS = ["bytedance", "cloudwego", "volcengine", "alibaba", "aliyun", "alipay", "antgroup", "tencent", "wechat", "weixin", "qcloud", "baidu", "paddlepaddle", "huawei", "huaweicloud", "openharmony", "xiaomi", "meituan", "jingdong", "netease", "bilibili", "deepseek", "zhipu", "pingcap", "npmmirror", "goproxy.cn"]
CITIES = ["beijing", "shanghai", "shenzhen", "hangzhou", "guangzhou", "chengdu", "wuhan", "nanjing", "suzhou", "zhongguo", "zhineng"]
PATTERNS = [
    ("simplified_character_candidate", re.compile("[" + re.escape(SIMP_CHARS) + "]")),
    ("enterprise_string_candidate", re.compile(r"(?i)(?<![\w])(?:" + "|".join(re.escape(x) for x in BRANDS) + r")(?![\w])")),
    ("pinyin_or_city_candidate", re.compile(r"(?i)(?<![a-z])(?:" + "|".join(re.escape(x) for x in CITIES) + r")(?![a-z])")),
    ("url_candidate", re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'<>\]\)]+")),
    ("java_namespace_candidate", re.compile(r"(?i)\b(?:com|org)\.(?:alibaba|aliyun|tencent|baidu|huawei|bytedance|volcengine|meituan|xiaomi)\.[\w.]+")),
    ("javascript_obfuscation_candidate", re.compile(r"\b(?:eval\s*\(|atob\s*\(|new\s+Function\s*\(|String\.fromCharCode\s*\()")),
]
ARCHIVE_PATH_PATTERNS = [
    ("archive_java_namespace_candidate", re.compile(r"(?i)(?:^|/)(?:com|org)/(?:alibaba|aliyun|tencent|baidu|huawei|bytedance|volcengine|meituan|xiaomi)/[\w/$]+")),
    ("archive_enterprise_path_candidate", re.compile(r"(?i)(?<![a-z0-9])(?:" + "|".join(re.escape(x) for x in BRANDS) + r")(?![a-z0-9])")),
]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def redact(value):
    value = re.sub(r"(?i)(bearer\s+)[a-z0-9_\-.]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)([?&](?:token|access_token|id_token|auth|authorization|key|secret|client_secret|password|api_key|sig|signature)=)[^&\s]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", value)
    value = re.sub(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_\-]{16,}\b", "[REDACTED_SECRET_KEY]", value)
    return value[:220]


def is_manifest_file(fname):
    if fname in MANIFEST_NAMES:
        return True
    if fname.startswith(("requirements-", "requirements_", "Dockerfile.", ".yarnrc")):
        return True
    return fname.endswith((".csproj", ".sln", ".tf", ".yml", ".yaml"))


def scan_lines(text, path, limit=10000):
    records = []
    if not text:
        return records
    for ln, line in enumerate(text.splitlines(), 1):
        for cat, pattern in PATTERNS:
            for m in pattern.finditer(line):
                records.append((path, ln, m.start() + 1, m.end(), None, cat, redact(m.group(0))))
                if len(records) >= limit:
                    return records
    return records


def scan_binary_strings(blob, path, limit=150):
    """Only bounded printable ASCII spans; byte offsets refer to original bytes."""
    records = []
    for span in re.finditer(rb"[\x20-\x7e]{8,}", blob):
        s = span.group().decode("ascii", "replace")
        for cat, pattern in PATTERNS[1:5]:
            for m in pattern.finditer(s):
                records.append((path, None, None, None, span.start() + m.start(), cat, redact(m.group(0))))
                if len(records) >= limit:
                    return records
    return records


def inspect_archive(path, rel, max_entries=15000, max_entry_bytes=262144, max_total_bytes=2097152, max_path_leads=500):
    recs = []
    details = {"member_count": 0, "nested_archives": 0, "warnings": []}
    read_total = 0
    path_leads = 0
    with zipfile.ZipFile(path, "r") as z:
        members = z.infolist()
        details["member_count"] = len(members)
        if len(members) > max_entries:
            details["warnings"].append("too_many_archive_members")
            members = members[:max_entries]
        for info in members:
            name = info.filename
            if name.startswith("/") or any(part == ".." for part in Path(name).parts):
                details["warnings"].append("unsafe_archive_member_path")
                continue
            if name.lower().endswith((".jar", ".aar", ".war")):
                details["nested_archives"] += 1
            if path_leads < max_path_leads:
                for cat, pattern in ARCHIVE_PATH_PATTERNS:
                    m = pattern.search(name)
                    if m:
                        recs.append((rel + "!/" + name, None, None, None, None, cat, redact(m.group(0))))
                        path_leads += 1
                        break
            interesting = (name == "META-INF/MANIFEST.MF" or name.endswith("pom.properties") or name.endswith("pom.xml") or name.endswith("LICENSE") or name.endswith("NOTICE") or name.endswith(".map"))
            if not interesting:
                continue
            if info.file_size > max_entry_bytes or read_total + info.file_size > max_total_bytes:
                details["warnings"].append("archive_metadata_budget_reached")
                continue
            if info.compress_size and info.file_size / max(1, info.compress_size) > 150:
                details["warnings"].append("archive_compression_ratio_high")
                continue
            raw = z.read(info)
            read_total += len(raw)
            recs.extend(scan_lines(raw.decode("utf-8", "replace"), rel + "!/" + name))
    if path_leads >= max_path_leads:
        details["warnings"].append("archive_path_lead_limit_reached")
    if details["nested_archives"]:
        details["warnings"].append("nested_archives_require_separate_inspection")
    return recs, details


def _is_audit_skill_dir(subdir):
    return (
        subdir.name == "team-provenance-audit"
        and (subdir / "SKILL.md").is_file()
        and (subdir / "scripts" / "static_scan.py").is_file()
    )


def init_db(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha256 TEXT, size INTEGER, mtime_ns INTEGER, status TEXT, reason TEXT, kind TEXT, detail TEXT, updated_utc TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS candidates (path TEXT, line INTEGER, col INTEGER, end_col INTEGER, byte_offset INTEGER, category TEXT, match TEXT)")
    old = conn.execute("SELECT value FROM meta WHERE key='scanner_rev'").fetchone()
    if old is None or old[0] != REV:
        conn.execute("DROP TABLE IF EXISTS files")
        conn.execute("CREATE TABLE files (path TEXT PRIMARY KEY, sha256 TEXT, size INTEGER, mtime_ns INTEGER, status TEXT, reason TEXT, kind TEXT, detail TEXT, updated_utc TEXT)")
        conn.execute("DELETE FROM candidates")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('scanner_rev', ?)", (REV,))
    conn.commit()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--max-text-bytes", type=int, default=4 * 1024 * 1024)
    p.add_argument("--max-binary-bytes", type=int, default=512 * 1024)
    p.add_argument("--max-files", type=int, default=0, help="0 = unlimited; if set, coverage gap is reported")
    p.add_argument("--exclude", action="append", default=[], help="Relative or absolute path prefix to exclude (logged in pruned_directories)")
    args = p.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    out = Path(args.out).resolve()
    excluded_paths = set()
    for ex in args.exclude:
        ex_p = Path(ex)
        ex_resolved = ex_p.resolve() if ex_p.is_absolute() else (repo / ex_p).resolve()
        try:
            excluded_paths.add(ex_resolved.relative_to(repo).as_posix())
        except ValueError:
            pass
    skill_self_rel = None
    try:
        skill_self_rel = SKILL_ROOT.relative_to(repo).as_posix()
    except ValueError:
        skill_self_rel = None
    if not repo.is_dir():
        p.error("repo must be a directory")
    try:
        inside_repo = os.path.commonpath([str(out), str(repo)]) == str(repo)
    except ValueError:
        inside_repo = False
    if inside_repo:
        p.error("output directory must be outside repository (use a sibling directory)")
    if args.max_text_bytes <= 0 or args.max_binary_bytes <= 0:
        p.error("byte budgets must be positive")
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(out / "analysis_state.sqlite"))
    init_db(conn)
    seen = set()
    pruned_dirs = []
    reused = 0
    changed = 0
    stopped_early = False
    extensions = collections.Counter()
    manifest_paths = []
    for root, dirs, files in os.walk(repo, followlinks=False):
        kept = []
        for d in sorted(dirs):
            subdir = Path(root) / d
            sub_rel = subdir.relative_to(repo).as_posix()
            if (skill_self_rel and sub_rel == skill_self_rel) or _is_audit_skill_dir(subdir):
                pruned_dirs.append({"path": sub_rel, "reason": "audit_skill_self_excluded"})
            elif sub_rel in excluded_paths:
                pruned_dirs.append({"path": sub_rel, "reason": "explicitly_excluded"})
            elif d in CACHED_DIRS or subdir.is_symlink():
                pruned_dirs.append({"path": sub_rel, "reason": "cache_or_git_metadata" if d in CACHED_DIRS else "symlink_directory_not_followed"})
            else:
                kept.append(d)
        dirs[:] = kept
        for fname in sorted(files):
            full = Path(root) / fname
            rel = full.relative_to(repo).as_posix()
            if args.max_files and len(seen) >= args.max_files:
                stopped_early = True
                break
            seen.add(rel)
            extensions[full.suffix.lower() or "[none]"] += 1
            if is_manifest_file(fname):
                manifest_paths.append(rel)
            try:
                if full.is_symlink():
                    sha, size, mtime_ns, status, reason, kind, detail, rows = "", 0, 0, "skipped_symlink", "symlink", "symlink", {}, []
                else:
                    st = full.stat()
                    size = st.st_size
                    mtime_ns = st.st_mtime_ns
                    old = conn.execute("SELECT sha256, size, mtime_ns, status FROM files WHERE path=?", (rel,)).fetchone()
                    if old is not None and old[1] == size and old[2] == mtime_ns and old[3] != "error":
                        reused += 1
                        continue
                    sha = digest(full)
                    if old is not None and old[0] == sha and old[1] == size and old[3] != "error":
                        with conn:
                            conn.execute("UPDATE files SET mtime_ns=?, updated_utc=? WHERE path=?", (mtime_ns, now(), rel))
                        reused += 1
                        continue
                    changed += 1
                    ext = full.suffix.lower()
                    detail = {}
                    if ext in ARCHIVES or (size >= 4 and full.open("rb").read(4) == b"PK\x03\x04"):
                        kind = "archive"
                        try:
                            if size > 256 * 1024 * 1024:
                                raise ValueError("archive_exceeds_256MiB_bounded_scan_limit")
                            rows, detail = inspect_archive(full, rel)
                            status = "inspected_archive"
                            reason = ";".join(sorted(set(detail.get("warnings", []))))
                        except (zipfile.BadZipFile, EOFError, RuntimeError, OSError, ValueError) as e:
                            rows, status, reason = [], "error", "archive_read_error_or_budget:" + type(e).__name__
                    elif ext in BINARY_EXT or b"\x00" in full.open("rb").read(4096):
                        kind = "binary"
                        with full.open("rb") as f:
                            rows = scan_binary_strings(f.read(args.max_binary_bytes), rel)
                        status = "inspected_binary_strings_partial" if size > args.max_binary_bytes else "inspected_binary_strings"
                        reason = "binary_needs_artifact_level_review"
                    elif size > args.max_text_bytes:
                        kind, rows, status, reason = "text", [], "skipped_size", "exceeds_text_budget"
                    else:
                        kind = "text"
                        rows = scan_lines(full.read_text(encoding="utf-8", errors="replace"), rel)
                        status, reason = "inspected_text", ""
                        if len(rows) >= 10000:
                            status, reason = "inspected_text_capped", "candidate_limit_10000_reached"
                with conn:
                    conn.execute("DELETE FROM candidates WHERE path=? OR substr(path,1,?)=?", (rel, len(rel) + 2, rel + "!/"))
                    conn.executemany("INSERT INTO candidates (path,line,col,end_col,byte_offset,category,match) VALUES (?,?,?,?,?,?,?)", rows)
                    conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)", (rel, sha, size, mtime_ns, status, reason, kind, json.dumps(detail, sort_keys=True), now()))
            except (OSError, UnicodeError, PermissionError, ValueError) as e:
                changed += 1
                with conn:
                    conn.execute("DELETE FROM candidates WHERE path=? OR substr(path,1,?)=?", (rel, len(rel) + 2, rel + "!/"))
                    conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)", (rel, "", 0, 0, "error", type(e).__name__, "unknown", "{}", now()))
        if stopped_early:
            break
    old_paths = {r[0] for r in conn.execute("SELECT path FROM files")}
    with conn:
        for stale in old_paths - seen:
            conn.execute("DELETE FROM files WHERE path=?", (stale,))
            conn.execute("DELETE FROM candidates WHERE path=? OR substr(path,1,?)=?", (stale, len(stale) + 2, stale + "!/"))
    statuses = collections.Counter(r[0] for r in conn.execute("SELECT status FROM files"))
    categories = collections.Counter(r[0] for r in conn.execute("SELECT category FROM candidates"))
    with (out / "candidates.jsonl").open("w", encoding="utf-8") as f:
        for row in conn.execute("SELECT path,line,col,end_col,byte_offset,category,match FROM candidates ORDER BY path,line,col,byte_offset,category"):
            path, line, col, end_col, offset, category, matched = row
            locator = (f"{path}:{line}:{col}-{end_col}" if line is not None else f"{path} @ 0x{offset:x}" if offset is not None else path)
            f.write(json.dumps({"path": path, "line": line, "column": col, "end_column": end_col, "byte_offset": offset, "locator": locator, "category": category, "match_redacted": matched}, ensure_ascii=False) + "\n")
    files_inventory = [{"path": row[0], "sha256": row[1], "size": row[2], "status": row[3], "reason": row[4], "kind": row[5], "details": json.loads(row[6])} for row in conn.execute("SELECT path,sha256,size,status,reason,kind,detail FROM files ORDER BY path")]
    result = {
        "scanner_revision": REV, "generated_utc": now(), "repo": str(repo),
        "file_count": len(seen), "reused_unchanged": reused, "rescan_or_new": changed,
        "stopped_at_max_files": stopped_early, "pruned_directories": pruned_dirs, "by_status": dict(statuses),
        "candidate_categories": dict(categories), "extensions": dict(extensions),
        "manifest_paths": manifest_paths, "files": files_inventory,
        "important_note": "Candidate strings do not establish enterprise authorship or compliance. Binary archives and project history require additional specialized inspection."
    }
    (out / "inventory.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"repo": str(repo), "out": str(out), "files": len(seen), "reused": reused, "rescanned": changed, "statuses": dict(statuses), "candidate_counts": dict(categories), "max_files_hit": stopped_early}, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
