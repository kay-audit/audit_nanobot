# Матрица исходных пересечений

Изменения каждой feature-ветки относительно её merge base с origin/vllm_fix. Матрица показывает совместное владение путями, а не факт текстового конфликта.

| Файл | D6 | D5 | D3 | OSIRIS |
|---|:---:|:---:|:---:|:---:|
| `AGENTS.md` | ✓ |  |  | ✓ |
| `appeals_osiris_job.py` |  |  | ✓ | ✓ |
| `config.json` | ✓ | ✓ | ✓ | ✓ |
| `docs/D3.md` |  |  | ✓ | ✓ |
| `docs/OSIRIS_RUNTIME.md` |  |  | ✓ | ✓ |
| `feature.yaml` | ✓ | ✓ | ✓ | ✓ |
| `gateway.py` |  |  | ✓ | ✓ |
| `lib/services/llm_client.py` | ✓ | ✓ | ✓ | ✓ |
| `lib/services/llm_config.py` | ✓ | ✓ | ✓ | ✓ |
| `lib/services/llm_reasoning.py` | ✓ | ✓ | ✓ | ✓ |
| `lib/services/runtime_patcher.py` | ✓ | ✓ | ✓ |  |
| `openspec/changes/appeals-structural-startup-cache/design.md` |  |  | ✓ | ✓ |
| `openspec/changes/appeals-structural-startup-cache/proposal.md` |  |  | ✓ | ✓ |
| `openspec/changes/appeals-structural-startup-cache/specs/appeals-structural-cache/spec.md` |  |  | ✓ | ✓ |
| `openspec/changes/appeals-structural-startup-cache/tasks.md` |  |  | ✓ | ✓ |
| `openspec/specs/data/appeals-structural-cache/spec.md` |  |  | ✓ | ✓ |
| `osiris_job.py` |  |  | ✓ | ✓ |
| `sql/test_d3_schema.sql` |  |  | ✓ | ✓ |
| `tests/test_appeals_hybrid_contracts.py` |  |  | ✓ | ✓ |
| `tests/test_appeals_osiris_lifecycle.py` |  |  | ✓ | ✓ |
| `tests/test_appeals_production_contracts.py` |  |  | ✓ | ✓ |
| `tests/test_appeals_routing.py` |  |  | ✓ | ✓ |
| `tests/test_appeals_standalone_cli.py` |  |  | ✓ | ✓ |
| `tests/test_appeals_structural_cache.py` |  |  | ✓ | ✓ |
| `tests/test_gateway.py` |  |  | ✓ | ✓ |
| `tests/test_prepare_outbound_suppressed.py` | ✓ | ✓ | ✓ |  |
| `tests/test_runtime_patcher.py` | ✓ | ✓ | ✓ |  |
| `tests/test_tools_appeals_analyzer.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/__init__.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/.gitignore` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/appeals_analyze.sh` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/benchmarks/gold_queries.jsonl` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/canonical_filters.json` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/CHANGELOG.md` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/config.json` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/PRODUCTION.md` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/rerank_osiris_worker.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/scripts/__init__.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/scripts/appeals_hypothesis.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/scripts/appeals_profiler.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/scripts/appeals_reports.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/scripts/cli.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/scripts/tool.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/SKILL.md` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/testing/__init__.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/testing/data_generator.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/testing/llm_search.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/testing/README.md` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/testing/report.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/testing/runner.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/testing/unit_checks.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/tests/test_appeals_contract.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/tool.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/__init__.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/appeal_text.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/appeals_artifacts.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/bge_search_engine.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/data_store.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/db.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/greenplum_engine.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/intent_router.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/local_qwen.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/osiris_config.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/pipeline_config.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/session_extract_manager.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/session_file_store.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/skill_config.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/srb_d3.py` |  |  | ✓ | ✓ |
| `workspace/skills/appeals-analyzer/utils/sva_metrics.py` |  |  | ✓ | ✓ |
| `workspace/tools/appeals_analyzer.py` |  |  | ✓ | ✓ |
| `workspace/utils/appeals_structural_cache.py` |  |  | ✓ | ✓ |
| `workspace/utils/osiris_runtime/__init__.py` | ✓ |  | ✓ | ✓ |
| `workspace/utils/osiris_runtime/client.py` | ✓ |  | ✓ | ✓ |
| `workspace/utils/osiris_runtime/config.py` | ✓ |  | ✓ | ✓ |
| `workspace/utils/osiris_runtime/errors.py` | ✓ |  | ✓ | ✓ |
| `workspace/utils/osiris_runtime/lifecycle.py` | ✓ |  | ✓ | ✓ |
| `workspace/utils/osiris_runtime/worker.py` | ✓ |  | ✓ | ✓ |
| `workspace/utils/skill_runtime_mode.py` |  |  | ✓ | ✓ |
