import importlib
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = SKILL_ROOT.parents[1]

# Порядок в sys.path здесь load-bearing, и каждая позиция проверяется
# существующими тестами навыка (scripts/test_analysis_mode.py делает
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
# Механика ниже повторяет IORAnalyzerTool._prepare_skill_utils_namespace
# (ior_analyzer.py), но продублирована здесь намеренно: conftest — это
# bootstrap тестов, и импорт IORAnalyzerTool увёл бы его в
# workspace.tools. Такой импорт запрещён инвариантом «skill не зависит от
# реализации Tool» (tests/test_skill_tool_independence.py), и навык обязан
# собирать тесты, не поднимая слой гейтвея.
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

# Модули навыка, лежащие в его utils/, доступны как utils.*: пакет utils уже
# импортирован (общий, из workspace), поэтому каталог навыка дописывается в
# конец __path__, а не в sys.path — вставка в sys.path перехватила бы имя
# utils целиком и отняла бы у гейтвея utils.media.
utils_package = importlib.import_module("utils")
skill_utils = str(SKILL_ROOT / "utils")
if not (SKILL_ROOT / "utils").is_dir():
    raise RuntimeError(f"IOR skill utils directory not found: {skill_utils}")
package_path = getattr(utils_package, "__path__", None)
if package_path is None:
    raise RuntimeError("Loaded module 'utils' is not a package")
if skill_utils in package_path:
    package_path.remove(skill_utils)
package_path.append(skill_utils)