from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from xx.config import ConfigError, default_config_path, load_config
from xx.discovery import discover_machine_context
from xx.executor import execute_command
from xx.providers import ProviderError, generate_command
from xx.reporting import serve_report
from xx.safety import assess_command
from xx.storage import connect, insert_execution, prune_old_records, update_execution_outcome
from xx.types import ExecutionRecord


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["report"]:
        report_argv = argv[1:]
        if report_argv[:1] == ["serve"]:
            report_argv = report_argv[1:]
        report_parser = _build_report_parser()
        args = report_parser.parse_args(report_argv)
        return _run_report_command(args)

    parser = _build_main_parser()
    args = parser.parse_args(argv)

    if not args.request:
        parser.print_help(sys.stderr)
        return 2

    try:
        config = load_config(
            config_path=Path(args.config).expanduser() if args.config else None,
            provider_override=args.provider,
            model_override=args.model,
            print_only=args.print_only,
            debug=args.debug,
            no_cache=args.no_cache,
            force=args.force,
        )
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    machine = discover_machine_context(cache_enabled=config.cache_enabled)
    conn = connect(config.reporting.database_path)
    try:
        prune_old_records(conn, config.reporting.retention_days)
        user_request = " ".join(args.request).strip()

        try:
            proposal = generate_command(config, machine, user_request)
        except ProviderError as exc:
            print(f"Provider error: {exc}", file=sys.stderr)
            return 1

        safety = assess_command(proposal.command, machine)
        if proposal.risk == "high" and safety.level != "high":
            safety.level = "high"
        if config.debug:
            _print_debug(config, machine, proposal, safety)

        record = ExecutionRecord(
            invoked_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            user_input=user_request,
            generated_command=proposal.command,
            executed=False,
            approved=False,
            provider=proposal.provider,
            model=proposal.model,
            prompt_tokens=proposal.token_usage.prompt_tokens,
            completion_tokens=proposal.token_usage.completion_tokens,
            total_tokens=proposal.token_usage.total_tokens,
            risk_level=safety.level,
            exit_code=None,
            cwd=str(machine.cwd),
        )
        record_id = insert_execution(conn, record)

        if safety.level == "high" and not config.force:
            print(f">>> {proposal.command}")
            print("Refusing to run a high-risk command without --force.", file=sys.stderr)
            update_execution_outcome(conn, record_id, executed=False, approved=False, exit_code=None)
            return 1

        print(f">>> {proposal.command}")
        if config.print_only:
            return 0

        approved = _confirm()
        if not approved:
            update_execution_outcome(conn, record_id, executed=False, approved=False, exit_code=None)
            print("Execution cancelled.")
            return 0

        exit_code = execute_command(proposal.command, machine.shell)
        update_execution_outcome(conn, record_id, executed=True, approved=True, exit_code=exit_code)
        return exit_code
    finally:
        conn.close()


def _run_report_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(
            config_path=Path(args.config).expanduser() if args.config else None,
            debug=args.debug,
            require_provider=False,
        )
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    return serve_report(config.reporting)


def _confirm() -> bool:
    answer = input("Execute this command? (Y/n): ").strip().lower()
    return answer in {"", "y", "yes"}


def _print_debug(config, machine, proposal, safety) -> None:
    print(f"[debug] provider={config.provider} model={config.model}")
    print(f"[debug] shell={machine.shell} cwd={machine.cwd}")
    print(
        "[debug] tokens="
        f"prompt={proposal.token_usage.prompt_tokens} "
        f"completion={proposal.token_usage.completion_tokens} "
        f"total={proposal.token_usage.total_tokens}"
    )
    print(f"[debug] risk={safety.level} flags={', '.join(safety.flags) if safety.flags else 'none'}")
    if proposal.reason:
        print(f"[debug] reason={proposal.reason}")


def _build_main_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xx")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("request", nargs="*")
    return parser


def _build_report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xx report serve")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--debug", action="store_true")
    return parser
