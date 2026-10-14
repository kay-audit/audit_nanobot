# Proposal

## Why

Существующий lifecycle/NFS runtime работает для Appeals, но не предоставляет
реестр, безопасные JSON-операции и native Tool для других потребителей.
Нужно расширить работающий механизм без изменения поисковых алгоритмов.

## What Changes

- Реестр профилей, единый Python API и явный worker dispatcher.
- JSON-протокол и файловые артефакты рядом с доверенным legacy pickle v2.
- Native `osiris` Tool с allowlist и запретом stop без административной сессии.
- Idle timeout 28800 секунд, сериализация публикации и остановки через NFS lock.
- Appeals как первый потребитель; offline-тесты и инструкции закрытого контура.

## Capabilities

### New Capabilities

- `runtime/osiris`: общий lifecycle, registry, transport и worker protocol.
- `tools/osiris`: native Tool с конфигурационной авторизацией и доставкой артефактов.

### Modified Capabilities

Нет. Выбор конфигурационного профиля Nanobot и бизнес-поведение Appeals не меняются.

## Impact

`workspace/utils/osiris_runtime`, `workspace/tools/osiris.py`, адаптер и worker
Appeals, `osiris_job.py`, `project.json`, тесты и документация. OSIRIS SDK и ML
загружаются лениво; локальная проверка не обращается к GPU, данным или SDK.
