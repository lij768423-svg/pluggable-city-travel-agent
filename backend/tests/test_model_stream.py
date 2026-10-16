from types import SimpleNamespace
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
