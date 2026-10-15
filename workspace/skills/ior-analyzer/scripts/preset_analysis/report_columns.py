"""Verified D6 column contracts (repository schema snapshot, not invented counts).

The business projection is explicit. A zero-row schema probe at the existing
store boundary negotiates missing/extra main attributes without reading data.
"""
from __future__ import annotations

MAIN_REPORT_COLUMNS = (
    'incdnt_id',
    'incdnt_sid',
    'incdnt_status_name',
    'incdnt_autoreg_flag',
    'incdnt_detection_person_name',
    'incdnt_source_name',
    'src_type_lvl_1_name',
    'src_type_lvl_2_name',
    'incdnt_type_lvl_1_name',
    'incdnt_type_lvl_2_name',
    'incdnt_detection_dt',
    'incdnt_start_dt',
    'incdnt_entry_dt',
    'incdnt_first_validated_dttm',
    'incdnt_last_validate_dttm',
    'risk_profile_id',
    'risk_profile_name',
    'incdnt_client_type_name',
    'incdnt_mistake_cnt',
    'incdnt_appl_num',
    'incdnt_agr_num',
    'incdnt_agr_sid',
    'incdnt_summary_descr_txt',
    'incdnt_full_descr_txt',
    'org_struct_id',
    'org_struct_lvl_2_name',
    'org_struct_lvl_3_name',
    'org_struct_lvl_4_name',
    'org_struct_lvl_5_name',
    'org_struct_lvl_6_name',
    'org_struct_lvl_7_name',
    'org_struct_lvl_8_name',
    'org_struct_lvl_9_name',
    'org_struct_lvl_10_name',
    'funct_block_id',
    'funct_block_lvl_2_name',
    'funct_block_lvl_3_name',
    'funct_block_lvl_4_name',
    'process_lvl_1_name',
    'process_lvl_2_name',
    'process_lvl_3_name',
    'process_lvl_4_name',
    'clntpth_lvl_4_name',
    'incdnt_security_risk_flag',
    'incdnt_infrmtn_sys_risk_flag',
    'incdnt_behavior_risk_flag',
    'incdnt_model_risk_flag',
    'incdnt_sum',
    'incdnt_drct_dmg_sum',
    'incdnt_drct_dmg_cred_rub_amt',
    'incdnt_drct_dmg_noncred_rub_amt',
    'incdnt_indrct_dmg_sum',
    'incdnt_indrct_dmg_cred_rub_amt',
    'incdnt_indrct_dmg_noncred_rub_amt',
    'incdnt_unrlzd_dmg_sum',
    'incdnt_unrlzd_dmg_cred_rub_amt',
    'incdnt_unrlzd_dmg_noncred_rub_amt',
    'incdnt_thrd_prt_sum',
    'incdnt_thrd_prt_cred_rub_amt',
    'incdnt_thrd_prt_noncred_rub_amt',
    'incdnt_gain_sum',
    'incdnt_gain_cred_rub_amt',
    'incdnt_gain_noncred_rub_amt',
    'recovery_rub_amt_aggr',
)

MAIN_AGGREGATE_COLUMNS = (
    'incdnt_sum',
    'incdnt_drct_dmg_sum',
    'incdnt_drct_dmg_cred_rub_amt',
    'incdnt_drct_dmg_noncred_rub_amt',
    'incdnt_indrct_dmg_sum',
    'incdnt_indrct_dmg_cred_rub_amt',
    'incdnt_indrct_dmg_noncred_rub_amt',
    'incdnt_unrlzd_dmg_sum',
    'incdnt_unrlzd_dmg_cred_rub_amt',
    'incdnt_unrlzd_dmg_noncred_rub_amt',
    'incdnt_thrd_prt_sum',
    'incdnt_thrd_prt_cred_rub_amt',
    'incdnt_thrd_prt_noncred_rub_amt',
    'incdnt_gain_sum',
    'incdnt_gain_cred_rub_amt',
    'incdnt_gain_noncred_rub_amt',
    'recovery_rub_amt_aggr',
)

LEGACY_CREDIT_COLUMNS = (
    'incdnt_id',
    'incdnt_sid',
    'incdnt_entry_dt',
    'incdnt_status_name',
    'org_struct_id',
    'org_struct_lvl_3_name',
    'funct_block_lvl_3_name',
    'risk_profile_id',
    'risk_profile_name',
    'incdnt_sum',
    'recovery_rub_amt_aggr',
    'incdnt_full_descr_txt',
    'incdnt_summary_descr_txt',
    'incdnt_type_lvl_1_name',
    'incdnt_type_lvl_2_name',
    'process_lvl_3_name',
    'process_lvl_4_name',
    'incdnt_autoreg_flag',
    'incdnt_detection_dt',
    'incdnt_start_dt',
    'incdnt_source_name',
    'src_type_lvl_1_name',
    'src_type_lvl_2_name',
    'incdnt_detection_person_name',
)

ATTRIBUTE_PRESETS = {"financial_consequences_ior", "vozmeshenie_ior", "ior_nonfinancial_consequences"}


def ordered_main_columns(available):
    """All actual main fields, canonical order followed by sorted schema additions."""
    actual = set(available)
    required = {'incdnt_id', 'incdnt_sid', 'incdnt_status_name', 'incdnt_entry_dt'}
    if required.difference(actual):
        raise ValueError('D6 main schema missing required fields: ' + ', '.join(sorted(required.difference(actual))))
    return tuple(c for c in MAIN_REPORT_COLUMNS if c in actual) + tuple(sorted(actual.difference(MAIN_REPORT_COLUMNS)))


def main_projection(alias="", columns=None, preset=None):
    columns = MAIN_REPORT_COLUMNS if columns is None else columns
    if preset == "credit_no_way_collect_debt":
        columns = LEGACY_CREDIT_COLUMNS
    elif preset in ATTRIBUTE_PRESETS:
        columns = tuple(c for c in columns if c not in MAIN_AGGREGATE_COLUMNS)
    prefix = alias + "." if alias else ""
    return ", ".join(prefix + '"' + c.replace('"', '""') + '"' for c in columns)


DELETION_ACTION_PREDICATE = """(
    LOWER(stts_chng_action_code) IN
      ('delete', 'todelete', 'approvedelete', 'approvedeletion', 'sendfordeletion', 'confirmdeletion')
    OR UPPER(stts_chng_action_name) = 'УДАЛИТЬ'
)"""
