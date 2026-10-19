"""自有补充资料的稳定登记、不可变发布目录，以及规划任务固定使用的景点目录版本。

运行中规划只通过 PlanningCatalog 读景点：任务开始时取一次当前发布版并固定，
之后发布新版或归档地点只影响下一次准备/生成，不改变进行中的任务和会话。
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unicodedata
from collections.abc import Iterator

from . import config
from .planning_context import poi_catalog
from .poi_admission import admitted_poi, persistence_issues, publication_issues
from .schemas import POI, CatalogSnapshot, SupplementRecord

FIRST_ID = 1_000_000
MAX_ID = 2**53 - 1


def normalize_alias(name: str) -> str:
    value = unicodedata.normalize("NFKC", name).casefold()
    return re.sub(r"[\W_]+", "", value)


def _encode(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class SupplementCatalog:
    """register仅维护审阅工作区；publish完整审核后一次切换指针。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.versions = self.root / "versions"
        self.versions.mkdir(exist_ok=True, mode=0o700)
        self.database = self.root / "registry.sqlite3"

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        # 先以0600创建，避免SQLite按进程umask创建可供其他用户读取的资料库。
        fd = os.open(self.database, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.database, timeout=30)
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS records (
                internal_id INTEGER PRIMARY KEY,
                canonical_key TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL)""")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, records: list[SupplementRecord]) -> list[SupplementRecord]:
        # 先检查整批，不把API发现对象、未知来源或半批事务写进长期目录。
        clean = [SupplementRecord.model_validate(r.model_dump()) for r in records]
        if len({r.canonical_key for r in clean}) != len(clean):
            raise ValueError("同一批canonical_key重复")
        for record in clean:
            if issues := persistence_issues(record):
                raise ValueError("; ".join(issues))
        registered = []
        with self._database() as db:
            for record in clean:
                row = db.execute("SELECT internal_id FROM records WHERE canonical_key=?",
                                 (record.canonical_key,)).fetchone()
                if row:
                    internal_id = row[0]
                else:
                    internal_id = db.execute(
                        "SELECT COALESCE(MAX(internal_id), ?) + 1 FROM records",
                        (FIRST_ID - 1,),
                    ).fetchone()[0]
                if internal_id > MAX_ID:
                    raise ValueError("补充ID超出JS安全整数")
                if record.internal_id is not None and record.internal_id != internal_id:
                    raise ValueError("内部ID与registry不一致，不能自行指定或更换")
                record.internal_id = internal_id
                db.execute("""INSERT INTO records VALUES (?, ?, ?)
                    ON CONFLICT(canonical_key) DO UPDATE SET payload=excluded.payload""",
                    (internal_id, record.canonical_key, record.model_dump_json()))
                registered.append(record)
        return registered

    def records(self) -> list[SupplementRecord]:
        with self._database() as db:
            rows = db.execute("SELECT payload FROM records ORDER BY internal_id").fetchall()
        return [SupplementRecord.model_validate_json(row[0]) for row in rows]

    def publish(self, *, as_of: date, base_digest: str) -> CatalogSnapshot:
        if not base_digest.strip():
            raise ValueError("发布必须记录原库摘要")
        with (self.root / "publish.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = [r for r in self.records() if r.state == "published"]
            groups: dict[str, str] = {}
            issues = []
            for record in records:
                issues.extend(f"{record.canonical_key}: {x}" for x in publication_issues(record, as_of=as_of))
                group = record.experience_group or record.canonical_key
                if group in groups:
                    issues.append(f"游览组重复: {groups[group]} / {record.canonical_key}")
                groups[group] = record.canonical_key
            if issues:
                raise ValueError("\n".join(issues))
            payload = _encode({
                "schema_version": 1, "base_digest": base_digest,
                "published_on": as_of.isoformat(),
                "records": [r.model_dump(mode="json") for r in records],
            })
            digest = hashlib.sha256(payload).hexdigest()
            snapshot = CatalogSnapshot(digest=digest, payload=payload.decode())
            version = self.versions / f"{digest}.json"
            if version.exists():
                if version.read_bytes() != payload:
                    raise ValueError("已有版本摘要不一致")
            else:
                # link使相同摘要版本永不被覆盖；进程崩溃也不暴露半写版本。
                staged = self._stage(self.versions, payload)
                try:
                    os.link(staged, version)
                finally:
                    staged.unlink(missing_ok=True)
                _fsync_dir(self.versions)
            pointer = self._stage(self.root, _encode({"schema_version": 1, "digest": digest}))
            try:
                os.replace(pointer, self.root / "manifest.json")
                _fsync_dir(self.root)
            finally:
                pointer.unlink(missing_ok=True)
            return snapshot

    @staticmethod
    def _stage(parent: Path, payload: bytes) -> Path:
        fd, filename = tempfile.mkstemp(prefix=".publish-", dir=parent)
        path = Path(filename)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def load(self, digest: str | None = None) -> CatalogSnapshot:
        return read_snapshot(self.root, digest)


def published_digest(root: Path) -> str | None:
    """只读当前发布指针；尚未发布时返回None，不创建目录。"""
    try:
        return json.loads((Path(root) / "manifest.json").read_text())["digest"]
    except FileNotFoundError:
        return None


def read_snapshot(root: Path, digest: str | None = None) -> CatalogSnapshot:
    if digest is None:
        digest = json.loads((Path(root) / "manifest.json").read_text())["digest"]
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("无效目录摘要")
    payload = (Path(root) / "versions" / f"{digest}.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("目录摘要校验失败")
    return CatalogSnapshot(digest=digest, payload=payload.decode())


class CatalogUnavailable(ValueError):
    """任务记录的目录版本无法在本进程原样复现；拒绝继续，而不是换成其他版本。"""


@dataclass(frozen=True)
class PlanningCatalog:
    """一次准备/生成/修改固定使用的景点集合；pois 视为只读，不得原地修改。"""
    version: str
    base_digest: str
    supplement_digest: str | None = None
    pois: dict[int, POI] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def build(cls, pois: dict[int, POI], base_digest: str, supplement_digest: str | None = None) -> "PlanningCatalog":
        # 原库版本号等于原库摘要，与此前 data_version 的用途一致；叠加补充目录后另算组合摘要。
        version = base_digest if supplement_digest is None else hashlib.sha256(
            f"{base_digest}+{supplement_digest}".encode()).hexdigest()
        return cls(version=version, base_digest=base_digest, supplement_digest=supplement_digest, pois=pois)

    def reference(self) -> dict[str, str | None]:
        """写入规划结果的目录身份；会话修改和展示按它解析，不保存景点内容本身。"""
        return {"version": self.version, "base_digest": self.base_digest, "supplement_digest": self.supplement_digest}


_CATALOGS: dict[str, PlanningCatalog] = {}
_CATALOGS_LOCK = threading.Lock()


def register_catalog(catalog: PlanningCatalog) -> PlanningCatalog:
    """登记后同版本在本进程内始终解析为同一对象；同版本不同内容视为篡改。"""
    with _CATALOGS_LOCK:
        existing = _CATALOGS.setdefault(catalog.version, catalog)
    if existing is not catalog and existing.pois != catalog.pois:
        raise CatalogUnavailable("同一目录版本出现不同内容")
    return existing


def base_catalog() -> PlanningCatalog:
    from .data import base_digest, load_base_pois
    digest = base_digest()
    with _CATALOGS_LOCK:
        found = _CATALOGS.get(digest)
    return found or register_catalog(PlanningCatalog.build(load_base_pois(), digest))


def supplement_pois(snapshot: CatalogSnapshot) -> dict[int, POI]:
    """已发布记录逐日准入后转为可排程POI；核对日取发布日，同一版本内容固定、可按版本复现。"""
    as_of = date.fromisoformat(json.loads(snapshot.payload)["published_on"])
    pois = (admitted_poi(record, as_of=as_of) for record in snapshot.records())
    return {poi.id: poi for poi in pois if poi is not None}


def supplement_catalog(digest: str, root: Path | None = None) -> PlanningCatalog:
    base = base_catalog()
    version = PlanningCatalog.build({}, base.base_digest, digest).version
    with _CATALOGS_LOCK:
        found = _CATALOGS.get(version)
    if found is not None:
        return found
    snapshot = read_snapshot(root or config.POI_SUPPLEMENT_DIR, digest)
    if json.loads(snapshot.payload)["base_digest"] != base.base_digest:
        raise CatalogUnavailable("补充目录发布时的原库摘要与当前原库不一致，须重新发布")
    extra = supplement_pois(snapshot)
    if extra.keys() & base.pois.keys():
        raise CatalogUnavailable("补充目录ID与原库冲突")
    pois = {**base.pois, **extra} if extra else base.pois
    return register_catalog(PlanningCatalog.build(pois, base.base_digest, digest))


def published_catalog() -> PlanningCatalog:
    """进程当前发布版：base_only 恒为原库；with_supplement 读发布指针，尚未发布则仍为原库。"""
    mode = config.POI_CATALOG_MODE
    if mode == "base_only":
        return base_catalog()
    if mode != "with_supplement":
        raise ValueError(f"未知景点目录模式：{mode}")
    digest = published_digest(config.POI_SUPPLEMENT_DIR)
    return base_catalog() if digest is None else supplement_catalog(digest)


def current_catalog() -> PlanningCatalog:
    return poi_catalog.get() or published_catalog()


def resolve_catalog(reference: dict | None) -> PlanningCatalog:
    """按规划结果记录的目录身份解析；没有记录的旧结果由原库生成，按原库处理。"""
    base = base_catalog()
    if not reference:
        return base
    version = reference.get("version")
    with _CATALOGS_LOCK:
        found = _CATALOGS.get(version)
    if found is not None:
        if found.reference() != {key: reference.get(key) for key in ("version", "base_digest", "supplement_digest")}:
            raise CatalogUnavailable("目录版本与记录不一致")
        return found
    if reference.get("base_digest") != base.base_digest:
        raise CatalogUnavailable("原库已变化，不能按旧目录版本继续")
    supplement = reference.get("supplement_digest")
    catalog = base if supplement is None else supplement_catalog(supplement)
    if catalog.version != version:
        raise CatalogUnavailable("目录版本与记录不一致")
    return catalog


@contextmanager
def use_catalog(catalog: PlanningCatalog) -> Iterator[PlanningCatalog]:
    token = poi_catalog.set(catalog)
    try:
        yield catalog
    finally:
        poi_catalog.reset(token)


def result_catalog(result: dict):
    """会话修改、校验和展示一律用该结果自己的目录版本，不受当前发布版或调用方上下文影响。"""
    return use_catalog(resolve_catalog(result.get("poi_catalog")))
