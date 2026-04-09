from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime
from pathlib import Path

from xx.config import ConfigError, default_config_path, load_config
from xx.colors import ColorConfig, colorize
from xx.discovery import discover_machine_context
from xx.executor import ExecutionError, execute_command
from xx.memory import describe_memory, lookup_repaired_command, remember_successful_repair
from xx.migrate import migrate_timestamps_to_local
from xx.providers import ProviderError, generate_command, generate_repaired_command
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
    if argv[:2] == ["migrate", "timestamps"]:
        return _run_timestamp_migration(argv[2:])
    if argv[:1] == ["doctor"] and len(argv) == 1:
        return _run_doctor()

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
            proposal = _generate_proposal(config, machine, user_request)
        except ProviderError as exc:
            print(f"Provider error: {exc}", file=sys.stderr)
            return 1
        execution_group_id = uuid.uuid4().hex

        safety = assess_command(proposal.command, machine)
        if proposal.risk == "high" and safety.level != "high":
            safety.level = "high"
        if config.debug:
            _print_debug(config, machine, proposal, safety)

        record = ExecutionRecord(
            invoked_at=_local_timestamp(),
            execution_group_id=execution_group_id,
            attempt_index=1,
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

        print(_format_command_preview(proposal.command, config.colors))
        if config.print_only:
            return 0

        approved = _confirm()
        if not approved:
            update_execution_outcome(conn, record_id, executed=False, approved=False, exit_code=None)
            print("Execution cancelled.")
            return 0

        try:
            result = execute_command(proposal.command, machine.shell, config.colors)
        except ExecutionError as exc:
            update_execution_outcome(conn, record_id, executed=False, approved=True, exit_code=None)
            print(f"Execution error: {exc}", file=sys.stderr)
            return 1
        update_execution_outcome(conn, record_id, executed=True, approved=True, exit_code=result.exit_code)
        if result.exit_code == 0:
            return 0
        return _attempt_repair(
            conn=conn,
            config=config,
            machine=machine,
            user_request=user_request,
            previous_command=proposal.command,
            previous_result=result,
            execution_group_id=execution_group_id,
            next_attempt_index=2,
        )
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


def _run_doctor() -> int:
    try:
        config = load_config(require_provider=False)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    machine = discover_machine_context(cache_enabled=config.cache_enabled)
    memory = describe_memory(config.memory_path)
    print(f"config_path: {config.config_path}")
    print(f"provider: {config.provider or '(not set)'}")
    print(f"model: {config.model or '(not set)'}")
    print(f"shell: {machine.shell}")
    print(f"cwd: {machine.cwd}")
    print(f"available_commands: {len(machine.available_commands)}")
    print(f"report_database: {config.reporting.database_path}")
    print(f"colors_enabled: {config.colors.enabled}")
    print(f"colors_preview: {config.colors.preview}")
    print(f"colors_output: {config.colors.output}")
    print(f"semantic_memory_backend: {memory['backend']}")
    print(f"semantic_memory_path: {memory['path']}")
    print(f"semantic_memory_entries: {memory['entries']}")
    print(f"report_url: http://{config.reporting.host}:{config.reporting.port}/report")
    print(f"repair_attempts: {config.repair_attempts}")
    print(f"retention_days: {config.reporting.retention_days}")
    print(f"default_report_days: {config.reporting.default_report_days}")
    return 0


def _run_timestamp_migration(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="xx migrate timestamps")
    parser.add_argument("--config", default=str(default_config_path()))
    args = parser.parse_args(argv)
    try:
        config = load_config(
            config_path=Path(args.config).expanduser() if args.config else None,
            require_provider=False,
        )
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    scanned, updated = migrate_timestamps_to_local(config.reporting.database_path)
    print(f"scanned_records: {scanned}")
    print(f"updated_records: {updated}")
    print(f"database: {config.reporting.database_path}")
    return 0


def _attempt_repair(
    conn,
    config,
    machine,
    user_request,
    previous_command,
    previous_result,
    *,
    execution_group_id: str,
    next_attempt_index: int,
) -> int:
    if config.repair_attempts <= 0:
        return previous_result.exit_code

    remembered_command = lookup_repaired_command(
        config.memory_path,
        user_request=user_request,
    )
    command = previous_command
    result = previous_result
    for attempt in range(1, config.repair_attempts + 1):
        print(
            f"Command failed with exit code {result.exit_code}. Attempting amended command {attempt}/{config.repair_attempts}...",
            file=sys.stderr,
        )
        try:
            proposal = generate_repaired_command(
                config,
                machine,
                user_request,
                command,
                result.exit_code,
                result.stdout,
                result.stderr,
                remembered_command,
            )
        except ProviderError as exc:
            print(f"Repair error: {exc}", file=sys.stderr)
            return result.exit_code

        safety = assess_command(proposal.command, machine)
        if proposal.risk == "high" and safety.level != "high":
            safety.level = "high"
        if config.debug:
            print(f"[debug] repaired proposal attempt={attempt}")
            _print_debug(config, machine, proposal, safety)

        record = ExecutionRecord(
            invoked_at=_local_timestamp(),
            execution_group_id=execution_group_id,
            attempt_index=next_attempt_index,
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

        print(_format_command_preview(proposal.command, config.colors))
        approved = _confirm()
        if not approved:
            update_execution_outcome(conn, record_id, executed=False, approved=False, exit_code=None)
            print("Amended execution cancelled.")
            return result.exit_code

        try:
            result = execute_command(proposal.command, machine.shell, config.colors)
        except ExecutionError as exc:
            update_execution_outcome(conn, record_id, executed=False, approved=True, exit_code=None)
            print(f"Execution error: {exc}", file=sys.stderr)
            return 1
        update_execution_outcome(conn, record_id, executed=True, approved=True, exit_code=result.exit_code)
        if result.exit_code == 0:
            remember_successful_repair(
                config.memory_path,
                user_request=user_request,
                failed_command=command,
                successful_command=proposal.command,
            )
            print(
                f"Saved successful repair to semantic memory: {config.memory_path}",
                file=sys.stderr,
            )
            return 0
        command = proposal.command
        next_attempt_index += 1

    return result.exit_code


def _generate_proposal(config, machine, user_request):
    prior_successful_command = lookup_repaired_command(
        config.memory_path,
        user_request=user_request,
    )
    return generate_command(config, machine, user_request, prior_successful_command)


def _confirm() -> bool:
    answer = input("Execute this command? (Y/n): ").strip().lower()
    return answer in {"", "y", "yes"}


def _local_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


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


def _format_command_preview(command: str, colors: ColorConfig) -> str:
    return colorize(f">>> {command}", colors.preview, enabled=colors.enabled)


def _build_main_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xx")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("request", nargs="*")
    return parser


def _build_report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xx report serve")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--debug", action="store_true")
    return parser
