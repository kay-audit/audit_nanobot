"""CLI entry-point для skill'а follow_up (D5).

Это тонкая обёртка, которая перенаправляет вызовы к реальному backend'у
skill'а (workspace/skills/follow_up/backend/main.py). Создан для соответствия
контракту feature.yaml (pattern='full' требует scripts/cli.py).

Код skill'а (workspace/skills/follow_up/backend/**) НЕ модифицируется —
эта обёртка импортирует и диспатчит к нему.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Подключаем backend к sys.path
SKILL_ROOT = Path(__file__).resolve().parents[1]
BACKEND = SKILL_ROOT / "backend"
sys.path.insert(0, str(SKILL_ROOT))
sys.path.insert(0, str(BACKEND))


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    # Делегируем в backend.main, если он существует
    try:
        from backend import main as backend_main
        return backend_main.main(argv if argv is not None else sys.argv[1:])
    except ImportError as e:
        sys.stderr.write(f"[follow_up.cli] Backend not available: {e}\n")
        return 1
    except AttributeError:
        sys.stderr.write("[follow_up.cli] backend.main does not expose main()\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
