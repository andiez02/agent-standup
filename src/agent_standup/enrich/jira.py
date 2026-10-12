import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from agent_standup import secrets
from agent_standup.config import Config, JiraSite
from agent_standup.models import JiraIssue

Opener = Callable[[urllib.request.Request, float], bytes]


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _request(site: JiraSite, token: str, path: str) -> urllib.request.Request:
    auth = base64.b64encode(f"{site.email}:{token}".encode()).decode()
    return urllib.request.Request(f"{site.base_url}{path}", headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})


def fetch_issue(site: JiraSite, token: str, key: str, opener: Opener = _open) -> JiraIssue | None:
    req = _request(site, token, f"/rest/api/3/issue/{urllib.parse.quote(key)}?fields=summary,status")
    try:
        data = json.loads(opener(req, 10))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    fields = data.get("fields") or {}
    status = fields.get("status") or {}
    return JiraIssue(
        key=key,
        title=fields.get("summary") or "",
        status=status.get("name"),
        category=(status.get("statusCategory") or {}).get("key"),
        url=f"{site.base_url}/browse/{key}",
    )


def fetch_all(cfg: Config, keys: list[str], resolve_secret=secrets.resolve, opener: Opener = _open) -> dict[str, JiraIssue]:
    tokens: dict[str, str | None] = {}
    jobs: list[tuple[JiraSite, str]] = []
    for key in sorted(set(keys)):
        site = cfg.jira_site_for(key)
        if site is None:
            continue
        if site.base_url not in tokens:
            try:
                tokens[site.base_url] = resolve_secret(site.api_token)
            except secrets.SecretError:
                tokens[site.base_url] = None
        if tokens[site.base_url]:
            jobs.append((site, key))
    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: fetch_issue(job[0], tokens[job[0].base_url], job[1], opener), jobs))
    return {issue.key: issue for issue in results if issue}


def check_auth(site: JiraSite, token: str, opener: Opener = _open) -> bool:
    try:
        opener(_request(site, token, "/rest/api/3/myself"), 10)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return True
