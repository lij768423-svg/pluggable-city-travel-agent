"""比较7a2aeb5交接结果与不折返补点结果；统一指标口径，不改历史结果。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

from app.areas import area_fill_conflicts, area_index
from app.data import load_pois
from app.schemas import Itinerary
from eval.batch20 import run_metrics


def read(folder):
    rows = [row for suffix in ('a', 'b') for row in json.loads((folder / f'part-{suffix}.json').read_text())['results']]
    assert len(rows) == len({row['id'] for row in rows}) == 20, '须有20份唯一画像'
    assert not any('error' in row for row in rows), '存在失败画像，须先处理'
    return {row['id']: row for row in rows}


def compare(baseline: Path, after: Path, output: Path):
    left, right = read(baseline), read(after)
    assert left.keys() == right.keys()
    assert all(left[key]['form'] == right[key]['form'] for key in left), '画像输入变化'
    assert all(left[key]['poi_catalog'] == right[key]['poi_catalog'] for key in left), '目录版本变化'
    zones, places = area_index(), load_pois()

    def summary(rows):
        metrics = [run_metrics(row, zones, places) for row in rows.values()]
        violations = Counter(v['code'] for row in rows.values() for v in row['report']['violations'])
        totals = {
            '画像数': len(rows),
            'area_scattered次数': violations['area_scattered'],
            'area_scattered画像数': sum(any(v['code'] == 'area_scattered' for v in row['report']['violations']) for row in rows.values()),
            '折返或超过2片区天数': sum(len(area_fill_conflicts(Itinerary.model_validate(row['itinerary']), places)) for row in rows.values()),
            '空档分钟均值': round(statistics.mean(m['空档分钟'] for m in metrics), 3),
            '约束通过': sum(row['report']['constraint_status'] == 'passed' for row in rows.values()),
            '硬违例': sum(v['severity'] == 'hard' for row in rows.values() for v in row['report']['violations']),
            '餐厅落实': sum(row['report']['arrangement_status'] == 'complete' for row in rows.values()),
            '体验完整': sum(row['report']['experience_status'] == 'complete' for row in rows.values()),
            '完整可执行': sum(row['report']['complete_executable'] for row in rows.values()),
            '必去满足': sum(m['必去满足'] is True for m in metrics),
            '高德请求合计': sum((row.get('travel_stats') or {}).get('live_calls', 0) for row in rows.values()),
            '模型实际调用': sum(row['usage'].get('calls', 0) for row in rows.values()),
            '模型缓存命中': sum(row['usage'].get('cached_calls', 0) for row in rows.values()),
        }
        for field in ('日内乘车分钟', '跨片区次数', '每天景点', '兴趣命中率', '秒'):
            values = [m[field] for m in metrics if m[field] is not None]
            totals[field + '均值'] = round(statistics.mean(values), 3) if values else None
        return totals

    old, new = summary(left), summary(right)
    manifest = json.loads((after / 'runtime-start.json').read_text())
    root = Path(__file__).resolve().parents[2]
    assert all(hashlib.sha256((root / path).read_bytes()).hexdigest() == sha for path, sha in manifest['app_hashes'].items()), '评估期间业务源码变化'
    assert all(hashlib.sha256((root / path).read_bytes()).hexdigest() == sha for path, sha in manifest['baseline_hashes'].items()), '历史基线变化'
    payload = {'before': old, 'after': new, 'runtime_commit': manifest['head'], 'baseline': str(baseline),
               'app_unchanged': True, 'baseline_unchanged': True}
    (after / 'compare-metrics.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    lines = ['# 片区不折返 · 20画像前后对照', '',
             f'基线为7a2aeb5交接的`data/eval-fixes-20261019/part-a.json`与`part-b.json`；本轮业务版本`{manifest["head"][:7]}`。',
             '两侧相同20个输入、相同382地点补充目录，完整流程、修复开启、精修关闭；模型默认缓存可复用，路线按本轮高德响应。每画像一次，只作现象对照，不作显著性或单因果结论。',
             '空档为统一校验器`totals.idle_min`，先按每份完整行程汇总再对20份取均值；area_scattered为保存的最终报告中违例条数（一般每个问题天一条）。另按实际POI顺序统计折返或超过2片区天数。', '',
             '| 指标 | 7a2aeb5结果 | 本轮 | 差值 |', '|---|---:|---:|---:|']
    for key in old:
        delta = round(new[key] - old[key], 3) if old[key] is not None and new[key] is not None else None
        lines.append(f'| {key} | {old[key]} | {new[key]} | {delta} |')
    lines += ['', '## 逐份片区与空档', '', '| 画像 | area_scattered 前→后 | 空档分钟 前→后 | 初始选点相同 |', '|---|---:|---:|---|']
    for key in sorted(left):
        counts = [sum(v['code'] == 'area_scattered' for v in rows[key]['report']['violations']) for rows in (left, right)]
        idle = [rows[key]['report']['totals'].get('idle_min', 0) for rows in (left, right)]
        lines.append(f'| {key} | {counts[0]}→{counts[1]} | {idle[0]}→{idle[1]} | {left[key]["initial_selected_ids"] == right[key]["initial_selected_ids"]} |')
    lines += ['', '历史基线摘要与本轮评估开始时的业务Python源码摘要已复核一致，未覆盖基线。证据完整性仍按保存报告呈现；partial不等于完整可执行。']
    output.write_text('\n'.join(lines) + '\n')
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    compare(args.baseline, args.after, args.output)
