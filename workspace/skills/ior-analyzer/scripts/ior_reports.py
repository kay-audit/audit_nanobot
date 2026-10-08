"""
ior_reports.py — Исполнение отчетов по ИОР с поддержкой точных контрактов БЗ (knowledge_base/scripts/*.md),
автоматического вызова JOIN-запросов витрин данных, динамической сборки сложных ad-hoc запросов,
сессионного BGE-M3 FAISS индекса, полного маппинга 67+ колонок (CYRILLIC_RENAME) и выгрузки Excel/CSV.
Полная перенесенная функциональность обработки запросов из ior_assistant/backend/agent.
"""
from __future__ import annotations

import sys
from pathlib import Path


import sys
import logging
import os
import re
import uuid
import asyncio
from pathlib import Path
from collections.abc import Mapping
from typing import Optional, Dict, Any
import pandas as pd

# Изолированный skill должен разрешать собственный пакет ``utils`` раньше
# старого ``workspace/utils`` всего nanobot.
_EARLY_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_EARLY_SKILL_ROOT) in sys.path:
    sys.path.remove(str(_EARLY_SKILL_ROOT))
sys.path.insert(0, str(_EARLY_SKILL_ROOT))

from utils.data_store import (
    DUCKDB_TABLES,
    GREENPLUM_TABLES,
    HIVE_TABLES,
    get_data_store,
)
from utils.session_extract_manager import get_session_extract, set_session_extract
from utils.bge_search_engine import build_and_cache_small_index, search_small_index
from utils.dataframe_ops import aggregate_by_incident_id, prepare_df_for_excel
from utils.ior_artifacts import output_directory, register_artifact
from utils.excel_literal import force_literal_excel_cells
try:
    from utils.local_qwen import answer_detail_with_qwen, classify_intent_with_qwen, answer_follow_up_with_qwen
except ImportError as _qwen_import_error:  # import-safe unit-test runtime
    _QWEN_IMPORT_ERROR = str(_qwen_import_error)
    def _qwen_unavailable(*args, **kwargs):
        raise RuntimeError(f"Локальный Qwen недоступен: {_QWEN_IMPORT_ERROR}")
    answer_detail_with_qwen = _qwen_unavailable
    classify_intent_with_qwen = _qwen_unavailable
    answer_follow_up_with_qwen = _qwen_unavailable
from utils.resolve.period_parser import parse_period
from utils.resolve.grounding import apply_smart_filter, ground_query, diagnose_empty
from ior_hypothesis import generate_hypothesis_narrative
from preset_analysis.registry import get_analyzer
from preset_analysis.common import deduplicate_detail_entities, find_column, format_amount

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

_WORKSPACE = Path(__file__).resolve().parents[3]
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import sys
from pathlib import Path


import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)
if str(_SKILL_DIR) in sys.path:
    sys.path.remove(str(_SKILL_DIR))
sys.path.insert(0, str(_SKILL_DIR))

logger = logging.getLogger(__name__)

