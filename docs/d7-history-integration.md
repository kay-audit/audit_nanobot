# D7: решения по переносу архива

Архив `legal_summarizer (2).zip` дополнен точечными интеграционными исправлениями.
Исходное дерево skill совпадает в vllm_fix и finalize_nanobot: `ff2869ae985d52322ec8c19175908b47bbc53e3d`.

162 идентичных, 69 изменённых, 15 новых, 2 base-only. 758 служебных файлов исключены.
Старый production Python и промпты оставлены без изменений. Новые history-модули используют отдельные checkpoint и session results.

| Файл | Категория | Решение | Причина |
|---|---|---|---|
| `README.md` | identical | retain base | identical |
| `__init__.py` | identical | retain base | identical |
| `prompts/reduce_system.md` | identical | retain base | identical |
| `prompts/summarize_system.md` | identical | retain base | identical |
| `references/architecture.md` | identical | retain base | identical |
| `references/contracts.md` | identical | retain base | identical |
| `tests/cyrillic_literals.py` | identical | retain base | identical |
| `tests/smoke_chunking_diagnostics.py` | identical | retain base | identical |
| `tests/test_application_brief_context.py` | identical | retain base | identical |
| `tests/test_batches_invariant.py` | identical | retain base | identical |
| `tests/test_block_ownership.py` | identical | retain base | identical |
| `tests/test_brief_plan.py` | identical | retain base | identical |
| `tests/test_canonical_cache_path.py` | identical | retain base | identical |
| `tests/test_canonical_determinism.py` | identical | retain base | identical |
| `tests/test_canonical_execution_path.py` | identical | retain base | identical |
| `tests/test_canonical_production_path.py` | identical | retain base | identical |
| `tests/test_canonical_retrieval.py` | identical | retain base | identical |
| `tests/test_chunk_ownership.py` | identical | retain base | identical |
| `tests/test_confirmation_context.py` | identical | retain base | identical |
| `tests/test_context_immutability.py` | identical | retain base | identical |
| `tests/test_corrective_guards.py` | identical | retain base | identical |
| `tests/test_detailed_plan.py` | identical | retain base | identical |
| `tests/test_direct_execution.py` | identical | retain base | identical |
| `tests/test_document_cache_hit_miss.py` | identical | retain base | identical |
| `tests/test_document_chunk_callback.py` | identical | retain base | identical |
| `tests/test_document_identity_invariants.py` | identical | retain base | identical |
| `tests/test_document_id_invariant.py` | identical | retain base | identical |
| `tests/test_document_snapshot.py` | identical | retain base | identical |
| `tests/test_e2e_600_page.py` | identical | retain base | identical |
| `tests/test_estimated_actual.py` | identical | retain base | identical |
| `tests/test_estimate_bounds.py` | identical | retain base | identical |
| `tests/test_estimate_confirmation.py` | identical | retain base | identical |
| `tests/test_estimate_semantics.py` | identical | retain base | identical |
| `tests/test_execution_context_snapshot.py` | identical | retain base | identical |
| `tests/test_execution_contract.py` | identical | retain base | identical |
| `tests/test_execution_plan_matches_selection.py` | identical | retain base | identical |
| `tests/test_execution_policy.py` | identical | retain base | identical |
| `tests/test_final_contract_integration.py` | identical | retain base | identical |
| `tests/test_final_integration_suite.py` | identical | retain base | identical |
| `tests/test_final_invariants.py` | identical | retain base | identical |
| `tests/test_heading_legal_document.py` | identical | retain base | identical |
| `tests/test_heading_nk_long_paragraphs.py` | identical | retain base | identical |
| `tests/test_hierarchical_reducer.py` | identical | retain base | identical |
| `tests/test_idempotency.py` | identical | retain base | identical |
| `tests/test_idempotency_no_reexecution.py` | identical | retain base | identical |
| `tests/test_integration_nk_realistic.py` | identical | retain base | identical |
| `tests/test_legacy_audit.py` | identical | retain base | identical |
| `tests/test_legal_summarizer_no_legacy.py` | identical | retain base | identical |
| `tests/test_manifest_document_id.py` | identical | retain base | identical |
| `tests/test_meaningful_sections.py` | identical | retain base | identical |
| `tests/test_no_dict_structure.py` | identical | retain base | identical |
| `tests/test_operation_identity.py` | identical | retain base | identical |
| `tests/test_order_and_fallback.py` | identical | retain base | identical |
| `tests/test_pipeline_recorder.py` | identical | retain base | identical |
| `tests/test_plan_build_count.py` | identical | retain base | identical |
| `tests/test_plan_immutable.py` | identical | retain base | identical |
| `tests/test_plan_to_actual_invariant.py` | identical | retain base | identical |
| `tests/test_public_api.py` | identical | retain base | identical |
| `tests/test_question_context.py` | identical | retain base | identical |
| `tests/test_question_integration.py` | identical | retain base | identical |
| `tests/test_question_via_document_cache.py` | identical | retain base | identical |
| `tests/test_recovered_invariants.py` | identical | retain base | identical |
| `tests/test_reducer_regression.py` | identical | retain base | identical |
| `tests/test_reduce_input_empty.py` | identical | retain base | identical |
| `tests/test_resume.py` | identical | retain base | identical |
| `tests/test_roundtrip_serialization.py` | identical | retain base | identical |
| `tests/test_run_canonical_path.py` | identical | retain base | identical |
| `tests/test_section_summaries_internal.py` | identical | retain base | identical |
| `tests/test_section_summaries_manifest.py` | identical | retain base | identical |
| `tests/test_section_summaries_persist.py` | identical | retain base | identical |
| `tests/test_single_flight.py` | identical | retain base | identical |
| `tests/test_single_flight_concurrent.py` | identical | retain base | identical |
| `tests/test_single_flight_concurrent_safety.py` | identical | retain base | identical |
| `tests/test_single_pipeline.py` | identical | retain base | identical |
| `tests/test_skill_legal_summarizer.py` | identical | retain base | identical |
| `tests/test_stable_operation_id.py` | identical | retain base | identical |
| `tests/test_structure_adjacent_packing.py` | identical | retain base | identical |
| `tests/test_structure_architecture_guard.py` | identical | retain base | identical |
| `tests/test_structure_aware_retrieval.py` | identical | retain base | identical |
| `tests/test_structure_benchmark.py` | identical | retain base | identical |
| `tests/test_structure_block_lookup.py` | identical | retain base | identical |
| `tests/test_structure_candidate_aggregator.py` | identical | retain base | identical |
| `tests/test_structure_chunker_invariants.py` | identical | retain base | identical |
| `tests/test_structure_chunker_regression.py` | identical | retain base | identical |
| `tests/test_structure_chunking_nk_smoke.py` | identical | retain base | identical |
| `tests/test_structure_context_expansion.py` | identical | retain base | identical |
| `tests/test_structure_correctness_suite.py` | identical | retain base | identical |
| `tests/test_structure_direct_threshold_benchmark.py` | identical | retain base | identical |
| `tests/test_structure_document_analysis.py` | identical | retain base | identical |
| `tests/test_structure_document_chunker.py` | identical | retain base | identical |
| `tests/test_structure_document_loader.py` | identical | retain base | identical |
| `tests/test_structure_execution_plan.py` | identical | retain base | identical |
| `tests/test_structure_followup.py` | identical | retain base | identical |
| `tests/test_structure_full_doc_fallback.py` | identical | retain base | identical |
| `tests/test_structure_hierarchical_reducer.py` | identical | retain base | identical |
| `tests/test_structure_hierarchy.py` | identical | retain base | identical |
| `tests/test_structure_hierarchy_invariants.py` | identical | retain base | identical |
| `tests/test_structure_hierarchy_sibling.py` | identical | retain base | identical |
| `tests/test_structure_identity.py` | identical | retain base | identical |
| `tests/test_structure_importance_score.py` | identical | retain base | identical |
| `tests/test_structure_list_detection.py` | identical | retain base | identical |
| `tests/test_structure_llm_invariant.py` | identical | retain base | identical |
| `tests/test_structure_low_quality_pdf.py` | identical | retain base | identical |
| `tests/test_structure_models.py` | identical | retain base | identical |
| `tests/test_structure_no_empty_reduce.py` | identical | retain base | identical |
| `tests/test_structure_numbering.py` | identical | retain base | identical |
| `tests/test_structure_order.py` | identical | retain base | identical |
| `tests/test_structure_pdf_outline.py` | identical | retain base | identical |
| `tests/test_structure_pipeline.py` | identical | retain base | identical |
| `tests/test_structure_provenance.py` | identical | retain base | identical |
| `tests/test_structure_quality_metrics.py` | identical | retain base | identical |
| `tests/test_structure_query_normalizer.py` | identical | retain base | identical |
| `tests/test_structure_question_from_analysis.py` | identical | retain base | identical |
| `tests/test_structure_reference_qa.py` | identical | retain base | identical |
| `tests/test_structure_repair.py` | identical | retain base | identical |
| `tests/test_structure_retrieval.py` | identical | retain base | identical |
| `tests/test_structure_retrieval_index.py` | identical | retain base | identical |
| `tests/test_structure_retry.py` | identical | retain base | identical |
| `tests/test_structure_safety_merge.py` | identical | retain base | identical |
| `tests/test_structure_semantic_record.py` | identical | retain base | identical |
| `tests/test_structure_single_flight.py` | identical | retain base | identical |
| `tests/test_structure_split_modules.py` | identical | retain base | identical |
| `tests/test_structure_tables_only.py` | identical | retain base | identical |
| `tests/test_structure_title.py` | identical | retain base | identical |
| `tests/test_structure_token_estimator.py` | identical | retain base | identical |
| `tests/test_structure_unified_execution.py` | identical | retain base | identical |
| `tests/test_structure_validation.py` | identical | retain base | identical |
| `tests/test_structure_validation_v4.py` | identical | retain base | identical |
| `tests/test_summarizer_canonical.py` | identical | retain base | identical |
| `scripts/application/brief_compression.py` | identical | retain base | identical |
| `scripts/application/manifest_builder.py` | identical | retain base | identical |
| `scripts/application/operation_id.py` | identical | retain base | identical |
| `scripts/application/__init__.py` | identical | retain base | identical |
| `scripts/cache/__init__.py` | identical | retain base | identical |
| `scripts/chunking/__init__.py` | identical | retain base | identical |
| `scripts/document/block_ownership.py` | identical | retain base | identical |
| `scripts/document/repair.py` | identical | retain base | identical |
| `scripts/document/__init__.py` | identical | retain base | identical |
| `scripts/execution/__init__.py` | identical | retain base | identical |
| `scripts/llm/config.py` | identical | retain base | identical |
| `scripts/llm/sanitize.py` | identical | retain base | identical |
| `scripts/llm/single_flight.py` | identical | retain base | identical |
| `scripts/llm/tokens.py` | identical | retain base | identical |
| `scripts/llm/__init__.py` | identical | retain base | identical |
| `scripts/output/__init__.py` | identical | retain base | identical |
| `scripts/planning/plan.py` | identical | retain base | identical |
| `scripts/planning/__init__.py` | identical | retain base | identical |
| `scripts/retrieval/__init__.py` | identical | retain base | identical |
| `tests/architecture/test_layer_boundaries.py` | identical | retain base | identical |
| `tests/architecture/test_skill_layout.py` | identical | retain base | identical |
| `tests/architecture/__init__.py` | identical | retain base | identical |
| `tests/integration/__init__.py` | identical | retain base | identical |
| `tests/unit/__init__.py` | identical | retain base | identical |
| `tests/unit/cache/__init__.py` | identical | retain base | identical |
| `tests/unit/chunking/__init__.py` | identical | retain base | identical |
| `tests/unit/document/__init__.py` | identical | retain base | identical |
| `tests/unit/domain/__init__.py` | identical | retain base | identical |
| `tests/unit/execution/__init__.py` | identical | retain base | identical |
| `tests/unit/llm/__init__.py` | identical | retain base | identical |
| `tests/unit/output/__init__.py` | identical | retain base | identical |
| `tests/unit/planning/__init__.py` | identical | retain base | identical |
| `tests/unit/retrieval/__init__.py` | identical | retain base | identical |
| `SKILL.md` | modified | Объединить | Сохранён весь исходный текст; добавлен новый history/native контракт. |
| `docs/document_cache_refactoring_inventory.md` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `references/testing.md` | modified | Объединить | Сохранён весь исходный текст; добавлен новый history/native контракт. |
| `scripts/cli.py` | modified | Сохранить базовый | Отклонён откат SETTINGS bootstrap и возврат удалённого register_embedding_config. |
| `scripts/cli_query.py` | modified | Сохранить базовый | Отклонён откат manifest diagnostics и structured domain errors. |
| `tests/test_demo_workflow.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `tests/test_no_data_loss.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `tests/test_structure_chunker_packing.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/brief_context.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/canonical.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/chunk_selection.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/context_builder.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/document_io.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/estimation.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/execution_orchestration.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/inspection.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/pipeline_structure.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/question_context.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/application/service.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/cache/document_cache.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/cache/manifest.py` | modified | Сохранить базовый | Отклонён откат diagnose_manifest и taxonomy; сохранить старый query IPC. |
| `scripts/chunking/chunker.py` | modified | Сохранить базовый | Сохранены исправления исходного table/oversized chunking; для history замена не нужна. |
| `scripts/chunking/chunks.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/chunking/importance_score.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/chunking/order.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/chunking/packing.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/chunking/structural_packing.py` | modified | Сохранить базовый | Сохранено исходное packing поведение, исключён unrelated откат таблиц. |
| `scripts/chunking/_text_helpers.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/analysis.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/block_lookup.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/heading.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/hierarchy.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/identity.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/list_detection.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/loader.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/numbering.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/pdf_outline.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/physical.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/safety_merge.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/section_helpers.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/structure.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/title.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/document/validation.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/execution/config.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/execution/hierarchical.py` | modified | Сохранить базовый | Отклонён возврат пустых section_summaries: ломает follow-up. |
| `scripts/execution/map_reduce.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/execution/pipeline.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/llm/calls.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/llm/client.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/llm/prompts.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/llm/prompts_runtime.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/llm/retry.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/output/presenter.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/planning/strategy.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/candidate_aggregator.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/canonical.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/context_expansion.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/fallback.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/followup.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/index.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/normalizer.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/provenance.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/qa.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/quality.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/query.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/question.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `scripts/retrieval/records.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `tests/architecture/test_document_cache_boundaries.py` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `tests/fixtures/analysis_cases/README.md` | modified | Сохранить базовый | Независимый history не требует замены этого модуля. Исходные summary/question/cache и исправления базовой версии приоритетны; форматирование и перестановки импортов не перенесены. |
| `test_history.sh` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `prompts/history_system.md` | new | Добавить | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `references/doc_history.md` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `scripts/cli_history.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `tests/test_doc_history.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `tests/test_history_pipeline.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `tests/test_history_shell.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `tests/test_history_transport.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `scripts/application/history.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `scripts/application/history_pipeline.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `scripts/cache/history.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `scripts/document/history.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `scripts/llm/history.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `scripts/output/history.py` | new | Добавить с адаптацией | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `tests/manual/input/.gitignore` | new | Добавить | Новый history-сценарий; интеграция безопасности, lifecycle, config и media проверена отдельно. |
| `prompts/section_reduce_system.md` | base_only | retain base | Файл отсутствует в архиве; сохранён без изменений. |
| `tests/test_manifest_diagnose.py` | base_only | retain base | Файл отсутствует в архиве; сохранён без изменений. |

