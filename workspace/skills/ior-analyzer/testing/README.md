# IOR external testing runtime

Активируется только `NANOBOT_SKILLS_RUNTIME=testing`. При первом вызове создаёт
1000 полностью синтетических записей в `workspace/data_store/cache/testing/ior`.
Числовые агрегации выполняются Python; LLM используется только для semantic
selection через общий `lib.services.llm_client`.

Force regeneration:

`python workspace/skills/ior-analyzer/testing/data_generator.py --force`
