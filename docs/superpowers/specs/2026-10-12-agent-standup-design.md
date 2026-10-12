# agent-standup — Design

Date: 2026-10-12
Status: Approved in chat, pending written-spec review

## Goal

Every weekday at 10:00 local time, post a Slack message summarizing what the user worked on during the previous working day, reconstructed from local Claude Code CLI and Codex CLI session logs, grouped by project, with a per-item status (Done / In progress / Blocked).

Open source on public GitHub. Nothing user-specific in code; everything user-specific lives in config.

## Non-goals (v1)

- Linux/Windows scheduling (core runs anywhere; only `schedule` is macOS launchd).
- Slack bot features (replies, slash commands, DMs via bot token).
- Posting back to Jira.
- "Today" / plans section. Report covers the previous working day only.
- Other agents (Cursor, opencode, Gemini). Collector interface allows adding later.

## Users and success criteria

- Developers who use Claude Code CLI and/or Codex CLI on macOS.
- Success: report lists the right tickets/projects for the day, no noise tickets (keys that appear only in injected context), status matches reality, text is ready to paste into a standup.

## CLI

Package `agent-standup`, Python ≥ 3.11, standard library only at runtime (pytest for dev). Installed via `uv tool install git+https://github.com/<owner>/agent-standup`.

| Command | Behavior |
|---|---|
| `agent-standup init` | Write a commented config template to `~/.config/agent-standup/config.toml` (refuse to overwrite unless `--force`). |
| `agent-standup run [--date YYYY-MM-DD] [--dry-run] [--no-ai]` | Build the report. Default date = previous working day. `--dry-run` prints to stdout, sends nothing, writes no sent-marker. `--no-ai` skips the engine and renders the raw fallback. |
| `agent-standup send` | Same as `run` for the default date, then deliver to Slack, save the `.md`, write the sent-marker. Used by the scheduler. |
| `agent-standup schedule [--uninstall]` | Write/load (or unload/remove) `~/Library/LaunchAgents/dev.agent-standup.plist` with `StartCalendarInterval` for Weekday 1–5 at configured hour:minute, running `agent-standup send`. The plist copies the installing shell's `PATH` into `EnvironmentVariables` so `claude`/`codex`/`git` resolve under launchd. |
| `agent-standup doctor` | Check config parses, secrets resolve, engines found on PATH, log dirs exist, Jira auth works, webhook URL well-formed (no message sent). |

## Architecture

```
collect ─► group ─► enrich ─► summarize ─► render ─► deliver
```

Each stage is a module with plain-data inputs/outputs (dataclasses), testable alone.

### `day`

- `previous_workday(today, holidays) -> date`: step back one day at a time, skipping Saturday, Sunday and configured holidays.
- All timestamps converted to the machine's local timezone (`datetime.astimezone()`), unless `timezone` is set in config.

### `collect`

Output: list of `Event(tool, ts, session_id, cwd, branch, role, text)` where `role ∈ {user, assistant}` and `ts` falls on the target local date.

Claude collector (`~/.claude/projects/*/*.jsonl`, plus `claude_dir` override):
- Only consider files with mtime ≥ target date (performance).
- Keep records `type == "user"` with `isMeta` falsy and `isSidechain` falsy, whose content is a string or contains `text` parts (skip pure `tool_result` lists).
- Keep records `type == "assistant"` text parts.
- `cwd` and `gitBranch` taken from the record.

Codex collector (`~/.codex/sessions/YYYY/MM/DD/*.jsonl` and `~/.codex/archived_sessions/**`, plus `codex_dir` override):
- `session_meta` gives `cwd`/git branch; `turn_context.cwd` updates cwd.
- User text from `response_item` with `payload.type == "message"`, `role == "user"`, content `input_text`; and `event_msg` `payload.type == "user_message"`. Deduplicate identical text within a session.
- Assistant text from `response_item` `message` with `role == "assistant"` (`output_text`).

