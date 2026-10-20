"""从冻结的C4证据汇总南京试点报告；失败留在分母，不联网。"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.areas import area_fill_conflicts,area_index
from app.poi_catalog import published_catalog,use_catalog
from app.schemas import Itinerary
from eval.batch20 import run_metrics


def read(path:Path):
    return json.loads(path.read_text())


def summary(rows:list[dict]) -> dict:
    good=[row for row in rows if 'error' not in row]
    with use_catalog(published_catalog('nanjing')) as catalog:
        areas=area_index()
        metrics=[run_metrics(row,areas,catalog.pois) for row in good]
        counts=Counter(v['code'] for row in good for v in row['report']['violations'])
        result={'画像数':len(rows),'成功回放':len(good),'失败':len(rows)-len(good),
                '约束通过':sum(row['report']['constraint_status']=='passed' for row in good),
                '硬违例':sum(v['severity']=='hard' for row in good for v in row['report']['violations']),
                '餐厅落实':sum(row['report']['arrangement_status']=='complete' for row in good),
                '体验完整':sum(row['report']['experience_status']=='complete' for row in good),
                '完整可执行':sum(row['report']['complete_executable'] for row in good),
                '必去满足':sum(item['必去满足'] is True for item in metrics),
                'area_scattered次数':counts['area_scattered'],
                '折返或超过2片区天数':sum(len(area_fill_conflicts(Itinerary.model_validate(row['itinerary']),catalog.pois)) for row in good),
                '逐字段重放一致':sum(all(row.get('capture_equal_fields',{}).values()) and len(row.get('capture_equal_fields',{}))==9 for row in good),
                '全部HTTP记录消费完毕':sum(row.get('all_http_records_consumed') is True for row in good),
                '重放实际联网请求':sum(row.get('actual_network_requests',0) for row in good)}
        for field in ('日内乘车分钟','跨片区次数','每天景点','兴趣命中率','空档分钟'):
            values=[metric[field] for metric in metrics if metric[field] is not None]
            result[field+'均值']=round(statistics.mean(values),3) if values else None
        return result


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--hangzhou',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=args.root
    replay=read(root/'batch20/replay.json')
    capture=read(root/'batch20/capture.json')
    if len(replay['results'])!=len({row['id'] for row in replay['results']}):
        raise ValueError('画像编号重复')
    latest=summary(replay['results'])
    baseline=read(args.hangzhou)['after']
    audit=read(root/'audit-summary.json')
    cityroot=root.parent/'nanjing'
    calibration=read(cityroot/'travel_calibration.json')
    invariants=read(root/'hangzhou-invariants.json')
    fixed=read(root/'hangzhou-area32-compare.json')
    smoke=read(root/'api-mcp-smoke.json')
    models=read(cityroot/'model-assets-run.json')
    # 包括停止保留的首轮；复用记录的 actual_http_attempts 为0，不重复算外部请求。
    http_actual=0
    sources=[]
    for directory in ('batch20-initial','batch20'):
        for path in sorted((root/directory).glob('*/amap.jsonl')):
            entries=[json.loads(line) for line in path.read_text().splitlines()]
            count=sum(entry.get('actual_http_attempts',entry['http_attempts']) for entry in entries)
            http_actual+=count
            sources.append({'file':str(path.relative_to(root)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'actual_http_attempts':count})
    calibration_http=sum(run['http_requests'] for run in read(cityroot/'calibration-v1/collection.json')['sessions'])
    old_model_actual=sum(item.get('usage',{}).get('calls',0) for path in (root/'batch20-initial').glob('*/llm/*.json') for item in [read(path)])
    selection_actual=old_model_actual+sum(row.get('model_request_attempts',0) for row in capture['results'])
    tag_attempts=models['stages']['tags']['request_attempts']+sum(session.get('tags',{}).get('request_attempts',0) for session in models.get('sessions',[]))
    stay=read(Path(__file__).resolve().parent.parent/'app/assets/cities/nanjing/stay_reference.json')
    # 独立的400预探一次，保留budget.json中的记录；不根据缺失usage估算账单金额。
    model_attempts=tag_attempts+1+stay['run_stats']['actual_calls']+selection_actual
    result={'created_at':datetime.now(timezone.utc).isoformat(),'nanjing':latest,'hangzhou':baseline,
            'capture_actual_http_including_interrupted':http_actual,'calibration_actual_http':calibration_http,
            'total_actual_http':http_actual+calibration_http,'selection_model_attempts_including_initial':selection_actual,
            'all_model_attempts_including_400':model_attempts,'transport_sources':sources}
    (root/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    pytest=(root/'pytest-full-final.log').read_text().splitlines()
    pytest_result=next((line for line in reversed(pytest) if ' passed' in line and ' in ' in line),'全量仍在运行，尚未确认')
    lines=['# 城市可插拔 C4：南京首轮试点（2026-10-19）','',
       '执行：Codex，经 Mac SSH；权威仓库路径见内部PROGRESS.md。开工时本地 main 为599f217，C1/C2已验收，C3前端选择城市尚未实现；本轮使用独立feat/m8-city-c4工作树。所有数字来自保存的脚本和证据，未推送。','',
       '## 当前结论','',
       f"最终冻结目录下，20画像回放成功{latest['成功回放']}/20，约束通过{latest['约束通过']}/20，硬违例{latest['硬违例']}；逐字段重放一致{latest['逐字段重放一致']}/20，HTTP记录完整消费{latest['全部HTTP记录消费完毕']}/20。断网评测实际外部请求为0。",
       '本轮落实城市配置、数据生成、交通校准、资料叠加、API/MCP入口和可复现的完整流程试点。南京只在私网测试实例启用。人工/现场核对尚未开展，C4的人工核对与工时验收仍待完成，不能据20组约束通过认定全部数据可靠或允许生产上线。','',
       '## 数据和城市资产','',
       '- ChinaTravel Sandbox 2026.08.2中文表（CC BY 4.0）：景点323、餐厅468、住宿373；保留原始Parquet、下载时间、SHA256及数据源。编号仅在南京内唯一。',
       '- 南京配置独立使用data/nanjing、app/assets/cities/nanjing及本地校准；默认启用列表保持杭州，测试实例显式加入南京。粗筛经纬度矩形有外扩，不代表行政边界；住宿坐标只是历史参考起点。',
       f"- 自动聚类{smoke['area_count']}个片区，依据成员起草{smoke['named_areas']}个主要片区展示名。聚类阈值和排程算法沿用杭州；片区草名待用户审阅。夜游候选为老门东、南京1912、夫子庙步行街、狮子桥；商铺时段和具体活动仍需确认。",
       '- 标签323条，抽样人审表32条；独立停留参考每点3个样本，81次请求完成，无失败，543.784秒；响应模型MiniMax-M3.1-Flash-Preview。引用为模型估计，未作为官方时长和人类审阅结果写入。','',
       '## 来源审阅和数据问题','',
       f"AI共整理{audit['ai_review_unique_pois']}点、{audit['ai_review_entries']}条字段/范围叠加，18个地点暂缓自动推荐。重点包括：",
       '- 总统府历史价格0元改为官网当前35元；补齐南京博物院、总统府、纪念馆的常规闭馆/预约/最晚入馆提示。季节时段无法完整表达时采用保守交集，并在说明里标记。',
       '- 南京价格手册的政府指导最高限价用于预算参考，价格核验状态继续保留“未核实实售价格”；不同于官网直接观察到的实售价格。手册2026年1月发布，所依据政策截至2025年12月，本轮2026-10-19下载并查看纸页13–14。',
       '- 聚合景区、父子项、街区地标重复、跨午夜时段、游船零票价等被拆开记录；单桥/亭/牌坊停留修正为项目估计，保留待核提示。',
       '- 濮塘风景区（原始id227）来自马鞍山市花山区，已从南京自动推荐中隔离；原始表与ID保留作问题证据。行政归属依据2026-03-09花山区政府信息，核对日期2026-10-19。','',
       '| 待核旗标 | 点位数 |','|---|---:|']
    labels={'model_duration_disagreement_not_ground_truth':'模型时长与原表显著分歧（不是真值错误率）','model_reference_low_confidence':'三采样参考低置信','zero_price_scope_needs_review':'零票价范围待核','landmark_duration_scope_needs_review':'地标时长范围待核','outside_city_quarantined':'跨城混入，已隔离','cross_midnight_or_invalid_hours':'跨午夜或不合法时段','type_scope_needs_review':'分类范围待核'}
    lines += [f'| {labels.get(key,key)} | {value} |' for key,value in audit['flag_counts'].items()]
    lines += ['',f"共{audit['issue_pois']}个点位带至少一种旗标，旗标互有重叠；清单为data/nanjing/audit-v1/issues.csv。人工核对0点/0分钟，人工准确率与完整城市核对工时均未实测。AI工作包括代码、检索和接口等待，不能折算人工工时；后续先处理旗标清单，再完成32条标签抽检及高频点位范围核验。",'',
       '## 交通校准','',
       '固定取样框为原始id前40点，按直线0.5–3、3–8、8–15、15–35公里各5对分层，另8对近距离步行。先固定设计再请求，不依据规划结果挑样；实际48次HTTP（上限90，含重试）。',
       '| 模型 | 截距a | 直线公里系数b | 五折绝对误差均值 | 样本 |','|---|---:|---:|---:|---:|']
    for key,label in [('drive_min','驾车分钟'),('transit_min','公交分钟'),('taxi_cost','打车费用元')]:
        c=calibration[key]
        lines.append(f"| {label} | {c['a']} | {c['b']} | {c['cv_mae']} | {c['n']} |")
    lines += ['',f"步行绕行中位数{calibration['walk_detour']}。校准样本为单一时点API估计，五折划分共享部分端点，不能推导未来拥堵或独立城市泛化误差。实际规划仍优先取路线响应，标定模型只用于既有粗筛/降级路径。",'',
       '## 20画像与杭州参照','',
       '画像在采集前冻结：6个历史住宿参考起点，1–4天、公交/打车、不同预算与节奏、情侣/独行/老人/亲子、2个必去、周一闭馆及夜游场景。先采集模型/高德响应，随后从空的派生缓存按原顺序断网重放整条图，包括召回、选点、分天排程、餐饮、修复和夜游；精修保持默认关闭。',
       '冻结模型回答与请求哈希、目录/城市数据/业务源码SHA256。比对画像、选点、舍弃、初始选点、排程尝试、修复移除、目录引用、报告、行程9组字段，仅忽略生成/抓取时间戳。历史额度消耗会模拟重放以保持降级分支，重放统计中的live_calls代表录制调用量，实际联网数另列为0。',
       '杭州参照取2026-10-19片区不折返已保存的20画像after结果（业务96af85f）；C1/C2已验证行为等价。本轮南京的点位、酒店与兴趣主题不同，且低档思考与杭州缓存选点条件不同，仅作描述性比较，不能归因为城市参数或算法效果。','',
       '日内乘车分钟和空档均先按每份完整行程合计，再对画像取均值；日内乘车剔除每日首段与返程，数字不是每天或每段的平均。兴趣均值只统计填写兴趣且存在游览点的画像。','',
       '| 指标 | 杭州参照 | 南京冻结回放 |','|---|---:|---:|']
    keys=['画像数','约束通过','硬违例','餐厅落实','体验完整','完整可执行','必去满足','area_scattered次数','折返或超过2片区天数','日内乘车分钟均值','跨片区次数均值','每天景点均值','兴趣命中率均值','空档分钟均值']
    lines += [f"| {key} | {baseline.get(key,'—')} | {latest.get(key,'—')} |" for key in keys]
    lines += ['', '片区/交通是否达到“相近”须结合此表与逐日路线判断；原计划没有量化容差，本轮不事后设一个通过阈值。完整可执行受日期、门票、预约、餐厅和路线证据影响，应沿用报告状态。','',
       '江宁徒步画像12仍有一顿午餐未落实，并保留191分钟空档；实际餐厅落实19/20。南京空档均值82.4分钟，杭州68.35分钟，增加14.05分钟；日内乘车24.65对28.4分钟。不能只报告改善项。','',
       '## 调用与验证','',
       f'- 模型：最初预计118次；包含2个截断输出与两次400请求的恢复估计122次。实际总请求尝试{model_attempts}次（含400），其中选点{selection_actual}次、独立停留81次。HTTP次数与usage不能直接换算账单金额。',
       f'- 高德：正式20份来源采集及已中止初次采集合计实际{http_actual}次，加校准{calibration_http}次，总计{http_actual+calibration_http}次；复用响应不重复计网络请求。每画像逻辑额度120，总预算仍受开工时2400+90约束。',
       '- 初始标签12次返回中2个截断，立即停止且禁止自动重试。只复用有效10批、补7批；M3.1不能禁用思考，官方文档与400返回已确认，显式低档思考用于本轮生成。默认生产参数及缓存键不变。',
       '- 首轮全量600 passed/3 failed（409.92s），3个流式测试替身缺可选effort字段，已用兼容默认值修复；新增复用测试的隔离key fixture也已修复，所有失败日志保留。',
       f'- 最终全量：{pytest_result.strip("=").strip()}。',
       f"- 杭州固定选点{fixed['cases']}组除耗时外逐字段一致；原库摘要{invariants['hangzhou_base_digest']}，21份既有数据/规则资产未变。",
       '- 南京API meta/pois均200；临时独立MCP prepare返回prepared、30候选，模型与高德调用均0；城市内编号解析与默认关闭行为均有回归。公开导出和测试服务现场结果见后续进度日志。','',
       '## 复现','',
       '从backend目录执行（data不进git，恢复本轮数字应使用已冻结的data而非重新抓取上游）：','',
       '```bash',
       'uv run python scripts/fetch_data.py --city 南京',
       'uv run python scripts/city_model_assets.py --city nanjing --dry-run',
       '# 以下采集命令有外部调用，先检查预算与已有检查点',
       'uv run python scripts/city_model_assets.py --city nanjing --stage both',
       'uv run python scripts/collect_city_calibration.py --city nanjing --dry-run',
       'uv run python scripts/collect_city_calibration.py --city nanjing',
       'uv run python scripts/curate_nanjing_c4.py',
       'uv run python scripts/audit_city_data.py --city nanjing',
       'TTA_CITIES=hangzhou,nanjing uv run python -m eval.city_batch20 capture --root ../data/city-c4-20261019/batch20',
       'TTA_CITIES=hangzhou,nanjing uv run python -m eval.city_batch20 replay --root ../data/city-c4-20261019/batch20',
       'TTA_CITIES=hangzhou,nanjing uv run python -m eval.city_batch20 assess --root ../data/city-c4-20261019/batch20',
       'uv run python -m eval.city_c4_report --root ../data/city-c4-20261019 --hangzhou ../data/area-no-return-20261019/compare-metrics.json --output ../docs/城市可插拔C4南京试点20261019.md',
       'uv run pytest',
       '```','',
       '不覆盖已有成功画像/检查点。目录或画像改变时使用新证据目录；--reuse-root只复用参数完全相同的旧响应，并保留原始采集时间及来源。失败快照不得宣称完整回放。','',
       '## 直接来源','',
       '- [ChinaTravel Sandbox，CC BY 4.0](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox)',
       '- [南京发改委2026-01-07发布页](https://fgw.nanjing.gov.cn/njsfzhggwyh/202601/t20260107_5759097.html)',
       '- [南京民生价格手册2026版](https://fgw.nanjing.gov.cn/njsfzhggwyh/202601/P020260107586054627681.pdf)',
       '- [南京博物院参观服务](https://www.njmuseum.org.cn/zh/visitIndex)',
       '- [南京总统府官网](https://www.njztf.cn/index.html)',
       '- [中山陵园管理局2025-06-23游览指引](https://zschina.nanjing.gov.cn/lyzx/202506/t20250623_5591074.html)',
       '- [纪念馆当前参观须知](https://www.19371213.org.cn/guide/how/)',
       '- [玄武湖旅游服务](https://www.xuanwuhu.net/lyfw/lyfw.aspx)',
       '- [马鞍山市花山区政府2026-03-09濮塘信息](https://www.mashsq.gov.cn/xwzx/jrhs/19368861.html)',
       '- [MiniMax当前M3.1思考参数说明](https://platform.minimax.cn/docs/guides/text-generation)','',
       '上述动态网页未标发布日期时，来源日期记为未注明；本轮读取/整理日期为2026-10-19。资料内容、AI整理时间和运行验证时间分别保留。']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text('\n'.join(lines)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
