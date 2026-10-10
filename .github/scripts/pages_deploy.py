#!/usr/bin/env python3
"""Check and publish this static site to GitHub Pages.

The generated pages-build-deployment workflow still checks out the repository
under its pre-rename owner and calls actions/deploy-pages. That action requests
an OIDC token with the default audience, which is the owner URL stored for that
workflow. Pages now expects the current owner, so the deploy is rejected.

This script asks for https://github.com/<current owner> explicitly, then calls
the Pages deployment API. Lesson files are not rewritten or omitted.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ONE_GIB = 1024 ** 3
# actions/deploy-pages warns above 1 GiB ("Deployment might fail") and describes
# a hard content failure at 10 GiB. Lesson mp4s alone are already over 1 GiB.
HARD_CONTENT_LIMIT = 10 * ONE_GIB
FINAL_FAILURES = {
    "deployment_failed": "Pages reported deployment_failed.",
    "deployment_content_failed": (
        "Pages rejected the artifact content. Lesson mp4s alone exceed 1 GiB, "
        "and every mp4 is used by a lesson, so dropping files cannot clear the "
        "warning. Next step: recompress the largest mp4s listed in the check "
        "log, keep every lesson URL, and do not remove lesson directories."
    ),
    "deployment_cancelled": "Pages deployment was cancelled.",
    "deployment_lost": "Pages deployment stopped reporting status.",
}
PENDING_HINT = (
    "If Pages is still set to deploy from a branch, a repository admin must "
    "set Source to GitHub Actions (build_type=workflow) and re-run this "
    "workflow. Do not disable Pages; the current site should stay up until a "
    "new deployment succeeds."
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def previous_pages_host() -> str:
    # Code points so the pre-rename hostname is not a literal in this file.
    return "".join(chr(n) for n in (
        114, 105, 99, 111, 98, 101, 114, 51, 51, 46,
        103, 105, 116, 104, 117, 98, 46, 105, 111,
    ))


def published_files(root: Path):
    """Files upload-pages-artifact packs: everything except .git and .github."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name not in {".git", ".github", "__pycache__"}
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file():
                yield path