Noise filters (both tools; drop the event or the text block):
- Text starting with injected-context markers: `# AGENTS.md`, `<environment_context`, `<user_instructions`, `<INSTRUCTIONS`, `<skills_instructions`, `<system-reminder`, `This session is being continued from a previous conversation`, `Caveat:`, `[Request interrupted`.
- Codex approval-reviewer sessions: any session whose first user text starts with `The following is the Codex agent history` is skipped entirely.
- Developer-role and tool call/output records are never read.

### `group`

- Project resolution for each event's `cwd`, first match wins:
  1. `[[projects]]` rules in config: `name` + list of `paths` globs (`~` expanded, `**` supported).
  2. Git repo name (`git -C cwd rev-parse --show-toplevel`, cached per cwd); linked worktrees take the main repo's name from `--git-common-dir`, so all worktrees of one repo form one project.
  3. Basename of `cwd`.
- When an event's `cwd` is not inside a git repo, its immediate child directories that are git repos are added to the project's repos (covers agents launched from a folder that holds several repos).
- Ticket keys: regex `\b([A-Z][A-Z0-9]{1,9})-(\d{1,6})\b` over user and assistant text, kept only if the prefix is in the configured `ticket_prefixes` (union of all Jira sites' `prefixes` plus top-level `extra_ticket_prefixes`). If nothing is configured, no ticket extraction (avoids `UTF-8`, `SHA-256` noise).
- A ticket is *confirmed* if it appears in user text or in a session's branch name. Keys that only the assistant mentions are passed to the engine separately as `mentioned_only_by_agent`, with an instruction to include them only when the evidence shows work on them that day; the no-AI fallback lists confirmed tickets only. Keys the engine returns that are not in the evidence are stripped.
- A ticket is attached to the project where it is mentioned most; a project-level `prefixes` hint in `[[projects]]` overrides.
- Per project, the evidence bundle: tickets (with mention counts and first/last time), sessions (tool, time span, branch), up to N user prompts (default 20, each truncated to 300 chars), last 3 assistant messages per session (truncated to 500 chars).
- Projects matching `ignore_projects` are dropped.

### `enrich`

- Jira (optional): for each ticket whose prefix maps to a configured site, `GET {base_url}/rest/api/3/issue/{key}?fields=summary,status` with basic auth (email + API token). Store `title`, `status.name`, `status.statusCategory.key`. Timeout 10 s, failures recorded and ignored. Requests run concurrently (thread pool, max 8).
- Fallback title when Jira is absent or fails: ask the engine to infer from evidence; in `--no-ai` mode, title is omitted.
- Git (per repo touched that day): commits by `git config user.email` with author date on the target day (`git log --since --until --author`), current branch, and whether the working tree has uncommitted changes (`git status --porcelain`). Branch names containing a ticket key link that ticket to the repo.

### `summarize`

- Engine selection: config `engine = "auto" | "claude" | "codex"`. `auto` picks `claude` if on PATH, else `codex`.
- Claude: `claude -p --output-format json --tools "" --strict-mcp-config --no-session-persistence`, prompt on stdin, answer in the `result` field.
- Codex: `codex exec --sandbox read-only --ephemeral --skip-git-repo-check --color never -o <file> -`, prompt on stdin, answer read from `<file>`.
- Both run with `cwd = ~/.local/share/agent-standup/engine`, and the collector drops any event whose `cwd` is under the data dir, so the summarizer never reports on itself.
- Timeout 180 s.
- Prompt contains: the evidence JSON, the status rules, language, and a strict JSON output schema:
  ```json
  {"projects":[{"name":"...","items":[{"key":"APP-1|null","title":"...","status":"done|in_progress|blocked","note":"<= 120 chars","jira_status":"...|null"}]}]}
  ```
- Status rules given to the model:
  - `done`: Jira status category is `done`, or commit/PR exists and evidence says the work is finished.
  - `blocked`: evidence explicitly says waiting on a named person/team; the note names who.
  - `in_progress`: everything else, including uncommitted changes.
  - Work without a ticket: 1–3 items per project describing the main pieces of work.
- Parse the first JSON object in the output; validate shape. On any failure → fallback renderer (no AI) and a warning line in the message.

### `render`

Slack mrkdwn:

```
*Daily report: Thu 08/10*

*<Project>*
Yesterday I worked on:
• APP-9588: <title>
  ✅ Done: <note> (Jira: <status>)
• <work item without ticket>
  🟡 In progress: <note>
```

- Emoji: ✅ done, 🟡 in_progress, 🔴 blocked.
- Projects ordered by number of events, descending.
- Jira keys linked as `<{base_url}/browse/KEY|KEY>` when the site is known.
- Same content rendered to Markdown for the archive file.
- Fallback (no AI): per project, list tickets with Jira title/status and session time spans; no status emoji.

### `deliver`

- POST `{"text": ..., "mrkdwn": true}` to the webhook URL. Retry 3 times with backoff (2 s, 4 s, 8 s) on network errors and 5xx.
- Always save `~/.local/share/agent-standup/reports/YYYY-MM-DD.md` (target date).
- Sent-marker `~/.local/share/agent-standup/sent/YYYY-MM-DD` (target date) written only after a successful POST; `send` exits early if the marker exists, so a launchd catch-up run never double-posts.
- No activity on target date: send nothing unless `send_when_empty = true`, then post "No agent activity recorded for <date>".
- Logs: `~/Library/Logs/agent-standup.log` on macOS (stdout/stderr of the launchd job also go there).

## Config

`~/.config/agent-standup/config.toml`:

```toml
engine = "auto"            # auto | claude | codex
language = "en"
schedule = { hour = 10, minute = 0 }
holidays = ["2026-12-25"]
send_when_empty = false
ignore_projects = []
extra_ticket_prefixes = []

[slack]
webhook_url = "keychain:agent-standup-slack"   # or "env:AGENT_STANDUP_SLACK_WEBHOOK"

[[jira]]
base_url = "https://example.atlassian.net"
email = "me@example.com"
api_token = "keychain:agent-standup-jira-example"
prefixes = ["APP"]

[[projects]]
name = "Example"
paths = ["~/Code/example/**"]
```

Secret references: `env:NAME` reads the environment variable; `keychain:SERVICE` runs `security find-generic-password -s SERVICE -w`. Plain strings are accepted but `doctor` warns. Secrets never appear in logs or error messages.

## Error handling summary

| Failure | Behavior |
|---|---|
| Config missing/invalid | Exit 2 with the parse error and path; `send` logs it. |
| Log dir missing for a tool | Skip that tool; note in `doctor`. |
| Jira request fails | Ignore for that ticket; title inferred by engine. |
| Engine missing/timeout/bad JSON | Fallback renderer + warning line. |
| Slack fails after retries | Exit 1, `.md` saved, no sent-marker (next run retries). |

## Testing

- pytest, fixtures under `tests/fixtures/` with small anonymized Claude and Codex JSONL files covering: normal prompts, injected AGENTS.md, compaction summary, Codex approval-reviewer session, tool results, records on adjacent days across a timezone boundary.
- Unit: `previous_workday` (Mon→Fri, holidays, Sunday run), project resolution order, ticket regex + prefix filtering, noise filters, secret resolution (env; keychain via injectable runner).
- Integration: full pipeline with fake engine (returns canned JSON / garbage / timeout) and fake HTTP (Slack + Jira via injectable opener), asserting rendered text, sent-marker behavior, retry.
- `run --dry-run` on the fixtures as a smoke test.
- GitHub Actions: macOS and Ubuntu, Python 3.11 and 3.13.

## Privacy

Excerpts of prompts and assistant replies are sent to the selected engine (Claude or Codex) to write the summary; only the summary goes to Slack. Documented in the README.
