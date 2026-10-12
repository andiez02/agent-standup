import shutil

from agent_standup import engines, secrets
from agent_standup.config import Config
from agent_standup.enrich import jira


def run_checks(cfg: Config, which=shutil.which, resolve=secrets.resolve, jira_auth=jira.check_auth) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    try:
        results.append((True, f"engine: {engines.pick(cfg.engine, which=which)}"))
    except engines.EngineError as e:
        results.append((False, f"engine: {e}"))

    if not cfg.slack_webhook:
        results.append((False, "slack webhook_url is not set"))
    else:
        if secrets.is_plain(cfg.slack_webhook):
            results.append((False, "slack webhook is stored in plain text; use env: or keychain:"))
        try:
            url = resolve(cfg.slack_webhook) or ""
            results.append((url.startswith("https://hooks.slack.com/"), "slack webhook resolves to a hooks.slack.com URL"))
        except secrets.SecretError as e:
            results.append((False, f"slack webhook: {e}"))

    for name, path in (("claude logs", cfg.claude_dir), ("codex logs", cfg.codex_dir)):
        results.append((path.is_dir(), f"{name}: {path}"))

    for site in cfg.jira:
        try:
            token = resolve(site.api_token)
            results.append((bool(token) and jira_auth(site, token), f"jira {site.base_url}"))
        except secrets.SecretError as e:
            results.append((False, f"jira {site.base_url}: {e}"))
    return results
