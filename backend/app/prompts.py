"""提示词与画像文本化（各节点、基线共用，保证对比公平）。"""
from __future__ import annotations

from .rules import HARD_INTERSTOP_RIDE_MIN, MAX_INTERSTOP_RIDE_MIN, day_window_text, activity_window, required_meals
from .geo import hm
from .schemas import INTERESTS, UserProfile

PACE_TEXT = {"relaxed": "轻松（每天约 3 个景点，优先顺路且低体力的组合，含午晚餐）", "normal": "适中（每天约 3 个景点，含午晚餐）",
             "intense": "紧凑（每天 4 个左右景点，可以早出晚归）"}
COMPANION_TEXT = {"solo": "独自出行", "couple": "情侣/夫妻", "friends": "朋友结伴",
                  "family_kids": "带小孩的家庭", "elderly": "带老人出行"}


def profile_to_text(p: UserProfile) -> str:
    lines = [
        f"- 城市：杭州，共 {p.days} 天",
        f"- 人均总预算：{p.budget:.0f} 元（含门票、餐饮、市内交通，不含住宿）",
        f"- 节奏：{p.pace}；活动时段：" + "；".join(f"第{day}天 {hm(activity_window(p, day)['start'])}–{hm(activity_window(p, day)['end'])}" for day in range(1, p.days + 1)),
        f"- 出游日期：{p.start_date.isoformat() if p.start_date else '未指定，不承诺具体日期开放'}",
        f"- 餐饮偏好：{p.food_preference}；每餐预算 {p.meal_budget_min or 0}–{p.meal_budget_max or '按总预算分配'} 元",
        f"- 兴趣：{'、'.join(p.interests) if p.interests else '无特别偏好'}",
        f"- 偏好片区：{'、'.join(p.preferred_regions) if p.preferred_regions else '无特别偏好'}（以候选中的片区记录为准）",
        f"- 饮食禁忌：{'、'.join(p.dietary) if p.dietary else '无'}",
        f"- 同行：{COMPANION_TEXT[p.companions]}",
        f"- 景点之间的交通：{'打车' if p.transport == 'taxi' else '公交/地铁'}（1.5 公里内步行）",
        f"- 日内衔接：除每天出发和返程外，景点及餐厅之间单段乘车尽量不超过{MAX_INTERSTOP_RIDE_MIN}分钟、最多{HARD_INTERSTOP_RIDE_MIN}分钟；步行不受此限制，优先选择同片区组合，具体耗时由路线查询与调度核验",
        f"- 每天的起点与终点：{p.hotel.name}（可以是酒店、地铁口或其他地点，不要假定它是住宿）",
    ]
    if p.must_visit:
        lines.append(f"- 必去：{'、'.join(p.must_visit)}")
    if p.avoid:
        lines.append(f"- 不去：{'、'.join(p.avoid)}")
    if p.notes:
        lines.append(f"- 其他要求：{p.notes}")
    return "\n".join(lines)


PARSE_SYSTEM = f"""你是旅行需求解析器。把用户的一句话需求解析成 JSON，只输出 JSON，不要解释。
字段：
- days: 整数 1-5（没说就用 2）
- budget: 人均总预算（元，数字；没说就用 1500）
- pace: "relaxed" | "normal" | "intense"（"轻松/慢/休闲/老人"→relaxed，"紧凑/特种兵/多去几个"→intense，其他→normal）
- interests: 从以下词表中选 0-5 个：{"、".join(INTERESTS)}
- dietary: 从 ["不吃辣","只吃中餐","清真","不吃海鲜"] 中选
- companions: "solo" | "couple" | "friends" | "family_kids" | "elderly"
- transport: "taxi" | "transit"（明确选择乘公交/地铁→transit；打车、网约车、自驾→taxi；仅把地铁站或地铁口作为起点不代表交通偏好；没说就不填）
- must_visit: 用户点名要去的景点名称列表
- preferred_regions: 用户明确偏好的片区名称列表；保留原名，不把起点或单个景点推断为片区偏好
- avoid: 用户明确不去的景点名称列表
- notes: 其他无法归入上述字段的要求（简短中文）
- start_date: 用户明确提供的出游日期 YYYY-MM-DD；未给出确定日期则省略，不猜年份
- day_start/day_end: 每天开始/结束活动的分钟数，例如 09:00 为 540，20:00 为 1200；未说则省略
- max_walking_m: 用户明确的每日步行上限（米），未说则省略
- allow_taxi_fallback: 默认允许公交太慢或没有时改打车；仅当用户明确不想打车时为 false，否则省略
- include_evening: 默认 true，优先补充顺路夜游；明确不想夜游、晚上休息则 false；最晚返回时间始终优先
- food_preference: balanced/local/quick/quality（均衡/当地特色/快捷/品质正餐），未说则省略
- meal_budget_min/meal_budget_max: 用户明确的每餐预算范围（元），未说则省略
"""

SELECT_SYSTEM = """你是杭州旅行规划师，负责"选点"：从给定候选景点中选出最适合该用户的景点。
规则：
1. 只能从候选列表中选，用候选的 id 表示；不要编造景点。
2. 选出恰好 N 个（N 会给出），用户"必去"的景点如果在候选中必须选上。
3. 以片区为单位安排：候选已按片区分组。按天数挑片区，每天以一个片区为主、至多再加一个相邻片区，并在该片区里挑够当天的数量；不要东一个西一个地跨片区挑。起点到第一个片区、最后回起点的路程远近不用顾虑。同时兼顾兴趣匹配、体力（节奏轻松或带老人小孩时少选高体力景点）、预算、类型多样性。同一段漫步的沿途地点不是几个独立的半日项目；全天不能只列同一条堤上的观景点。
晚间型地点（开放不早于16:00，或名称含夜游/夜市）只能在17:00以后安排，仅作晚间项目，不能占用某天的白天主片区；白天仍须在主片区选够当天数量。
4. 每个景点写一句不超过 40 字的中文推荐理由，要具体说明它和用户画像的哪一点匹配。
5. 你还不知道景点最终安排的时段和顺序。推荐理由不得承诺早晚、日落夜景、餐前餐后或前后衔接，也不得编造预约、设施安全或实时开放情况。门票是参考记录，只有“免费已核验:True”才可以说免费；推荐理由侧重兴趣和地点特点，不重复金额。
6. 地名中的残雪、秋月、春晓不是天气或花期预报。未查证当日天气时不承诺雪景、盛花或晴朗月色；遵守候选的季节限制。
只输出 JSON：{"selected": [{"id": 整数, "reason": "理由"}]}
"""

BASELINE_SYSTEM = """你是杭州旅行规划师。根据用户画像，直接给出完整的逐日行程，包括景点和午餐、晚餐的餐厅。
要求：使用真实存在的杭州景点和餐厅名称；时间要合理（考虑开放时间和路上的时间）；控制在预算内。
只输出 JSON，格式：
{"days": [{"day": 1, "items": [
  {"type": "attraction" 或 "restaurant", "name": "名称", "start": "HH:MM", "end": "HH:MM", "cost": 人均费用数字, "reason": "一句理由"}
]}]}
"""
