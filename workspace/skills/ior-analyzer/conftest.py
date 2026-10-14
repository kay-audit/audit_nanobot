import sys
from pathlib import Path

from workspace.tools.ior_analyzer import IORAnalyzerTool

SKILL_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = SKILL_ROOT.parents[1]

# Порядок в sys.path здесь load-bearing, и каждая позиция проверяется
# существующим тестом навыка (scripts/test_analysis_mode.py делает
# ``import cli`` — в репозитории четыре cli.py, и берётся не тот, если
# scripts/ уедет в хвост):
#
#   1. SKILL_ROOT/scripts — впереди всех: ``cli``, ``ior_reports`` и прочие
#      top-level модули навыка должны браться именно отсюда. Каталога
#      ``utils`` в нём нет, поэтому ``utils`` он не перехватывает.
#   2. WORKSPACE_ROOT — перед корнем навыка. Имя ``utils`` (и ``data_store``)
#      общее у workspace и навыка; если корень навыка окажется раньше
#      workspace, первый же ``import utils`` уедет в ior-analyzer/utils, и
#      корневые тесты (``utils.media`` из redis_channel, ``utils.db`` из
#      cache_ownership) перестанут собираться — этот conftest грузится раньше
#      них.
#   3. SKILL_ROOT — последним: в нём лежит ``utils/``, и его собственные
#      top-level модули (rerank_osiris_worker) в конфликте с workspace не
#      вступают.
#
# Навыковые модули, лежащие в корне навыка, доступны как ``utils.*`` через
# расширение ``__path__`` ниже — ровно тот приём, что использует
# ``IORAnalyzerTool._load_runner`` на рантайме.
for directory in (
    WORKSPACE_ROOT,
    SKILL_ROOT / "utils",
    SKILL_ROOT / "scripts",
    SKILL_ROOT,
):
    while str(directory) in sys.path:
        sys.path.remove(str(directory))

# Порядок внутри префикса задаётся одним срезом: последовательные insert(0, …)
# переставляли бы эти три места относительно друг друга.
sys.path[0:0] = [str(SKILL_ROOT / "scripts"), str(WORKSPACE_ROOT), str(SKILL_ROOT)]

IORAnalyzerTool._prepare_skill_utils_namespace(SKILL_ROOT)