CYRILLIC_RENAME = {
    'incdnt_id': 'Идентификационный ключ инцидента операционного риска',
    'incdnt_sid': 'Идентификатор события',
    'incdnt_status_name': 'Статус события',
    'incdnt_autoreg_flag': 'Признак авторегистрации инцидента',
    'incdnt_detection_person_name': 'Кем выявлено событие',
    'incdnt_source_name': 'Название источника',
    'src_type_lvl_1_name': 'Тип источника инцидента (уровень 1)',
    'src_type_lvl_2_name': 'Тип источника инцидента (уровень 2)',
    'incdnt_type_lvl_1_name': 'Тип события – уровень 1',
    'incdnt_type_lvl_2_name': 'Тип события – уровень 2',
    'incdnt_detection_dt': 'Дата обнаружения (Событие)',
    'incdnt_start_dt': 'Дата начала инцидента операционного риска',
    'incdnt_entry_dt': 'Дата ввода (Событие)',
    'incdnt_first_validated_dttm': 'Дата первого подтверждения',
    'incdnt_last_validate_dttm': 'Дата последнего подтверждения',
    'risk_profile_id': 'Идентификатор профиля риска',
    'risk_profile_name': 'Наименование профиля риска',
    'incdnt_client_type_name': 'Тип клиента',
    'incdnt_mistake_cnt': 'Количество ошибок',
    'incdnt_appl_num': 'Номер заявки',
    'incdnt_agr_num': 'Номер договора',
    'incdnt_agr_sid': 'Идентификатор договора',
    'incdnt_summary_descr_txt': 'Предварительное описание',
    'incdnt_full_descr_txt': 'Подробное описание',
    'org_struct_id': 'Идентификатор оргструктуры',
    'org_struct_lvl_2_name': 'Орг. структура – уровень 2 (Терр. структура / Департамент ДЗО)',
    'org_struct_lvl_3_name': 'Орг. структура – уровень 3 (Блок / ТБ / ПЦП)',
    'org_struct_lvl_5_name': 'Орг. структура – уровень 5',
    'org_struct_lvl_6_name': 'Орг. структура – уровень 6',
    'org_struct_lvl_7_name': 'Орг. структура – уровень 7',
    'org_struct_lvl_8_name': 'Орг. структура – уровень 8',
    'org_struct_lvl_9_name': 'Орг. структура – уровень 9',
    'org_struct_lvl_10_name': 'Орг. структура – уровень 10',
    'funct_block_id': 'Идентификатор функционального блока',
    'funct_block_lvl_2_name': 'Функциональный блок – уровень 2',
    'funct_block_lvl_3_name': 'Функциональный блок – уровень 3',
    'process_lvl_1_name': 'Процесс – уровень 1',
    'process_lvl_2_name': 'Процесс – уровень 2',
    'process_lvl_3_name': 'Процесс – уровень 3',
    'process_lvl_4_name': 'Процесс – уровень 4 (Наименование процесса)',
    'clntpth_lvl_4_name': 'Клиентский путь – уровень 4',
    'incdnt_security_risk_flag': 'Связь с ИБ-риском',
    'incdnt_infrmtn_sys_risk_flag': 'Связь с риском информационных систем',
    'incdnt_behavior_risk_flag': 'Связь с поведенческим риском',
    'incdnt_model_risk_flag': 'Связь с модельным риском',
    'incdnt_sum': 'Общая сумма всех последствий (руб.)',
    'incdnt_drct_dmg_sum': 'Прямая потеря – итого (руб.)',
    'incdnt_drct_dmg_cred_rub_amt': 'Прямая потеря – с кредитным риском (руб.)',
    'incdnt_drct_dmg_noncred_rub_amt': 'Прямая потеря – без кредитного риска (руб.)',
    'incdnt_indrct_dmg_sum': 'Косвенная потеря – итого (руб.)',
    'incdnt_indrct_dmg_cred_rub_amt': 'Косвенная потеря – с кредитным риском (руб.)',
    'incdnt_indrct_dmg_noncred_rub_amt': 'Косвенная потеря – без кредитного риска (руб.)',
    'incdnt_unrlzd_dmg_sum': 'Нереализовавшаяся потеря – итого (руб.)',
    'incdnt_unrlzd_dmg_cred_rub_amt': 'Нереализовавшаяся потеря – с кредитным риском (руб.)',
    'incdnt_unrlzd_dmg_noncred_rub_amt': 'Нереализовавшаяся потеря – без кредитного риска (руб.)',
    'incdnt_thrd_prt_sum': 'Потеря третьих лиц – итого (руб.)',
    'incdnt_thrd_prt_cred_rub_amt': 'Потеря третьих лиц – с кредитным риском (руб.)',
    'incdnt_thrd_prt_noncred_rub_amt': 'Потеря третьих лиц – без кредитного риска (руб.)',
    'incdnt_gain_sum': 'Прибыль – итого (руб.)',
    'incdnt_gain_cred_rub_amt': 'Прибыль – с кредитным риском (руб.)',
    'incdnt_gain_noncred_rub_amt': 'Прибыль – без кредитного риска (руб.)',
    'recovery_rub_amt_aggr': 'Возмещение – итого по инциденту (руб.)',
    # --- Детализация фин. последствий ---
    'fin_impact_id': 'Идентификатор финансового последствия',
    'fin_impact_sid': 'SID финансового последствия',
    'fin_impact_type_name': 'Тип финансового последствия',
    'fin_impact_kind_name': 'Вид финансового последствия',
    'fin_impact_monitoring_flag': 'Признак мониторинга',
    'fin_impact_crncy_code': 'Код валюты последствия',
    'fin_impact_local_crncy_code': 'Код локальной валюты',
    'fin_impact_detection_dt': 'Дата обнаружения последствия',
    'fin_impact_creation_dttm': 'Дата создания последствия',
    'fin_impact_reg_dt': 'Дата регистрации последствия',
    'fin_impact_account_num': 'Номер счета последствия',
    'fin_impact_docum_num': 'Номер документа последствия',
    'fin_impact_ccy_amt': 'Сумма в валюте последствия',
    'fin_impact_local_ccy_amt': 'Сумма в локальной валюте',
    'fin_impact_rub_amt': 'Сумма в рублях (последствие)',
    # --- Изменение статусов (Удаление) ---
    'incdnt_status_code': 'Код статуса события',
    'stts_chng_action_code': 'Код действия изменения статуса',
    'stts_chng_action_name': 'Действие изменения статуса',
    'stts_chng_comment_txt': 'Комментарий при удалении',
    'stts_chng_action_dttm': 'Дата и время действия',
    'stts_chng_user_num': 'Табельный номер сотрудника',
    'incdnt_status_name_at_action': 'Статус на момент действия',
    # --- Детализация возмещений ---
    'recovery_sid': 'SID возмещения',
    'recovery_type_name': 'Вид возмещения',
    'recovery_crncy_code': 'Код валюты возмещения',
    'recovery_local_crncy_code': 'Код локальной валюты возмещения',
    'recovery_src_account_num': 'Номер счета источника возмещения',
    'recovery_doc_num': 'Номер документа возмещения',
    'recovery_creation_dttm': 'Дата создания возмещения',
    'recovery_reg_dt': 'Дата регистрации возмещения',
    'recovery_ccy_amt': 'Сумма возмещения в валюте',
    'recovery_local_ccy_amt': 'Сумма возмещения в локальной валюте',
    'recovery_rub_amt': 'Сумма возмещения в рублях',
    # --- Детализация нефинансовых последствий ---
    'nonfin_impact_sid': 'SID нефинансового последствия',
    'nonfin_impact_kind_name': 'Вид нефинансового последствия',
    'nonfin_impact_influence_class_name': 'Класс влияния нефинансового последствия',
    'consequence_type_name': 'Тип нефинансового последствия',
    'consequence_descr_txt': 'Описание последствия',
    # --- Кредитная задолженность ---
    'credit_agr_num': 'Номер кредитного договора',
    'credit_debt_rub_amt': 'Задолженность по кредиту (руб.)'
}

VOZMESHENIE_RENAME = {
    'incdnt_id':                        'Идентификационный ключ инцидента операционного риска',
    'incdnt_sid':                       'Идентификатор события',
    'incdnt_status_name':               'Статус события',
    'incdnt_autoreg_flag':              'Признак авторегистрации инцидента',
    'incdnt_detection_person_name':     'Кем выявлено событие',
    'incdnt_source_name':               'Название источника',
    'src_type_lvl_1_name':              'Тип источника инцидента (уровень 1)',
    'src_type_lvl_2_name':              'Тип источника инцидента (уровень 2)',
    'incdnt_type_lvl_1_name':           'Тип события — уровень 1',
    'incdnt_type_lvl_2_name':           'Тип события — уровень 2',
    'incdnt_detection_dt':              'Дата обнаружения (Событие)',
    'incdnt_start_dt':                  'Дата начала инцидента операционного риска',
    'incdnt_entry_dt':                  'Дата ввода (Событие)',
    'incdnt_first_validated_dttm':      'Первая дата утверждения инцидента',
    'incdnt_last_validate_dttm':        'Последняя дата утверждения инцидента',
    'risk_profile_id':                  'Ключ Цифрового Профиля Риска',
    'risk_profile_name':                'Название цифрового профиля риска',
    'incdnt_client_type_name':          'Наименование типа клиента',
    'incdnt_mistake_cnt':               'Количество ошибок',
    'incdnt_appl_num':                  'Номер заявки (сделки) по инциденту',
    'incdnt_agr_num':                   'Номер кредитного договора по инциденту',
    'incdnt_agr_sid':                   'Идентификатор кредитного договора по инциденту',
    'incdnt_summary_descr_txt':         'Предварительное описание',
    'incdnt_full_descr_txt':            'Подробное описание',
    'org_struct_id':                    'Идентификационный ключ организации структуры ИОР',
    'org_struct_lvl_2_name':            'Орг. структура — уровень 2 (Терр. структура / Департамент ДЗО)',
    'org_struct_lvl_3_name':            'Орг. структура — уровень 3 (Блок / ТБ / ПЦП)',
    'org_struct_lvl_5_name':            'Орг. структура — уровень 5',
    'org_struct_lvl_6_name':            'Управление / Отдел / Группа',
    'org_struct_lvl_7_name':            'УРМ / Группа / Управление ГОСБ / ВСП',
    'org_struct_lvl_8_name':            'Отдел ГОСБ / Сектор ГОСБ / Центр ГОСБ / ВСП',
    'org_struct_lvl_9_name':            'Отдел ГОСБ / ВСП',
    'org_struct_lvl_10_name':           'Группа ГОСБ и прочие подструктуры',
    'funct_block_id':                   'Идентификационный ключ функционального блока',
    'funct_block_lvl_2_name':           'Функк. блок — уровень 2 (Дивизион / трайб)',
    'funct_block_lvl_3_name':           'Функк. блок — уровень 3 (Дивизион / Департамент / Центр)',
    'process_lvl_1_name':               'Процесс — уровень 1 (Банк / ДЗО)',
    'process_lvl_2_name':               'Процесс — уровень 2 (Функ. блок)',
    'process_lvl_3_name':               'Процесс — уровень 3 (Дивизион / трайб)',
    'process_lvl_4_name':               'Процесс — уровень 4 (Наименование процесса)',
    'clntpth_lvl_4_name':               'Клиентский путь — уровень 4',
    'incdnt_security_risk_flag':        'Связь с ИБ-риском',
    'incdnt_infrmtn_sys_risk_flag':     'Связь с риском информационных систем',
    'incdnt_behavior_risk_flag':        'Связь с поведенческим риском',
    'incdnt_model_risk_flag':           'Связь с модельным риском',
    'recovery_sid':                     'Идентификатор возмещения',
    'recovery_type_name':               'Тип возмещения',
    'recovery_rub_amt':                 'Сумма возмещения (руб.)',
    'recovery_ccy_amt':                 'Сумма возмещения (в валюте)',
    'recovery_local_ccy_amt':           'Сумма возмещения (в локальной валюте)',
    'recovery_crncy_code':              'Код валюты возмещения',
    'recovery_local_crncy_code':        'Код локальной валюты возмещения',
    'recovery_src_account_num':         'Номер счёта — источник перевода',
    'recovery_doc_num':                 'Номер бухгалтерского документа',
    'recovery_creation_dttm':           'Дата создания возмещения',
    'recovery_reg_dt':                  'Дата регистрации в учёте',
}

