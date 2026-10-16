"""统一的 LLM 调用入口（AGENTS.md 规则 4）。

- OpenAI 兼容接口；MVP 用 MiniMax-M3。
- MiniMax-M3 即使 response_format=json_object，content 里也会带 <think>…</think>（2026-10-14 实测），
  所以总是先剥离 think，再从文本中提取 JSON，并用 Pydantic 校验，失败时把错误反馈给模型重试。
- MiniMax 默认开启 adaptive 思考；评测时所有方法使用同一设置，缓存按参数隔离。
- 默认对响应做磁盘缓存（键 = 模型 + 消息 + 参数），保证评测可复现，也节省与律师站共用的额度。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
import httpx

from . import config

T = TypeVar("T", bound=BaseModel)


def evaluate_jev(state: dict, questions: dict, *, cache: bool = True) -> dict:
    payload = {"model": config.TYPESAFE_MODEL, "state": state, "questions": questions}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = config.DATA_DIR / "jev_cache" / f"{digest}.json"
    started = time.perf_counter()

    def validated(data: dict) -> dict:
        import math
        if not isinstance(data, dict) or not isinstance(data.get("model"), str):
            raise ValueError("invalid_jev_response")
        answers = data.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("incomplete_jev_answers")
        for answer in answers.values():
            if not isinstance(answer, dict) or answer.get("type") != "score":
                raise ValueError("invalid_jev_score")
            for field, maximum in (("score", 3), ("confidence", 1)):
                value = answer.get(field)
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
                    raise ValueError("invalid_jev_number")
        usage = data.get("usage", {})
        tokens = {field: usage.get(field, 0) for field in ("input_tokens", "output_tokens")}
        if any(type(value) is not int or value < 0 for value in tokens.values()):
            raise ValueError("invalid_jev_usage")
        return {"model": data["model"], "answers": answers, "usage": tokens}

    if cache and path.exists():
        try:
            data = validated(json.loads(path.read_text(encoding="utf-8")))
            return {**data, "cached": True, "seconds": round(time.perf_counter() - started, 3)}
        except (ValueError, TypeError, OSError, AttributeError):
            pass
    if os.environ.get("TTA_OFFLINE") == "1":
        raise RuntimeError("jev_offline_cache_miss")
    if not config.TYPESAFE_API_KEY:
        raise RuntimeError("jev_key_missing")
    try:
        response = httpx.post("https://api.typesafe.ai/v1/systemone", json=payload,
                              headers={"Authorization": f"Bearer {config.TYPESAFE_API_KEY}"},
                              timeout=config.TYPESAFE_TIMEOUT, follow_redirects=False,
                              proxy=config.TYPESAFE_PROXY_URL or None, trust_env=False)
        if response.status_code != 200:
            raise RuntimeError(f"jev_http_{response.status_code}")
        data = validated(response.json())
    except httpx.HTTPError:
        raise RuntimeError("jev_network_error") from None
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("jev_invalid_response") from None
    if cache:
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                temporary = handle.name
                json.dump(data, handle, ensure_ascii=False)
            os.replace(temporary, path)
        except OSError:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
    return {**data, "cached": False, "seconds": round(time.perf_counter() - started, 3)}

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    if "<think>" in text:  # 未闭合（被 max_tokens 截断）
        text = text.split("<think>")[0]
    return text.strip()


class PublicOutput:
    def __init__(self):
        self.pending = ""
        self.hidden = False

    def feed(self, text: str, final: bool = False) -> tuple[str, str]:
        """返回 (公开回答, 思考内容) 两段，思考内容由调用方决定是否下发。"""
        self.pending += text
        visible, hidden = "", ""
        while self.pending:
            marker = "</think>" if self.hidden else "<think>"
            position = self.pending.find(marker)
            if position >= 0:
                if self.hidden:
                    hidden += self.pending[:position]
                else:
                    visible += self.pending[:position]
                self.pending = self.pending[position + len(marker):]
                self.hidden = not self.hidden
                continue
            retained = 0
            if not final:
                for length in range(1, min(len(marker), len(self.pending) + 1)):
                    if self.pending.endswith(marker[:length]):
                        retained = length
            emitted = self.pending[:-retained] if retained else self.pending
            if self.hidden:
                hidden += emitted
            else:
                visible += emitted
            self.pending = self.pending[-retained:] if retained else ""
            break
        return visible, hidden


def extract_json(text: str) -> Any:
    """从模型输出中提取第一个 JSON 对象或数组。"""
    text = strip_think(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError(f"no JSON found in: {text[:200]!r}")
    start = min(starts)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"unbalanced JSON in: {text[:200]!r}")


@dataclass
class Usage:
    calls: int = 0
    cached_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    by_tag: dict[str, int] = field(default_factory=dict)

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.cached_calls += other.cached_calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.seconds += other.seconds
        for k, v in other.by_tag.items():
            self.by_tag[k] = self.by_tag.get(k, 0) + v

    def as_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "cached_calls": self.cached_calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "total_tokens": self.prompt_tokens + self.completion_tokens,
                "llm_seconds": round(self.seconds, 2), "by_tag": dict(self.by_tag)}


class LLM(Protocol):
    usage: Usage

    def complete(self, messages: list[dict[str, str]], *, tag: str, temperature: float, max_tokens: int) -> str: ...


class SnapshotLLM:
    def __init__(self, directory: str | os.PathLike[str], delegate: LLM | None = None) -> None:
        from pathlib import Path
        self.directory = Path(directory)
        self.delegate = delegate
        self.usage = Usage()
        self.records: list[dict] = []

    def complete(self, messages: list[dict[str, str]], *, tag: str, temperature: float, max_tokens: int) -> str:
        payload = {"messages": messages, "tag": tag, "temperature": temperature, "max_tokens": max_tokens,
                   "model": config.LLM_MODEL, "thinking": config.LLM_THINKING}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        path = self.directory / f"{digest}.json"
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["request_sha256"] != digest or hashlib.sha256(record["content"].encode()).hexdigest() != record["content_sha256"]:
                raise ValueError("LLM 快照损坏")
            self.usage.cached_calls += 1
        elif self.delegate is None or os.environ.get("TTA_OFFLINE") == "1":
            raise RuntimeError(f"缺少冻结的模型响应 {digest}，禁止联网回退")
        else:
            before = self.delegate.usage.as_dict()
            started = time.perf_counter()
            content = self.delegate.complete(messages, tag=tag, temperature=temperature, max_tokens=max_tokens)
            after = self.delegate.usage.as_dict()
            record = {"request_sha256": digest, "content": content,
                      "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                      "model": config.LLM_MODEL, "thinking": config.LLM_THINKING,
                      "capture_seconds": time.perf_counter() - started,
                      "usage": {key: after[key] - before[key] for key in ("calls", "cached_calls", "prompt_tokens", "completion_tokens", "total_tokens")}}
            self.directory.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as output:
                json.dump(record, output, ensure_ascii=False, indent=2)
            self.usage.calls += record["usage"]["calls"]
            self.usage.prompt_tokens += record["usage"]["prompt_tokens"]
            self.usage.completion_tokens += record["usage"]["completion_tokens"]
        self.records.append({key: value for key, value in record.items() if key != "content"})
        return record["content"]


class OpenAICompatLLM:
    retry_attempts = 3
    request_attempts = 0

    @property
    def response_models(self) -> set[str]:
        if not hasattr(self, "_response_models"):
            self._response_models = set()
        return self._response_models

    @response_models.setter
    def response_models(self, value: set[str]) -> None:
        self._response_models = value

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None,
                 cache: bool | None = None, retry_attempts: int = 3, sdk_max_retries: int = 2):
        from openai import OpenAI

        key = api_key or config.LLM_API_KEY or ("offline-placeholder" if os.environ.get("TTA_OFFLINE") == "1" else "")
        if not key:
            raise RuntimeError("LLM_API_KEY 未设置：请在 backend/.env 中配置（见 .env.example）")
        self.model = model or config.LLM_MODEL
        if retry_attempts < 1:
            raise ValueError("retry_attempts 必须大于 0")
        self.client = OpenAI(base_url=base_url or config.LLM_BASE_URL, api_key=key,
                             timeout=config.LLM_TIMEOUT, max_retries=sdk_max_retries)
        self.retry_attempts = retry_attempts
        self.request_attempts = 0
        self.response_models: set[str] = set()
        self.cache = True if os.environ.get("TTA_OFFLINE") == "1" else config.LLM_CACHE if cache is None else cache
        self.usage = Usage()
        self._lock = threading.Lock()

    def fork(self) -> "OpenAICompatLLM":
        """共享连接与缓存、但独立计数的实例：并发请求各自统计 token，互不串数。"""
        import copy

        f = copy.copy(self)
        f.usage = Usage()
        f.request_attempts = 0
        f.response_models = set()
        f._lock = threading.Lock()
        f.on_output = None
        return f

    def _cache_path(self, payload: dict[str, Any]):
        h = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return config.LLM_CACHE_DIR / h[:2] / f"{h}.json"

    def complete(self, messages, *, tag="misc", temperature=0.2, max_tokens=4096):
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature,
                                   "max_tokens": max_tokens}
        if self.model.startswith("MiniMax") and config.LLM_THINKING:
            # MiniMax-M3: disabled = 跳过思考直接回答；adaptive = 默认思考（官方文档 text-openai-api）
            payload["extra_body"] = {"thinking": {"type": config.LLM_THINKING}, "reasoning_split": True}
            if config.LLM_THINKING == "adaptive":
                payload["max_tokens"] = max(max_tokens, 12000)
        path = self._cache_path(payload)
        output = getattr(self, "on_output", None)
        if self.cache and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            with self._lock:
                self.usage.cached_calls += 1
                self.usage.by_tag[tag] = self.usage.by_tag.get(tag, 0) + 1
                self.response_models.add(data.get("model", self.model))
            if output:
                output({"type": "model", "tag": tag, "text": strip_think(data["content"]), "source": "cache", "attempt": 1})
            return data["content"]
        if os.environ.get("TTA_OFFLINE") == "1":
            raise RuntimeError(f"离线评测缺少 LLM 快照：{path.name}（{tag}），已禁止联网")
        last_err: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                with self._lock:
                    self.request_attempts += 1
                t0 = time.time()
                response_model = self.model
                if output:
                    output({"type": "model", "tag": tag, "text": "", "think": "", "source": "live", "attempt": attempt + 1, "phase": "thinking", "thought": 0})
                    content, usage = "", None
                    public = PublicOutput()
                    # 思考内容随 think 字段下发，前端只保留最近一段展示
                    thought, answering = 0, False
                    def emit(text: str = "", think: str = "") -> None:
                        output({"type": "model", "tag": tag, "text": text, "think": think, "source": "live", "attempt": attempt + 1,
                                "phase": "answer" if answering else "thinking", "thought": thought})
                    with self.client.chat.completions.create(**payload, stream=True, stream_options={"include_usage": True}) as stream:
                        for chunk in stream:
                            response_model = getattr(chunk, "model", None) or response_model
                            if chunk.usage:
                                usage = chunk.usage
                            delta = chunk.choices[0].delta if chunk.choices else None
                            reasoning = (getattr(delta, "reasoning_content", None)
                                         or (getattr(delta, "model_extra", None) or {}).get("reasoning_content")) if delta else None
                            thinking = reasoning if isinstance(reasoning, str) else ""
                            thought += len(thinking)
                            fragment = delta.content if delta else None
                            visible = ""
                            if fragment:
                                content += fragment
                                visible, hidden = public.feed(fragment)
                                thought += len(hidden)
                                thinking += hidden
                            if visible:
                                answering = True
                            emit(visible, thinking)
                        visible, hidden = public.feed("", final=True)
                        if visible or hidden:
                            answering = answering or bool(visible)
                            emit(visible, hidden)
                else:
                    response = self.client.chat.completions.create(**payload)
                    response_model = getattr(response, "model", None) or response_model
                    content = response.choices[0].message.content or ""
                    usage = response.usage
                dt = time.time() - t0
                with self._lock:
                    self.usage.calls += 1
                    self.usage.seconds += dt
                    self.usage.by_tag[tag] = self.usage.by_tag.get(tag, 0) + 1
                    self.response_models.add(response_model)
                    if usage:
                        self.usage.prompt_tokens += usage.prompt_tokens or 0
                        self.usage.completion_tokens += usage.completion_tokens or 0
                if self.cache:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"content": content, "tag": tag, "model": response_model}, ensure_ascii=False), encoding="utf-8")
                return content
            except Exception as e:  # 网络/限流：指数退避
                from .progress import PlanningStopped
                if isinstance(e, PlanningStopped):
                    raise
                last_err = e
                if self.retry_attempts == 1:
                    raise
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败（{tag}）：{last_err}")


class MockLLM:
    """测试用：按 tag 返回预设回复（字符串或 callable(messages)->str）。"""

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.usage = Usage()
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def complete(self, messages, *, tag="misc", temperature=0.2, max_tokens=4096):
        self.calls.append((tag, messages))
        self.usage.calls += 1
        self.usage.by_tag[tag] = self.usage.by_tag.get(tag, 0) + 1
        r = self.responses[tag]
        return r(messages) if callable(r) else r


def chat_json(llm: LLM, messages: list[dict[str, str]], model: type[T] | None = None, *, tag: str,
              temperature: float = 0.2, max_tokens: int = 4096, retries: int = 2) -> Any:
    """调用并解析 JSON；给了 model 就做 Pydantic 校验，失败时把错误告诉模型重试。"""
    msgs = list(messages)
    last: Exception | None = None
    for _ in range(retries + 1):
        text = llm.complete(msgs, tag=tag, temperature=temperature, max_tokens=max_tokens)
        try:
            obj = extract_json(text)
            return model.model_validate(obj) if model else obj
        except (ValueError, ValidationError) as e:
            last = e
            msgs = msgs + [{"role": "assistant", "content": strip_think(text)[:2000]},
                           {"role": "user", "content": f"上面的输出无法解析或不符合格式要求：{str(e)[:500]}。请只输出符合要求的 JSON。"}]
    raise ValueError(f"{tag}: LLM 输出多次无法解析：{last}")


_default: OpenAICompatLLM | None = None
_default_lock = threading.Lock()


def get_llm() -> OpenAICompatLLM:
    global _default
    with _default_lock:  # 多线程首次调用时只建一个实例，否则 usage 会被拆散漏记
        if _default is None:
            _default = OpenAICompatLLM()
    return _default
