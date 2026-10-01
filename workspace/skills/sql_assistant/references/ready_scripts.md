# Existing ready scripts

`script_id`, `km_id`, filename сравниваются exact (filename/km case-insensitive).
Path использует только scoped contains lookup. Отсутствующий exact identifier не
расширяется до semantic search. Несколько exact записей можно вернуть все;
semantic detail при общем km_id ранжирует только этот exact-KM набор.

Поле `sql` сохраняется character-for-character, включая CRLF, Unicode, пробелы,
комментарии и embedded backticks. Advisory validator не вправе переписать или
скрыть существующий SQL, даже если он не read-only.

