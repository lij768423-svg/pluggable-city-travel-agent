from types import SimpleNamespace
import logging
import threading

import pytest

from app.llm import OpenAICompatLLM, PublicOutput, Usage, strip_think
from app.progress import PlanningStopped


@pytest.mark.parametrize("width", range(1, 15))
def test_public_filter_handles_fragmented_tags(width):
    source = '<think>private reasoning</think>{"selected": []}<think>more private</think>done'
    output = PublicOutput()
    visible = ''.join(output.feed(source[index:index + width])[0] for index in range(0, len(source), width)) + output.feed('', final=True)[0]
    assert visible == '{"selected": []}done'
    assert strip_think(source) == visible


def test_unclosed_private_output_is_never_shown():
    output = PublicOutput()
    assert output.feed('<think>secret')[0] + output.feed('', final=True)[0] == ''


def stream_llm(tmp_path, monkeypatch, callback):
    from app import config
    monkeypatch.setattr(config, 'LLM_CACHE_DIR', tmp_path)
    model = OpenAICompatLLM.__new__(OpenAICompatLLM)
    model.model = 'test-model'
    model.cache = True
    model.usage = Usage()
    model._lock = threading.Lock()
    model.on_output = callback
    requests = []
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *args): self.closed = True
        def __iter__(self):
            for fragment in ['<thi', 'nk>secret', '</think>', '{"ok":', ' true}']:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=fragment, reasoning_content='must never appear'))], usage=None)
            yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=8, completion_tokens=12))
    def create(**kwargs):
        requests.append(kwargs)
        return Stream()
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return model, requests


def test_real_chunks_usage_and_cache_provenance(tmp_path, monkeypatch):
    events = []
    model, requests = stream_llm(tmp_path, monkeypatch, events.append)
    result = model.complete([], tag='select')
    assert strip_think(result) == '{"ok": true}'
    assert ''.join(event['text'] for event in events) == '{"ok": true}'
    assert requests[0]['stream'] is True and model.usage.completion_tokens == 12
    events.clear()
    assert model.complete([], tag='select') == result
    assert len(requests) == 1 and events[0]['source'] == 'cache'
    assert model.usage.cached_calls == 1
    assert model.fork().on_output is None


def test_thinking_streams_separately_from_the_answer(tmp_path, monkeypatch):
    events = []
    model, _ = stream_llm(tmp_path, monkeypatch, events.append)
    model.complete([], tag='select')
    live = [event for event in events if event['source'] == 'live']
    thinking = [event for event in live if event['phase'] == 'thinking']
    assert thinking and all(event['text'] == '' for event in thinking)
    assert max(event['thought'] for event in live) > 0
    assert live[-1]['phase'] == 'answer'
    # 思考内容只走 think 字段，公开回答里一个字都不混进来
    answer = ''.join(event['text'] for event in live)
    thought = ''.join(event.get('think', '') for event in live)
    assert answer == '{"ok": true}'
    assert 'secret' in thought and 'must never appear' in thought
    assert events[0]['think'] == ''


def test_cancellation_interrupts_private_stream_without_retry(tmp_path, monkeypatch):
    events = []
    def emit(event):
        events.append(event)
        if len(events) == 3:
            raise PlanningStopped()
    model, requests = stream_llm(tmp_path, monkeypatch, emit)
    with pytest.raises(PlanningStopped):
        model.complete([], tag='select')
    assert len(requests) == 1
    assert model.usage.timings[-1]['outcome'] == 'stopped'


def test_first_event_waits_and_call_timing_is_recorded(tmp_path, monkeypatch, caplog):
    """发出请求后先报"等待模型服务"，收到片段才算思考；每次调用记下首字等待与总耗时，并写日志。"""
    events = []
    model, _ = stream_llm(tmp_path, monkeypatch, events.append)
    with caplog.at_level(logging.INFO, logger='app.llm'):
        model.complete([], tag='select')
    live = [event for event in events if event['source'] == 'live']
    assert live[0]['phase'] == 'waiting' and live[0]['thought'] == 0
    assert all(event['phase'] != 'waiting' for event in live[1:])
    [entry] = model.usage.timings
    assert (entry['tag'], entry['outcome'], entry['attempts'], entry['model']) == ('select', 'ok', 1, 'test-model')
    assert entry['first_chunk_s'] is not None and entry['first_chunk_s'] <= entry['seconds']
    assert 'LLM ok tag=select attempts=1 first_chunk=' in caplog.text
    model.complete([], tag='select')  # 命中缓存也留一条，但不写日志
    assert model.usage.timings[-1]['outcome'] == 'cache' and caplog.text.count('LLM ok') == 1


def flaky_client(model, failures: int):
    """前 failures 次请求直接抛错，之后照常返回流。"""
    create, calls = model.client.chat.completions.create, {'n': 0}

    def maybe_fail(**kwargs):
        calls['n'] += 1
        if calls['n'] <= failures:
            raise TimeoutError('read timed out')
        return create(**kwargs)
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=maybe_fail)))
    return calls


def test_retry_records_each_failed_attempt(tmp_path, monkeypatch, caplog):
    from app import llm as llm_module
    events, sleeps = [], []
    model, _ = stream_llm(tmp_path, monkeypatch, events.append)
    flaky_client(model, failures=1)
    monkeypatch.setattr(llm_module.time, 'sleep', sleeps.append)
    with caplog.at_level(logging.INFO, logger='app.llm'):
        model.complete([], tag='select')
    [entry] = model.usage.timings
    assert entry['outcome'] == 'ok' and entry['attempts'] == 2 and sleeps == [2]
    first, second = entry['attempt_log']
    assert first['error'] == 'TimeoutError: read timed out' and first['first_chunk_s'] is None
    assert 'error' not in second and second['first_chunk_s'] is not None
    # 重试会重新发"等待"事件，前端据此标出"第 2 次尝试"
    assert [event['attempt'] for event in events if event.get('phase') == 'waiting'] == [1, 2]
    assert 'attempts=2' in caplog.text and 'errors=[#1 TimeoutError: read timed out]' in caplog.text


def test_exhausted_retries_are_logged_as_failed(tmp_path, monkeypatch, caplog):
    from app import llm as llm_module
    sleeps = []
    model, _ = stream_llm(tmp_path, monkeypatch, lambda event: None)
    flaky_client(model, failures=99)
    monkeypatch.setattr(llm_module.time, 'sleep', sleeps.append)
    with caplog.at_level(logging.INFO, logger='app.llm'), pytest.raises(RuntimeError, match='LLM 调用失败'):
        model.complete([], tag='select')
    [entry] = model.usage.timings
    assert entry['outcome'] == 'failed' and entry['attempts'] == 3
    assert sleeps == [2, 4]  # 最后一次失败后不再空等
    assert any(record.levelno == logging.WARNING and 'LLM failed tag=select attempts=3' in record.getMessage()
               for record in caplog.records)


def test_usage_add_keeps_call_timings():
    one, two = Usage(), Usage()
    one.timings.append({'tag': 'parse'})
    two.timings.append({'tag': 'select'})
    one.add(two)
    assert [entry['tag'] for entry in one.timings] == ['parse', 'select']
    assert 'timings' not in one.as_dict()
