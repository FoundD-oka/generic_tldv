"""conftest.py – make admin_models and the service root importable.

実行前提(CI = .github/workflows/test-admin-api.yml と同一手順):
python3.11 の fresh venv で
`pip install -e libs/admin-models/` → `pip install -e services/meeting-api/`
→ `pip install -r services/admin-api/requirements.txt`
→ `pip install pytest pytest-asyncio httpx` を実行し、
リポジトリルートから `pytest services/admin-api/tests/ -v` を走らせる。
配布物の依存解決は pip に一本化する(ここで sys.path を継ぎ足して補わない)。
"""

import sys
from pathlib import Path

_repo = Path(__file__).resolve().parents[3]  # <repo>/services/admin-api/tests -> <repo>
sys.path.insert(0, str(_repo / "libs" / "admin-models"))

# Add the service root so `import app` works (admin-api is not pip-installable)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
