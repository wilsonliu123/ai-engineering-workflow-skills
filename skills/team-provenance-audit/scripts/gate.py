#!/usr/bin/env python3
"""Deterministic conservative compliance gate for human-authored structured evidence.

This is NOT primary-source verification: it checks whether the recorded review
is internally consistent and whether its coverage supports any PASS assertion.
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess

VALID_STATUSES = {"Completed", "Partially Completed", "Not Applicable", "Not Available"}
VALID_ASSOCIATION = {"A", "B", "C", "D"}
COMMIT_SHA_RE = re.compile(r"\A[0-9a-fA-F]{7,64}\Z")


def resolve_repo_binding(repo=None, commit=None):
    """Resolve optional commit/branch/repo binding metadata for decision.json."""
    binding = {}
    if commit is not None:
        clean_sha = str(commit).strip()
        if not COMMIT_SHA_RE.match(clean_sha):
            raise ValueError("invalid --commit SHA: expected 7-64 hexadecimal characters")
        binding["audited_commit"] = clean_sha.lower()

    if repo is not None:
        repo_path = Path(repo).resolve(strict=True)
        if not repo_path.is_dir():
            raise ValueError(f"--repo must be an existing directory: {repo}")
        binding["repo_name"] = repo_path.name
        if "audited_commit" not in binding:
            try:
                head_res = subprocess.run(
                    ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                head_sha = (head_res.stdout or "").strip()
                if head_res.returncode == 0 and COMMIT_SHA_RE.match(head_sha):
                    binding["audited_commit"] = head_sha.lower()
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            branch_res = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            branch_name = (branch_res.stdout or "").strip()
            if branch_res.returncode == 0 and branch_name:
                binding["audited_branch"] = branch_name
        except (OSError, subprocess.SubprocessError):
            pass
    return binding


def decide(coverage, findings, policy):
    if not isinstance(coverage, dict) or not isinstance(findings, list):
        raise ValueError("coverage must be an object and findings must be an array")
    reasons = []
    failures = []
    required = set(policy.get("required_checks", [f"{x:02}" for x in range(1, 15)]))
    prohibited = set(policy.get("prohibited_component_bases", []))
    require_complete_coverage = bool(policy.get("pass_requires_complete_coverage", True))
    require_no_critical_unverified = bool(policy.get("pass_requires_no_critical_unverified", True))
    service_handling = str(policy.get("service_and_mirror_handling", "review_separately"))
    confirmed_status = policy.get("confirmed_evidence_status", "verified_primary")
    state = {}
    for item in coverage.get("checks", []):
        if not isinstance(item, dict):
            reasons.append("malformed coverage check item")
            continue
        cid = str(item.get("id", "")).zfill(2)
        status = item.get("status", "")
        if cid in state:
            reasons.append(f"duplicate check id: {cid}")
        if status not in VALID_STATUSES:
            reasons.append(f"invalid check status for {cid}: {status}")
        state[cid] = item
    if require_complete_coverage:
        for cid in sorted(required):
            item = state.get(cid)
            if item is None:
                reasons.append(f"required check {cid} is missing")
                continue
            status = item.get("status")
            if status == "Not Applicable" and not str(item.get("reason", "")).strip():
                reasons.append(f"required check {cid} has unjustified Not Applicable status")
            elif status not in ("Completed", "Not Applicable"):
                reasons.append(f"required check {cid} incomplete: {status}")
            if status == "Completed" and not str(item.get("method", "")).strip():
                reasons.append(f"required check {cid} completed without method evidence")
        for k in ("scan_errors", "scan_skipped"):
            try:
                count = int(coverage.get(k, 0))
            except (TypeError, ValueError):
                count = 1
            if count > 0:
                reasons.append(f"{k}={count}")
        if coverage.get("material_blind_spots"):
            reasons.append("material blind spots remain")
    if require_no_critical_unverified and coverage.get("critical_unverified_components"):
        reasons.append("critical components have unverified provenance")
    for idx, finding in enumerate(findings, 1):
        if not isinstance(finding, dict):
            reasons.append(f"finding #{idx} has malformed structure")
            continue
        fid = str(finding.get("id", f"#{idx}"))
        assoc = str(finding.get("association", ""))
        basis = str(finding.get("policy_basis", ""))
        status = str(finding.get("evidence_status", ""))
        evidence = finding.get("evidence", [])
        locator = str(finding.get("location", ""))
        if assoc not in VALID_ASSOCIATION:
            reasons.append(f"{fid}: invalid or missing association classification")
            continue
        if assoc in ("B", "C"):
            reasons.append(f"{fid}: suspected or unknown provenance")
        if basis in prohibited:
            if assoc == "A" and status == confirmed_status and locator and isinstance(evidence, list) and len(evidence) > 0:
                failures.append(f"{fid}: verified prohibited component basis {basis}")
            elif assoc == "A":
                reasons.append(f"{fid}: confirmed association lacks verified primary evidence or location")
            elif assoc == "D":
                reasons.append(f"{fid}: prohibited policy basis conflicts with no-association classification")
        elif basis == "service_or_mirror":
            if assoc == "A":
                if service_handling in ("fail", "prohibit"):
                    if status == confirmed_status and locator and isinstance(evidence, list) and len(evidence) > 0:
                        failures.append(f"{fid}: verified prohibited service/mirror policy")
                    else:
                        reasons.append(f"{fid}: confirmed service/mirror lacks verified primary evidence or location")
                elif service_handling == "review_separately":
                    reasons.append(f"{fid}: separately review service/mirror policy")
                elif service_handling in ("informational", "ignore"):
                    pass
                else:
                    reasons.append(f"{fid}: unrecognized service_and_mirror_handling '{service_handling}'")
        elif basis == "language_or_brand_clue":
            # Never promote locale/brand-only evidence to an in-scope component violation
            pass
        elif basis == "unknown_component":
            reasons.append(f"{fid}: component origin unknown")
        else:
            reasons.append(f"{fid}: missing or unrecognized policy basis '{basis}'")
    if failures:
        verdict = "FAIL"
    elif reasons:
        verdict = "REVIEW REQUIRED"
    else:
        verdict = "PASS"
    return {
        "verdict": verdict,
        "label": {"PASS": "No Identified Policy Violation", "FAIL": "Confirmed Policy Violation", "REVIEW REQUIRED": "Insufficient Evidence or Policy Ambiguity"}[verdict],
        "confirmed_failures": sorted(failures),
        "unresolved_reasons": sorted(set(reasons)),
        "checked_controls": sorted(state),
        "required_controls": sorted(required),
        "important_note": "This gate checks structured inputs only. Human reviewer must verify evidence and cannot treat PASS as absolute assurance.",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=None, help="Optional Git repository root to bind audited_commit (HEAD), audited_branch, and repo_name")
    ap.add_argument("--commit", default=None, help="Optional explicit Git commit SHA (7-64 hex chars) to bind audited_commit")
    args = ap.parse_args()
    coverage = json.loads(Path(args.coverage).read_text(encoding="utf-8"))
    findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    result = decide(coverage, findings, policy)
    if args.repo is not None or args.commit is not None:
        try:
            result.update(resolve_repo_binding(repo=args.repo, commit=args.commit))
        except (OSError, ValueError) as exc:
            ap.error(str(exc))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {
        "verdict": result["verdict"],
        "confirmed_failures": len(result["confirmed_failures"]),
        "unresolved_reasons": len(result["unresolved_reasons"]),
        "decision_path": str(out),
    }
    if "audited_commit" in result:
        summary["audited_commit"] = result["audited_commit"]
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