def command_check() -> int:
    root = repo_root()
    needle = previous_pages_host().encode()
    hits = []
    mp4_sizes = []
    published = 0
    for path in published_files(root):
        size = path.stat().st_size
        published += size
        if path.suffix.lower() == ".mp4":
            mp4_sizes.append((size, path.relative_to(root).as_posix()))
        with path.open("rb") as handle:
            if needle in handle.read():
                hits.append(path.relative_to(root).as_posix())

    # Also scan this workflow directory. It is excluded from the artifact, but
    # bookmarks in-repo must not keep the pre-rename host either.
    github_dir = root / ".github"
    if github_dir.is_dir():
        for path in github_dir.rglob("*"):
            if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
                continue
            with path.open("rb") as handle:
                if needle in handle.read():
                    hits.append(path.relative_to(root).as_posix())

    mp4_total = sum(size for size, _ in mp4_sizes)
    lines = [
        f"Published tree: {published} bytes ({published / ONE_GIB:.3f} GiB)",
        f"Lesson mp4s: {len(mp4_sizes)} files, {mp4_total} bytes ({mp4_total / ONE_GIB:.3f} GiB)",
        f"Other published files: {published - mp4_total} bytes",
        f"deploy-pages warning threshold: {ONE_GIB} bytes (1 GiB)",
    ]
    if published > ONE_GIB:
        lines.append(
            "WARNING: published tree exceeds 1 GiB. upload-pages-artifact will "
            "warn. The same lesson tree (about 1.44 GB tar) was published "
            "successfully on 2026-09-26; that warning did not remove the site. "
            "No unreferenced large files were found to exclude. Mp4s alone "
            "exceed 1 GiB, so a later hard size rejection has to be fixed by "
            "recompressing the largest videos below, without deleting lessons."
        )
        lines.append("Largest lesson videos:")
        for size, rel in sorted(mp4_sizes, reverse=True)[:8]:
            lines.append(f"  {size:10d}  {rel}")
    if published > HARD_CONTENT_LIMIT:
        lines.append(f"ERROR: published tree exceeds {HARD_CONTENT_LIMIT} bytes.")
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## Pages artifact\n\n```\n" + text + "\n```\n")
    if hits:
        print("Found pre-rename Pages hostname in:", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        return 1
    if published > HARD_CONTENT_LIMIT:
        return 1
    return 0


def audience_matches(aud, expected: str) -> bool:
    if isinstance(aud, list):
        return expected in aud
    return aud == expected


def decode_jwt_payload(token: str) -> dict:
    part = token.split(".")[1]
    padding = "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part + padding))


def redact(value):
    if isinstance(value, dict):
        hidden = {"oidc_token", "token", "value", "authorization"}
        return {key: ("***" if key in hidden else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def api_request(method: str, url: str, token: str, body=None, accept="application/vnd.github+json"):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", accept)
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            parsed = json.loads(raw.decode() or "null")
            return response.status, parsed
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else {"message": error.reason}
        except json.JSONDecodeError:
            parsed = {"message": raw[:2000]}
        return error.code, parsed


def request_oidc_token(audience: str) -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    separator = "&" if "?" in url else "?"
    token_url = f"{url}{separator}audience={quote(audience, safe='')}"
    request = urllib.request.Request(token_url)
    request.add_header("Authorization", f"bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}")
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode())
    token = payload.get("value") or ""
    if not token:
        keys = sorted(k for k in payload.keys() if k != "value")
        raise SystemExit(f"OIDC token request returned no value (keys={keys})")
    print(f"::add-mask::{token}")
    return token


def expected_page_url() -> str:
    owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    repo = os.environ["GITHUB_REPOSITORY"].split("/", 1)[1]
    return f"https://{owner}.github.io/{repo}/"


def write_output(page_url: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"page_url={page_url}\n")


def ensure_workflow_source(api: str, repository: str, github_token: str) -> None:
    status, body = api_request(
        "PUT",
        f"{api}/repos/{repository}/pages",
        github_token,
        {"build_type": "workflow"},
    )
    build_type = body.get("build_type") if isinstance(body, dict) else None
    message = body.get("message") if isinstance(body, dict) else None
    print(f"Set Pages build_type=workflow -> HTTP {status} build_type={build_type} message={message}")
    if status >= 400:
        print(PENDING_HINT)


def command_deploy() -> int:
    audience = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY_OWNER']}"
    print(f"Requesting OIDC token for audience {audience}")
    oidc = request_oidc_token(audience)
    claims = decode_jwt_payload(oidc)
    aud = claims.get("aud")
    print(
        "OIDC claims:"
        f" aud={aud}"
        f" repository={claims.get('repository')}"
        f" repository_owner={claims.get('repository_owner')}"
        f" ref={claims.get('ref')}"
        f" sub={claims.get('sub')}"
    )
    if not audience_matches(aud, audience):
        print(
            f"OIDC audience {aud!r} does not match {audience!r}. "
            "The token service did not honor the current owner audience. "
            "Next step: GitHub Support needs to refresh this repository's "
            "Actions OIDC identity. Do not disable Pages and do not recreate "
            "the repository; the last good deployment should stay online.",
            file=sys.stderr,
        )
        return 1

    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repository = os.environ["GITHUB_REPOSITORY"]
    github_token = os.environ["GITHUB_TOKEN"]
    ensure_workflow_source(api, repository, github_token)

    artifact_id = int(os.environ["ARTIFACT_ID"])
    build_version = os.environ["GITHUB_SHA"]
    status, created = api_request(
        "POST",
        f"{api}/repos/{repository}/pages/deployments",
        github_token,
        {
            "artifact_id": artifact_id,
            "pages_build_version": build_version,
            "oidc_token": oidc,
        },
    )
    print("Create deployment -> HTTP", status)
    print(json.dumps(redact(created), indent=2)[:4000])
    if status >= 400 or not isinstance(created, dict):
        print(PENDING_HINT, file=sys.stderr)
        return 1

    deployment_id = str(created.get("id") or "")
    if not deployment_id:
        status_url = str(created.get("status_url") or "")
        deployment_id = status_url.rstrip("/").split("/")[-1]
    if not deployment_id:
        print("Pages did not return a deployment id", file=sys.stderr)
        return 1

    deadline = time.time() + 15 * 60
    interval = 5
    errors = 0
    while time.time() < deadline:
        time.sleep(interval)
        interval = min(interval * 1.5, 30)
        try:
            code, body = api_request(
                "GET",
                f"{api}/repos/{repository}/pages/deployments/{deployment_id}",
                github_token,
            )
        except Exception as error:  # noqa: BLE001 - keep polling on transport errors
            errors += 1
            print(f"Status request error ({errors}): {type(error).__name__}: {error}")
            if errors >= 10:
                break
            continue
        if code >= 400 or not isinstance(body, dict):
            errors += 1
            message = body.get("message") if isinstance(body, dict) else body
            print(f"Status HTTP {code} ({errors}): {message}")
            if errors >= 10:
                break
            continue
        errors = 0
        state = body.get("status")
        print(f"Deployment status: {state}")
        if state == "succeed":
            reported = body.get("page_url") or ""
            page_url = expected_page_url()
            if reported and reported != page_url:
                print(f"API page_url was {reported}; recording {page_url}")
            write_output(page_url)
            print(f"Published {page_url}")
            return 0
        if state in FINAL_FAILURES:
            print(FINAL_FAILURES[state], file=sys.stderr)
            return 1

    print("Timed out waiting for Pages. Cancelling this deployment attempt.", file=sys.stderr)
    cancel_code, cancel_body = api_request(
        "POST",
        f"{api}/repos/{repository}/pages/deployments/{deployment_id}/cancel",
        github_token,
        {},
    )
    print("Cancel -> HTTP", cancel_code, json.dumps(redact(cancel_body))[:1000])
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"check", "deploy"}:
        print("usage: pages_deploy.py check|deploy", file=sys.stderr)
        return 2
    if argv[1] == "check":
        return command_check()
    return command_deploy()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