FINANCIAL_RENAME = {
    'incdnt_id':                        'Идентификационный ключ инцидента операционного риска',
    'incdnt_sid':                       'Идентификатор события',
    'incdnt_status_name':               'Статус события',
    'incdnt_autoreg_flag':              'Признак авторегистрации инцидента',
    'incdnt_detection_person_name':     'Кем выявлено событие',
    'incdnt_source_name':               'Название источника',
    'src_type_lvl_1_name':              'Тип источника инцидента (уровень 1)',
    'src_type_lvl_2_name':              'Тип источника инцидента (уровень 2)',
    'incdnt_type_lvl_1_name':           'Тип события - уровень 1',
    'incdnt_type_lvl_2_name':           'Тип события - уровень 2',
    'incdnt_detection_dt':              'Дата обнаружения (Событие)',
    'incdnt_start_dt':                  'Дата начала инцидента операционного риска',
    'incdnt_entry_dt':                  'Дата ввода (Событие)',
    'incdnt_first_validated_dttm':      'Первая дата утверждения инцидента',
    'incdnt_last_validate_dttm':        'Последняя дата утверждения инцидента',
    'risk_profile_id':                  'Ключ цифрового Профиля Риска',
    'risk_profile_name':                'Название цифрового профиля риска',
    'incdnt_client_type_name':          'Наименование типа клиента',
    'incdnt_mistake_cnt':               'Количество ошибок',
    'incdnt_appl_num':                  'Номер заявки (сделки) по инциденту',
    'incdnt_agr_num':                   'Номер кредитного договора по инциденту',
    'incdnt_agr_sid':                   'Идентификатор кредитного договора по инциденту',
    'incdnt_summary_descr_txt':         'Предварительное описание',
    'incdnt_full_descr_txt':            'Подробное описание',
    'org_struct_id':                    'Идентификационный ключ организационной структуры ИОР',
    'org_struct_lvl_2_name':            'Орг. структура - уровень 2 (Терр. структура / Департамент ДЗО)',
    'org_struct_lvl_3_name':            'Орг. структура - уровень 3 (Блок / ТБ / ПЦП)',
    'org_struct_lvl_5_name':            'Орг. структура - уровень 5',
    'org_struct_lvl_6_name':            'Управление / Отдел / Группа',
    'org_struct_lvl_7_name':            'УРМ / Группа / Управление ГОСБ / ВСП',
    'org_struct_lvl_8_name':            'Отдел ГОСБ / Сектор ГОСБ / Центр ГОСБ / ВСП',
    'org_struct_lvl_9_name':            'Отдел ГОСБ / ВСП',
    'org_struct_lvl_10_name':           'Группа ГОСБ и прочие подструктуры',
    'funct_block_id':                   'Идентификационный ключ функционального блока',
    'funct_block_lvl_2_name':           'Функ. блок - уровень 2 (Дивизион / Трайб)',
    'funct_block_lvl_3_name':           'Функ. блок - уровень 3 (Дивизион / Департамент / Центр)',
    'process_lvl_1_name':               'Процесс - уровень 1 (Банк / ДЗО)',
    'process_lvl_2_name':               'Процесс - уровень 2 (Функ. блок)',
    'process_lvl_3_name':               'Процесс - уровень 3 (Дивизион / Трайб)',
    'process_lvl_4_name':               'Процесс - уровень 4 (Наименование процесса)',
    'clntpth_lvl_4_name':               'Клиентский путь - уровень 4',
    'incdnt_security_risk_flag':        'Связь с ИБ-риском',
    'incdnt_infrmtn_sys_risk_flag':     'Связь с риском информационных систем',
    'incdnt_behavior_risk_flag':        'Связь с поведенческим риском',
    'incdnt_model_risk_flag':           'Связь с модельным риском',
    'fin_impact_id':                    'Идентификатор фин. последствия (ключ)',
    'fin_impact_sid':                   'Идентификатор фин. последствия (бизнес)',
    'fin_impact_type_name':             'Тип финансового последствия',
    'fin_impact_kind_name':             'Вид финансового последствия',
    'fin_impact_rub_amt':               'Сумма последствия (руб.)',
    'fin_impact_ccy_amt':               'Сумма последствия (в валюте)',
    'fin_impact_local_ccy_amt':         'Сумма последствия (в локальной валюте)',
    'fin_impact_crncy_code':            'Код валюты последствия',
    'fin_impact_local_crncy_code':      'Код локальной валюты последствия',
    'fin_impact_monitoring_flag':       'Требует мониторинга (Последствие)',
    'fin_impact_detection_dt':          'Дата обнаружения (Последствие)',
    'fin_impact_creation_dttm':         'Дата создания (Последствие)',
    'fin_impact_reg_dt':                'Дата регистрации в учёте',
    'fin_impact_account_num':           'Аналитический счёт отражения в учёте',
    'fin_impact_docum_num':             'Номер бухгалтерского документа',
    'fi_busn_area_id':                  'Идентификатор бизнес-области финансового последствия',
    'fi_org_struct_id':                 'Идентификатор оргструктуры финансового последствия',
}


