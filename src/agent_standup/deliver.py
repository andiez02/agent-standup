import json
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path


class DeliveryError(Exception):
    pass


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def post_slack(url: str, text: str, opener=_open, sleep=time.sleep) -> None:
    body = json.dumps({"text": text, "mrkdwn": True}).encode()
    for delay in (2, 4, 8, None):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            opener(req, 15)
            return
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise DeliveryError(f"Slack rejected the message: HTTP {e.code}") from None
            reason = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = str(getattr(e, "reason", e))
        if delay is None:
            raise DeliveryError(f"Slack unreachable after retries: {reason}")
        sleep(delay)


def archive(markdown: str, day: date, base: Path) -> Path:
    path = base / "reports" / f"{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown)
    return path


def _marker(day: date, base: Path) -> Path:
    return base / "sent" / day.isoformat()


def already_sent(day: date, base: Path) -> bool:
    return _marker(day, base).exists()


def mark_sent(day: date, base: Path) -> None:
    path = _marker(day, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
