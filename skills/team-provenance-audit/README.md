# Software Provenance Auditor — Skill package

Version: 1.2. A reusable, **evidence-led** audit of software components and mainland-China corporate origin, including obfuscated JavaScript, Java archives, SBOM generation, official-registry hash corroboration, and native binaries. Audits are read-only; outputs are written to a separate folder.

## Inside this skill

- `SKILL.md`: the agent's orchestration instructions and three reliability mechanisms
- `references/`: 14-check matrix, static reverse engineering, provenance/fingerprinting, conservative policy decision rules, full Traditional Chinese report format
- `scripts/check_env.py`: toolchain preflight & doctor verifying real `jar`/`javap`, `cfr`, `jadx`, `readelf`/`greadelf`, `syft`, `trivy` execution and Homebrew keg-only paths; optional explicit `--install` mode on macOS Homebrew
- `scripts/static_scan.py`: checkpointed/resumable local candidate scan with SQLite state, JSON inventory, self-exclusion when placed inside `--repo`, and precise locators
- `scripts/fetch_reference.py`: allowlisted official public-registry (`repo1.maven.org`, `registry.npmjs.org`, `pypi.org`) checksum verification and local reference corpus builder (public coordinates only; never uploads target files)
- `scripts/fingerprint.py`: exact SHA-256/SHA-1, byte-identical archive members, and exploratory normalized text / JS-shape fingerprints supporting both `--reference FILE` and `--corpus-manifest corpus_manifest.json`
- `scripts/gate.py`: PASS / FAIL / REVIEW REQUIRED decisions from human-reviewed coverage and findings JSON
- `assets/policy.example.json`: editable policy boundary; `assets/*example.json`: conservative input examples
- `scripts/tests/test_skill.py`: built-in offline unit and smoke tests

## Example usage by a compatible coding agent

Ask: `Use team-provenance-audit to audit the authorized repository at /path/to/repo. Output the report to /path/to/audit-output. Do not run target code.`

For the deterministic local pass, from this skill directory:

```bash
python3 scripts/check_env.py --out /path/to/audit-output/env_check.json
python3 scripts/static_scan.py --repo /path/to/repo --out /path/to/audit-output
python3 scripts/static_scan.py --repo /path/to/repo --out /path/to/audit-output
```

Optional authorized official-registry reference verification & fingerprinting:

```bash
python3 scripts/fetch_reference.py --from-jar /path/to/target.jar --mode download --corpus-dir /path/to/audit-output/reference-corpus
python3 scripts/fingerprint.py --target /path/to/target.jar --corpus-manifest /path/to/audit-output/reference-corpus/corpus_manifest.json --out /path/to/audit-output/fingerprint.json
python3 scripts/gate.py --repo /path/to/repo --coverage /path/to/audit-output/coverage.json --findings /path/to/audit-output/findings.json --policy assets/policy.example.json --out /path/to/audit-output/decision.json
python3 -m unittest discover -s scripts/tests -v
```

**Limitations:** None of these scripts automatically verify corporate ownership, unpack arbitrary proprietary obfuscators, or guarantee exhaustive dynamic dependencies. Incomplete checks remain `REVIEW REQUIRED`.
