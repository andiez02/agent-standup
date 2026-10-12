from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Event:
    tool: str
    ts: datetime
    session_id: str
    cwd: str | None
    branch: str | None
    role: str
    text: str


@dataclass
class TicketRef:
    key: str
    user_mentions: int = 0
    assistant_mentions: int = 0
    from_branch: bool = False

    @property
    def confirmed(self) -> bool:
        return self.user_mentions > 0 or self.from_branch


@dataclass
class SessionInfo:
    tool: str
    session_id: str
    start: datetime
    end: datetime
    branch: str | None


@dataclass(frozen=True)
class JiraIssue:
    key: str
    title: str
    status: str | None
    category: str | None
    url: str


@dataclass(frozen=True)
class GitInfo:
    repo_root: str
    branch: str | None
    commits: tuple[str, ...]
    dirty: bool


@dataclass
class ProjectEvidence:
    name: str
    events: int = 0
    repo_roots: set[str] = field(default_factory=set)
    tickets: dict[str, TicketRef] = field(default_factory=dict)
    sessions: dict[tuple[str, str], SessionInfo] = field(default_factory=dict)
    prompts: list[str] = field(default_factory=list)
    assistant_tail: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    jira: dict[str, JiraIssue] = field(default_factory=dict)
    git: list[GitInfo] = field(default_factory=list)


@dataclass
class ReportItem:
    key: str | None
    title: str
    status: str | None
    note: str
    jira_status: str | None = None
    url: str | None = None


@dataclass
class ProjectReport:
    name: str
    items: list[ReportItem]


@dataclass
class Report:
    day: date
    projects: list[ProjectReport]
    warning: str | None = None
