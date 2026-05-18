# Scratchpad

Index status: `count_documents` на всех vector backends + `index_coverage.get_index_coverage`, админка `/admin/graph-search/index-status/`, команда `search_index_status` с таблицей покрытия. Searcher: если у `graph.invoke()` нет ключа `final_results`, вызывается `postprocess_results_node` (LangGraph + dict state).

DONE — полный pytest 87 passed, 1 skipped; ruff на изменённых файлах — ok.
