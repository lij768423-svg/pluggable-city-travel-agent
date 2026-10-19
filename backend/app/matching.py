"""把 LLM 输出的地点名称匹配回数据库（用于纯 LLM 基线；匹配不上视为幻觉/库外地点）。"""
from __future__ import annotations

import difflib
import re

from .geo import haversine_km
from .schemas import POI

_PUNCT = re.compile(r"[\s·•\-—()（）【】\[\]「」'\"“”,，。]")


def norm(name: str) -> str:
    n = _PUNCT.sub("", name or "")
    for prefix in ("杭州市", "杭州", "浙江省", "浙江"):
        if n.startswith(prefix) and len(n) > len(prefix) + 1:
            n = n[len(prefix):]
    return n


def same_attraction(left: POI, right: POI) -> bool:
    aliases = {"南宋德寿宫遗址博物馆": "德寿宫遗址"}
    left_name = aliases.get(norm(left.name), norm(left.name))
    right_name = aliases.get(norm(right.name), norm(right.name))
    return left_name == right_name and haversine_km(left.lat, left.lon, right.lat, right.lon) <= 0.2


_PAREN = re.compile(r"[（(【\[](.*?)[）)】\]]")


def variants(name: str) -> list[str]:
    """名称的多种写法：原名、去括号、括号内、按 · - — 拆开的各段（用于从宽匹配）。"""
    out = [name, _PAREN.sub("", name)] + _PAREN.findall(name)
    for v in list(out):
        out += re.split(r"[·•\-—]", v)
    seen, res = set(), []
    for v in out:
        n = norm(v)
        if len(n) >= 2 and n not in seen:
            seen.add(n)
            res.append(v)
    return res


def match_place(name: str, places: dict[int, POI]) -> int | None:
    """先按补充地点登记的别名精确匹配，同一别名对应多处时不自动取第一条；再走原有从宽匹配。"""
    wanted = {norm(v) for v in variants(name)}
    hits = {place.id for place in places.values() for alias in place.aliases if norm(alias) in wanted}
    if hits:
        return hits.pop() if len(hits) == 1 else None
    return match_name(name, {identifier: place.name for identifier, place in places.items()})


def match_name(name: str, names: dict[int, str], threshold: float = 0.8) -> int | None:
    for v in variants(name):
        hit = _match_one(v, names, threshold)
        if hit is not None:
            return hit
    return None


def _match_one(name: str, names: dict[int, str], threshold: float) -> int | None:
    """返回匹配到的 id；依次尝试精确、包含（较短一方至少 2 个字，取长度最接近者）、相似度。

    匹配从宽：宁可把基线的地点算作"库内"，也不冤枉它（对比结论更保守）。
    """
    q = norm(name)
    if not q:
        return None
    normed = {i: norm(n) for i, n in names.items()}
    for i, n in normed.items():
        if n == q:
            return i
    contains = [(i, n) for i, n in normed.items() if min(len(n), len(q)) >= 2 and (q in n or n in q)]
    if contains:
        return min(contains, key=lambda x: abs(len(x[1]) - len(q)))[0]
    best, best_r = None, 0.0
    for i, n in normed.items():
        r = difflib.SequenceMatcher(None, q, n).ratio()
        if r > best_r:
            best, best_r = i, r
    return best if best_r >= threshold else None
