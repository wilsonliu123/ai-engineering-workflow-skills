#!/usr/bin/env python3
"""Read-only toolchain preflight & doctor for team-provenance-audit.

Verifies real tool execution (detecting macOS /usr/bin/jar & javap stubs),
locates Homebrew keg-only paths (openjdk, binutils/greadelf), and reports
trusted install commands. Never installs tools unless --install is explicitly passed.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

REV = "1.0.0"

KEG_CANDIDATES = [
    "/opt/homebrew/opt/openjdk/bin",
    "/usr/local/opt/openjdk/bin",
    "/opt/homebrew/opt/openjdk@21/bin",
    "/opt/homebrew/opt/openjdk@17/bin",
    "/opt/homebrew/opt/binutils/bin",
    "/usr/local/opt/binutils/bin",
]

# (name, category, candidate_binaries, version_args, brew_formula, apt_pkg)
TOOL_SPECS = [
    ("python3", "baseline", ["python3"], ["--version"], "python", "python3"),
    ("rg", "baseline", ["rg"], ["--version"], "ripgrep", "ripgrep"),
    ("git", "baseline", ["git"], ["--version"], "git", "git"),
    ("file", "baseline", ["file"], ["--version"], None, "file"),
    ("strings", "baseline", ["strings"], ["--version"], "binutils", "binutils"),
    ("sqlite3", "baseline", ["sqlite3"], ["--version"], "sqlite", "sqlite3"),
    ("openssl", "baseline", ["openssl"], ["version"], "openssl", "openssl"),
    ("jar", "deep_jvm", ["jar"], ["--version"], "openjdk", "default-jdk-headless"),
    ("javap", "deep_jvm", ["javap"], ["-version"], "openjdk", "default-jdk-headless"),
    ("cfr", "deep_jvm", ["cfr-decompiler", "cfr"], ["--help"], "cfr-decompiler", None),
    ("jadx", "deep_jvm", ["jadx"], ["--version"], "jadx", None),
    ("readelf", "deep_binary", ["readelf", "greadelf"], ["--version"], "binutils", "binutils"),
    ("nm", "deep_binary", ["nm"], ["--version"], "binutils", "binutils"),
    ("objdump", "deep_binary", ["objdump"], ["--version"], "binutils", "binutils"),
    ("syft", "sbom", ["syft"], ["--version"], "syft", None),
    ("trivy", "sbom", ["trivy"], ["--version"], "trivy", None),
    ("wasm2wat", "optional", ["wasm2wat"], ["--version"], "wabt", "wabt"),
]

STUB_MARKERS = (
    "unable to locate a java runtime",
    "no java runtime present",
    "xcode-select: note",
)


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def discover_extra_bin_paths():
    found = []
    for candidate in KEG_CANDIDATES:
        if Path(candidate).is_dir() and candidate not in found:
            found.append(candidate)
    # Also scan macOS JVM directories
    jvm_root = Path("/Library/Java/JavaVirtualMachines")
    if jvm_root.is_dir():
        for home_bin in sorted(jvm_root.glob("*/Contents/Home/bin")):
            s = str(home_bin)
            if home_bin.is_dir() and s not in found:
                found.append(s)
    return found


def build_env(extra_paths):
    env = os.environ.copy()
    existing = env.get("PATH", "")
    prefix = os.pathsep.join(extra_paths)
    if prefix:
        env["PATH"] = prefix + (os.pathsep + existing if existing else "")
    for p in extra_paths:
        if "openjdk" in p or "JavaVirtualMachines" in p:
            env.setdefault("JAVA_HOME", str(Path(p).parent))
            break
    return env


def probe_tool(candidates, version_args, env):
    search_path = env.get("PATH", "")
    resolved = None
    for cand in candidates:
        hit = shutil.which(cand, path=search_path)
        if hit:
            resolved = hit
            break
    if not resolved:
        return {"status": "missing", "path": None, "version": None, "detail": "not found in PATH or known keg directories"}
    try:
        proc = subprocess.run(
            [resolved, *version_args],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        low = combined.lower()
        if any(marker in low for marker in STUB_MARKERS):
            return {"status": "stub_unusable", "path": resolved, "version": None, "detail": combined.splitlines()[0][:180]}
        first_line = next((ln.strip() for ln in combined.splitlines() if ln.strip()), "")
        # cfr --help may exit 0 or 1 while printing usage/version banner
        if proc.returncode == 0 or (version_args == ["--help"] and ("cfr" in low or "usage" in low)):
            return {"status": "available", "path": resolved, "version": first_line[:180], "detail": ""}
        return {"status": "error", "path": resolved, "version": first_line[:180], "detail": f"exit_code={proc.returncode}"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "path": resolved, "version": None, "detail": f"{type(exc).__name__}: {exc}"}


def inspect_environment():
    extra_paths = discover_extra_bin_paths()
    env = build_env(extra_paths)
    tools = {}
    missing_deep = []
    brew_formulas = []
    apt_pkgs = []
    for name, category, candidates, vargs, brew_pkg, apt_pkg in TOOL_SPECS:
        info = probe_tool(candidates, vargs, env)
        info["category"] = category
        info["brew_formula"] = brew_pkg
        tools[name] = info
        if category in ("deep_jvm", "deep_binary", "sbom") and name in ("jar", "javap", "cfr", "jadx", "readelf", "syft", "trivy"):
            if info["status"] != "available":
                missing_deep.append(name)
                if brew_pkg and brew_pkg not in brew_formulas:
                    brew_formulas.append(brew_pkg)
                if apt_pkg and apt_pkg not in apt_pkgs:
                    apt_pkgs.append(apt_pkg)

    sys_name = platform.system()
    has_brew = shutil.which("brew") is not None
    install_cmd = None
    if missing_deep:
        if sys_name == "Darwin" and has_brew and brew_formulas:
            install_cmd = f"brew install {' '.join(brew_formulas)}"
        elif sys_name == "Linux" and apt_pkgs:
            install_cmd = f"sudo apt-get update && sudo apt-get install -y {' '.join(apt_pkgs)}"
            if any(t in missing_deep for t in ("cfr", "jadx", "syft", "trivy")):
                install_cmd += "  # plus official releases for cfr/jadx/syft/trivy"

    return {
        "tool_doctor_revision": REV,
        "generated_utc": now_utc(),
        "os": sys_name,
        "os_release": platform.release(),
        "arch": platform.machine(),
        "extra_bin_paths": extra_paths,
        "recommended_path_export": f'export PATH="{":".join(extra_paths)}:$PATH"' if extra_paths else "",
        "deep_toolchain_ready": len(missing_deep) == 0,
        "missing_or_unusable_deep_tools": missing_deep,
        "recommended_install_command": install_cmd,
        "tools": tools,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="Optional path to write JSON environment report")
    ap.add_argument(
        "--install",
        action="store_true",
        help="Explicitly authorized mode: install missing deep tools via Homebrew on macOS and re-verify",
    )
    args = ap.parse_args()

    report = inspect_environment()
    if args.install and report["missing_or_unusable_deep_tools"]:
        if platform.system() != "Darwin" or not shutil.which("brew"):
            print("ERROR: --install currently supports macOS Homebrew only. Run recommended_install_command manually.", file=sys.stderr)
            sys.exit(2)
        formulas = []
        for name in report["missing_or_unusable_deep_tools"]:
            f = report["tools"][name].get("brew_formula")
            if f and f not in formulas:
                formulas.append(f)
        if formulas:
            subprocess.run(["brew", "install", *formulas], check=True)
            report = inspect_environment()

    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
