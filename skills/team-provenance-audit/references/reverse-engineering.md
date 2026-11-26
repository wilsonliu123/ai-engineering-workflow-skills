# Authorized static reverse-engineering playbook

## Preflight

Analyze only in-scope software. Run `python3 scripts/check_env.py --out /path/to/audit-output/env_check.json` first to verify real execution of `jar`/`javap` (avoiding macOS stub false positives), `cfr`/`cfr-decompiler`, `jadx`, `readelf`/`greadelf`, `syft`, and `trivy`, and prepend any reported `extra_bin_paths` (such as Homebrew keg-only `/opt/homebrew/opt/openjdk/bin` and `/opt/homebrew/opt/binutils/bin`) to `PATH`. Confirm file type/magic first. Capture size and SHA-256 of *original bytes*. Use trusted offline/approved tooling; record exact version and commands. Do not run unknown target programs, launch project build hooks, use JS evaluation, or let an archive extract outside the temporary output directory. If dynamic behavior is truly necessary, describe a proposed isolated test; require separate authorization and no production credentials or network by default.

## JS: source map and static deobfuscation

1. Collect `.js`, `.mjs`, `.cjs`, `.min.js`, `.bundle.js`, `.chunk.js`, `.map`, ASAR, and CDN script URLs; map the corresponding runtime path/import reference. Differentiate ordinary minification from deliberate obfuscation.
2. Inspect `//# sourceMappingURL=` and source map `sources`, `sourcesContent` when available. Source paths may be forged; corroborate using package lock/other source metadata.
3. Beautify locally; use approved Babel/Acorn/Esprima AST tools to extract literal strings, `import`, `require`, `fetch`, XHR, `WebSocket`, `import()`, and `new Function` sites. Identify string arrays, Base64, hex/unicode encoding, constant folding, control-flow flattening, and split/lazy-loaded chunk references.
4. Only **decode declarative, bounded data** with static transforms. Never execute generated or obfuscated JS in Node/browser or by `eval`. Preserve before/after transforms and source offset or identifiable AST node ranges.
5. Recover module boundaries, license banners, source path, exported APIs and candidate upstream library fingerprints. Compare with an approved known upstream release; only source evidence plus metadata plus corroborated signature supports attribution.
6. Report original hash, recovered strings/endpoints, unresolved dynamic expressions, candidate authorship with confidence, and absence of runtime evidence.

Useful commands (only where approved): `rg -n`, `node --check` only for syntactic checks (avoid if unknown toolchain behavior), Babel parser, Source Map consumers, Prettier. Avoid install scripts on an untrusted project.

## JAR / WAR / Java bytecode

1. Validate archive size and ZIP central directory; avoid high expansion ratios / traversal. List files without extracting (`jar tf`, Python `zipfile.infolist`).
2. Inspect `META-INF/MANIFEST.MF`, `META-INF/maven/**/pom.properties`/`pom.xml`, `META-INF/services/*`, class package paths, `module-info.class`, `BOOT-INF/lib`, `WEB-INF/lib`, native `.so/.dll` and nested JARs.
3. Recover Maven coordinates/version/license. Detect shading and relocation: compare class/package paths, unchanged resource identifiers, known class/method signatures, and per-member hash to verifiable upstream release.
4. Run `javap -verbose -p -c` on selected classes, or approved CFR/Vineflower/Procyon static decompiler. Decompiled code may omit names, original comments, and control-flow structure; mark as reconstructed.
5. Extract constant-pool strings, URLs, class names and reflective loading hooks. Reflection, JNI, and class loaders are not inherently malicious. Never invoke class initializer or run JAR to inspect.
6. Summarize exact archive/member locator `path.jar!/BOOT-INF/lib/nested.jar!/com/x/Foo.class`, checksum and source attribution evidence.

## Native / other binaries

- ELF/PE/Mach-O: `file`, `strings` (preserve actual byte offsets where possible), `readelf`, `nm`, `otool`, `objdump`, `codesign`/`jarsigner`/signature metadata when available. Symbol paths, build IDs and signing certificates are leads subject to corroboration.
- WASM: section inspection and trusted `wasm2wat` as static disassembly. .NET: IL metadata and approved ILSpy; Android APK/AAR: manifest/classes via approved jadx/apktool with zip safety; Python bytecode: Python-version-aware static disassembly. For unknown binaries, do not claim readable source was recovered.
- Side-channel provenance: exact SHA-256 vs trustworthy release checksum, build definitions / Docker images / install scripts / lockfile / Git history. Distinguish actual artifact bytes from inferred association.

## Fallback levels

- **L1 Decompiled/Unpacked:** supported static decompilation or archive inspection completed, with concrete evidence positions.
- **L2 Static forensic / supply-chain inference:** no reliable source recovered; collect hashes, strings, symbols, metadata, signatures, build origin and alternative references.
- **L3 Unknown:** no defensible origin after L1/L2; set `association=C`, `policy_result=Review Required`, list next verification method. Never turn absence of evidence into PASS for that component.

## Safe resource bounds

Apply file byte budget and log oversized skipped files. Never extract archives blindly. Bound per-member uncompressed bytes, total uncompressed size, compression ratio, and nesting depth. Reject traversal (`../`, absolute) or symlinks when extracting in any later authorized task. Default to inspection without extraction.
