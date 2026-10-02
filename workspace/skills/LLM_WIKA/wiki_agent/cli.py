"""Командная строка локального агента."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .api import DoctorResult, WikiAgent
from .errors import WikiAgentError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-wiki-agent",
        description=(
            "Безопасный локальный агент для query, lint и двухфазного ingest."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="Корень LLM-Wiki; по умолчанию определяется по AGENTS.md",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser(
        "doctor", help="Проверить структуру и конфигурацию без изменений"
    )
    doctor.add_argument(
        "--ping",
        action="store_true",
        help="Выполнить минимальный реальный вызов настроенного LLM",
    )

    model = commands.add_parser("model", help="Локальная embedding-модель")
    model_actions = model.add_subparsers(dest="model_action", required=True)
    model_install = model_actions.add_parser("install", help="Использовать локальную BGE-M3 или скачать её при отсутствии")
    model_install.add_argument("--revision", default="main", help="Редакция Hugging Face; при установке фиксируется точный commit")

    query = commands.add_parser("query", help="Ответить по Wiki без записи")
    query.add_argument("question", help="Вопрос пользователя")
    query.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать локально выбранные пути, не вызывать LLM",
    )
    query.add_argument(
        "--save-markdown",
        action="store_true",
        help="Сохранить ответ в reports/query/",
    )

    jira = commands.add_parser(
        "jira",
        help="Подготовить Jira/Confluence-карту из JSON в raw/sources",
    )
    jira_actions = jira.add_subparsers(
        dest="jira_action", required=True
    )
    jira_prepare = jira_actions.add_parser(
        "prepare",
        help=(
            "Создать Markdown, summaries, связи и перестроить общий FAISS"
        ),
    )
    jira_prepare.add_argument("key", nargs="?", help="Ключ проекта или задачи; без ключа — все JSON")
    jira_load = jira_actions.add_parser("load", help="Добавить проект или задачу и связанные Confluence в базу поиска")
    jira_load.add_argument("key", help="Например trcore или TRCORE-10047")
    jira_ingest = jira_actions.add_parser(
        "ingest",
        help="Создать один Proposal из Jira JSON через отдельный анализ документов",
    )
    jira_ingest.add_argument("source", help="Путь raw/sources/<файл>.json")
    jira_ingest.add_argument("--request", default="")
    jira_query = jira_actions.add_parser(
        "query",
        help="Ответить по Wiki с Jira/Confluence-связями",
    )
    jira_query.add_argument("question", help="Вопрос пользователя")
    jira_query.add_argument("--dry-run", action="store_true")
    jira_query.add_argument(
        "--save-markdown",
        action="store_true",
        help="Сохранить ответ в reports/query/",
    )

    index = commands.add_parser(
        "index", help="Управлять локальным FAISS-индексом страниц"
    )
    index_actions = index.add_subparsers(
        dest="index_action", required=True
    )
    index_actions.add_parser(
        "build",
        help="Обновить FAISS-индекс с повторным использованием эмбеддингов",
    )
    index_actions.add_parser(
        "status",
        help="Проверить наличие и актуальность FAISS-индекса",
    )
    index_search = index_actions.add_parser(
        "search",
        help="Показать результаты FAISS без вызова GigaChat",
    )
    index_search.add_argument("question", help="Текст поискового запроса")

    lint = commands.add_parser("lint", help="Создать lint-отчёт")
    lint.add_argument(
        "--technical-only",
        action="store_true",
        help="Не вызывать LLM; выполнить только локальные проверки",
    )

    ingest = commands.add_parser(
        "ingest", help="Создать Proposal из источника и остановиться"
    )
    ingest.add_argument(
        "source", help="Путь к оригиналу внутри raw/sources/"
    )

    watch = commands.add_parser(
        "watch",
        help=(
            "Следить за raw/sources и автоматически создавать Proposal "
            "для новых файлов"
        ),
    )
    watch.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Интервал проверки каталога в секундах (по умолчанию 1)",
    )
    watch.add_argument(
        "--settle",
        type=float,
        default=2.0,
        help=(
            "Сколько секунд размер и mtime файла должны быть стабильны "
            "перед ingest (по умолчанию 2)"
        ),
    )
    watch.add_argument(
        "--include-existing",
        action="store_true",
        help="Также обработать источники, существовавшие до запуска watcher",
    )
    ingest.add_argument(
        "--request",
        default="",
        help="Дополнительное ограничение области интеграции",
    )

    apply_command = commands.add_parser(
        "apply",
        help="Применить точный подтверждённый Proposal без вызова LLM",
    )
    apply_command.add_argument("proposal", help="Путь внутри proposals/")
    apply_command.add_argument(
        "--yes",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    apply_command.add_argument(
        "--revision",
        help="16-символьная редакция Proposal, показанная командой",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12) and args.command != "doctor":
        print("Нужен Python 3.12. Запустите команду через python3.12 -m wiki_agent.", file=sys.stderr)
        return 1
    try:
        agent = WikiAgent(args.root or Path.cwd())
        if args.command == "model":
            from .model_install import install_embedding_model
            path = install_embedding_model(agent.settings, revision=args.revision)
            print(f"Embedding-модель установлена: {path}")
            print("Для проверки загрузки выполните python3.12 -m wiki_agent index build.")
            return 0
        if args.command == "doctor":
            result = agent.doctor(ping=args.ping)
            _print_doctor(result)
            return 0
        if args.command == "index":
            if args.index_action == "build":
                result = agent.build_index()
                print(
                    f"{result.message}: {result.document_count} страниц. "
                    f"Векторов из кэша: {result.reused_embeddings}; "
                    f"пересчитано: {result.updated_embeddings}. "
                    f"Файл: {agent.settings.faiss_cache_dir / 'pages.faiss'}"
                )
                return 0
            if args.index_action == "status":
                result = agent.index_status()
                print(
                    f"{result.state}: {result.message}; "
                    f"страниц: {result.document_count}"
                )
                return 0 if result.state == "current" else 2
            hits = agent.search_index(args.question)
            if not hits:
                print("Релевантные страницы не найдены.")
                return 0
            for hit in hits:
                print(f"{hit.score:.4f}  {hit.path}")
            return 0
        if args.command == "query":
            result = agent.query(
                args.question,
                dry_run=args.dry_run,
                save_markdown=args.save_markdown,
            )
            if result.dry_run:
                if result.semantic_hits:
                    print("Стартовые страницы FAISS:")
                    for hit in result.semantic_hits:
                        print(f"- {hit.score:.4f}: {hit.path}")
                    print("")
                print("Итоговый контекст query:")
                for path in result.selected_paths:
                    print(f"- {path}")
            else:
                print(result.answer or "")
                if result.answer_path:
                    print(f"\nОтвет сохранён: {result.answer_path}")
            return 0
        if args.command == "jira":
            if args.jira_action == "ingest":
                result = agent.ingest_jira(args.source, request=args.request)
                print(f"Проанализировано документов: {len(result.analyzed_documents)}")
                print(f"Proposal создан: {result.proposal_path}")
                print("Wiki не изменена. Проверьте Proposal и остановитесь.")
                return 0
            if args.jira_action == "query":
                result = agent.jira.query(
                    args.question,
                    dry_run=args.dry_run,
                    save_markdown=args.save_markdown,
                )
                if result.dry_run:
                    for path in result.selected_paths:
                        print(f"- {path}")
                else:
                    print(result.answer or "")
                    if result.answer_path:
                        print(f"\nОтвет сохранён: {result.answer_path}")
                return 0
            result = agent.prepare_jira_confluence(key=args.key)
            print(
                f"Подготовлено Jira: {result.jira_count}; "
                f"Confluence: {result.confluence_count}; "
                f"новых вызовов LLM: {result.llm_calls}."
            )
            print(f"Карта: {result.manifest_path}")
            if result.warnings:
                print("Предупреждения:")
                for warning in result.warnings:
                    print(f"- {warning}")
            return 0
        if args.command == "lint":
            result = agent.lint(technical_only=args.technical_only)
            print(f"Отчёт: {result.report_path}")
            print(
                "Проблемы: "
                f"error={result.counts['error']}, "
                f"warning={result.counts['warning']}, "
                f"info={result.counts['info']}"
            )
            print(
                "Смысловой LLM-lint: "
                + ("выполнен" if result.semantic_checked else "не выполнялся")
            )
            return 0
        if args.command == "ingest":
            result = agent.ingest(
                args.source,
                request=args.request,
            )
            if result.extracted_path:
                print(
                    "Текстовая копия создана автоматически: "
                    f"{result.extracted_path}"
                )
            print(f"Proposal создан: {result.proposal_path}")
            print("Wiki не изменена. Проверьте Proposal и остановитесь.")
            return 0
        if args.command == "watch":
            return agent.watch(
                interval_seconds=args.interval,
                settle_seconds=args.settle,
                include_existing=args.include_existing,
            )
        if args.command == "apply":
            del args.yes
            preview = agent.inspect_proposal(args.proposal)
            print(f"Proposal: {preview.path}")
            print(f"Редакция: {preview.revision}")
            print("Будут изменены:")
            for action, path in preview.changes:
                print(f"- {action}: {path}")
            print("\nТочный diff из машиночитаемого ChangeSet:")
            print(preview.diff)
            result = agent.apply(
                preview.path,
                expected_revision=args.revision or preview.revision,
            )
            print("Применено:")
            for item in result.changed_files:
                print(f"- {item}")
            print(
                f"Proposal получил status: applied: {result.proposal_path}"
            )
            if result.index_error:
                print(
                    "\nПРЕДУПРЕЖДЕНИЕ: Wiki обновлена и Proposal применён, "
                    "но FAISS-индекс перестроить не удалось.",
                    file=sys.stderr,
                )
                print(
                    f"Точная ошибка: {result.index_error}",
                    file=sys.stderr,
                )
                print(
                    "Повторите отдельно: "
                    "python3.12 -m wiki_agent index build",
                    file=sys.stderr,
                )
            elif result.index_status:
                print(
                    "\nWiki и FAISS-индекс обновлены: "
                    f"{result.index_status.document_count} страниц."
                )
            return 0
        parser.error("Неизвестная команда")
    except WikiAgentError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nОперация отменена пользователем.", file=sys.stderr)
        return 130
    return 1


def _print_doctor(result: DoctorResult) -> None:
    print(f"Корень: {result.root}")
    print(f"Python: {result.python_version}")
    print(
        "Совместимость версии Python: "
        + (
            ("да (по Requires-Python установленных пакетов)" if result.provider == "minimax" else "да")
            if result.agent_python_supported
            else ("нет; нужен Python 3.12 и совместимые зависимости из requirements.txt"
                  if result.provider == "minimax" else "нет; требуется отдельное окружение Python 3.10–3.13")
        )
    )
    print(f"Provider: {result.provider}")
    provider_dependency = (
        "requests (HTTP-клиент)"
        if result.provider in {"gigachat_internal", "minimax"}
        else "langchain-gigachat"
    )
    print(
        f"{provider_dependency}: "
        + ("установлен" if result.sdk_installed else "не установлен")
    )
    print(
        "Credentials: "
        + ("заданы" if result.credentials_configured else "не заданы")
    )
    print(
        "Передача выбранного контекста в LLM: "
        + (
            "разрешена"
            if result.external_context_allowed
            else "запрещена"
        )
    )
    if result.provider == "gigachat_internal":
        print(
            "TLS: для HTTP endpoint неприменим; для HTTPS всегда "
            "проверяется"
        )
    else:
        print(
            "Проверка TLS: "
            + (
                "включена"
                if result.tls_verification_enabled
                else "ОТКЛЮЧЕНА"
            )
        )
    if result.ca_bundle_file:
        print(f"CA bundle: {result.ca_bundle_file}")
    else:
        print("CA bundle: не задан")
    print(
        "Структура: AGENTS/schema/wiki/raw/proposals/reports — присутствует"
    )
    print(
        "FAISS: "
        + ("установлен" if result.faiss_installed else "не установлен")
    )
    print(
        "sentence-transformers: "
        + (
            "установлен"
            if result.embeddings_installed
            else "не установлен"
        )
    )
    print(f"Поиск query: {result.query_search}")
    state = result.index_status
    print(
        f"FAISS-индекс: {state.state} — {state.message} "
        f"(страниц: {state.document_count})"
    )
    if result.ping_response is not None:
        print(f"Ping: {result.ping_response}")


if __name__ == "__main__":
    raise SystemExit(main())