## Проверки интеграции

- Исходная база: origin/vllm_fix `9b0a314dbd3c0423c2d7a010dad0e04beb1c9d49`.
- Ветка D7 не содержит обратного merge finalize или истории других feature-веток.
- Baseline D7: 762 passed, 24 failed, 1 skipped. После переноса и UUID compatibility: 835 passed,
  те же 24 failed, 4 skipped. Новых падений старой реализации не обнаружено.
- Итоговый focused D7/native/config/query/schema: 399 passed, 3 skipped.
- Gateway/PostgresChannel startup/lifecycle/media: 129 passed.
- D1/D3/D5/D6/shared OSIRIS: 336 passed.
- Production project_tool_loader зарегистрировал 22 инструмента без ошибок,
  включая legal_summarizer_history с инъекцией MessageTool.
- Отдельно проверен UUID-префикс настоящего SessionFileStore, bounded retry
  Windows checkpoint replace и освобождение OS lock после убийства процесса.
- Реальные ConsultantPlus fixtures и real-model E2E не выполнялись.
- Сравнение JSONC до/после подтверждает только две новые секции:
  skills.legal_summarizer.history и tools.legal_summarizer_history.
- В finalize отдельно исправлена прежняя несовместимость TableEntry startup
  schema с существующим sync_mode=manual_snapshot. Контракт реестра и D1
  runtime не изменены; schema/D1 проверки: 92 passed.
- Тестовые XML, JSON-доказательства и полный локальный отчёт находятся в
  workspace/data_store/cache/sessions/d7_20261021/ (не публикуются в Git).

## Проверки закрытого контура

Проверить подлинные RequestContext.session_key/sender_id, реальные пары
ConsultantPlus DOCX/ZIP, качество пояснений Qwen/vLLM и фактический бюджет
контекста, resume при рестарте процесса, Linux fcntl lock, доставку двух файлов
через Postgres/frontend. Старые 24 baseline-падения требуют отдельного
разбора legacy тестовых ожиданий; они не отключены.
Глобальные LLM лимиты не менялись, новые OSIRIS/GPU jobs не добавлены.