def apply_preset_column_filter(df: pd.DataFrame, rename_dict: dict) -> pd.DataFrame:
    """
    Оставляем ТОЛЬКО те столбцы, которые описаны в rename_dict,
    в их строго заданном порядке, и переименовываем в кириллицу.
    Лишние столбцы принудительно отбрасываются.
    """
    if df is None or len(df.columns) == 0:
        return df
    
    cols_map = {}
    for c in df.columns:
        c_str = str(c).strip()
        cols_map[c_str.lower()] = c
    
    ordered_cols = []
    cyr_rename = {}
    
    for tech_col, cyr_name in rename_dict.items():
        t_low = tech_col.strip().lower()
        cyr_low = cyr_name.strip().lower()
        
        actual_col = None
        if t_low in cols_map:
            actual_col = cols_map[t_low]
        elif cyr_low in cols_map:
            actual_col = cols_map[cyr_low]
            
        if actual_col and actual_col not in ordered_cols:
            ordered_cols.append(actual_col)
            cyr_rename[actual_col] = cyr_name
            
    if ordered_cols:
        out = df[ordered_cols].copy()
        out = out.rename(columns=cyr_rename)
        return out
    return df

MAIN_REPORT_COLUMNS = ('incdnt_id', 'incdnt_sid', 'incdnt_entry_dt', 'incdnt_status_name', 'org_struct_id', 'org_struct_lvl_3_name', 'funct_block_lvl_3_name', 'risk_profile_id', 'risk_profile_name', 'incdnt_sum', 'recovery_rub_amt_aggr', 'incdnt_full_descr_txt', 'incdnt_summary_descr_txt', 'incdnt_type_lvl_1_name', 'incdnt_type_lvl_2_name', 'process_lvl_3_name', 'process_lvl_4_name', 'incdnt_autoreg_flag', 'incdnt_detection_dt', 'incdnt_start_dt', 'incdnt_source_name', 'src_type_lvl_1_name', 'src_type_lvl_2_name', 'incdnt_detection_person_name')

def main_projection(alias=""):
    return ", ".join((alias + "." if alias else "") + c for c in MAIN_REPORT_COLUMNS)

# Точные JOIN-запросы пресетов согласно спецификациям ior_assistant.
# Физические имена поступают только из backend-specific table registry.
def build_preset_sql_queries(tables: Mapping[str, str]) -> dict[str, str]:
    required = {"ior", "status", "recovery", "financial_impact", "nonfinancial_impact"}
    missing = sorted(required.difference(tables))
    if missing:
        raise ValueError(f"Table registry is missing required entries: {', '.join(missing)}")

    queries = {
    "financial_consequences_ior": f"""
        SELECT {main_projection("ior")},
               fi.fin_impact_id, fi.fin_impact_sid, fi.fin_impact_type_name, fi.fin_impact_kind_name, fi.fin_impact_monitoring_flag, 
               fi.fin_impact_crncy_code, fi.fin_impact_local_crncy_code, fi.fin_impact_detection_dt, 
               fi.fin_impact_creation_dttm, fi.fin_impact_reg_dt, fi.fin_impact_account_num, 
               fi.fin_impact_docum_num, fi.fi_busn_area_id, fi.fi_org_struct_id,
               fi.fin_impact_ccy_amt, fi.fin_impact_local_ccy_amt, fi.fin_impact_rub_amt
        FROM {tables['ior']} AS ior
        INNER JOIN {tables['financial_impact']} AS fi ON ior.incdnt_id = fi.incdnt_id
        ORDER BY ior.incdnt_entry_dt DESC
    """,
    "deleted_ior": f"""
        SELECT {main_projection("ior")},
               st.incdnt_status_name_at_action, st.incdnt_status_code, st.stts_chng_action_code, 
               st.stts_chng_action_name, st.stts_chng_comment_txt, st.stts_chng_action_dttm, st.stts_chng_user_num
        FROM {tables['ior']} AS ior
        LEFT JOIN (
            SELECT incdnt_id AS st_incdnt_id, incdnt_status_name AS incdnt_status_name_at_action, 
                   incdnt_status_code, stts_chng_action_code, stts_chng_action_name, 
                   stts_chng_comment_txt, stts_chng_action_dttm, stts_chng_user_num
            FROM {tables['status']}
            WHERE UPPER(stts_chng_action_name) = 'УДАЛИТЬ'
        ) AS st ON ior.incdnt_id = st.st_incdnt_id
        WHERE UPPER(ior.incdnt_status_name) = 'УДАЛЁН'
        ORDER BY ior.incdnt_entry_dt DESC
    """,
    "vozmeshenie_ior": f"""
        SELECT {main_projection("ior")},
               r.recovery_sid, r.recovery_type_name, r.recovery_crncy_code, r.recovery_local_crncy_code, 
               r.recovery_src_account_num, r.recovery_doc_num, r.recovery_creation_dttm, r.recovery_reg_dt, 
               r.recovery_ccy_amt, r.recovery_local_ccy_amt, r.recovery_rub_amt
        FROM {tables['ior']} AS ior
        INNER JOIN {tables['recovery']} AS r ON ior.incdnt_id = r.incdnt_id
        ORDER BY ior.incdnt_entry_dt DESC
    """,
    "ior_nonfinancial_consequences": f"""
        SELECT {main_projection("ior")},
               nfi.nonfin_impact_sid, nfi.nonfin_impact_kind_name, nfi.nonfin_impact_influence_class_name
        FROM {tables['ior']} AS ior
        INNER JOIN {tables['nonfinancial_impact']} AS nfi ON ior.incdnt_id = nfi.incdnt_id
        ORDER BY ior.incdnt_entry_dt DESC
    """,
    "ior_period_pao_sberbank": f"""
        SELECT {main_projection()} FROM {tables['ior']}
        WHERE SUBSTR(UPPER(org_struct_id), 1, 4) IN ('SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_')
        ORDER BY incdnt_entry_dt DESC
    """,
    "report_period_specific_ior": f"""
        SELECT {main_projection("ior")},
               fi.fin_impact_id, fi.fin_impact_sid, fi.fin_impact_type_name, fi.fin_impact_kind_name, fi.fin_impact_monitoring_flag, 
               fi.fin_impact_crncy_code, fi.fin_impact_local_crncy_code, fi.fin_impact_detection_dt, 
               fi.fin_impact_creation_dttm, fi.fin_impact_reg_dt, fi.fin_impact_account_num, 
               fi.fin_impact_docum_num, fi.fi_busn_area_id, fi.fi_org_struct_id,
               fi.fin_impact_ccy_amt, fi.fin_impact_local_ccy_amt, fi.fin_impact_rub_amt,
               r.recovery_sid, r.recovery_type_name, r.recovery_crncy_code, r.recovery_local_crncy_code, 
               r.recovery_src_account_num, r.recovery_doc_num, r.recovery_creation_dttm, r.recovery_reg_dt, 
               r.recovery_ccy_amt, r.recovery_local_ccy_amt, r.recovery_rub_amt
        FROM {tables['ior']} AS ior
        LEFT JOIN {tables['financial_impact']} AS fi ON ior.incdnt_id = fi.incdnt_id
        LEFT JOIN {tables['recovery']} AS r ON ior.incdnt_id = r.incdnt_id
        ORDER BY ior.incdnt_entry_dt DESC
    """,
    "ior_hypothesis": f"""
        SELECT {main_projection()} FROM {tables['ior']}
        ORDER BY incdnt_entry_dt DESC
    """
    }
    # The credit source has not been migrated to GP. Keep the legacy preset
    # only for registries which explicitly provide that physical table.
    if tables.get("credits"):
        queries["credit_no_way_collect_debt"] = f"""
            SELECT {main_projection("ior")}, c.credit_agr_num, c.credit_debt_rub_amt
            FROM {tables['ior']} AS ior
            LEFT JOIN {tables['credits']} AS c ON ior.incdnt_id = c.incdnt_id
            ORDER BY ior.incdnt_entry_dt DESC
        """
    return queries


