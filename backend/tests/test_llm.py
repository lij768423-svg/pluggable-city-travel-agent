import pytest
from pydantic import BaseModel

from app.llm import MockLLM, chat_json, extract_json, strip_think


def test_strip_think_closed_and_unclosed():
    assert strip_think("<think>abc</think>\n\n{\"a\":1}") == '{"a":1}'
    assert strip_think("ok<think>truncated") == "ok"


@pytest.mark.parametrize("text,expected", [
    ('<think>x</think>{"a": 1}', {"a": 1}),
    ('前言```json\n{"a": [1, 2]}\n```后记', {"a": [1, 2]}),
    ('好的：{"a": "含}括号"} 以上', {"a": "含}括号"}),
    ("[1, 2, 3]", [1, 2, 3]),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


class M(BaseModel):
    n: int


def test_chat_json_retries_on_bad_output():
    outputs = iter(["<think>..</think>not json", '{"n": 3}'])
    llm = MockLLM({"t": lambda msgs: next(outputs)})
    assert chat_json(llm, [{"role": "user", "content": "x"}], M, tag="t").n == 3
    assert llm.usage.calls == 2
    # 重试时把错误反馈给模型
    assert "无法解析" in llm.calls[1][1][-1]["content"]


def test_chat_json_gives_up():
    llm = MockLLM({"t": "nope"})
    with pytest.raises(ValueError):
        chat_json(llm, [{"role": "user", "content": "x"}], M, tag="t", retries=1)


def test_get_llm_singleton_threadsafe(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    import app.llm as m

    monkeypatch.setattr(m, "_default", None)
    monkeypatch.setattr(m, "OpenAICompatLLM", lambda: object())
    with ThreadPoolExecutor(8) as ex:
        objs = list(ex.map(lambda _: m.get_llm(), range(32)))
    assert len({id(o) for o in objs}) == 1


def test_single_attempt_client_stops_on_rate_limit(monkeypatch):
    import httpx
    import openai
    from app.llm import OpenAICompatLLM

    calls = []

    def limited(*args, **kwargs):
        calls.append(kwargs)
        response = httpx.Response(429, request=httpx.Request("POST", "https://example.test"))
        raise openai.RateLimitError("rate limited", response=response, body=None)

    client = OpenAICompatLLM(api_key="test-placeholder", cache=False, retry_attempts=1, sdk_max_retries=0)
    monkeypatch.setattr(client.client.chat.completions, "create", limited)
    with pytest.raises(openai.RateLimitError):
        client.complete([{"role": "user", "content": "test"}])
    assert len(calls) == client.request_attempts == 1
    assert client.client.max_retries == 0


def test_stream_flag_streams_without_subscriber(monkeypatch):
    from types import SimpleNamespace

    from app.llm import OpenAICompatLLM

    calls = []

    class Stream:
        def __enter__(self):
            delta = SimpleNamespace(content="好", reasoning_content=None, model_extra={})
            usage = SimpleNamespace(prompt_tokens=3, completion_tokens=1)
            return iter([SimpleNamespace(model="m", usage=None, choices=[SimpleNamespace(delta=delta)]),
                         SimpleNamespace(model="m", usage=usage, choices=[])])

        def __exit__(self, *exc):
            return False

    def create(**kwargs):
        calls.append(kwargs)
        return Stream()

    client = OpenAICompatLLM(api_key="test-placeholder", model="gpt-test", cache=False, stream=True)
    monkeypatch.setattr(client.client.chat.completions, "create", create)
    assert client.complete([{"role": "user", "content": "x"}]) == "好"
    assert client.fork().complete([{"role": "user", "content": "x"}]) == "好"  # 并发用的分身也流式
    assert [c.get("stream") for c in calls] == [True, True]
    assert client.usage.completion_tokens == 1
