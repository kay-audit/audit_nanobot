# Tasks

## 1. Runtime

- [x] 1.1 Реестр, ошибки и API: проверить независимые и общие сервисы offline-тестами, описать API.
- [x] 1.2 JSON/artifact transport и явные handlers: проверить валидацию, корреляцию, timeout и ошибки, описать protocol.
- [x] 1.3 Locks, recovery и idle 28800: проверить concurrency/stop/idle тестами, описать lifecycle.

## 2. Потребители

- [x] 2.1 Appeals adapters/worker и CLI: сохранить алгоритмы, выполнить старые regression-тесты, описать совместимость.
- [x] 2.2 Native Tool: проверить loader, session context, allowlists, stop authorization и файлы; описать конфигурацию.
- [x] 2.3 Пример echo worker: выполнить без GPU/SDK, добавить smoke-инструкции закрытого контура.

## 3. Итоговая проверка

- [x] 3.1 Запустить целевые тесты, lint и OpenSpec validate; записать фактические результаты и ограничения.
- [x] 3.2 Проверить diff и создать раздельные локальные commits в feature/osiris без push.

## Проверка

Целевые offline-тесты: 432 passed, 15 subtests passed. Ruff runtime/Tool/CLI и
OpenSpec validate --strict прошли. Дополнительный structural-cache прогон обнаружил
два прежних сбоя mock Gateway (нет evaluate_cache_snapshot); Gateway, application_context
и этот тестовый файл не менялись. Закрытый smoke описан, фактически не запускался.
Runtime commit d20d491; native Tool commit 96246f3; push не выполняется.
