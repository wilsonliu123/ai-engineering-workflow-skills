#!/usr/bin/env python3
"""Local attribution leads: file SHA-256/SHA-1, archive member hashes, and text token shingles.

Supports single-file comparison (--reference) or batch comparison against an
approved reference corpus manifest (--corpus-manifest produced by fetch_reference.py).
No internet access and no third-party dependencies. Fuzzy overlap is never a compliance verdict.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import zipfile

TEXT_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".py", ".rs", ".cpp", ".c", ".h", ".cs", ".txt"}
MAX_MEMBER = 8 * 1024 * 1024
JS_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}
JS_KEYWORDS = {"var", "let", "const", "function", "return", "class", "new", "if", "else", "for", "while", "switch", "case", "break", "try", "catch", "throw", "await", "async", "import", "export", "default", "from", "typeof", "void", "delete", "instanceof", "this", "true", "false", "null", "undefined"}


def hash_file(p):
    h256 = hashlib.sha256()
    h1 = hashlib.new("sha1", usedforsecurity=False)
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h256.update(chunk)
            h1.update(chunk)
    return h256.hexdigest(), h1.hexdigest()


def sha256_file(p):
    return hash_file(p)[0]


def archive_members(p):
    members = {}
    warnings = []
    with zipfile.ZipFile(p) as z:
        infos = z.infolist()
        if len(infos) > 20000:
            warnings.append("archive_member_count_exceeds_limit")
            infos = infos[:20000]
        for info in infos:
            if info.is_dir():
                continue
            if info.file_size > MAX_MEMBER or (info.compress_size and info.file_size / max(info.compress_size, 1) > 150):
                warnings.append("member_size_or_ratio_exceeds_limit")
                continue
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts:
                warnings.append("unsafe_member_name")
                continue
            try:
                data = z.read(info)
            except (OSError, RuntimeError, EOFError, ValueError, zipfile.BadZipFile):
                warnings.append("member_read_error")
                continue
            members[name] = hashlib.sha256(data).hexdigest()
    return members, sorted(set(warnings))


def text_shingles(p, n=5, max_bytes=4 * 1024 * 1024):
    if p.stat().st_size > max_bytes:
        return set(), "oversized_text"
    text = p.read_text(encoding="utf-8", errors="replace")
    # Remove common whitespace/comments but retain identifiers and symbols. Fuzzy only.
    tokens = re.findall(r"[\w$]+|[^\s\w]", text, flags=re.UNICODE)
    if len(tokens) < 40:
        return set(), "too_few_tokens"
    shingles = set()
    for i in range(len(tokens) - n + 1):
        shingles.add(hashlib.sha256("\x1f".join(tokens[i:i + n]).encode("utf-8")).hexdigest()[:16])
    return shingles, "ok"


def structural_js_shingles(p, n=9, max_bytes=4 * 1024 * 1024):
    """Exploratory JS token shape. Ignores identifier spelling for renamed/minified JS.

    This approximate tokenizer cannot parse JavaScript faithfully and can produce
    false matches among boilerplate. Never use as primary attribution evidence.
    """
    if p.stat().st_size > max_bytes:
        return set(), "oversized_text"
    source = p.read_text(encoding="utf-8", errors="replace")
    pattern = r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|[^\s]"
    tokens = re.findall(pattern, source)
    normalized = []
    for tok in tokens:
        if tok[0] in ('"', "'", "`"):
            normalized.append("STR")
        elif re.fullmatch(r"\d+(?:\.\d+)?", tok):
            normalized.append("NUM")
        elif re.fullmatch(r"[A-Za-z_$][\w$]*", tok):
            normalized.append(tok if tok in JS_KEYWORDS else "ID")
        else:
            normalized.append(tok)
    if len(normalized) < 60:
        return set(), "too_few_tokens"
    return {hashlib.sha256("\x1f".join(normalized[i:i + n]).encode()).hexdigest()[:16] for i in range(len(normalized) - n + 1)}, "ok"


def compare_pair(a, b, origin_meta=None):
    a_sha256, a_sha1 = hash_file(a)
    b_sha256, b_sha1 = hash_file(b)
    origin_status = "NOT_VERIFIED_BY_THIS_TOOL"
    if origin_meta and origin_meta.get("registry_checksum_verified") and origin_meta.get("source_url"):
        origin_status = origin_meta.get("reference_origin_status") or "VERIFIED_PUBLIC_REGISTRY"
    report = {
        "target": str(a),
        "reference": str(b),
        "target_sha256": a_sha256,
        "target_sha1": a_sha1,
        "reference_sha256": b_sha256,
        "reference_sha1": b_sha1,
        "exact_file_match": a_sha256 == b_sha256,
        "reference_origin_status": origin_status,
        "fingerprint_warning": "Any fuzzy similarity is a lead, not proof of authorship or ownership. Verify upstream release and company independently.",
    }
    if origin_meta:
        report["reference_provenance"] = {
            "ecosystem": origin_meta.get("ecosystem"),
            "coordinate": origin_meta.get("coordinate"),
            "source_url": origin_meta.get("source_url"),
            "checksum_url": origin_meta.get("checksum_url"),
            "fetched_utc": origin_meta.get("fetched_utc"),
            "registry_checksum_verified": bool(origin_meta.get("registry_checksum_verified")),
        }
    if zipfile.is_zipfile(a) and zipfile.is_zipfile(b):
        am, aw = archive_members(a)
        bm, bw = archive_members(b)
        a_by_hash = collections.defaultdict(list)
        b_by_hash = collections.defaultdict(list)
        for name, h in am.items():
            a_by_hash[h].append(name)
        for name, h in bm.items():
            b_by_hash[h].append(name)
        shared = set(a_by_hash) & set(b_by_hash)
        # Count distinct target/reference member paths matching shared hashes
        shared_target_paths = sum(len(a_by_hash[h]) for h in shared)
        shared_ref_paths = sum(len(b_by_hash[h]) for h in shared)
        report["archive_member_comparison"] = {
            "target_examined_members": len(am),
            "reference_examined_members": len(bm),
            "exact_shared_member_hash_count": len(shared),
            "target_member_match_ratio": round(shared_target_paths / len(am), 6) if am else 0.0,
            "reference_member_match_ratio": round(shared_ref_paths / len(bm), 6) if bm else 0.0,
            "exact_matches": [{"sha256": h, "target_paths": a_by_hash[h][:8], "reference_paths": b_by_hash[h][:8]} for h in sorted(shared)[:200]],
            "warnings_target": aw,
            "warnings_reference": bw,
            "match_list_truncated": len(shared) > 200,
        }
    if a.suffix.lower() in TEXT_SUFFIXES and b.suffix.lower() in TEXT_SUFFIXES:
        sa, sta = text_shingles(a)
        sb, stb = text_shingles(b)
        if sta == "ok" and stb == "ok":
            intersection = len(sa & sb)
            union = len(sa | sb)
            similarity = round(intersection / union, 6) if union else 0.0
        else:
            intersection, similarity = 0, None
        report["text_token_shingles"] = {
            "status_target": sta,
            "status_reference": stb,
            "shingle_n": 5,
            "target_unique": len(sa),
            "reference_unique": len(sb),
            "intersection": intersection,
            "jaccard_similarity": similarity,
            "interpretation": "exploratory similarity, not source identity",
        }
    if a.suffix.lower() in JS_SUFFIXES and b.suffix.lower() in JS_SUFFIXES:
        sa, sta = structural_js_shingles(a)
        sb, stb = structural_js_shingles(b)
        union = len(sa | sb)
        structural = round(len(sa & sb) / union, 6) if sta == "ok" and stb == "ok" and union else None
        report["javascript_structural_token_shingles"] = {
            "status_target": sta,
            "status_reference": stb,
            "jaccard_similarity": structural,
            "normalization": "identifier->ID; string->STR; number->NUM; keywords/operators preserved",
            "interpretation": "exploratory shape only; NOT an AST or proof of common source",
        }
    return report


def compare_against_corpus(target_path, manifest_path):
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    t_sha256, t_sha1 = hash_file(target_path)
    comparisons = []
    for entry in doc.get("entries", []):
        local_file = entry.get("local_file")
        if local_file:
            ref_raw = Path(local_file)
            if ref_raw.is_symlink() or not ref_raw.is_file():
                comparisons.append({
                    "coordinate": entry.get("coordinate"),
                    "ecosystem": entry.get("ecosystem"),
                    "status": "local_reference_missing_or_symlink",
                    "local_file": local_file,
                })
                continue
            pair = compare_pair(target_path, ref_raw.resolve(strict=True), origin_meta=entry)
            comparisons.append(pair)
        else:
            # Hash-only manifest entry
            off_sha1 = (entry.get("official_sha1") or "").lower()
            off_sha256 = (entry.get("official_sha256") or "").lower()
            all_256 = [x.lower() for x in entry.get("all_official_sha256s", [])]
            exact = bool((off_sha1 and t_sha1 == off_sha1) or (off_sha256 and t_sha256 == off_sha256) or (t_sha256 in all_256))
            comparisons.append({
                "target": str(target_path),
                "target_sha256": t_sha256,
                "target_sha1": t_sha1,
                "exact_file_match": exact,
                "comparison_mode": "hash_only_manifest",
                "reference_origin_status": entry.get("reference_origin_status", "UNVERIFIED"),
                "reference_provenance": {
                    "ecosystem": entry.get("ecosystem"),
                    "coordinate": entry.get("coordinate"),
                    "source_url": entry.get("source_url"),
                    "checksum_url": entry.get("checksum_url"),
                    "fetched_utc": entry.get("fetched_utc"),
                    "official_sha1": off_sha1 or None,
                    "official_sha256": off_sha256 or None,
                    "registry_checksum_verified": bool(entry.get("registry_checksum_verified")),
                },
            })

    def score(item):
        if item.get("exact_file_match"):
            return (2, 1.0)
        amc = item.get("archive_member_comparison") or {}
        if amc.get("exact_shared_member_hash_count"):
            return (1, float(amc.get("reference_member_match_ratio") or 0.0))
        return (0, float((item.get("text_token_shingles") or {}).get("jaccard_similarity") or 0.0))

    comparisons.sort(key=score, reverse=True)
    return {
        "target": str(target_path),
        "target_sha256": t_sha256,
        "target_sha1": t_sha1,
        "corpus_manifest": str(manifest_path),
        "compared_entries": len(comparisons),
        "any_exact_file_match": any(c.get("exact_file_match") for c in comparisons),
        "comparisons": comparisons,
        "fingerprint_warning": "Exact registry-verified hashes establish artifact identity; fuzzy similarity is a lead only. Corporate ownership must still be independently verified.",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--reference", help="Locally acquired and separately provenance-verified file")
    grp.add_argument("--corpus-manifest", help="Path to corpus_manifest.json generated by fetch_reference.py")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    raw_a = Path(args.target)
    if raw_a.is_symlink():
        ap.error("symlinks not supported")
    a = raw_a.resolve(strict=True)
    if not a.is_file():
        ap.error("target must be a regular file")

    out = Path(args.out)
    if args.reference:
        raw_b = Path(args.reference)
        if raw_b.is_symlink():
            ap.error("symlinks not supported")
        b = raw_b.resolve(strict=True)
        if not b.is_file():
            ap.error("reference must be a regular file")
        if out.resolve() in {a, b}:
            ap.error("output cannot overwrite input")
        report = compare_pair(a, b)
        summary = {
            "output": str(out),
            "exact_file_match": report["exact_file_match"],
            "archive_member_comparison": report.get("archive_member_comparison", {}).get("exact_shared_member_hash_count"),
            "jaccard": report.get("text_token_shingles", {}).get("jaccard_similarity"),
        }
    else:
        raw_m = Path(args.corpus_manifest)
        if raw_m.is_symlink():
            ap.error("symlinks not supported")
        m = raw_m.resolve(strict=True)
        if not m.is_file():
            ap.error("corpus manifest must be a regular file")
        if out.resolve() in {a, m}:
            ap.error("output cannot overwrite input")
        report = compare_against_corpus(a, m)
        top = report["comparisons"][0] if report["comparisons"] else {}
        summary = {
            "output": str(out),
            "compared_entries": report["compared_entries"],
            "any_exact_file_match": report["any_exact_file_match"],
            "top_coordinate": (top.get("reference_provenance") or {}).get("coordinate"),
            "top_archive_shared_members": (top.get("archive_member_comparison") or {}).get("exact_shared_member_hash_count"),
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
