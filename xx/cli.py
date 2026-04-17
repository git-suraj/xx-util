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
from xx.providers import ProviderError, generate_chat_command, generate_command, generate_repaired_command
from xx.reporting import serve_report
from xx.safety import assess_command
from xx.spinner import Spinner
from xx.storage import connect, insert_execution, prune_old_records, update_execution_outcome
from xx.types import ChatTurn, ExecutionRecord


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        chat_parser = _build_chat_parser()
        args = chat_parser.parse_args([])
        return _run_chat_command(args)
    if argv[:1] == ["report"]:
        report_argv = argv[1:]
        if report_argv[:1] == ["serve"]:
            report_argv = report_argv[1:]
        report_parser = _build_report_parser()
        args = report_parser.parse_args(report_argv)
        return _run_report_command(args)
    if argv[:1] == ["chat"]:
        chat_parser = _build_chat_parser()
        args = chat_parser.parse_args(argv[1:])
        return _run_chat_command(args)
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
        print(_format_error(f"Config error: {exc}", ColorConfig()), file=sys.stderr)
        return 2

    machine = discover_machine_context(cache_enabled=config.cache_enabled)
    conn = connect(config.reporting.database_path)
    try:
        prune_old_records(conn, config.reporting.retention_days)
        user_request = " ".join(args.request).strip()

        try:
            proposal = _generate_proposal(config, machine, user_request)
        except ProviderError as exc:
            print(_format_error(f"Provider error: {exc}", config.colors), file=sys.stderr)
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
            execution_type="standalone",
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
            _print_system("cancelled", config.colors)
            return 0

        try:
            result = execute_command(proposal.command, machine.shell, config.colors)
        except ExecutionError as exc:
            update_execution_outcome(conn, record_id, executed=False, approved=True, exit_code=None)
            print(_format_error(f"Execution error: {exc}", config.colors), file=sys.stderr)
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
        print(_format_error(f"Config error: {exc}", ColorConfig()), file=sys.stderr)
        return 2
    return serve_report(config.reporting)


