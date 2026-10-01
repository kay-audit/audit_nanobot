# Spark SQL

- Даты: `date_add`, `date_sub`, `add_months`, `trunc`, `to_date`.
- Форматы Spark 3: `yyyy`, `MM`, `dd`, `HH`, `mm`, `ss`.
- Конкатенация: `concat()`, не `||`; cast: `cast(x as type)`, не `::`.
- STRING-даты разбирай явным форматом; STRING-флаги сравнивай со строкой.
- Window-filter выноси в подзапрос. Не выдумывай schema entities.
- Optional live analyze строит logical plan без action/collect/write.

