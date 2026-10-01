# Grounded explanation

Объяснение generated SQL опирается на `sql_facts` и KB descriptions/types.
Обязательно раскрывает sources, JOIN keys, WHERE/HAVING/JOIN filters,
aggregations/windows, assumptions, unknown tables/columns и low confidence.
Литералы фильтров не считаются проверенными по данным. Для ready SQL объяснение
можно дать вокруг source, но нельзя менять сам SQL.

