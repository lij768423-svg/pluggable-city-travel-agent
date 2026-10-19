from __future__ import annotations

import html
import json
from pathlib import Path
import random
import re

from .common import GROUPS, SnapshotError, digest, read_json, write_json


def blind_packet(inputs: dict, rows: list[dict], *, allow_incomplete: bool = False, seed: int = 20261017) -> tuple[dict, dict]:
    by_key = {(row["case_id"], row["group"]): row for row in rows}
    randomizer = random.Random(seed)
    packets, keys = [], {}
    for case in inputs["cases"]:
        entries = [by_key[(case["id"], group)] for group in GROUPS]
        if any(row["status"] != "ok" for row in entries):
            continue
        complete = all(row["report"]["complete_executable"] for row in entries)
        if not complete and not allow_incomplete:
            continue
        randomizer.shuffle(entries)
        options, assignment = [], {}
        for label, row in zip("ABCD", entries):
            assignment[label] = row["group"]
            days = [{"day": day["day"], "items": [{name: item[name] for name in (
                "name", "kind", "start", "end", "travel_min", "travel_mode", "cost")} for item in day["items"]],
                "return_travel_min": day["return_travel_min"],
                "reserved_meals": [{name: slot.get(name) for name in ("meal", "start", "end", "estimated_cost", "status")}
                                   for slot in day.get("reserved_meals", [])]} for day in row["itinerary"]["days"]]
            options.append({"label": label, "days": days,
                "constraint_status": row["report"]["constraint_status"],
                "arrangement_status": row["report"]["arrangement_status"], "evidence_status": row["report"]["evidence_status"]})
        token = "Q-" + digest({"case": case["id"], "seed": seed})[:12]
        keys[token] = {"case_id": case["id"], "assignment": assignment, "eligible_primary": complete}
        packets.append({"question": token, "profile": case["profile"], "options": options, "eligible_primary": complete})
    public = {"study": digest(inputs), "purpose": "diagnostic_only" if allow_incomplete else "complete_plans_only",
              "packets": packets, "responses_collected": 0}
    private = {"study": public["study"], "assignments": keys}
    return public, private


def export_study(inputs: dict, rows: list[dict], directory: Path, *, allow_incomplete: bool = False) -> None:
    public, private = blind_packet(inputs, rows, allow_incomplete=allow_incomplete)
    write_json(directory / "blind-public.json", public)
    write_json(directory / "blind-key-private.json", private)
    payload = json.dumps(public, ensure_ascii=False).replace("<", "\\u003c")
    title = "诊断回放问卷（不得当作正式盲评）" if allow_incomplete else "完整行程盲评"
    document = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>行程对照问卷</title><style>body{font:16px system-ui;background:#f4f6fa;color:#182234;margin:0;padding:28px}main{max-width:1024px;margin:auto}section{background:white;padding:24px;margin:18px 0;border-radius:14px}.options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}article{border:1px solid #dce3ed;padding:16px;border-radius:10px}p{line-height:1.7}select,input,button{padding:10px;font:inherit;margin:6px}button{background:#315bcd;color:white;border:0;border-radius:8px}@media(max-width:650px){.options{grid-template-columns:1fr}body{padding:14px}}</style>
<main><h1>TITLE</h1><p>不展示算法名称。估算路程不等于真实导航；自行用餐不等于落实餐厅。不符合完整标准的诊断样本不能进入正式偏好结论。</p><label>匿名参与者代号（不填姓名）<input id="participant" maxlength="40"></label><div id="questions"></div><button id="download">导出评分 JSON</button><p id="message"></p></main>
<script type="application/json" id="data">PAYLOAD</script><script>
const data=JSON.parse(document.getElementById('data').textContent),root=document.getElementById('questions');
const clock=value=>String(Math.floor(value/60)).padStart(2,'0')+':'+String(value%60).padStart(2,'0');
if(!data.packets.length){root.textContent='本次没有符合完整执行标准的四组样本，正式问卷暂未启用。';document.getElementById('download').disabled=true;}
for(const packet of data.packets){const section=document.createElement('section'),heading=document.createElement('h2');heading.textContent=packet.question+' · '+packet.profile.days+' 天 · '+packet.profile.interests.join('、');section.append(heading);const grid=document.createElement('div');grid.className='options';for(const option of packet.options){const card=document.createElement('article'),name=document.createElement('h3');name.textContent='方案 '+option.label;card.append(name);for(const day of option.days){const text=document.createElement('p');text.textContent='第 '+day.day+' 天\n'+day.items.map(item=>clock(item.start)+'–'+clock(item.end)+' '+item.name+' · '+item.travel_mode+item.travel_min+'分钟').join('\n');text.style.whiteSpace='pre-line';card.append(text);}for(const dimension of ['交通舒适','体验匹配','餐饮安排','整体偏好']){const label=document.createElement('label'),select=document.createElement('select');label.textContent=dimension;select.dataset.question=packet.question;select.dataset.option=option.label;select.dataset.dimension=dimension;for(let score=0;score<=5;score++){const item=document.createElement('option');item.value=String(score);item.textContent=score?String(score):'未评分';select.append(item);}label.append(select);card.append(label);}grid.append(card);}section.append(grid);root.append(section);}
document.getElementById('download').onclick=()=>{const participant=document.getElementById('participant').value.trim();if(!/^[a-zA-Z0-9_-]{1,40}$/.test(participant)){document.getElementById('message').textContent='请填写匿名英文/数字代号，不填写姓名。';return;}const answers=[...document.querySelectorAll('select')].map(select=>({question:select.dataset.question,option:select.dataset.option,dimension:select.dataset.dimension,score:Number(select.value)}));if(answers.some(answer=>answer.score<1)){document.getElementById('message').textContent='请完成全部评分。';return;}const blob=new Blob([JSON.stringify({study:data.study,participant,answers},null,2)],{type:'application/json'}),link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='blind-'+participant+'.json';link.click();URL.revokeObjectURL(link.href);};
</script></html>"""
    document = document.replace("TITLE", html.escape(title)).replace("PAYLOAD", payload)
    (directory / "blind.html").write_text(document, encoding="utf-8")


def import_responses(public: dict, private: dict, paths: list[Path]) -> dict:
    dimensions = {"交通舒适", "体验匹配", "餐饮安排", "整体偏好"}
    expected = {(packet["question"], option["label"], dimension) for packet in public["packets"]
                for option in packet["options"] for dimension in dimensions}
    participants, collected = set(), []
    for path in paths:
        response = read_json(path)
        participant = response["participant"]
        if not isinstance(participant, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", participant):
            raise SnapshotError("参与者须使用匿名代号")
        if response["study"] != public["study"] or private["study"] != public["study"] or participant in participants:
            raise SnapshotError("盲评实验不匹配或参与者重复")
        participants.add(participant)
        keys = [(entry["question"], entry["option"], entry["dimension"]) for entry in response["answers"]]
        if len(keys) != len(set(keys)) or set(keys) != expected or not expected:
            raise SnapshotError("盲评缺题、重复、空答卷或出现未知方案")
        for answer in response["answers"]:
            if type(answer["score"]) is not int or not 1 <= answer["score"] <= 5:
                raise SnapshotError("评分必须是 1–5 的整数")
            key = private["assignments"][answer["question"]]
            collected.append({**answer, "participant": participant, "group": key["assignment"][answer["option"]],
                              "eligible_primary": key["eligible_primary"]})
    return {"participants": len(participants), "ratings": collected,
            "primary_ratings": sum(row["eligible_primary"] for row in collected),
            "diagnostic_only_ratings": sum(not row["eligible_primary"] for row in collected)}
