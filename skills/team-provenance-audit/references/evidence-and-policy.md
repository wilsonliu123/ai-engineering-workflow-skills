# Evidence schema and conservative compliance policy

## Independent axes

**Association**: `A` = verified corporate association; `B` = plausible unverified; `C` = unknown origin; `D` = no identified association in tested scope. Record `association_kind` separately (`original_developer`, `current_owner`, `actual_controller`, `maintainer`, `brand`, `service`, `language_clue`, ...).

**Policy scope**: `component_developed_by_mainland_enterprise`, `component_owned_by_mainland_enterprise`, `component_actually_controlled_by_mainland_enterprise`, `service_or_mirror`, `language_or_brand_clue`, `unknown_component`. Service/mirror risks are separately reviewed unless the organization defines a service policy.

**Evidence status**: `verified_primary`, `corroborated_secondary`, `unverified`, `none`. `verified_primary` means an authoritative primary source or exact official signed/release hash and independently linked company identity has actually been checked. A name or doc example is not primary verification.

**Finding severity**: Critical/High/Medium/Low/Informational is impact, not proof of company origin. A suspicious telemetry endpoint may be High but remain B/Review Required.

## `findings.json` data contract

```json
[
  {
    "id": "FIND-001",
    "title": "Verified third-party SDK in runtime package",
    "association": "A",
    "association_kind": "original_developer",
    "policy_basis": "component_developed_by_mainland_enterprise",
    "evidence_status": "verified_primary",
    "component_name": "example-sdk",
    "version": "1.2.3",
    "location": "app.jar!/BOOT-INF/lib/example-sdk.jar",
    "sha256": "<actual hash>",
    "severity": "High",
    "evidence": [
      {"kind": "official_repo", "ref": "https://example.org/real-upstream", "observed_on": "YYYY-MM-DD", "note": "exact component + developer"}
    ],
    "dependency_path": ["app", "app.jar", "example-sdk"],
    "recommendation": "Replace with independently verified alternative"
  }
]
```

Replace all placeholders with measured values. The gate expects `association`, `policy_basis`, `evidence_status`, `evidence` (nonempty), `location` (nonempty). It does not independently validate URLs or companies; the reviewing agent remains responsible for primary-source verification. A malicious or incorrect JSON input can create a false decision; signed approval remains human-owned.

## `coverage.json` data contract

```json
{
  "repo": "/absolute/authorized/repo",
  "commit": "full SHA or N/A",
  "checks": [
    {"id": "01", "status": "Completed", "method": "bounded Unicode scan", "scope": "all readable text", "reason": ""},
    {"id": "02", "status": "Partially Completed", "method": "SCA", "scope": "npm lock", "reason": "private modules unavailable"},
    {"id": "13", "status": "Not Applicable", "method": "inventory", "scope": "no JS artifacts", "reason": "0 JS files in authorized scope"}
  ],
  "scan_errors": 0,
  "scan_skipped": 0,
  "critical_unverified_components": [],
  "material_blind_spots": []
}
```

Include **all 14 IDs** before invoking `gate.py`. `Completed` must mean meaningful coverage, not just that a command exited 0. `Not Applicable` is only allowed with explicit `reason` supported by inventory. A report can include additional detailed tables or JSONL annexes.

## Gate behavior

1. If any `A` finding names a prohibited **component** policy basis and has `verified_primary` plus nonempty locator and evidence: `FAIL` (even when other checks incomplete); manual verification of actual evidence still required.
2. Otherwise `REVIEW REQUIRED` if association `B` or `C`, any unknown critical component, any `unverified` in-scope prohibited-component evidence, any missing required check or check that is not `Completed` / defensibly `Not Applicable`, scan error, scan skipped file, material blind spot, or unclear policy scope.
3. Otherwise `PASS — No Identified Policy Violation`, qualified to evidence and scope.
4. A service/mirror cannot produce prohibited-component FAIL by itself under the included policy. If the enterprise uses a separate ban on Chinese infrastructure/services, supply a separate explicit policy instead of silently expanding this one.
5. A `D` finding may still document a weak lead. Missing findings alone never prove the scanner covered all files and dependencies.

The bundled gate is a **consistency check** on provided structured review, not a replacement for authoritative source verification or human compliance approval.
