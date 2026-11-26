# Provenance, fingerprints and corroboration

A substring like `com.alibaba`, `beijing`, or `.cn` may indicate an association candidate. It is **not** proof that the running product embeds an in-scope prohibited component.

## Attribution chain

1. Establish exact artifact identity (path, SHA-256, version and provenance of acquisition).
2. Identify upstream component/project, direct and transitive dependency route, build-time vs runtime, compiled/shaded/vendored/forked relation.
3. Record original developing **organization**, current owning/controlling **enterprise**, timeline of transfer/foundation governance, and actual vendor for this version.
4. Prefer primary evidence: official repository history/release, official company statements, verified registries, license/copyright, signed release/checksum. Record source URLs, viewed dates and relevant lines/revisions.
5. Distinguish 3 bases: `component_developed_by_mainland_enterprise`, `component_owned_by_mainland_enterprise`, `component_actually_controlled_by_mainland_enterprise`; no extrapolation from ethnicity/name/locale. When a governance boundary is unclear, `B` or `C` with manual review.

## Fingerprint levels

| Evidence | Strength | What it does NOT prove |
|---|---|---|
| Exact artifact hash matching a verified upstream release | Strong for identity | Who currently controls the org without independent verification |
| Exact class / archive member hashes across versioned releases | Strong for byte-identical code reuse | Full product authorship; shaded subclasses may differ |
| Copyright/license, Maven coordinates, source map source path | Contextual | Definitive match if metadata can be modified |
| Token shingles / normalized text similarity | Exploratory lead | Exact identity or common ownership; boilerplate can match |
| Symbol/string/pinyin/company name | Weak lead | Development ownership or current runtime inclusion |

## Verified public-registry reference corpus (`fetch_reference.py` + `fingerprint.py`)

When the user authorizes public-registry verification and network access is available, build an isolated local reference corpus outside the target repository without uploading any target files or private source:

```bash
# 1a. Lightweight hash-only verification from a local JAR's META-INF/maven/**/pom.properties
python3 scripts/fetch_reference.py \
  --from-jar /path/to/target.jar \
  --mode hash-only \
  --corpus-dir /path/to/audit-output/reference-corpus

# 1b. Download verified official public artifacts (Maven Central / npm / PyPI allowlist only)
python3 scripts/fetch_reference.py \
  --spec maven:com.amazonaws:aws-java-sdk-s3:1.11.830 \
  --mode download \
  --corpus-dir /path/to/audit-output/reference-corpus

# 2. Run member-level & file-level fingerprint comparison against the verified corpus
python3 scripts/fingerprint.py \
  --target /path/to/repacked-or-unknown.jar \
  --corpus-manifest /path/to/audit-output/reference-corpus/corpus_manifest.json \
  --out /path/to/audit-output/fingerprint.json
```

`fetch_reference.py` enforces a strict official-host allowlist (`repo1.maven.org`, `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`), validates official registry checksums (SHA-1/SHA-256/SHA-512), and records `source_url`, `checksum_url`, `fetched_utc`, and `registry_checksum_verified` in `corpus_manifest.json`. `fingerprint.py` also remains usable completely offline with `--reference LOCAL_FILE`, in which case `reference_origin_status` stays `NOT_VERIFIED_BY_THIS_TOOL` until independently documented.

## Business-rule examples

- `registry.npmmirror.com` in `.npmrc`: classify as a **package mirror/service** risk, not automatically component-policy `FAIL`.
- Shaded ByteDance-origin SDK copied into `app.jar`: if matching source attribution is primary-verified and the policy prohibits enterprise-developed components, classify as `A` and eligible `FAIL`, even if the package was renamed.
- Only a `zhineng` variable or Simplified Chinese comment: informational clue (`D` or `B` if other independent signals), not prohibited component.
- A no-network, unknown closed-source `.so`: `C` and `Review Required`, with SHA and missing provenance explicitly documented.