IOR_FULL_SQL_QUERIES = build_preset_sql_queries(GREENPLUM_TABLES)
# Backward-compatible technical access to the inactive legacy preset.
IOR_FULL_SQL_QUERIES["credit_no_way_collect_debt"] = build_preset_sql_queries(
    HIVE_TABLES
)["credit_no_way_collect_debt"]

def has_specific_codes(user_prompt: str) -> bool:
    """
    Проверяет наличие точных кодов профилей риска, блоков, процессов или событий.
    При их наличии пресет перехватывается в пользу прямого таргетированного запроса СУБД.
    """
    if not user_prompt:
        return False
    patterns = [
        r'DRP-\d+',
        r'П-?\d{4,}',
        r'EVE-\d+'
    ]
    return any(re.search(pat, user_prompt, re.IGNORECASE) for pat in patterns)


from utils.resolve.request_plan import build_plan, resolve_preset, explicit_followup
from utils.resolve.request_outcome import ClarificationRequired

def detect_preset_from_prompt(user_prompt):
    return resolve_preset(None, user_prompt) if user_prompt else None

def resolve_preset_for_request(preset_name, user_prompt):
    return resolve_preset(preset_name, user_prompt)


def build_dynamic_sql_from_prompt(
    user_prompt: str,
    table_name: Optional[str] = None,
    preset_name: Optional[str] = None,
    tables: Optional[Mapping[str, str]] = None,
) -> str:
    """
    Динамическая сборка сложных нетиповых SQL-запросов для произвольных вопросов аудитора
    с интеграцией заземления (grounding) и детерминированного разбора периодов (period_parser).
    """
    registry = dict(tables or GREENPLUM_TABLES)
    if table_name is not None:
        # Backward-compatible main-table override. New callers pass ``tables``.
        registry["ior"] = table_name
    main_table = registry["ior"]
    is_local_backend = registry.get("ior") == DUCKDB_TABLES["ior"]

    plan = build_plan(preset_name, user_prompt)
    preset = plan.preset
    where_clauses = plan.predicates()
    financial_thresholds = plan.money if preset == "financial_consequences_ior" else []
    where_str = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    if preset == "deleted_ior":
        stts_table = registry["status"]
        return f"""
            SELECT {main_projection("ior")},
                   st.incdnt_status_name_at_action, st.incdnt_status_code, st.stts_chng_action_code, 
                   st.stts_chng_action_name, st.stts_chng_comment_txt, st.stts_chng_action_dttm, st.stts_chng_user_num
            FROM {main_table} AS ior
            LEFT JOIN (
                SELECT incdnt_id AS st_incdnt_id, incdnt_status_name AS incdnt_status_name_at_action, 
                       incdnt_status_code, stts_chng_action_code, stts_chng_action_name, 
                       stts_chng_comment_txt, stts_chng_action_dttm, stts_chng_user_num
                FROM {stts_table}
                WHERE UPPER(stts_chng_action_name) = 'УДАЛИТЬ'
            ) AS st ON ior.incdnt_id = st.st_incdnt_id
            {where_str}
            ORDER BY ior.incdnt_entry_dt DESC
        """
    if preset == "vozmeshenie_ior":
        rec_table = registry["recovery"]
        if plan.money:
            having = " AND ".join(m.sql("SUM(recovery_rub_amt)") for m in plan.money)
            where_clauses.append(f"ior.incdnt_id IN (SELECT incdnt_id FROM {rec_table} GROUP BY incdnt_id HAVING {having})")
            where_str = " WHERE " + " AND ".join(where_clauses)
        return f"""
            SELECT {main_projection("ior")},
                   r.recovery_sid, r.recovery_type_name, r.recovery_crncy_code, r.recovery_local_crncy_code, 
                   r.recovery_src_account_num, r.recovery_doc_num, r.recovery_creation_dttm, r.recovery_reg_dt, 
                   r.recovery_ccy_amt, r.recovery_local_ccy_amt, r.recovery_rub_amt
            FROM {main_table} AS ior
            INNER JOIN {rec_table} AS r ON ior.incdnt_id = r.incdnt_id
            {where_str}
            ORDER BY ior.incdnt_entry_dt DESC
        """
    if preset == "ior_nonfinancial_consequences":
        nfi_table = registry["nonfinancial_impact"]
        return f"""
            SELECT {main_projection("ior")},
                   nfi.nonfin_impact_sid, nfi.nonfin_impact_kind_name, nfi.nonfin_impact_influence_class_name
            FROM {main_table} AS ior
            INNER JOIN {nfi_table} AS nfi ON ior.incdnt_id = nfi.incdnt_id
            {where_str}
            ORDER BY ior.incdnt_entry_dt DESC
        """
    if preset == "financial_consequences_ior":
        fin_table = registry["financial_impact"]
        financial_scope_join = ""
        if financial_thresholds:
            having = " AND ".join(m.sql("SUM(fin_impact_rub_amt)") for m in financial_thresholds)
            financial_scope_join = f"""
            INNER JOIN (
                SELECT incdnt_id AS fin_scope_incdnt_id
                FROM {fin_table}
                GROUP BY incdnt_id
                HAVING {having}
            ) AS fin_scope ON ior.incdnt_id = fin_scope.fin_scope_incdnt_id
            """
        return f"""
            SELECT {main_projection("ior")},
                   fi.fin_impact_sid, fi.fin_impact_type_name, fi.fin_impact_kind_name, 
                   fi.fin_impact_monitoring_flag, fi.fin_impact_crncy_code, fi.fin_impact_local_crncy_code, 
                   fi.fin_impact_detection_dt, fi.fin_impact_creation_dttm, fi.fin_impact_reg_dt, 
                   fi.fin_impact_account_num, fi.fin_impact_docum_num, fi.fi_busn_area_id, fi.fi_org_struct_id, 
                   fi.fin_impact_ccy_amt, fi.fin_impact_local_ccy_amt, fi.fin_impact_rub_amt
            FROM {main_table} AS ior
            INNER JOIN {fin_table} AS fi ON ior.incdnt_id = fi.incdnt_id
            {financial_scope_join}
            {where_str}
            ORDER BY ior.incdnt_entry_dt DESC
        """
    if preset == "report_period_specific_ior":
        fin_table = registry["financial_impact"]
        rec_table = registry["recovery"]
        return f"""
            SELECT {main_projection("ior")},
                   fi.fin_impact_id, fi.fin_impact_sid, fi.fin_impact_type_name,
                   fi.fin_impact_kind_name, fi.fin_impact_monitoring_flag,
                   fi.fin_impact_crncy_code, fi.fin_impact_local_crncy_code,
                   fi.fin_impact_detection_dt, fi.fin_impact_creation_dttm,
                   fi.fin_impact_reg_dt, fi.fin_impact_account_num,
                   fi.fin_impact_docum_num, fi.fi_busn_area_id, fi.fi_org_struct_id,
                   fi.fin_impact_ccy_amt, fi.fin_impact_local_ccy_amt,
                   fi.fin_impact_rub_amt,
                   r.recovery_sid, r.recovery_type_name, r.recovery_crncy_code,
                   r.recovery_local_crncy_code, r.recovery_src_account_num,
                   r.recovery_doc_num, r.recovery_creation_dttm, r.recovery_reg_dt,
                   r.recovery_ccy_amt, r.recovery_local_ccy_amt, r.recovery_rub_amt
            FROM {main_table} AS ior
            LEFT JOIN {fin_table} AS fi ON ior.incdnt_id = fi.incdnt_id
            LEFT JOIN {rec_table} AS r ON ior.incdnt_id = r.incdnt_id
            {where_str}
            ORDER BY ior.incdnt_entry_dt DESC
        """
    if preset == "credit_no_way_collect_debt":
        credit_table=registry.get('credits')
        if not credit_table:
            raise RuntimeError("Preset credit_no_way_collect_debt is not available: its physical source table is not configured")
        return f"SELECT {main_projection('ior')}, c.credit_agr_num, c.credit_debt_rub_amt FROM {main_table} AS ior LEFT JOIN {credit_table} AS c ON ior.incdnt_id = c.incdnt_id {where_str} ORDER BY ior.incdnt_entry_dt DESC"
    return f"SELECT {main_projection()} FROM {main_table}{where_str} ORDER BY incdnt_entry_dt DESC"


