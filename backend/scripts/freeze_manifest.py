from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def create_manifest() -> dict:
    root = Path(__file__).resolve().parents[2]
    paths = list((root / "backend" / "app").rglob("*.py"))
    for directory in ("eval", "scripts", "tests", "baselines"):
        paths += list((root / "backend" / directory).rglob("*.py"))
    paths += list((root / "backend" / "app" / "assets").rglob("*.json"))
    paths += list((root / "backend" / "app" / "assets").rglob("*.md"))
    paths += list((root / "backend" / "eval" / "cases").glob("*.json"))
    paths += list((root / "backend" / "eval").glob("cases*.json"))
    paths += list((root / "backend" / "eval").glob("*.html"))
    paths += list((root / "frontend" / "src").rglob("*.vue"))
    paths += list((root / "frontend" / "src").rglob("*.ts"))
    paths += list((root / "frontend" / "src").rglob("*.css"))
    paths += list((root / "data" / "hangzhou").glob("*.parquet"))
    paths += [root / "data" / "travel_calibration.json"]
    paths += list((root / "frontend").glob("*.json"))
    paths += list((root / "frontend").glob("*.ts"))
    paths += [root / "backend" / "pyproject.toml", root / "frontend" / "index.html"]
    paths += [root / "backend" / "uv.lock", root / "frontend" / "package-lock.json"]
    digests = {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(set(paths)) if path.is_file()}
    return {"created_at":datetime.now(timezone.utc).isoformat(),"python":platform.python_version(),
            # 从压缩包解出的公开版不是 git 检出，提交号记 unknown，文件摘要照样能认出版本
            "head":subprocess.run(["git","rev-parse","HEAD"],cwd=root,capture_output=True,text=True).stdout.strip() or "unknown",
            "working_tree_hash":hashlib.sha256(json.dumps(digests,sort_keys=True).encode()).hexdigest(),
            "dependencies":{name:importlib.metadata.version(name) for name in ("pydantic","ortools","langgraph","pandas")},
            "files":digests,"external_api_responses_included":False,
            "scope":"Code and data identities only; no credentials or automatic API snapshot collection"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as output:
        json.dump(create_manifest(),output,ensure_ascii=False,indent=2)
    print(args.output)


if __name__ == "__main__":
    main()
