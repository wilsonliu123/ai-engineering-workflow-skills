#!/usr/bin/env python3
"""Authorized official public-registry reference resolver and local corpus builder.

Fetches ONLY official public release checksums or artifacts from an allowlist of
trusted public registries (Maven Central, npm, PyPI) using public package coordinates.
NEVER uploads target files, private source code, or local paths to any remote host.
"""
import argparse
import base64
import datetime
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import ssl
import urllib.parse
import zipfile

REV = "1.1.0"

ALLOWED_HOSTS = {
    "repo1.maven.org",
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
}

MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024  # 128 MiB per public release artifact
USER_AGENT = "team-provenance-audit/1.1 (read-only provenance verification)"

_HTTP_METHOD_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9!#$%&'*+\-.^_`|~]*\Z")
_HTTP_HEADER_NAME_RE = re.compile(r"\A[A-Za-z0-9!#$%&'*+\-.^_`|~]+\Z")
_SENSITIVE_REDIRECT_HEADERS = frozenset({
    "authorization",
    "token",
    "cookie",
    "proxy-authorization",
    "x-api-key",
})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class HTTPRequestError(OSError):
    """Raised on non-2xx HTTP responses from an allowlisted registry."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def hash_file(path):
    h256 = hashlib.sha256()
    h1 = hashlib.new("sha1", usedforsecurity=False)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h256.update(chunk)
            h1.update(chunk)
    return {"sha256": h256.hexdigest(), "sha1": h1.hexdigest(), "size": path.stat().st_size}


def _parse_and_validate_url(url, allowed_hosts=ALLOWED_HOSTS):
    """Strictly validate URL scheme, authority, port, and registry allowlist."""
    if not isinstance(url, str) or not url:
        raise ValueError("HTTP request URL must be a non-empty string")
    if any(ord(ch) < 0x20 or ord(ch) >= 0x7F or ch.isspace() for ch in url):
        raise ValueError("HTTP request URL contains invalid control, whitespace, or non-ASCII characters")
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("https", "http"):
        raise ValueError(f"disallowed URL scheme: {scheme or '<empty>'}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("HTTP request URL must not contain embedded userinfo credentials")
    if "\\" in parsed.netloc:
        raise ValueError("HTTP request URL authority must not contain backslashes")
    hostname = parsed.hostname
    if not hostname or any(ch.isspace() for ch in hostname):
        raise ValueError("HTTP request URL must include a non-empty hostname")
    host_lower = hostname.lower()
    normalized_allowed = {str(h).lower() for h in (allowed_hosts or ())}
    if host_lower not in normalized_allowed:
        raise ValueError(f"host '{host_lower}' is not in trusted public registry allowlist")
    if scheme == "http" and host_lower not in _LOOPBACK_HOSTS:
        raise ValueError("HTTPS is required for all external registry requests")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid port in HTTP request URL: {exc}") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(f"HTTP request URL port out of range: {port}")
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    target = f"{path}?{parsed.query}" if parsed.query else path
    return scheme, hostname, port, effective_port, target


def validate_url(url, allowed_hosts=ALLOWED_HOSTS):
    _parse_and_validate_url(url, allowed_hosts=allowed_hosts)
    return url


class _VerifiedTLSConnection(http.client.HTTPConnection):
    """Pure-stdlib TLS-wrapped HTTPConnection enforcing ssl.create_default_context()."""

    default_port = http.client.HTTPS_PORT

    def __init__(
        self,
        host,
        port=None,
        timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address=None,
        blocksize=8192,
        *,
        context=None,
    ):
        super().__init__(
            host,
            port=port,
            timeout=timeout,
            source_address=source_address,
            blocksize=blocksize,
        )
        if context is None:
            ctx = ssl.create_default_context()
            if hasattr(ssl, "TLSVersion") and ctx.minimum_version < ssl.TLSVersion.TLSv1_2:
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            try:
                ctx.set_alpn_protocols(["http/1.1"])
            except (AttributeError, NotImplementedError):
                pass
            self._context = ctx
        else:
            if not isinstance(context, ssl.SSLContext):
                raise TypeError("TLS context must be an ssl.SSLContext instance.")
            if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
                raise ValueError(
                    "TLS context must require certificate verification and hostname checks."
                )
            if (
                hasattr(ssl, "TLSVersion")
                and context.minimum_version < ssl.TLSVersion.TLSv1_2
                and context.minimum_version != ssl.TLSVersion.MAXIMUM_SUPPORTED
            ):
                raise ValueError("TLS context must require TLS 1.2 or higher.")
            self._context = context

    def connect(self):
        super().connect()
        server_hostname = self._tunnel_host if self._tunnel_host else self.host
        try:
            self.sock = self._context.wrap_socket(
                self.sock,
                server_hostname=server_hostname,
            )
        except BaseException:
            raw_sock = self.sock
            self.sock = None
            if raw_sock is not None:
                try:
                    raw_sock.close()
                except OSError:
                    pass
            raise


def _http_request(
    url,
    method="GET",
    headers=None,
    timeout=30,
    max_bytes=MAX_DOWNLOAD_BYTES,
    max_redirects=5,
    allowed_hosts=ALLOWED_HOSTS,
    ssl_context=None,
):
    """Execute a hardened pure-stdlib HTTP/HTTPS request enforcing allowlisted hosts on every hop."""
    if max_redirects < 0:
        raise ValueError("max_redirects must be non-negative")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not isinstance(method, str) or not _HTTP_METHOD_RE.match(method):
        raise ValueError("Invalid HTTP method")
    cur_method = method.upper()

    cur_headers = {}
    for k, v in (headers or {}).items():
        if not isinstance(k, str) or not _HTTP_HEADER_NAME_RE.match(k):
            raise ValueError("Invalid HTTP header name")
        v_str = str(v)
        if any((ord(ch) < 0x20 and ch != "\t") or ord(ch) == 0x7F for ch in v_str):
            raise ValueError(f"Invalid control character in HTTP header '{k}'")
        try:
            v_str.encode("latin-1")
        except UnicodeEncodeError:
            raise ValueError("Invalid HTTP header value") from None
        cur_headers[k] = v_str

    current_url = url
    scheme, hostname, port, effective_port, target = _parse_and_validate_url(
        current_url, allowed_hosts=allowed_hosts
    )
    current_origin = (scheme, hostname.lower(), effective_port)
    seen_hops = {(cur_method, current_origin, target)}
    redirects_followed = 0

    while True:
        if scheme == "https":
            conn = _VerifiedTLSConnection(
                hostname,
                port=effective_port,
                timeout=timeout,
                context=ssl_context,
            )
        else:
            conn = http.client.HTTPConnection(
                hostname,
                port=effective_port,
                timeout=timeout,
            )
        try:
            conn.request(cur_method, target, headers=cur_headers)
            resp = conn.getresponse()
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            location = resp.getheader("Location")
            if status in (301, 302, 303, 307, 308) and location is not None:
                body_bytes = b""
            else:
                chunks = []
                total = 0
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"artifact exceeds {max_bytes} byte download cap")
                    chunks.append(chunk)
                body_bytes = b"".join(chunks)
        finally:
            conn.close()

        if status in (301, 302, 303, 307, 308) and location is not None:
            if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in location):
                raise ValueError("HTTP redirect Location header contains invalid control characters")
            loc_clean = location.strip()
            if not loc_clean:
                return status, resp_headers, body_bytes, current_url
            if redirects_followed >= max_redirects:
                raise RuntimeError(f"HTTP redirect limit ({max_redirects}) exceeded")

            next_url = urllib.parse.urljoin(current_url, loc_clean)
            next_scheme, next_host, next_port, next_eff_port, next_target = _parse_and_validate_url(
                next_url, allowed_hosts=allowed_hosts
            )
            if scheme == "https" and next_scheme == "http":
                raise ValueError("HTTPS to HTTP redirect downgrade is not allowed")

            next_origin = (next_scheme, next_host.lower(), next_eff_port)
            next_headers = {k: v for k, v in cur_headers.items() if k.lower() != "host"}
            if next_origin != current_origin:
                next_headers = {
                    k: v for k, v in next_headers.items()
                    if k.lower() not in _SENSITIVE_REDIRECT_HEADERS
                }

            hop_key = (cur_method, next_origin, next_target)
            if hop_key in seen_hops:
                raise RuntimeError("HTTP redirect loop detected")
            seen_hops.add(hop_key)

            redirects_followed += 1
            current_url = next_url
            scheme, hostname, port, effective_port, target = (
                next_scheme, next_host, next_port, next_eff_port, next_target
            )
            current_origin = next_origin
            cur_headers = next_headers
            continue

        return status, resp_headers, body_bytes, current_url


def http_get_bytes(url, max_bytes=MAX_DOWNLOAD_BYTES, timeout=30, allowed_hosts=ALLOWED_HOSTS, ssl_context=None):
    status, _, body_bytes, final_url = _http_request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        max_bytes=max_bytes,
        allowed_hosts=allowed_hosts,
        ssl_context=ssl_context,
    )
    if status < 200 or status >= 300:
        raise HTTPRequestError(f"HTTP {status} for {final_url}", status=status)
    return body_bytes, final_url


def safe_filename(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "artifact"


def parse_spec(spec):
    if ":" not in spec:
        raise ValueError(f"spec must start with ecosystem prefix (maven:, npm:, pypi:): {spec}")
    eco, rest = spec.split(":", 1)
    eco = eco.lower().strip()
    if eco == "maven":
        parts = rest.split(":")
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"invalid maven spec (expected maven:groupId:artifactId:version): {spec}")
        return {"ecosystem": "maven", "group": parts[0], "artifact": parts[1], "version": parts[2], "coordinate": rest}
    if eco == "npm":
        idx = rest.rfind("@")
        if idx <= 0 or idx == len(rest) - 1:
            raise ValueError(f"invalid npm spec (expected npm:name@version): {spec}")
        return {"ecosystem": "npm", "name": rest[:idx], "version": rest[idx + 1:], "coordinate": rest}
    if eco == "pypi":
        if "==" not in rest:
            raise ValueError(f"invalid pypi spec (expected pypi:name==version): {spec}")
        name, ver = rest.split("==", 1)
        if not name or not ver:
            raise ValueError(f"invalid pypi spec: {spec}")
        return {"ecosystem": "pypi", "name": name, "version": ver, "coordinate": rest}
    raise ValueError(f"unsupported ecosystem '{eco}' (supported: maven, npm, pypi)")


def extract_maven_specs_from_jar(jar_path):
    """Read ONLY META-INF/maven/**/pom.properties locally; never transmit the JAR."""
    specs = []
    with zipfile.ZipFile(jar_path, "r") as z:
        for info in z.infolist():
            if not info.filename.endswith("pom.properties") or info.file_size > 65536:
                continue
            txt = z.read(info).decode("utf-8", "replace")
            props = {}
            for line in txt.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()
            g, a, v = props.get("groupId"), props.get("artifactId"), props.get("version")
            if g and a and v:
                specs.append(f"maven:{g}:{a}:{v}")
    return sorted(set(specs))


def resolve_maven(parsed, mode, corpus_dir, target_hashes=None, base_url="https://repo1.maven.org/maven2", allowed_hosts=ALLOWED_HOSTS):
    g_path = "/".join(parsed["group"].split("."))
    art = parsed["artifact"]
    ver = parsed["version"]
    jar_url = f"{base_url.rstrip('/')}/{g_path}/{art}/{ver}/{art}-{ver}.jar"
    sha1_url = jar_url + ".sha1"
    entry = {
        "ecosystem": "maven",
        "coordinate": parsed["coordinate"],
        "source_url": jar_url,
        "checksum_url": sha1_url,
        "fetched_utc": now_utc(),
        "mode": mode,
        "reference_origin_status": "UNVERIFIED",
        "registry_checksum_verified": False,
    }
    try:
        sha1_raw, _ = http_get_bytes(sha1_url, max_bytes=4096, allowed_hosts=allowed_hosts)
        official_sha1 = sha1_raw.decode("ascii", "replace").strip().split()[0].lower()
        if not re.fullmatch(r"[0-9a-f]{40}", official_sha1):
            raise ValueError(f"unexpected sha1 payload from Maven Central: {official_sha1[:60]}")
        entry["official_sha1"] = official_sha1
        if target_hashes:
            entry["target_sha1_match"] = (target_hashes["sha1"] == official_sha1)
            entry["verification_verdict"] = "EXACT_UPSTREAM_MATCH" if entry["target_sha1_match"] else "MISMATCH_OR_REPACKAGED"
        if mode == "download":
            blob, final_url = http_get_bytes(jar_url, allowed_hosts=allowed_hosts)
            got_sha1 = hashlib.new("sha1", blob, usedforsecurity=False).hexdigest()
            got_sha256 = hashlib.sha256(blob).hexdigest()
            if got_sha1 != official_sha1:
                raise ValueError(f"downloaded JAR sha1 {got_sha1} != official {official_sha1}")
            dest = corpus_dir / f"maven_{safe_filename(parsed['group'])}_{safe_filename(art)}_{safe_filename(ver)}.jar"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
            entry.update({
                "source_url": final_url,
                "local_file": str(dest.resolve()),
                "sha1": got_sha1,
                "sha256": got_sha256,
                "size": len(blob),
                "registry_checksum_verified": True,
                "reference_origin_status": "VERIFIED_PUBLIC_REGISTRY",
            })
        else:
            entry["registry_checksum_verified"] = True
            entry["reference_origin_status"] = "VERIFIED_PUBLIC_REGISTRY"
    except (HTTPRequestError, http.client.HTTPException, ValueError, OSError, RuntimeError) as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["verification_verdict"] = "REGISTRY_LOOKUP_FAILED"
    return entry


def resolve_npm(parsed, mode, corpus_dir, target_hashes=None, base_url="https://registry.npmjs.org", allowed_hosts=ALLOWED_HOSTS):
    pkg = urllib.parse.quote(parsed["name"], safe="@/")
    ver = urllib.parse.quote(parsed["version"], safe="")
    meta_url = f"{base_url.rstrip('/')}/{pkg}/{ver}"
    entry = {
        "ecosystem": "npm",
        "coordinate": parsed["coordinate"],
        "checksum_url": meta_url,
        "fetched_utc": now_utc(),
        "mode": mode,
        "reference_origin_status": "UNVERIFIED",
        "registry_checksum_verified": False,
    }
    try:
        raw, final_meta = http_get_bytes(meta_url, max_bytes=2 * 1024 * 1024, allowed_hosts=allowed_hosts)
        doc = json.loads(raw.decode("utf-8"))
        dist = doc.get("dist", {})
        tarball_url = dist.get("tarball", "")
        shasum = (dist.get("shasum") or "").lower()
        integrity = dist.get("integrity") or ""
        entry["source_url"] = tarball_url or final_meta
        entry["official_sha1"] = shasum
        entry["official_integrity"] = integrity
        if target_hashes and shasum:
            entry["target_sha1_match"] = (target_hashes["sha1"] == shasum)
            entry["verification_verdict"] = "EXACT_UPSTREAM_MATCH" if entry["target_sha1_match"] else "MISMATCH_OR_REPACKAGED"
        if mode == "download" and tarball_url:
            blob, final_tb = http_get_bytes(tarball_url, allowed_hosts=allowed_hosts)
            got_sha1 = hashlib.new("sha1", blob, usedforsecurity=False).hexdigest()
            got_sha256 = hashlib.sha256(blob).hexdigest()
            if shasum and got_sha1 != shasum:
                raise ValueError(f"downloaded npm tarball sha1 {got_sha1} != official {shasum}")
            if integrity.startswith("sha512-"):
                expected_b64 = integrity.split("-", 1)[1]
                got_b64 = base64.b64encode(hashlib.sha512(blob).digest()).decode("ascii")
                if got_b64 != expected_b64:
                    raise ValueError("downloaded npm tarball sha512 integrity mismatch")
            dest = corpus_dir / f"npm_{safe_filename(parsed['name'])}_{safe_filename(parsed['version'])}.tgz"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
            entry.update({
                "source_url": final_tb,
                "local_file": str(dest.resolve()),
                "sha1": got_sha1,
                "sha256": got_sha256,
                "size": len(blob),
                "registry_checksum_verified": True,
                "reference_origin_status": "VERIFIED_PUBLIC_REGISTRY",
            })
        else:
            entry["registry_checksum_verified"] = bool(shasum or integrity)
            entry["reference_origin_status"] = "VERIFIED_PUBLIC_REGISTRY" if entry["registry_checksum_verified"] else "UNVERIFIED"
    except (HTTPRequestError, http.client.HTTPException, ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["verification_verdict"] = "REGISTRY_LOOKUP_FAILED"
    return entry


def resolve_pypi(parsed, mode, corpus_dir, target_hashes=None, base_url="https://pypi.org/pypi", allowed_hosts=ALLOWED_HOSTS):
    name = urllib.parse.quote(parsed["name"], safe="")
    ver = urllib.parse.quote(parsed["version"], safe="")
    meta_url = f"{base_url.rstrip('/')}/{name}/{ver}/json"
    entry = {
        "ecosystem": "pypi",
        "coordinate": parsed["coordinate"],
        "checksum_url": meta_url,
        "fetched_utc": now_utc(),
        "mode": mode,
        "reference_origin_status": "UNVERIFIED",
        "registry_checksum_verified": False,
    }
    try:
        raw, _ = http_get_bytes(meta_url, max_bytes=2 * 1024 * 1024, allowed_hosts=allowed_hosts)
        doc = json.loads(raw.decode("utf-8"))
        urls = doc.get("urls", [])
        if not urls:
            raise ValueError("no release artifacts in PyPI metadata")
        official_sha256s = [u.get("digests", {}).get("sha256", "").lower() for u in urls if u.get("digests", {}).get("sha256")]
        chosen = next((u for u in urls if u.get("packagetype") == "bdist_wheel"), urls[0])
        art_url = chosen.get("url", "")
        art_sha256 = (chosen.get("digests", {}).get("sha256") or "").lower()
        entry["source_url"] = art_url
        entry["official_sha256"] = art_sha256
        entry["all_official_sha256s"] = official_sha256s
        if target_hashes:
            entry["target_sha256_match"] = target_hashes["sha256"] in official_sha256s
            entry["verification_verdict"] = "EXACT_UPSTREAM_MATCH" if entry["target_sha256_match"] else "MISMATCH_OR_REPACKAGED"
        if mode == "download" and art_url:
            blob, final_url = http_get_bytes(art_url, allowed_hosts=allowed_hosts)
            got_sha256 = hashlib.sha256(blob).hexdigest()
            got_sha1 = hashlib.new("sha1", blob, usedforsecurity=False).hexdigest()
            if art_sha256 and got_sha256 != art_sha256:
                raise ValueError(f"downloaded PyPI file sha256 {got_sha256} != official {art_sha256}")
            fname = safe_filename(chosen.get("filename") or f"pypi_{parsed['name']}_{parsed['version']}")
            dest = corpus_dir / fname
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
            entry.update({
                "source_url": final_url,
                "local_file": str(dest.resolve()),
                "sha1": got_sha1,
                "sha256": got_sha256,
                "size": len(blob),
                "registry_checksum_verified": True,
                "reference_origin_status": "VERIFIED_PUBLIC_REGISTRY",
            })
        else:
            entry["registry_checksum_verified"] = bool(official_sha256s)
            entry["reference_origin_status"] = "VERIFIED_PUBLIC_REGISTRY" if entry["registry_checksum_verified"] else "UNVERIFIED"
    except (HTTPRequestError, http.client.HTTPException, ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["verification_verdict"] = "REGISTRY_LOOKUP_FAILED"
    return entry


def resolve_spec(spec_str, mode, corpus_dir, target_hashes=None, registry_overrides=None, allowed_hosts=ALLOWED_HOSTS):
    parsed = parse_spec(spec_str)
    overrides = registry_overrides or {}
    if parsed["ecosystem"] == "maven":
        return resolve_maven(parsed, mode, corpus_dir, target_hashes, base_url=overrides.get("maven", "https://repo1.maven.org/maven2"), allowed_hosts=allowed_hosts)
    if parsed["ecosystem"] == "npm":
        return resolve_npm(parsed, mode, corpus_dir, target_hashes, base_url=overrides.get("npm", "https://registry.npmjs.org"), allowed_hosts=allowed_hosts)
    return resolve_pypi(parsed, mode, corpus_dir, target_hashes, base_url=overrides.get("pypi", "https://pypi.org/pypi"), allowed_hosts=allowed_hosts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", action="append", default=[], help="Public coordinate: maven:g:a:v | npm:name@v | pypi:name==v (repeatable)")
    ap.add_argument("--from-jar", help="Extract public Maven coordinates from a local JAR's META-INF/maven/**/pom.properties without uploading the JAR")
    ap.add_argument("--target", help="Optional local file whose SHA-1/SHA-256 will be compared against the fetched official registry checksum")
    ap.add_argument("--mode", choices=["hash-only", "download"], default="hash-only", help="hash-only fetches checksum metadata only; download saves verified public artifacts into --corpus-dir")
    ap.add_argument("--corpus-dir", required=True, help="Directory outside target repo for corpus_manifest.json and downloaded public artifacts")
    args = ap.parse_args()

    specs = list(args.spec)
    target_path = Path(args.target).resolve(strict=True) if args.target else None
    if args.from_jar:
        jar_p = Path(args.from_jar).resolve(strict=True)
        if not target_path:
            target_path = jar_p
        specs.extend(extract_maven_specs_from_jar(jar_p))
    specs = sorted(set(specs))
    if not specs:
        ap.error("provide at least one --spec or a --from-jar containing pom.properties")

    target_hashes = hash_file(target_path) if target_path else None
    corpus_dir = Path(args.corpus_dir).resolve()
    corpus_dir.mkdir(parents=True, exist_ok=True)

    entries = [resolve_spec(s, args.mode, corpus_dir, target_hashes=target_hashes) for s in specs]
    manifest_path = corpus_dir / "corpus_manifest.json"
    existing = []
    if manifest_path.exists():
        try:
            old_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing = old_doc.get("entries", [])
        except (OSError, json.JSONDecodeError):
            existing = []

    by_key = {(e.get("ecosystem"), e.get("coordinate")): e for e in existing}
    for e in entries:
        by_key[(e.get("ecosystem"), e.get("coordinate"))] = e

    manifest = {
        "resolver_revision": REV,
        "generated_utc": now_utc(),
        "allowed_hosts": sorted(ALLOWED_HOSTS),
        "privacy_guarantee": "Only public package coordinates are requested from official registries; no local files or private code are uploaded.",
        "target_checked": {"path": str(target_path), **target_hashes} if target_path else None,
        "entries": list(by_key.values()),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "manifest": str(manifest_path),
        "resolved_this_run": len(entries),
        "verified_count": sum(1 for e in entries if e.get("registry_checksum_verified")),
        "entries": entries,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