def format_excel_inspection_markdown(
    xlsx_path: Path,
    include_row_count: bool = True,
    include_loss_metrics: bool = True,
    include_unique_count: bool = True,
    preset_name: Optional[str] = None,
) -> str:
    """Формирует текстовое представление статистики и таблицы-превью (5x6 ячеек) отчета через excel_inspector."""
    if not xlsx_path or not xlsx_path.exists():
        return ""
    try:
        from utils.excel_inspector import inspect_excel
        res = inspect_excel(xlsx_path)
        stats = res.get("stats", {})
        meta = res.get("excel_meta", {})

        lines = ["\n---\n### 📊 Карточка выгрузки отчёта Excel (`" + meta.get("name", xlsx_path.name) + "`)"]
        if include_row_count:
            rows_cnt = stats.get("rows", 0)
            row_labels = {
                "vozmeshenie_ior": "Операций возмещения в файле",
                "financial_consequences_ior": "Финансовых последствий в файле",
                "ior_nonfinancial_consequences": "Нефинансовых последствий в файле",
                "deleted_ior": "Строк выгрузки",
            }
            row_label = row_labels.get(preset_name, "Количество записей")
            lines.append(f"- **{row_label}**: {rows_cnt:,}".replace(",", " "))
        if include_unique_count:
            unique_cnt = stats.get("n_unique_incdnt_sid")
            if unique_cnt is not None:
                lines.append(f"- **Количество уникальных ИОР**: {unique_cnt:,}".replace(",", " "))
        lines.append(f"- **Размер файла**: {meta.get('size', '-')}")

        sum_loss = stats.get("sum_total_loss", 0.0)
        if preset_name == "financial_consequences_ior":
            fin_amount = stats.get("financial_impact", 0.0)
            if fin_amount is not None:
                lines.append(f"- **Сумма финансовых последствий**: {fin_amount:,.2f} ₽".replace(",", " "))
        elif preset_name == "deleted_ior":
            lines.append("- **Сумма последствий**: " + format_amount(sum_loss))
        elif include_loss_metrics and preset_name in {"ior_hypothesis", "ior_period_pao_sberbank"}:
            lines.append("- **Сумма последствий**: " + format_amount(sum_loss))

        recovery = stats.get("recovery", 0.0)
        if preset_name in {"ior_hypothesis", "ior_period_pao_sberbank", "deleted_ior"}:
            lines.append("- **Сумма возмещений**: " + format_amount(recovery))
        elif preset_name == "vozmeshenie_ior" and recovery is not None:
            lines.append("- **Сумма возмещений**: " + format_amount(recovery))

        top_tb = stats.get("top_tb")
        if top_tb:
            unit = "операций" if preset_name == "vozmeshenie_ior" else (
                "последствий" if preset_name == "financial_consequences_ior" else "строк")
            lines.append(f"- **Преобладающий ТБ**: `{top_tb.get('label')}` ({top_tb.get('value')} {unit}, {top_tb.get('pct')}%)")

        top_type = stats.get("top_type")
        if top_type:
            lines.append(f"- **Преобладающий тип события**: `{top_type.get('label')}` ({top_type.get('value')} инц.)")

        sample = meta.get("sample", [])
        sample_headers = meta.get("sample_headers", [])
        if sample and sample_headers:
            sample_headers = list(sample_headers)
            if "Сумма" in sample_headers:
                amount_label = "Сумма финансовых последствий" if preset_name == "financial_consequences_ior" else (
                    "Сумма возмещений" if preset_name == "vozmeshenie_ior" else "Сумма последствий"
                )
                sample_headers = [amount_label if header == "Сумма" else header for header in sample_headers]
            lines.append("\n**Превью выгрузки (первые 5 строк):**")
            lines.append("| " + " | ".join(sample_headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(sample_headers)) + " |")
            for row in sample:
                lines.append("| " + " | ".join(str(c) for c in row) + " |")

        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[ior_reports] Excel inspection formatting error: {e}")
        return ""


