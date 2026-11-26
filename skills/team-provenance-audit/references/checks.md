# Fourteen-check audit control catalog

Each check must have `status`, `methods`, `scope`, `counts`, `limitations`, and `reason` when Not Applicable. `Completed` means the specified relevant population was inspected and its remaining limits disclosed; it does not mean every upstream claim has been verified. A lack of tools or incomplete inventory means `Partially Completed`/`Not Available`, not `Completed`.

| ID | Control | Required examination | Evidence / caveat |
|---|---|---|---|
| 01 | Simplified Chinese text | Unicode-distinctive characters in source/comments/README/i18n/logs/commits/embedded strings | Traditional/simplified shared characters are not proof of simplified; localization is not origin |
| 02 | Enterprise-developed open source | All libraries/frameworks/SDKs and their original developer, current corporate owner/control/maintainer | ByteDance/CloudWeGo, Alibaba, Tencent, Baidu, Huawei, Xiaomi, Meituan, JD, NetEase, Didi, Bilibili, PingCAP, DeepSeek, Zhipu etc. are *leads*, not a complete denylist |
| 03 | Logo and branding | SVG, PNG, WebP, CSS resources, icons, README assets, UI brand strings | Visual resemblance not enough; verify brand with authoritative sources |
| 04 | Pinyin / PRC city / naming | Packages, modules, API paths, functions, directories, domains, branches, project codenames | Place names and phonetic overlap are leads, never evidence of corporate control |
| 05 | Direct + transitive dependencies | Package manager graph, lockfiles, vendored code, forked source, build/runtime dependencies | Show `app -> direct -> transitive -> upstream` where possible; unresolvable tree is an explicit gap |
| 06 | Organization/ownership | Git origins, owner history, transfer/forks, original authorship organization, present maintainers/foundation | Individual nationality/name is irrelevant to classification |
| 07 | Domains / remote resources | API, CDN, websocket, update/download, registry, image host, docs, package mirror | TLD `.cn` not enough; record provider, code path, when active |
| 08 | Cloud services, data flows, models and MCP | Model API, weights/model IDs, ASR/TTS/OCR/embeddings, telemetry, storage, auth, MCP tools | Distinguish mentioned / integrated / enabled / runtime observed; never assert exfiltration from static URL |
| 09 | Build and deployment supply chain | Docker base image/digest, GitHub/GitLab/Jenkins CI, IaC, code generators, mirrors, installer downloads | An Alibaba mirror is a service/mirror finding, not automatically a Chinese-written code component |
| 10 | Git history | Historic dependencies/logos/URLs/removed code and current influence | Never mark removed code as current without provenance path |
| 11 | Binary/native/packaged | ELF, Mach-O, PE, WASM, .so/.dll/.node/.a/.pyc/.apk/.ipa/.whl, .jar/.war, shaded/embedded archives | Hash, signs, strings/symbols, package metadata, decompiler level/limits; do not run |
| 12 | License/SBOM/controlling company | LICENSE, NOTICE, SPDX/CycloneDX, registry metadata, publisher/copyright, fork/transfer | Forking/relabeling doesn't erase provenance; corporate governance exceptions require explicit policy |
| 13 | Minified / obfuscated JS | JS/.mjs/.cjs/.min.js/.bundle.js source maps, decoded strings, AST/bundle module signatures | Minification is common; static deobfuscation first; never eval generated code |
| 14 | JAR/JVM reverse engineering | MANIFEST, Maven coordinates, Class files, `BOOT-INF/lib`, `WEB-INF/lib`, shaded relocated packages | Use `jar`, `javap`, CFR/Vineflower if approved; decompilation is approximate and may lose names |

## Manifest and source coverage checklist

- JS/TS: `package.json`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lock*`, `.npmrc`, `.yarnrc*`, Vite/Webpack/Next config, `node_modules`/`dist` when unique code exists.
- Java/JVM: `pom.xml`, `build.gradle*`, `.mvn/`, `settings.xml`, `gradle.lockfile`, Spring fat JAR/shaded JAR.
- Python: `requirements*.txt`, `pyproject.toml`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `pip.conf`, wheels, native extensions.
- Go: `go.mod`, `go.sum`, `vendor/modules.txt`, `replace`, `GOPROXY`.
- Rust: `Cargo.*`, `.cargo/config.toml`, `build.rs`.
- .NET: `*.csproj`, `*.sln`, `packages.lock.json`, `Directory.Packages.props`, `NuGet.Config`, bundled DLL.
- C/C++: CMake, Meson, Conan, Vcpkg, Bazel, Make, `FetchContent`, downloaded/vendored sources.
- PHP/Ruby: Composer and Gem manifests/locks.
- Mobile: Podfile, Swift packages, Gradle, Flutter pubspec, APK/AAR/IPA frameworks and bundled analytics/login SDKs.
- Cloud: `Dockerfile`, Compose, Terraform, K8s, Helm, CI YAML, `actions/*`, registry/mirrors, pinned image digest.
- AI: weights `.gguf`, `.safetensors`, `.onnx`, `.pt`; model IDs/Hub origin, license, hosting, MCP tools and external model APIs.

## Recommended sequence for large repositories

1. Enumerate all accessible paths without following external symlinks; include hidden config; exclude internal Git object database from raw byte scan (inspect Git with Git tooling separately).
2. Prioritize manifest and lockfile inventory, runtime entrypoints, binary packages and remote endpoints; treat `node_modules`/`dist`/`vendor` as populations, not blanket excludes.
3. Analyze unique hashes once, but retain **all usage locations** and dependency paths; deduplicate repeated third-party artifact bytes, not occurrences.
4. Perform targeted JS/JAR deep analysis on high-impact and unknown provenance. No source map -> fallback to bounded static analysis; no decompiler -> level-2 metadata/strings + unresolved.
5. Record full coverage, including exclusions by count, size and reason. For very large outputs prefer machine-readable exhaustive annex over flooding Markdown.
