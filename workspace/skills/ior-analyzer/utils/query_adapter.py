"""Schema-bound SELECT for internal dataframe tools using the existing store API."""
from datetime import date,datetime
from decimal import Decimal
import math
import re
from utils.data_store import DUCKDB_TABLES,GREENPLUM_TABLES,HIVE_TABLES

def query_table(store, **kwargs):
    from utils.schema.loader import get_schema
    return store.query_sql(select_sql(store,get_schema(),**kwargs))

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?',value):
        raise ValueError('Invalid SQL identifier')
    return '.'.join('"'+part+'"' for part in value.split('.'))

def literal(value):
    if value is None: return 'NULL'
    if isinstance(value,bool): return 'TRUE' if value else 'FALSE'
    if isinstance(value,(int,float,Decimal)):
        if not math.isfinite(value): raise ValueError('Nonfinite filter value')
        return str(value)
    if isinstance(value,(str,date,datetime)):
        return "'"+str(value).replace("'","''")+"'"
    raise ValueError('Unsupported filter value')

def select_sql(store,schema,table,where=None,columns=None,order_by=None,order_desc=True,limit=None):
    definition=schema.get(table)
    if definition is None: raise ValueError('Unknown source table')
    allowed=set(definition.column_names())
    selected=list(columns) if columns is not None else list(definition.column_names())
    def column(c):
        if c not in allowed: raise ValueError('Unknown source column: '+str(c))
        return identifier(c)
    if not selected: raise ValueError('Empty projection')
    physical=None
    for mapping in (DUCKDB_TABLES,GREENPLUM_TABLES,HIVE_TABLES):
        for logical,name in mapping.items():
            if table in (name,name.rsplit('.',1)[-1]):
                physical=store.tables.get(logical)
    if not physical: raise ValueError('Source table is not registered in this backend')
    def predicate(filters):
        clauses=[]
        for key,value in (filters or {}).items():
            if key=='_or':
                clauses.append('('+' OR '.join(predicate(item) for item in value)+')' if value else 'FALSE')
                continue
            parts=key.rsplit('__',1); col=parts[0]
            operations=value if isinstance(value,dict) else {parts[1] if len(parts)>1 else 'eq':value}
            for op,val in operations.items():
                op={'eq':'=','ne':'!=','gt':'>','gte':'>=','lt':'<','lte':'<=','like':'ILIKE'}.get(op,op.upper())
                if isinstance(val,(list,tuple)):
                    clauses.append(column(col)+' IN ('+', '.join(literal(v) for v in val)+')' if val else 'FALSE')
                elif val is None and op in ('=','!='):
                    clauses.append(column(col)+(' IS NULL' if op=='=' else ' IS NOT NULL'))
                elif op in ('=','!=','>','>=','<','<=','ILIKE'):
                    clauses.append(column(col)+' '+op+' '+literal(val))
                else: raise ValueError('Unsupported filter operation')
        return '('+' AND '.join(clauses)+')' if clauses else 'TRUE'
    sql='SELECT '+', '.join(column(c) for c in selected)+' FROM '+identifier(physical)
    if where: sql+=' WHERE '+predicate(where)
    if order_by: sql+=' ORDER BY '+column(order_by)+(' DESC' if order_desc else ' ASC')
    if limit is not None: sql+=' LIMIT '+str(int(limit))
    return sql
