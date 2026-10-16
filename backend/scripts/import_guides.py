from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.guides import eligible, validate_corpus
from app.schemas import GuideCorpus


def main() -> None:
    parser = argparse.ArgumentParser(description="导入已获得使用依据的结构化攻略；不登录、不抓取笔记")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    corpus = GuideCorpus.model_validate_json(args.input.read_text(encoding="utf-8"))
    errors = validate_corpus(corpus)
    if errors:
        raise SystemExit("\n".join(errors))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(corpus.model_dump_json(indent=2))
    print(json.dumps({"samples": len(corpus.samples), "eligible": sum(eligible(sample, corpus) for sample in corpus.samples),
                      "quarantined": [sample.id for sample in corpus.samples if not eligible(sample, corpus)]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
