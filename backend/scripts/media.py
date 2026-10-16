"""地点图片管理工具（规范见 docs/图片资源规范.md）。

  uv run python scripts/media.py list                     # 列出注册表里的人工图片与屏蔽项
  uv run python scripts/media.py check                    # 校验注册表（文件、大小、许可证字段、孤儿文件、名称）
  uv run python scripts/media.py show poi:0               # 查看某地点最终会展示的图片（会联网查高德）
  uv run python scripts/media.py add poi:0 ./a.jpg --author 张三 --license "CC BY 4.0" --source-url https://... [--caption 夜景]
  uv run python scripts/media.py remove poi:0 poi0-1a2b3c4d.jpg
  uv run python scripts/media.py hide poi:0 <图片 URL 或 12 位指纹>   # 屏蔽不合适的高德图片
  （高德动态餐厅 meal:<负数 id> 不在本地快照里，add / hide / unhide 需加 --name 餐厅名）
  uv run python scripts/media.py unhide poi:0 <URL 或指纹>
地点键：poi:<景点 id> 或 meal:<餐厅 id>（行程接口返回的 ref_id）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import media  # noqa: E402


def parse_key(value: str) -> tuple[str, int]:
    kind, _, identifier = value.partition(":")
    if kind not in {"poi", "meal"} or not identifier.lstrip("-").isdigit():
        raise argparse.ArgumentTypeError("地点键形如 poi:12 或 meal:-123")
    return kind, int(identifier)


def place_name(kind: str, identifier: int, override: str | None = None) -> str:
    if override:
        return override
    from app.place_details import resolve_place
    from app.schemas import PlaceReference
    place = resolve_place(PlaceReference(kind=kind, id=identifier))
    if place is None:
        raise SystemExit(f"找不到 {kind}:{identifier}。高德动态餐厅不在本地快照里，请加 --name 指定餐厅名称")
    return place.name


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    show = sub.add_parser("show")
    show.add_argument("key", type=parse_key)
    add = sub.add_parser("add")
    add.add_argument("key", type=parse_key)
    add.add_argument("file", type=Path)
    add.add_argument("--author", required=True)
    add.add_argument("--license", required=True)
    add.add_argument("--source-url", required=True)
    add.add_argument("--caption", default="")
    add.add_argument("--name", help="地点名称（高德动态餐厅需要手动指定）")
    rm = sub.add_parser("remove")
    rm.add_argument("key", type=parse_key)
    rm.add_argument("filename")
    for name in ("hide", "unhide"):
        p = sub.add_parser(name)
        p.add_argument("key", type=parse_key)
        p.add_argument("target", help="图片 URL 或 12 位指纹")
        p.add_argument("--name", help="地点名称（高德动态餐厅需要手动指定）")
    a = ap.parse_args()

    if a.cmd == "list":
        registry = media.load_registry()
        if not registry.places:
            print("注册表为空：目前只使用高德图片。")
        for key, entry in registry.places.items():
            print(f"{key}  {entry.name}  人工 {len(entry.photos)} 张  屏蔽 {len(entry.hide)} 张")
            for photo in entry.photos:
                print(f"    {photo.file}  {photo.caption or '-'}  {photo.author} · {photo.license}  {photo.source_url}")
    elif a.cmd == "check":
        problems = media.check_registry()
        print("\n".join(problems) if problems else "注册表检查通过。")
        sys.exit(1 if problems else 0)
    elif a.cmd == "show":
        result = media.get_place_media(*a.key)
        if result is None:
            raise SystemExit("找不到该地点")
        print(f"{result.name}  状态 {result.status}  共 {len(result.photos)} 张  查询于 {result.fetched_at or '-'}")
        for photo in result.photos:
            print(f"  [{photo.fingerprint}] {photo.source_label}  {photo.caption or '-'}\n      {photo.url}")
    elif a.cmd == "add":
        photo = media.add_manual_photo(*a.key, place_name(*a.key, a.name), a.file, caption=a.caption, author=a.author,
                                       license=a.license, source_url=a.source_url)
        print(f"已登记 files/{photo.file}")
    elif a.cmd == "remove":
        print("已移除" if media.remove_manual_photo(*a.key, a.filename) else "未找到该文件")
    else:
        value = media.set_hidden(*a.key, place_name(*a.key, a.name), a.target, hidden=a.cmd == "hide")
        print(f"{'已屏蔽' if a.cmd == 'hide' else '已取消屏蔽'}指纹 {value}")


if __name__ == "__main__":
    main()
