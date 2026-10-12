import base64
import json
import urllib.error
from datetime import date, timedelta, timezone

from agent_standup.config import Config, JiraSite
from agent_standup.enrich import git as gitmod
from agent_standup.enrich import jira
from agent_standup.secrets import SecretError

SITE = JiraSite("https://acme.atlassian.net", "me@acme.com", "env:T", ("APP",))
ICT = timezone(timedelta(hours=7))


def issue_json(summary="History page loads forever", status="In Progress", category="indeterminate"):
    return json.dumps({"fields": {"summary": summary, "status": {"name": status, "statusCategory": {"key": category}}}}).encode()


def test_fetch_issue_builds_request_and_parses():
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["auth"], seen["timeout"] = req.full_url, req.get_header("Authorization"), timeout
        return issue_json()

    issue = jira.fetch_issue(SITE, "tok", "APP-9588", opener)
    assert seen["url"] == "https://acme.atlassian.net/rest/api/3/issue/APP-9588?fields=summary,status"
    assert seen["auth"] == "Basic " + base64.b64encode(b"me@acme.com:tok").decode()
    assert seen["timeout"] == 10
    assert issue.title == "History page loads forever"
    assert issue.status == "In Progress"
    assert issue.category == "indeterminate"
    assert issue.url == "https://acme.atlassian.net/browse/APP-9588"


def test_fetch_issue_failure_returns_none():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    assert jira.fetch_issue(SITE, "tok", "APP-1", opener) is None
    assert jira.fetch_issue(SITE, "tok", "APP-1", lambda r, t: b"not json") is None


def test_fetch_all_skips_unknown_prefix_and_bad_token():
    cfg = Config(jira=(SITE, JiraSite("https://other.net", "a@b", "env:MISSING", ("LM",))))

    def resolve(ref):
        if ref == "env:MISSING":
            raise SecretError("nope")
        return "tok"

    out = jira.fetch_all(cfg, ["APP-1", "APP-1", "LM-2", "OPS-3"], resolve_secret=resolve, opener=lambda r, t: issue_json())
    assert set(out) == {"APP-1"}


def test_check_auth():
    assert jira.check_auth(SITE, "tok", lambda r, t: b"{}")

    def denied(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)

    assert not jira.check_auth(SITE, "tok", denied)


def test_repo_info_commands_and_parsing():
    calls = []
    answers = {
        "rev-parse": "fix/APP-9588\n",
        "status": " M src/a.ts\n",
        "config": "me@acme.com\n",
        "log": "a1b2c3d fix(history): stop spinner\n9f8e7d6 test: cover 403\n",
    }

    def run(args):
        calls.append(args)
        return answers[args[2]]

    info = gitmod.repo_info("/r/app", date(2026, 10, 8), ICT, run)
    assert info.branch == "fix/APP-9588"
    assert info.dirty is True
    assert info.commits == ("a1b2c3d fix(history): stop spinner", "9f8e7d6 test: cover 403")
    log_call = next(c for c in calls if c[2] == "log")
    assert "--since=2026-10-08T00:00:00+07:00" in log_call
    assert "--until=2026-10-09T00:00:00+07:00" in log_call
    assert "--author=me@acme.com" in log_call


def test_repo_info_caps_commits():
    log = "".join(f"{i:07x} commit {i}\n" for i in range(40))
    answers = {"rev-parse": "main\n", "status": "", "config": "me@acme.com\n", "log": log}
    info = gitmod.repo_info("/r/app", date(2026, 10, 8), ICT, lambda args: answers[args[2]])
    assert len(info.commits) == 16
    assert info.commits[0] == "0000000 commit 0"
    assert info.commits[-1] == "… 25 more"


def test_repo_info_without_email_has_no_commits():
    info = gitmod.repo_info("/r/app", date(2026, 10, 8), ICT, lambda args: "" if args[2] != "rev-parse" else "main\n")
    assert info.commits == ()
    assert info.dirty is False


def test_repo_info_git_failure():
    info = gitmod.repo_info("/gone", date(2026, 10, 8), ICT, lambda args: None)
    assert info == gitmod.GitInfo("/gone", None, (), False)