def _run_chat_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(
            config_path=Path(args.config).expanduser() if args.config else None,
            provider_override=args.provider,
            model_override=args.model,
            debug=args.debug,
            no_cache=args.no_cache,
        )
    except ConfigError as exc:
        print(_format_error(f"Config error: {exc}", ColorConfig()), file=sys.stderr)
        return 2

    include_command_output = (
        args.include_output
        if args.include_output is not None
        else config.chat.include_command_output
    )
    machine = discover_machine_context(cache_enabled=config.cache_enabled)
    conn = connect(config.reporting.database_path)
    turns: list[ChatTurn] = []
    turn_index = 1
    _print_chat_header(config.colors)
    if not include_command_output:
        _print_system(
            "output context is off; use /include-output on to opt in",
            config.colors,
        )

    try:
        prune_old_records(conn, config.reporting.retention_days)
        while True:
            try:
                user_message = input(_chat_prompt(turns, config.colors)).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not user_message:
                continue
            command_result = _handle_chat_meta_command(user_message, turns, config.colors)
            if command_result == "exit":
                return 0
            if command_result == "handled":
                continue
            normalized_message = user_message.lower()
            if normalized_message == "/include-output on":
                include_command_output = True
                _print_system("output context enabled for this session", config.colors)
                continue
            if normalized_message == "/include-output off":
                include_command_output = False
                _print_system("output context disabled for this session", config.colors)
                continue

            try:
                with Spinner("Generating command"):
                    proposal = generate_chat_command(
                        config,
                        machine,
                        user_message,
                        turns,
                        include_command_output=include_command_output,
                    )
            except ProviderError as exc:
                print(f"Provider error: {exc}", file=sys.stderr)
                continue

            safety = assess_command(proposal.command, machine)
            if proposal.risk == "high" and safety.level != "high":
                safety.level = "high"
            if config.debug:
                _print_debug(config, machine, proposal, safety)
            execution_group_id = uuid.uuid4().hex

            record = ExecutionRecord(
                invoked_at=_local_timestamp(),
                execution_group_id=execution_group_id,
                attempt_index=turn_index,
                execution_type="chat",
                user_input=user_message,
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
                turns.append(
                    ChatTurn(
                        user_message=user_message,
                        command=proposal.command,
                        approved=False,
                        executed=False,
                        risk_level=safety.level,
                    )
                )
                turn_index += 1
                _print_system("cancelled", config.colors)
                continue

            try:
                result = execute_command(proposal.command, machine.shell, config.colors)
            except ExecutionError as exc:
                update_execution_outcome(conn, record_id, executed=False, approved=True, exit_code=None)
                print(f"Execution error: {exc}", file=sys.stderr)
                turns.append(
                    ChatTurn(
                        user_message=user_message,
                        command=proposal.command,
                        approved=True,
                        executed=False,
                        risk_level=safety.level,
                        stderr=str(exc),
                    )
                )
                turn_index += 1
                continue
            update_execution_outcome(conn, record_id, executed=True, approved=True, exit_code=result.exit_code)
            turns.append(
                ChatTurn(
                    user_message=user_message,
                    command=proposal.command,
                    approved=True,
                    executed=True,
                    risk_level=safety.level,
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            )
            if result.exit_code != 0:
                turn_index = _attempt_chat_repair(
                    conn=conn,
                    config=config,
                    machine=machine,
                    turns=turns,
                    user_message=user_message,
                    previous_command=proposal.command,
                    previous_result=result,
                    execution_group_id=execution_group_id,
                    next_turn_index=turn_index + 1,
                )
            else:
                turn_index += 1
    finally:
        conn.close()


def _run_doctor() -> int:
    try:
        config = load_config(require_provider=False)
    except ConfigError as exc:
        print(_format_error(f"Config error: {exc}", ColorConfig()), file=sys.stderr)
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
    print(f"colors_chat_prompt: {config.colors.chat_prompt}")
    print(f"colors_error: {config.colors.error}")
    print(f"colors_system: {config.colors.system}")
    print(f"semantic_memory_backend: {memory['backend']}")
    print(f"semantic_memory_path: {memory['path']}")
    print(f"semantic_memory_entries: {memory['entries']}")
    print(f"report_url: http://{config.reporting.host}:{config.reporting.port}/report")
    print(f"repair_attempts: {config.repair_attempts}")
    print(f"chat_include_command_output: {config.chat.include_command_output}")
    print(f"chat_max_output_context_chars: {config.chat.max_output_context_chars}")
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


def _chat_prompt(turns: list[ChatTurn], colors: ColorConfig) -> str:
    if turns:
        return "\n" + colorize("ask › ", colors.chat_prompt, enabled=colors.enabled)
    return "\n" + colorize("ask › ", colors.chat_prompt, enabled=colors.enabled)


def _handle_chat_meta_command(user_message: str, turns: list[ChatTurn], colors: ColorConfig) -> str | None:
    normalized = user_message.strip().lower()
    if normalized in {"/exit", "/quit"}:
        return "exit"
    if normalized in {"/", "/help"}:
        _print_chat_commands()
        return "handled"
    if normalized in {"/include-output on", "/include-output off"}:
        return None
    if normalized == "/clear":
        turns.clear()
        _print_system("chat context cleared", colors)
        return "handled"
    if normalized == "/history":
        if not turns:
            print("No commands yet.")
            return "handled"
        for index, turn in enumerate(turns, start=1):
            if turn.executed and turn.exit_code == 0:
                status = "succeeded"
            elif turn.executed:
                status = f"failed ({turn.exit_code})"
            elif turn.approved:
                status = "not started"
            else:
                status = "cancelled"
            print(f"{index:>2}. {turn.command} [{status}]")
        return "handled"
    if normalized.startswith("/"):
        _print_system(f"unknown chat command: {user_message}", colors)
        return "handled"
    return None


def _print_chat_commands() -> None:
    print("Commands:")
    print("  /                 show this command list")
    print("  /history          show commands from this chat session")
    print("  /clear            clear chat context")
    print("  /include-output on")
    print("  /include-output off")
    print("  /exit             quit chat")


def _attempt_chat_repair(
    conn,
    config,
    machine,
    turns: list[ChatTurn],
    user_message: str,
    previous_command: str,
    previous_result,
    *,
    execution_group_id: str,
    next_turn_index: int,
) -> int:
    if config.repair_attempts <= 0:
        return next_turn_index

    remembered_command = lookup_repaired_command(
        config.memory_path,
        user_request=user_message,
    )
    command = previous_command
    result = previous_result
    turn_index = next_turn_index
    for attempt in range(1, config.repair_attempts + 1):
        print(
            _format_error(
                f"Command failed with exit code {result.exit_code}. Attempting amended command {attempt}/{config.repair_attempts}...",
                config.colors,
            ),
            file=sys.stderr,
        )
        try:
            with Spinner("Generating amended command"):
                proposal = generate_repaired_command(
                    config,
                    machine,
                    user_message,
                    command,
                    result.exit_code,
                    result.stdout,
                    result.stderr,
                    remembered_command,
                )
        except ProviderError as exc:
            print(_format_error(f"Repair error: {exc}", config.colors), file=sys.stderr)
            return turn_index

        safety = assess_command(proposal.command, machine)
        if proposal.risk == "high" and safety.level != "high":
            safety.level = "high"
        if config.debug:
            print(f"[debug] repaired chat proposal attempt={attempt}")
            _print_debug(config, machine, proposal, safety)

        record = ExecutionRecord(
            invoked_at=_local_timestamp(),
            execution_group_id=execution_group_id,
            attempt_index=turn_index,
            execution_type="chat",
            user_input=user_message,
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
            turns.append(
                ChatTurn(
                    user_message=user_message,
                    command=proposal.command,
                    approved=False,
                    executed=False,
                    risk_level=safety.level,
                )
            )
            _print_system("amended command cancelled", config.colors)
            return turn_index + 1

        try:
            result = execute_command(proposal.command, machine.shell, config.colors)
        except ExecutionError as exc:
            update_execution_outcome(conn, record_id, executed=False, approved=True, exit_code=None)
            print(_format_error(f"Execution error: {exc}", config.colors), file=sys.stderr)
            turns.append(
                ChatTurn(
                    user_message=user_message,
                    command=proposal.command,
                    approved=True,
                    executed=False,
                    risk_level=safety.level,
                    stderr=str(exc),
                )
            )
            return turn_index + 1

        update_execution_outcome(conn, record_id, executed=True, approved=True, exit_code=result.exit_code)
        turns.append(
            ChatTurn(
                user_message=user_message,
                command=proposal.command,
                approved=True,
                executed=True,
                risk_level=safety.level,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )
        if result.exit_code == 0:
            remember_successful_repair(
                config.memory_path,
                user_request=user_message,
                failed_command=command,
                successful_command=proposal.command,
            )
            print(
                _format_system(
                    f"saved successful repair to semantic memory: {config.memory_path}",
                    config.colors,
                ),
                file=sys.stderr,
            )
            return turn_index + 1
        command = proposal.command
        turn_index += 1

    return turn_index


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
            _format_error(
                f"Command failed with exit code {result.exit_code}. Attempting amended command {attempt}/{config.repair_attempts}...",
                config.colors,
            ),
            file=sys.stderr,
        )
        try:
            with Spinner("Generating amended command"):
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
            print(_format_error(f"Repair error: {exc}", config.colors), file=sys.stderr)
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
            execution_type="standalone",
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
            _print_system("amended command cancelled", config.colors)
            return result.exit_code

        try:
            result = execute_command(proposal.command, machine.shell, config.colors)
        except ExecutionError as exc:
            update_execution_outcome(conn, record_id, executed=False, approved=True, exit_code=None)
            print(_format_error(f"Execution error: {exc}", config.colors), file=sys.stderr)
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
                _format_system(
                    f"saved successful repair to semantic memory: {config.memory_path}",
                    config.colors,
                ),
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
    with Spinner("Generating command"):
        return generate_command(config, machine, user_request, prior_successful_command)


def _confirm() -> bool:
    answer = input("run? [Y/n] ").strip().lower()
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
    return colorize(f"cmd › {command}", colors.preview, enabled=colors.enabled)


def _format_error(message: str, colors: ColorConfig) -> str:
    return colorize(message, colors.error, enabled=colors.enabled)


def _format_system(message: str, colors: ColorConfig) -> str:
    return colorize(message, colors.system, enabled=colors.enabled)


def _print_system(message: str, colors: ColorConfig) -> None:
    print(_format_system(message, colors))


def _print_chat_header(colors: ColorConfig) -> None:
    print(colorize("xx chat", colors.preview, enabled=colors.enabled))
    print(_format_system("/ for commands, /exit to quit", colors))


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


def _build_chat_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xx chat")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--include-output",
        dest="include_output",
        action="store_true",
        help="Send truncated command output to the model as chat context.",
    )
    output_group.add_argument(
        "--no-include-output",
        dest="include_output",
        action="store_false",
        help="Do not send command output to the model as chat context.",
    )
    parser.set_defaults(include_output=None)
    return parser
