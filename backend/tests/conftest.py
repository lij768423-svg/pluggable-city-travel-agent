import re

import pytest

import app.config as _config

from app.config import CITY_DIR
from app.llm import MockLLM

needs_data = pytest.mark.skipif(not (CITY_DIR / "attractions.parquet").exists(),
                                reason="需要先运行 scripts/fetch_data.py")


def mock_select(messages):
    """模拟 LLM 选点：按候选顺序取前 N 个 id（N 从提示词里读）。"""
    text = messages[-1]["content"]
    n = int(re.search(r"N=(\d+)", text).group(1))
    ids = [int(x) for x in re.findall(r"id=(\d+)", text)][:n]
    return '<think>mock</think>{"selected": [' + ",".join(f'{{"id": {i}, "reason": "mock 理由"}}' for i in ids) + "]}"


@pytest.fixture
def mock_llm():
    return MockLLM({"select": mock_select,
                    "parse": '{"days": 2, "budget": 1500, "pace": "normal", "interests": ["人文历史"]}'})


@pytest.fixture(autouse=True)
def _no_network_travel(monkeypatch):
    """测试一律用估算模型，绝不联网调用高德（需要高德的测试自行注入假的 router）。"""
    monkeypatch.setattr(_config, "AMAP_WEB_KEY", "")
    monkeypatch.setattr(_config, "ONLINE_RESTAURANTS", False)