def build_graceful_fallback_report(df: pd.DataFrame, xlsx_path: Optional[Path], preset_name: str, error_msg: str) -> str:
    """Формирует гарантийный текстовый отчет с профилем, суммами, Топ-5 инцидентами и путем к Excel при сбое LLM."""
    from ior_hypothesis import profile_dataframe, get_incident_id_col, format_loss, _to_numeric_clean

    analyzer = get_analyzer(preset_name)
    if analyzer is not None and not df.empty:
        report = analyzer.prepare(df).deterministic_report()
        return report

    lines = [
        "### ⚠️ Аналитический отчет по ИОР (Режим надежности / Graceful Fallback)",
        "*Примечание: аналитический LLM-этап недоступен. Ниже приведены рассчитанные показатели выгрузки.*\n"
    ]

    if df.empty:
        lines.append("Выгрузка пуста. Нет данных для отображения.")
        return "\n".join(lines)

    if preset_name == "vozmeshenie_ior":
        from vozmeshenie_analysis import format_vozmeshenie_header, prepare_vozmeshenie_views
        incidents, metrics = prepare_vozmeshenie_views(df)
        lines = [format_vozmeshenie_header(metrics).rstrip()]
        lines.append(profile_dataframe(incidents, running_skill=preset_name))
        if xlsx_path:
            excel_card = format_excel_inspection_markdown(
                xlsx_path,
                include_row_count=False,
                include_loss_metrics=False,
            )
            if excel_card:
                lines.append(excel_card)
        return "\n\n".join(lines + ["Гипотезы не были сформированы из-за недоступности аналитического LLM-этапа."])

    lines.append(profile_dataframe(df, running_skill=preset_name))
    lines.append("")

    id_col = get_incident_id_col(df) or df.columns[0]
    loss_cols = ["incdnt_sum", "общая сумма всех последствий (руб.)", "общая сумма последствий (руб.)", "сумма последствий, ₽", "fin_impact_rub_amt"]
    primary_loss = next((c for c in df.columns if str(c).lower() in loss_cols), None)
    if not primary_loss:
        money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт"))]
        primary_loss = money_cols[0] if money_cols else None

    status_col = next((c for c in df.columns if any(x in str(c).lower() for x in ("incdnt_status_name", "статус события", "статус"))), None)
    desc_col = next((c for c in df.columns if any(x in str(c).lower() for x in ("incdnt_full_descr_txt", "подробное описание", "полное описание", "описание", "incdnt_summary_descr_txt"))), None)

    df_sorted = df.copy()
    if primary_loss:
        df_sorted[primary_loss] = _to_numeric_clean(df_sorted[primary_loss])
        df_sorted = df_sorted.sort_values(by=primary_loss, ascending=False)

    top_5 = df_sorted.head(5)
    lines.append("### Топ-5 крупнейших инцидентов по сумме потерь:")
    lines.append("| № | Идентификатор | Сумма потерь | Статус | Описание |")
    lines.append("|---|---|---|---|---|")

    for idx, (_, row) in enumerate(top_5.iterrows(), 1):
        sid = str(row[id_col]) if id_col in row and pd.notna(row[id_col]) else "—"
        loss_val = format_loss(row[primary_loss]) if primary_loss and primary_loss in row and pd.notna(row[primary_loss]) else "0.00 ₽"
        status_val = str(row[status_col]) if status_col and status_col in row and pd.notna(row[status_col]) else "—"
        raw_desc = str(row[desc_col]).strip().replace("\n", " ") if desc_col and desc_col in row and pd.notna(row[desc_col]) else "—"
        desc_short = raw_desc[:120] + "..." if len(raw_desc) > 120 else raw_desc
        lines.append(f"| {idx} | {sid} | {loss_val} | {status_val} | {desc_short} |")

    lines.append("")
    if xlsx_path:
        excel_card = format_excel_inspection_markdown(
            xlsx_path,
            include_row_count=preset_name != "vozmeshenie_ior",
            include_loss_metrics=preset_name != "vozmeshenie_ior",
        )
        if excel_card:
            lines.append(excel_card)

    return "\n".join(lines + ["Гипотезы не были сформированы из-за недоступности аналитического LLM-этапа."])


