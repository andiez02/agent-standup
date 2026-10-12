# agent-standup

## What it does

Every weekday, agent-standup reconstructs the previous working day's activity from local Claude Code and Codex CLI session logs, groups it by project and ticket, and posts a daily report to Slack. An optional Claude or Codex summary assigns Done, In progress, or Blocked to each item; without AI, the report lists confirmed tickets and session activity.

```text
*Daily report: Thu 08/10*

*Storefront*
Yesterday I worked on:
• <https://acme.atlassian.net/browse/APP-9588|APP-9588>: History page loads indefinitely
    ✅ Done: Fix ready (Jira: In Review)

*jukebox*
Yesterday I worked on:
• Stake picker polish
    🟡 In progress: PR opened
• Waiting on design
    🔴 Blocked: Needs Alex's mockup
```

## Requirements

- macOS for scheduling; the core CLI also runs on other platforms.
- Python 3.11 or newer; uv can install Python for you.
- Claude Code CLI and/or Codex CLI installed and logged in.
- A Slack workspace where you can add an app.

## Install

```bash
uv tool install git+https://github.com/andiez02/agent-standup
agent-standup init
```

Edit `~/.config/agent-standup/config.toml`. `init` refuses to overwrite an existing config unless you pass `--force`.

A minimal config:

```toml
engine = "auto"            # auto | claude | codex
model = "sonnet"           # optional; claude defaults to sonnet, codex to its own default
language = "en"
extra_ticket_prefixes = ["APP"]

[slack]
webhook_url = "keychain:agent-standup-slack"

[[projects]]
name = "Storefront"
paths = ["~/Code/storefront/**"]
prefixes = ["APP"]
```

Top-level keys must come before any `[table]` header.

## Model and cost

One report is a single non-agentic call with about 20k input and 3k output tokens. Measured per run: Opus about $0.20, Sonnet about $0.11, Haiku under $0.01. Sonnet and Haiku produce reports of similar quality; Haiku occasionally adds tickets that were only mentioned. On a Claude subscription the call counts toward your usage limits instead.

## Slack webhook

Create an app at https://api.slack.com/apps → **From scratch** → **Incoming Webhooks** → **On** → **Add New Webhook to Workspace**. Pick a private channel that only you are in.

Store the webhook in macOS Keychain, pasting the URL when prompted:

```bash
security add-generic-password -a "$USER" -s agent-standup-slack -w
```

Set this in your config:

```toml
[slack]
webhook_url = "keychain:agent-standup-slack"
```

Secrets also accept `env:VARIABLE` references. Keep secrets out of your repository.

## Jira (optional)

Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens and store it in Keychain:

```bash
security add-generic-password -a "$USER" -s agent-standup-jira -w
```

Add a site to your config:

```toml
[[jira]]
base_url = "https://your-company.atlassian.net"
email = "you@company.com"
api_token = "keychain:agent-standup-jira"
prefixes = ["APP"]
```

Jira supplies ticket titles and status. Without Jira, set the top-level `extra_ticket_prefixes = ["APP", "LM"]` before any table headers to enable ticket extraction. With no configured prefixes, ticket extraction is disabled.

## Projects

Project names come from the first matching configured path rule, then the Git repository name, then the working directory's basename. Linked worktrees use the main repository name. Rules support `~` and `/**` for a directory and its descendants.

```toml
[[projects]]
name = "My Project"
paths = ["~/Code/my-project/**"]
```

Top-level `ignore_projects` excludes named projects. A project rule can include `prefixes = ["APP"]` to assign matching tickets to that project.

## Try it

```bash
agent-standup doctor
agent-standup run --dry-run
agent-standup run --date YYYY-MM-DD --no-ai
```

Replace `YYYY-MM-DD` with the date you want to report. `run` prints the report without sending it or creating an archive or sent marker. The default date is the previous working day, skipping weekends and configured holidays.

## Schedule

```bash
agent-standup schedule
```

The macOS launchd job runs on weekdays at the configured time, defaulting to 10:00, and catches up on wake if the Mac was asleep. Set the top-level `schedule = { hour = 10, minute = 0 }` before any table headers to change the time.

The job captures your shell's `PATH` at installation. Re-run `agent-standup schedule` after installing Claude or Codex somewhere new.

```bash
agent-standup schedule --uninstall
```

## Privacy

Excerpts of your prompts and agent replies are sent to the engine you chose, Claude or Codex, to write the summary. Only the summary goes to Slack. Optional Jira lookups request ticket titles and statuses; other local session content stays on your machine outside the selected engine request. The summarizer runs without saving its own session, and activity under the application's data directory is excluded from collection. Use `--no-ai` to skip the engine.

## Files

| File | Default location |
| --- | --- |
| Config | `~/.config/agent-standup/config.toml` |
| Report archive | `~/.local/share/agent-standup/reports/YYYY-MM-DD.md` |
| Sent marker | `~/.local/share/agent-standup/sent/YYYY-MM-DD` |
| macOS log | `~/Library/Logs/agent-standup.log` |
| launchd job | `~/Library/LaunchAgents/dev.agent-standup.plist` |

`XDG_CONFIG_HOME` overrides the config base directory. `XDG_DATA_HOME` overrides the data base directory and places the log there as `agent-standup/agent-standup.log`. On other platforms, the log is in the data directory.

## Troubleshooting

An **AI summary unavailable** warning means the report used raw activity. Run `agent-standup doctor` and check that the selected engine works from a plain terminal.

If nothing is posted, check `~/Library/Logs/agent-standup.log` and whether the target date's sent marker already exists. Successful sends create a marker to prevent duplicate posts. Empty days send nothing unless the top-level `send_when_empty = true` is set. A failed Slack delivery leaves the archive and no sent marker so the next run can retry.
