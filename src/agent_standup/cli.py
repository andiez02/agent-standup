import argparse
import logging
import sys
from datetime import date, datetime

from agent_standup import config, paths, pipeline, render
from agent_standup.day import previous_workday
from agent_standup.deliver import DeliveryError
from agent_standup.secrets import SecretError

log = logging.getLogger("agent_standup")


def _today(cfg: config.Config) -> date:
    return datetime.now(cfg.tz()).date() if cfg.tz() else datetime.now().astimezone().date()


def _cmd_init(args) -> int:
    path = paths.config_path()
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.TEMPLATE)
    print(f"wrote {path}")
    return 0


def _cmd_run(args, cfg: config.Config) -> int:
    day = date.fromisoformat(args.date) if args.date else previous_workday(_today(cfg), cfg.holidays)
    report = pipeline.build_report(cfg, day, data=paths.data_dir(), use_ai=not args.no_ai)
    report = report or pipeline.Report(day, [])
    print(render.to_slack(report))
    return 0


def _cmd_send(args, cfg: config.Config) -> int:
    status = pipeline.send(cfg, _today(cfg), data=paths.data_dir())
    log.info("send: %s", status)
    print(status)
    return 0


def _setup_logging() -> None:
    path = paths.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-standup", description="Daily standup from your Claude Code and Codex sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="write a config template")
    p_init.add_argument("--force", action="store_true")
    p_run = sub.add_parser("run", help="build a report and print it")
    p_run.add_argument("--date", help="YYYY-MM-DD (default: previous working day)")
    p_run.add_argument("--dry-run", action="store_true", help="print only (run never sends)")
    p_run.add_argument("--no-ai", action="store_true", help="skip the AI summary")
    sub.add_parser("send", help="build yesterday's report and post it to Slack")
    p_schedule = sub.add_parser("schedule", help="install the weekday launchd job (macOS)")
    p_schedule.add_argument("--uninstall", action="store_true")
    sub.add_parser("doctor", help="check configuration and environment")
    return parser


def _cmd_schedule(args, cfg: config.Config) -> int:
    from agent_standup import schedule

    if sys.platform != "darwin":
        print("schedule is macOS-only; use cron to run `agent-standup send` on other systems", file=sys.stderr)
        return 2
    if args.uninstall:
        schedule.uninstall()
        print("removed launchd job")
        return 0
    path = schedule.install(cfg)
    print(f"installed {path}: weekdays at {cfg.hour:02d}:{cfg.minute:02d}")
    return 0


def _cmd_doctor(args, cfg: config.Config) -> int:
    from agent_standup import doctor

    results = doctor.run_checks(cfg)
    for ok, message in results:
        print(f"{'ok  ' if ok else 'FAIL'} {message}")
    return 0 if all(ok for ok, _ in results) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return _cmd_init(args)
    try:
        cfg = config.load(paths.config_path())
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    try:
        if args.command == "run":
            return _cmd_run(args, cfg)
        if args.command == "schedule":
            return _cmd_schedule(args, cfg)
        if args.command == "doctor":
            return _cmd_doctor(args, cfg)
        _setup_logging()
        return _cmd_send(args, cfg)
    except (config.ConfigError, SecretError) as e:
        log.error("%s", e)
        print(e, file=sys.stderr)
        return 2
    except DeliveryError as e:
        log.error("%s", e)
        print(e, file=sys.stderr)
        return 1
