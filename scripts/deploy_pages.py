#!/usr/bin/env python3
"""Deploy the already-uploaded Pages artifact.

actions/deploy-pages requests an OIDC token with no audience. After the
account rename, that default can still be https://github.com/ricober33 while
Pages expects https://github.com/ricobingo. This script requests the current
owner audience explicitly and refuses any other aud claim.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

EXPECTED_AUDIENCE = "https://github.com/ricobingo"
FINAL_FAILURES = {
    "deployment_failed": "Pages reported deployment_failed.",
    "deployment_content_failed": "Pages rejected the artifact content.",
    "deployment_cancelled": "Pages deployment was cancelled.",
    "deployment_lost": "Pages deployment stopped reporting status.",
}


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
        return {
            key: ("***" if key in hidden else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def api_request(method: str, url: str, token: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
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
    request.add_header(
        "Authorization",
        f"bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode())
    token = payload.get("value") or ""
    if not token:
        keys = sorted(key for key in payload.keys() if key != "value")
        raise SystemExit(f"OIDC token request returned no value (keys={keys})")
    print(f"::add-mask::{token}")
    return token


def remind_pages_source() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    print(
        "If this deploy is rejected because Pages still builds from a branch, "
        f"set https://github.com/{repository}/settings/pages "
        "→ Build and deployment → Source to GitHub Actions, then re-run "
        "Deploy GitHub Pages. That stops the generated workflow, which still "
        "requests audience https://github.com/ricober33 and uploads the full tree."
    )


def try_select_workflow_source(api: str, repository: str, github_token: str) -> None:
    status, body = api_request(
        "PUT",
        f"{api}/repos/{repository}/pages",
        github_token,
        {"build_type": "workflow"},
    )
    build_type = body.get("build_type") if isinstance(body, dict) else None
    message = body.get("message") if isinstance(body, dict) else None
    print(
        "Set Pages build_type=workflow -> "
        f"HTTP {status} build_type={build_type} message={message}"
    )
    if status >= 400:
        remind_pages_source()


def main() -> int:
    owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    audience = f"https://github.com/{owner}"
    if audience != EXPECTED_AUDIENCE:
        print(
            f"Refusing to deploy. OIDC audience must be {EXPECTED_AUDIENCE}, "
            f"repository owner is {owner}.",
            file=sys.stderr,
        )
        return 1

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
    )
    if not audience_matches(aud, EXPECTED_AUDIENCE):
        print(
            f"OIDC audience {aud!r} does not match {EXPECTED_AUDIENCE!r}.",
            file=sys.stderr,
        )
        return 1

    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repository = os.environ["GITHUB_REPOSITORY"]
    github_token = os.environ["GITHUB_TOKEN"]
    try_select_workflow_source(api, repository, github_token)

    artifact_id = int(os.environ["ARTIFACT_ID"])
    status, created = api_request(
        "POST",
        f"{api}/repos/{repository}/pages/deployments",
        github_token,
        {
            "artifact_id": artifact_id,
            "pages_build_version": os.environ["GITHUB_SHA"],
            "oidc_token": oidc,
        },
    )
    print("Create deployment -> HTTP", status)
    print(json.dumps(redact(created), indent=2)[:4000])
    if status >= 400 or not isinstance(created, dict):
        remind_pages_source()
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
        code, body = api_request(
            "GET",
            f"{api}/repos/{repository}/pages/deployments/{deployment_id}",
            github_token,
        )
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
            page_url = f"https://{owner}.github.io/{repository.split('/', 1)[1]}/"
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a", encoding="utf-8") as handle:
                    handle.write(f"page_url={page_url}\n")
            print(f"Published {page_url}")
            return 0
        if state in FINAL_FAILURES:
            print(FINAL_FAILURES[state], file=sys.stderr)
            return 1

    print("Timed out waiting for Pages.", file=sys.stderr)
    api_request(
        "POST",
        f"{api}/repos/{repository}/pages/deployments/{deployment_id}/cancel",
        github_token,
        {},
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
