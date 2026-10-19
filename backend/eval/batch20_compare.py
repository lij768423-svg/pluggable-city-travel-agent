"""读取两批已完成的batch20产物；缓存诊断不联网，不更改排程。"""
import hashlib
import json
import re
import statistics
from pathlib import Path

from app.areas import area_index
from app.data import load_pois
from app.poi_semantics import evening_only, visit_open_min
from app.rules import LUNCH, activity_window
from app.schemas import UserProfile
from app.travel import Travel
from app.nodes.schedule import service_min
from eval.batch20 import PROFILES, run_metrics

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT/'data/eval-fixes-20261019'
places = {}
index = None


def rows(folder: Path) -> list[dict]:
    result = sorted([row for p in folder.glob('part-*.json')
                     for row in json.loads(p.read_text())['results']], key=lambda row: row['id'])
    assert len(result) == 20 and len({row['id'] for row in result}) == 20
    assert [(row['id'], row['form']) for row in result] == PROFILES
    assert not any('error' in row for row in result)
    return result


def night_events(row: dict) -> list[dict]:
    events = []
    for day in row['itinerary']['days']:
        previous = None
        for item in day['items']:
            if item['kind'] != 'poi':
                continue
            if item.get('evening'):
                events.append({'case': row['id'], 'day': day['day'], 'poi': item['name'],
                               'previous_poi': previous['name'] if previous else None,
                               'cross_area': previous is not None and index.area_of(previous['ref_id']) != index.area_of(item['ref_id']),
                               'mode': item['travel_mode'], 'minutes': item['travel_min']})
            previous = item
    return events


def cycle_events(row: dict) -> list[dict]:
    removed, found = set(), []
    # 同一轮的日志按实际动作顺序展开，也检查“片区移除后马上超预算换回”。
    names = {p.name for p in places.values()}
    for line in row['log']:
        for part in line.split('；'):
            additions = []
            if '超预算：用「' in part:
                new, old = re.search(r'用「(.+?)」替换门票更贵的「(.+?)」', part).groups()
                additions.append(new)
                removed.add(old)
            elif '补入' in part:
                suffix = part.split('补入', 1)[1]
                additions += [name for token in suffix.split('、')
                              if (name := re.sub(r'^第\s*\d+\s*天\s*', '', token.strip()).strip('「」')) in names]
            for name in additions:
                if name in removed:
                    found.append({'case': row['id'], 'name': name, 'log': part})
            removed.update(re.findall(r'(?:移除|最低的|较远的)「(.+?)」', part))
            if '移除求解器舍弃的：' in part:
                removed.update(part.split('移除求解器舍弃的：', 1)[1].split('、'))
            if '为用餐与交通腾出时间：移除' in part:
                removed.add(part.split('为用餐与交通腾出时间：移除', 1)[1])
    return found


def stats(source: list[dict]) -> dict:
    table = [run_metrics(row, index, places) for row in source]
    nights = [event for row in source for event in night_events(row)]
    result = {'约束通过': sum(row['report']['constraint_status'] == 'passed' for row in source),
              '硬违例': sum(len([v for v in row['report']['violations'] if v['severity'] == 'hard']) for row in source),
              '餐厅落实': sum(row['report']['arrangement_status'] == 'complete' for row in source),
              '体验完整': sum(row['report']['experience_status'] == 'complete' for row in source),
              '夜游补点总数': len(nights), '跨片区夜游补点': sum(event['cross_area'] for event in nights),
              '移除后回补事件': sum(len(cycle_events(row)) for row in source),
              '17点前晚间地点': sum(item['start'] < 1020 and evening_only(places[item['ref_id']])
                                 for row in source for day in row['itinerary']['days']
                                 for item in day['items'] if item['kind'] == 'poi'),
              '首个景点17点起的天数': sum(bool(visits := [i for i in day['items'] if i['kind'] == 'poi'])
                                    and visits[0]['start'] >= 1020
                                    for row in source for day in row['itinerary']['days'])}
    for key in ('每天片区数均值', '跨片区次数', '不顺路跳转次数', '被拆到多天的片区',
                '日内乘车分钟', '每天景点', '空档分钟', '兴趣命中率', '高德请求', '秒'):
        values = [m[key] for m in table if m[key] is not None]
        result[key if key.endswith('均值') else key + '均值'] = round(statistics.mean(values), 3)
    result['高德请求合计'] = sum(row['travel_stats']['live_calls'] for row in source)
    result['必去满足'] = sum(m['必去满足'] is True for m in table)
    result['减少跨区折返日志'] = sum('减少跨区折返' in line for row in source for line in row['log'])
    return result



