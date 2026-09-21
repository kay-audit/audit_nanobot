# Appeals external testing runtime

Активируется только `NANOBOT_SKILLS_RUNTIME=testing`. Создаёт 100 коротких
синтетических обращений в `workspace/data_store/cache/testing/appeals`.
Фильтры выполняются Python, relevance — батчами через общий LLM client;
не используются Greenplum, FAISS, BM25, BGE, reranker или GPU.

`python workspace/skills/appeals-analyzer/testing/data_generator.py --force`
