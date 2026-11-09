"""E2 消融（docs/评测协议v2.md 第 2、7 节）：沿用 fair_compare 的采集、回放、重算和汇总，换成 E2 的方法清单。

用法与 fair_compare 相同，只是默认方法换成 E2 全部各档，design.json 记为 E2：
  python eval/ablation.py capture --root eval/runs/<目录> --cases-dir <用例目录>
  python eval/ablation.py replay  --root eval/runs/<目录>
  python eval/ablation.py assess  --root eval/runs/<目录>

除 C 外每档只比 B 改一个开关（见 fair_compare.PLAN_KWARGS 和 tests/test_e2_switches.py）；
C 同时关修复回路和空档补点，与 B、B−回路、B−补点合成四格一起报告。E4 的 --sweep 还没做。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import fair_compare  # noqa: E402

E2_METHODS = ["B", "Bcap", "C", "Bnoloop", "Bnofill", "Br1", "Br5", "E", "D", "Bplus", "Bnopop", "Bflat"]


def main(argv: list[str] | None = None) -> None:
    fair_compare.main(argv, experiment="E2", default_methods=E2_METHODS)


if __name__ == "__main__":
    main()
