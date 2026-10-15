# Design

## Context

Существующие config/lifecycle/client/worker уже обслуживают Appeals. SDK
недоступен разработчику. Legacy v2 использует pickle и DataFrame. Native
Tool получает только session context; встроенного подтверждённого admin role нет.

## Goals / Non-Goals

Расширить единый backend и обеспечить shared/dedicated deployment одним
профилем сервиса. Не менять ML, Gateway, Greenplum или протокол старых клиентов.

## Decisions

- Registry не импортирует навыки. Доверенные конфигурационные файлы
  регистрируются composition caller; операции и пути LLM не определяют imports.
- API по имени сервиса вызывает существующий lifecycle. Новый JSON transport
  сосуществует с legacy pickle; dispatcher разрешает pickle только явно.
- Файлы ограничены namespace сессии и request; JSON ограничен размером и глубиной.
  Все envelopes проверяют service/operation/session/consumer/request.
- Публикация, idle decision и административный stop используют одну NFS lock.
  Stop с активными заданиями возвращает busy. Во время recovery устаревший
  heartbeat не заменяет подтверждённый SDK state.
- Конфигурационные allowlists и admin_session_keys служат авторизацией Tool;
  произвольный session_id/role из аргументов не предоставляет stop.
- Timeout 28800 передаётся worker через существующий metadata channel.

## Risks / Trade-offs

Legacy pickle требует доверенного NFS → Tool никогда не отправляет pickle.
NFS atomic rename и scheduler restart=False требуют smoke в контуре.
Scheduler может ограничивать жизнь job независимо от idle timer → документация
не гарантирует восемь часов работы без подтверждения политики платформы.

## Migration Plan

Остановить старый worker, развернуть профиль/runtime/worker совместно,
проверить start/status/invoke, затем долгий idle smoke. Rollback — предыдущий
локальный commit синхронизации. Старые retrieve/rerank wrappers остаются.
