## ADDED Requirements

### Requirement: Startup-only structural projection

Gateway SHALL до допуска production requests загрузить из готовой `s_grnplm_ld_audit_da_project_34.t_db_oarb_appeals_d3` и опубликовать в shared snapshot колонки app_row_id, req_reg_date, prd, s_prd, chnl. Source population 2026 и eligibility SHALL подготавливаться внешним/manual ETL. Gateway SHALL NOT вычислять eligibility, создавать/обновлять source table или читать исходные appeal/dialog texts. Startup SELECT SHALL NOT содержать WHERE, DISTINCT, JOIN, EXISTS или regex. Loader SHALL использовать bounded batches и сохранять duplicate IDs.

#### Scenario: Prebuilt source rows

- **WHEN** external source содержит два structural rows одного app_row_id с разными prd/s_prd
- **THEN** оба rows SHALL попадать в projection без text columns

### Requirement: Explicit population and hydration sources

Production SHALL получать DISTINCT allowed IDs из DuckDB; standalone SHALL сохранять GP structural prefilter. Hydration SHALL выполняться в GP только для hybrid candidates. Base hydration SHALL повторять исходные structural filters/date; dialogs/tasks SHALL ограничиваться candidate IDs.

#### Scenario: Duplicate canonical ID

- **WHEN** ID имеет строки A/X и B/Y, а пользователь задаёт A/X
- **THEN** DuckDB SHALL допустить ID, а GP hydration SHALL возвращать только base rows, соответствующие A/X

### Requirement: Fail closed on structural cache errors

Production SHALL завершаться диагностической ошибкой при отсутствии, неправильной схеме или недоступности structural snapshot. Production SHALL NOT использовать Greenplum structural fallback.

#### Scenario: Broken prebuilt source

- **WHEN** prebuilt GP table отсутствует, не содержит required columns или значения не приводятся к target types
- **THEN** gateway startup SHALL завершиться диагностической ошибкой с source name до публикации/request lifecycle без fallback на original GP tables

#### Scenario: Missing structural table

- **WHEN** shared snapshot не содержит structural table
- **THEN** production lookup SHALL завершиться явной ошибкой до retrieval и GP hydration
