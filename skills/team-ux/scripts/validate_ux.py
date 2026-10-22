#!/usr/bin/env python3
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_upstream_gate import approval_problems, validity_problems

def fm(text, key):
    m = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
    return m.group(1).strip() if m else None

def gov(text, errors):
    for k in ["artifact_status", "review_status", "validity", "risk_level"]:
        if not fm(text, k):
            errors.append(f"missing governance metadata: {k}")
    if fm(text, "artifact_status") not in {"draft", "complete", "superseded"}:
        errors.append("invalid artifact_status")
    if fm(text, "review_status") not in {"not-started", "in-progress", "changes-requested", "approved", "rejected"}:
        errors.append("invalid review_status")
    if fm(text, "validity") not in {"current", "stale", "unknown"}:
        errors.append("invalid validity")
    if fm(text, "risk_level") not in {"low", "medium", "high", "critical", "<low|medium|high|critical>"}:
        errors.append("invalid risk_level")
    if fm(text, "review_status") == "approved" and (fm(text, "approval_reference") or "").lower() in {"", "null", "none", "pending"}:
        errors.append("approved artifact requires approval_reference")

HEAD = [
    "## 1. Package Overview",
    "## 2. Upstream Traceability",
    "## 3. Package Manifest",
]

def main():
    if len(sys.argv) != 3:
        print("Usage: validate_ux.py <docs/ux/<slug>> <docs/requirements/<slug>.md>  (run from repo root)")
        return 2

    d = Path(sys.argv[1])
    rp = Path(sys.argv[2])
    readme = d / "README.md"
    errors = []

    if not readme.is_file():
        print(f"ERROR: missing UX package root {readme}")
        return 1
    if not rp.is_file():
        print(f"ERROR: missing requirements file {rp}")
        return 1

    t = readme.read_text()
    r = rp.read_text()

    gov(t, errors)
    errors += approval_problems(readme, t) + validity_problems(readme, t)

    for h in HEAD:
        if h not in t:
            errors.append("missing heading in README.md: " + h)

    # Review readiness
    if fm(t, "artifact_status") == "draft":
        errors.append("README.md artifact_status is draft: not ready for review (set complete when review-ready)")

    # Check for blocking open questions
    oq = re.search(r"(?ms)^### 5\.1.*?(?=^### 5\.2|^## |\Z)", t)
    for line in (oq.group(0).splitlines() if oq else []):
        if re.search(r"Blocking\s*[:：|]\s*Yes", line, re.I):
            errors.append("Blocking open question: " + line.strip()[:120])

    # Check ui-acceptance.md presence
    ui_acc = d / "ui-acceptance.md"
    if not ui_acc.is_file():
        errors.append("missing required ui-acceptance.md in UX package")
    else:
        acc_text = ui_acc.read_text()
        if not fm(acc_text, "artifact_status"):
            errors.append("ui-acceptance.md missing artifact_status")
        errors += validity_problems(ui_acc, acc_text)
        if "## 1. Coverage matrix" not in acc_text:
            errors.append("ui-acceptance.md missing Coverage matrix")
        situations = ["normal", "loading", "invalid", "403", "404", "empty", "offline"]
        for s in situations:
            if s not in acc_text:
                errors.append(f"ui-acceptance.md situation matrix missing '{s}' column")

    # Mode and wireframes check
    mode = fm(t, "design_mode") or "generative"
    wireframes = d / "wireframes.md"
    if mode == "figma-bridge":
        figma_url = fm(t, "figma_url")
        if not figma_url or figma_url in {"null", "none", "<url-or-none>"}:
            errors.append("figma-bridge mode requires a valid figma_url in README.md frontmatter")
    elif not wireframes.is_file():
        errors.append("generative mode requires wireframes.md in UX package")

    # Optional attachments frontmatter check
    for a in ["wireframes.md", "copy-deck.md"]:
        ap = d / a
        if ap.is_file():
            at = ap.read_text()
            if not fm(at, "artifact_status"):
                errors.append(f"{a} missing artifact_status")
            errors += validity_problems(ap, at)

    # Full coverage: check screen IDs from requirements (e.g. S01, S02)
    screens = sorted(set(re.findall(r"(?m)^\|\s*(S\d+[a-z]?)\s*\|", r)))
    all_pkg_text = t + "".join((d / a).read_text() for a in ["wireframes.md", "copy-deck.md", "ui-acceptance.md"] if (d / a).is_file())
    for sid in screens:
        if not re.search(rf"\b{sid}\b", all_pkg_text):
            errors.append(f"UX package does not cover requirement screen ID: {sid}")

    # Traceability check: user-facing REQ/AC from requirements
    for pre in ("REQ", "AC"):
        ids = set(re.findall(rf"\b{pre}-[A-Z0-9_-]+\b", r))
        # Exclude pure non-functional or backend-only tags if requirements separate them, but warn on missing IDs in package
        miss = sorted(ids - set(re.findall(rf"\b{pre}-[A-Z0-9_-]+\b", all_pkg_text)))
        if miss and len(miss) == len(ids):
            errors.append(f"UX package contains zero upstream {pre} identifiers from requirements")

    for x in errors:
        print("ERROR:", x)
    if errors:
        return 1

    print("OK: UX package valid")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