async def run_ior_report(
    preset_name: Optional[str],
    session_id: str,
    user_prompt: str = "",
    filters: Optional[dict] = None
) -> str:
    """Главная точка входа выгрузки ИОР с поддержкой пресетов БЗ, динамических сложных запросов и BGE-M3 индекса."""
    logger.info(f"[ior_reports] 🚀 Starting run_ior_report | preset_name='{preset_name}' | session_id='{session_id}' | prompt='{user_prompt}'")
    from analysis_mode.parser import try_parse_analysis_request
    from analysis_mode.models import AnalysisRequestError

    structured_request = try_parse_analysis_request(user_prompt)
    if structured_request is not None:
        if preset_name or filters:
            raise AnalysisRequestError(
                "Конфликт режимов: структурированный анализ нельзя совмещать "
                "с --preset или внешними filters."
            )
        from analysis_mode.runner import run_analysis_mode

        return await run_analysis_mode(structured_request, get_data_store())
    session_data = get_session_extract(session_id)
    xlsx_path: Optional[Path] = None

    if explicit_followup(user_prompt):
        if not session_data:
            return "В этой сессии ещё нет выгрузки. Сначала выполните запрос по ИОР."
        if session_data["df"].empty:
            return "Последняя выгрузка пуста. ИОР для поиска в ней нет."
        matches = search_small_index(session_id, user_prompt)
        if not matches:
            return "Поиск в последней выгрузке недоступен или совпадений не найдено. Повторите поиск позже либо выполните новую выгрузку."
        return answer_follow_up_with_qwen(user_query=user_prompt, descriptions=matches)
    try:
        plan = build_plan(preset_name, user_prompt)
    except ClarificationRequired as outcome:
        return str(outcome)
    preset = plan.preset
    period = plan.period
    has_filters = bool(plan.predicates() or plan.money)
    logger.info(
        "[ior_reports] resolved preset=%s; parsed period=%s..%s; base table=%s; joined table=%s",
        preset,
        getattr(period, "start", None), getattr(period, "end", None),
        "d6_base_of_knowledge_ior",
        {
            "financial_consequences_ior": "d6_base_of_knowledge_incident_fin_impact",
            "vozmeshenie_ior": "d6_base_of_knowledge_incident_recovery",
            "ior_nonfinancial_consequences": "d6_base_of_knowledge_incident_nonfin_impact",
            "deleted_ior": "d6_base_of_knowledge_incident_stts_chng",
            "report_period_specific_ior": "financial_impact + recovery",
        }.get(preset),
    )

    if session_data and not preset_name and not user_prompt:
        logger.info(f"[ior_reports] Re-using existing session DataFrame ({len(session_data['df'])} rows)")
        df = session_data["df"]
    else:
        store = get_data_store()
        table_registry = getattr(store, "tables", GREENPLUM_TABLES)
        preset_queries = build_preset_sql_queries(table_registry)

        if has_filters:
            logger.info(f"[ior_reports] Executing targeted dynamic SQL query for prompt: '{user_prompt}' (has_filters=True)")
            dynamic_sql = build_dynamic_sql_from_prompt(
                user_prompt,
                preset_name=preset,
                tables=table_registry,
            )
            where_debug = re.search(r"\bWHERE\b(.+?)(?:\bORDER\s+BY\b|$)", dynamic_sql, re.IGNORECASE | re.DOTALL)
            logger.debug("[ior_reports] generated WHERE predicates: %s", where_debug.group(1).strip() if where_debug else "<none>")
            # A production GP error must reach the tool boundary.  Returning an
            # empty report here would make an outage look like a valid zero-row
            # business result.
            df = store.query_sql(dynamic_sql)
        else:
            sql_query = preset_queries.get(preset)
            if sql_query is None:
                raise RuntimeError(
                    f"Preset {preset!r} is not available for "
                    f"{getattr(store, 'backend_name', 'unknown')!r}: "
                    "its physical source table is not configured"
                )
            else:
                logger.info(f"[ior_reports] Executing preset SQL query for preset '{preset}'")
                df = store.query_sql(sql_query)

        # Вторичная точная фильтрация DataFrame по периоду при необходимости (если SQL не отфильтровал)
        if not df.empty and period:
            date_col = next((c for c in df.columns if str(c).lower() in (period.column, "incdnt_entry_dt", "incdnt_start_dt", "дата ввода (событие)")), None)
            if date_col:
                dt_series = pd.to_datetime(df[date_col], errors='coerce')
                mask = pd.Series(False, index=df.index)
                for a, b in period.intervals:
                    mask |= (dt_series >= pd.Timestamp(a)) & (dt_series < pd.Timestamp(b))
                df = df[mask]


        logger.info("[ior_reports] row funnel final joined population: %s rows, %s columns", len(df), len(df.columns))

        if not df.empty:
            detail_presets = {"vozmeshenie_ior", "financial_consequences_ior", "ior_nonfinancial_consequences", "report_period_specific_ior", "deleted_ior"}
            if preset not in detail_presets:
                df_export = aggregate_by_incident_id(df)
            else:
                df_export = df

            detail_keys = {
                "vozmeshenie_ior": ("recovery_sid", "recovery_rub_amt"),
                "financial_consequences_ior": ("fin_impact_sid", "fin_impact_rub_amt"),
                "ior_nonfinancial_consequences": ("nonfin_impact_sid", None),
            }
            if preset in detail_keys:
                entity_col, amount_col = detail_keys[preset]
                incident_col = find_column(df_export, ("incdnt_sid", "incdnt_id", "идентификатор события"))
                entity_actual = find_column(df_export, (entity_col,))
                amount_actual = find_column(df_export, (amount_col,)) if amount_col else None
                df_export = deduplicate_detail_entities(df_export, incident_col, entity_actual, amount_actual)

            low_prompt = user_prompt.lower()
            is_vozmeshenie = preset == "vozmeshenie_ior"
            is_financial = preset == "financial_consequences_ior"

            if is_vozmeshenie:
                df_export = apply_preset_column_filter(df_export, VOZMESHENIE_RENAME)
            elif is_financial:
                df_export = apply_preset_column_filter(df_export, FINANCIAL_RENAME)
            else:
                rename_map = {}
                for col in df_export.columns:
                    col_lower = str(col).strip().lower()
                    if col_lower in CYRILLIC_RENAME:
                        rename_map[col] = CYRILLIC_RENAME[col_lower]
                if rename_map:
                    df_export = df_export.rename(columns=rename_map)

            df_export = prepare_df_for_excel(df_export)

            output_dir = output_directory(Path("workspace/data_store/generated_files"))
            output_dir.mkdir(parents=True, exist_ok=True)
            file_id = str(uuid.uuid4())
            xlsx_path = output_dir / f"{file_id}.xlsx"
            csv_path = output_dir / f"{file_id}.csv"

            try:
                # 1. Сначала сохраняем CSV (гарантированный вывод)
                df_export.to_csv(csv_path, index=False, encoding="utf-8")

                # 2. Сохраняем Excel с защитой от ошибок версии xlsxwriter
                try:
                    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
                        df_export.to_excel(writer, index=False)
                        force_literal_excel_cells(writer.sheets["Sheet1"])
                except Exception:
                    try:
                        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
                            df_export.to_excel(writer, index=False)
                            force_literal_excel_cells(writer.sheets["Sheet1"])
                    except Exception:
                        import openpyxl
                        wb = openpyxl.Workbook()
                        ws = wb.active
                        ws.append([str(c) for c in df_export.columns])
                        for row in df_export.values.tolist():
                            ws.append([str(v) if (v is not None and not pd.isna(v)) else "" for v in row])
                        force_literal_excel_cells(ws)
                        wb.save(xlsx_path)
                logger.info(f"[ior_reports] Generated export files for query/preset '{preset}': {xlsx_path}")
                register_artifact(xlsx_path)
            except Exception as export_err:
                logger.warning(f"[ior_reports] Failed to write Excel/CSV files: {export_err}")

        set_session_extract(session_id, df, skill_name="ior-analyzer")
        if not df.empty:
            build_and_cache_small_index(session_id, df)

    try:
        logger.info(f"[ior_reports] Invoking generate_hypothesis_narrative for preset '{preset}'...")
        narrative = await generate_hypothesis_narrative(
            user_prompt or "Выгрузка ИОР", df, session_id, preset_name=preset,
            analysis_context=plan.context(user_prompt),
        )
        from preset_analysis.slices import render_slices
        slice_text = render_slices(df, plan)
        if slice_text:
            narrative += "\n\n" + slice_text
        if xlsx_path and xlsx_path.exists():
            excel_card = format_excel_inspection_markdown(
                xlsx_path,
                include_row_count=True,
                include_loss_metrics=preset not in {"vozmeshenie_ior", "ior_nonfinancial_consequences", "deleted_ior"},
                preset_name=preset,
            )
            if excel_card:
                narrative += f"\n\n{excel_card}"
        return narrative
    except Exception as hypothesis_err:
        logger.error(f"[ior_reports] Hypothesis generation failed, returning Graceful Fallback report: {hypothesis_err}", exc_info=True)
        from preset_analysis.slices import render_slices
        return build_graceful_fallback_report(df, xlsx_path, preset, str(hypothesis_err)) + "\n\n" + render_slices(df, plan)
