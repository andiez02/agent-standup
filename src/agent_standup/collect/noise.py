import re

INJECTED_PREFIXES = (
    "# AGENTS.md",
    "<environment_context",
    "<user_instructions",
    "<INSTRUCTIONS",
    "<skills_instructions",
    "<system-reminder",
    "This session is being continued from a previous conversation",
    "Caveat:",
    "[Request interrupted",
    "<task-notification",
    "<command-name>",
    "<command-message>",
    "<local-command-",
    "<bash-stdout",
    "<bash-stderr",
    "<user_shell_command",
    "<send_user_message_question",
)
REVIEWER_PREFIX = "The following is the Codex agent history"
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def clean(text: str) -> str:
    return _REMINDER.sub("", text).strip()


def is_injected(text: str) -> bool:
    return text.lstrip().startswith(INJECTED_PREFIXES)
