"""n8n 운영 워크플로의 합성 경계 검사. API·DB 접속 없음."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_daily_stock_workflow():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js가 없어 독립 n8n JavaScript 검사 생략")
    script = Path(__file__).resolve().parents[1] / "scripts/daily_stock_collection/checks.cjs"
    result = subprocess.run([node, str(script)], capture_output=True, timeout=30)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")
