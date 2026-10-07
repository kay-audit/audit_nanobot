## Решения

Валидация отделяет внутренний SQL для repair от публичного результата.
Отрицательный результат содержит причины и delivery gate, но не готовый SQL.

Административные entrypoints инициализируют config по --profile prod|test;
workspace.utils.db.resolve_dsn остаётся единственным resolver. Явный --dsn-env
сохраняется только как необязательный compatibility override.

Адаптер подключается к existing ServiceProfile/heartbeat, submit_request и
wait_result. Авто-start/recover запрещён этому потребителю. В existing worker
добавляется embed; rerank использует нормализованные sigmoid scores.

Данные для индекса читаются keyset-страницами по id; подписи до/после сборки
совпадают, количество строк проверяется. GPU inference только в Osiris;
BM25/FAISS публикация остаётся на машине builder.