def main() -> None:
    global places, index
    places = load_pois()
    index = area_index(places)
    before = rows(ROOT/'data/batch20-20261019')
    after = rows(RUN)
    old, new = stats(before), stats(after)
    lines = ['# F1–F4：20画像修复前后对照', '',
             '固定20个输入，每个版本每画像一次；本轮显式with_supplement、382个地点。模型选点和实时路线有波动，结果不作显著性结论。约束/空档使用各版本保存的报告；片区由当前同一索引重算。', '',
             '| 指标 | 基线 | 修复后 |', '|---|---:|---:|']
    lines += [f'| {key} | {old[key]} | {new[key]} |' for key in old]
    lines += ['', '## 逐份空档、约束与夜游补点', '',
              '| 画像 | 空档前→后(分) | 景点数前→后 | 约束 | 夜游总数前→后 | 跨区夜游前→后 |', '|---|---:|---:|---|---:|---:|']
    for left, right in zip(before, after):
        a, b = night_events(left), night_events(right)
        count = lambda row: sum(i['kind'] == 'poi' for day in row['itinerary']['days'] for i in day['items'])
        lines.append(f"| {left['id']} | {left['report']['totals']['idle_min']:g}→{right['report']['totals']['idle_min']:g} | {count(left)}→{count(right)} | {right['report']['constraint_status']} | {len(a)}→{len(b)} | {sum(e['cross_area'] for e in a)}→{sum(e['cross_area'] for e in b)} |")
    lines += ['', '## 移除后回补事件', '', '基线：', '```json', json.dumps([event for row in before for event in cycle_events(row)], ensure_ascii=False, indent=2), '```',
              '修复后：', '```json', json.dumps([event for row in after for event in cycle_events(row)], ensure_ascii=False, indent=2), '```']
    (RUN/'batch-compare.md').write_text('\n'.join(lines)+'\n')
    (RUN/'compare-metrics.json').write_text(json.dumps({'before': old, 'after': new,
        'night_before': [e for row in before for e in night_events(row)],
        'night_after': [e for row in after for e in night_events(row)]}, ensure_ascii=False, indent=2))

    diagnostics = []
    for row in before + after:
        if not row['id'].startswith(('09-', '19-')):
            continue
        profile = UserProfile.model_validate(row['profile'])
        for day in row['itinerary']['days']:
            if not day['items'] or day['items'][0]['kind'] != 'meal':
                continue
            visits = [i for i in day['items'] if i['kind'] == 'poi']
            if not visits:
                continue
            first = places[visits[0]['ref_id']]
            route = Travel('amap', profile.transport, live_budget=0)
            route.set_day(profile, day['day'])
            direct = route.detail(profile.hotel.lat, profile.hotel.lon, first.lat, first.lon, live=False)
            window = activity_window(profile, day['day'])
            start = max(window['start'] + direct.minutes, visit_open_min(first))
            diagnostics.append({'version': 'before' if row in before else 'after', 'case': row['id'], 'day': day['day'],
                'departure_min': window['start'], 'first_item': day['items'][0]['name'], 'first_item_start': day['items'][0]['start'],
                'first_poi': first.name, 'open_min': first.open_min, 'close_min': first.close_min,
                'service_min': service_min(first, profile.pace),
                'cached_direct_leg': direct.model_dump(mode='json'), 'direct_visit_start': start,
                'pre_lunch_trigger': start >= LUNCH[0] - 15 or (window['start'] >= LUNCH[0] - 60 and start + service_min(first, profile.pace) > LUNCH[1]),
                'live_calls': route.live_calls,
                'other_pois': [{'name': i['name'], 'open_min': places[i['ref_id']].open_min, 'start': i['start']} for i in visits[1:]]})
    (RUN/'f5-diagnostics.json').write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    runtime = json.loads((RUN/'runtime-before.json').read_text())
    changed = [path for path, digest in runtime['source_sha256'].items()
               if hashlib.sha256((ROOT/path).read_bytes()).hexdigest() != digest]
    # 正式批次进程启动于6d26d61；后续只修了其关闭的refine分支，所有实际执行的路径一致。
    from app.graph import run_plan
    assert set(changed) <= {'backend/app/refine.py'} and run_plan.__kwdefaults__['enable_refine'] is False, changed
    final = json.loads((RUN/'runtime-final-before.json').read_text())
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == digest for path, digest in final['source_sha256'].items()), '最终全量验收期间源码发生变化'
    print(json.dumps({'before': old, 'after': new, 'f5': diagnostics,
        'pipeline_version': runtime['git'], 'final_code_version': final['git'],
        'only_disabled_refine_changed': changed, 'final_sources_unchanged': True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
