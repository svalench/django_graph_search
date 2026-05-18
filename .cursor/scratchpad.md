# Scratchpad

Pylint CI: `_parse_float_param` один return + `math.isnan`; `_stringify_numeric_param`; переносы строк в views; фикстуры через `fixture(name=...)` (W0621); pgvector/settings/tasks длина строк; `tasks.delete_instance_task` сигнатура.

DONE — `pylint $(git ls-files '*.py')` 10/10; pytest по затронутым модулям 16 passed. Полный pytest: 2 fail в `test_langgraph_search` (Searcher + LANGGRAPH), вне этого PR.

Prerelease **0.3.0a1**: `setup.cfg` + `CHANGELOG.md`; `python -m build` → `dist/*.whl` и `dist/*.tar.gz` (папка в `.gitignore`).
