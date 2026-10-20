"""C4 南京首轮来源审阅与项目范围整理；AI 整理，不冒充人工核验。"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.cities import city_config


def main() -> None:
    city=city_config('nanjing')
    rows=pd.read_parquet(city.data_dir/'attractions.parquet').set_index('id')
    sources={
      'price_2026':{'url':'https://fgw.nanjing.gov.cn/njsfzhggwyh/202601/P020260107586054627681.pdf','published_at':'2026-01-07','effective_as_of':'2025-12','verification':'upper_bound','facts':'2026版，纸页13–14；政府指导最高限价，不是当前售票价。2026-10-19下载并查看表格。'},
      'museum':{'url':'https://www.njmuseum.org.cn/zh/visitIndex','published_at':None,'verification':'observed','facts':'常规9–17，16点停入，周一闭馆（节假日例外）；实名预约；基本展览免费，收费特展另计。'},
      'palace':{'url':'https://www.njztf.cn/index.html','published_at':None,'verification':'observed','facts':'官网35元；旺季3/1–10/14为8:30–18，17点停入；淡季8:30–17，16点停入；周一闭馆（法定节假日除外）。'},
      'zhongshan':{'url':'https://zschina.nanjing.gov.cn/lyzx/202506/t20250623_5591074.html','published_at':'2025-06-23','verification':'observed','facts':'中山陵8:30–17，周一维护（节日及孙中山诞辰/逝世纪念日例外）；美龄宫7:30–18，17:30停入；明孝陵/音乐台/灵谷季节时段另有差异。'},
      'memorial':{'url':'https://www.19371213.org.cn/guide/how/','published_at':None,'verification':'observed','facts':'8:30–17:30，16:30停入，周一闭馆（节日例外），所有观众实名分馆预约。'},
      'lake':{'url':'https://www.xuanwuhu.net/lyfw/lyfw.aspx','published_at':None,'verification':'observed','facts':'免费；环湖路24小时，五洲及情侣园4–10月6–22，11–3月6–21；游船另收费、受天气影响。'},
      'curator':{'url':'https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox','published_at':None,'verification':'curator_inference','facts':'Codex基于名称、位置、父子范围的项目安排规则；停留时长为估计，不是现场事实。'}
    }
    sources['putang']={'url':'https://www.mashsq.gov.cn/xwzx/jrhs/19368861.html','published_at':'2026-03-09','verification':'observed','facts':'马鞍山市花山区政府发布濮塘休闲度假区的官方活动信息；中国旅行数据南京表中混入该地。'}
    places=[]
    def add(identifier,changes,sources_used=(),note=''):
        places.append({'id':identifier,'name':rows.loc[identifier,'name'],'sources':list(sources_used),'changes':changes,'note':note})
    # 用政策最高限价作预算保守值，保留未核实票价状态。
    for identifier,price in {0:160,3:70,4:70,10:90,11:30,12:30,13:30,15:80,17:40,19:10,20:30,22:10,28:25,29:50,36:35,45:30,51:144,55:27,65:40,67:10,97:30,128:40,193:18,215:30,232:18}.items():
        add(identifier,{'price':price},['price_2026'],'预算采用政府指导最高限价；不是实时售票价，优惠及联票另核。'+('红枫节最高80元，全年保守按80元预算。' if identifier==15 else ''))
    add(6,{'open_min':540,'close_min':1020,'price':0,'opening_rules':{'closed_weekdays':[0],'last_entry':960,'booking_required':True}},['museum'],'仅基本展览；收费临展另核。周一法定节假日例外仍需确认，未代为预约。')
    add(7,{'price':35},['palace'],'官网当前单人票35元，动态优惠与库存另核。')
    # 当前 schema 不表达季节性日内时段；用全年交集作保守安排，不能称已核实的全年实际时段。
    add(7,{'open_min':510,'close_min':1020,'opening_rules':{'closed_weekdays':[0],'last_entry':960,'booking_required':True}},['palace'],'采用淡季安全时段；旺季可延至18点，项目保守截断到17点，周一及预约按官网常规规则，节假日另核。')
    add(9,{'open_min':510,'close_min':1020,'opening_rules':{'closed_weekdays':[0],'booking_required':True}},['zhongshan'],'中山陵陵寝范围；周一维护及纪念日例外另核，不承诺墓室开放。')
    add(11,{'open_min':450,'close_min':1080,'opening_rules':{'last_entry':1050}},['zhongshan'],'常规时段已有来源；临时安排、售票及预约另核。')
    add(23,{'open_min':510,'close_min':1050,'opening_rules':{'closed_weekdays':[0],'last_entry':990,'booking_required':True}},['memorial'],'仅南京大屠杀史实展区；三个展馆分开预约，不能合并视作同一张预约。')
    add(18,{'price':0},['lake'],'公园免费不含游船、观光车和收费项目。')
    add(18,{'open_min':360,'close_min':1260},['curator'],'为覆盖冬季内洲游览，项目保守采用6–21点交集；环湖路24小时不能推导全部设施全天开放。')
    for identifier in (4,19,36):
        add(identifier,{'open_min':420,'close_min':1050},['curator'],'按钟山官方冬季时段7–17:30取全年安全交集；非实时园方时刻，旺季可更长。')
    add(227,{'recommendable':False},['putang'],'濮塘风景区属于马鞍山市花山区；保留原始表及ID作为问题证据，南京自动规划隔离此点。')
    # 有明确父项时保留父项，子景点只作范围线索，避免一地重复安排。
    exclusion={2:'与夫子庙/步行街范围重叠，自动召回保留具体主项。',5:'钟山聚合范围含多个独立收费景区，0.5–1小时不能代表全景区。',14:'南京城墙为多入口多票价聚合项，具体登城段未定。',39:'秦淮河为广义水系，不能按一个确定收费/开放的独立停留点安排。',27:'园博园历史零票价未核实，暂不自动推荐。',43:'艺术馆历史零票价未区分收费展与公共空间。',63:'游船历史零票价、班次和码头范围未核实。',90:'游船历史零票价及班次未核实。',167:'码头游船不是免费公园游览，票价班次未核实。',314:'公路桥被归为游乐园且建议3–4小时，步行资格和游览范围未核实。',266:'跨午夜时段尚无可核来源，当前排程不支持翌日闭店。',293:'夜间商业场馆跨午夜时段和收费未核实。',139:'玄武门是玄武湖入口，不能与全湖重复算2–3小时。',163:'南京博物院民国馆是院内子项，随父项参观，不另算一次预约和游览。',166:'小熊猫馆是动物园内子项，随父项参观。',79:'佛顶宫是牛首山园内子项，随父项参观。',91:'佛顶寺是牛首山园内子项，随父项参观。'}
    for identifier,note in exclusion.items():
        add(identifier,{'recommendable':False},['curator'],note+'本轮AI整理，待人审。')
    for identifier,low,high in ((248,.25,.5),(274,.25,.5),(94,.25,.5),(238,.25,.5),(47,.25,.5)):
        add(identifier,{'min_hours':low,'max_hours':high,'visit_role':'viewpoint','duration_source':'2026-10-19 Codex项目估计：单个地标停留，不含周边街区；待现场/人工核验'},['curator'],'修正单桥/亭/门楼被当作数小时活动的范围问题；不是官方时长。')
    for identifier in (1,66,248):
        add(identifier,{'visit_group':'nanjing-fuzimiao-walk','visit_group_label':'夫子庙·秦淮街区漫步'},['curator'],'按一段街区游览计体验，商铺、船班及大成殿收费分开核实。')
    for identifier in (32,94):
        add(identifier,{'visit_group':'nanjing-laomendong-walk','visit_group_label':'老门东街区漫步'},['curator'],'牌坊与街区连续游览，不重复算独立活动。')
    notes={}
    last={}
    for index,entry in enumerate(places):
        notes.setdefault(entry['id'],[]).append(entry['note'])
        last[entry['id']]=index
    for identifier,index in last.items():
        places[index]['note']='；'.join(dict.fromkeys(notes[identifier]))
    value={'version':'2026-10-19-c4-v1','reviewed_at':'2026-10-19','scope':'Codex AI来源审阅及安排范围整理，未做人类或现场核验。价格上限与实际票价分开；所有未修订字段继续保留数据集证据。','sources':sources,'places':places}
    city.assets_dir.mkdir(parents=True,exist_ok=True)
    (city.assets_dir/'poi_reviews.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    # 名称来自聚类成员，属于项目展示名称，不是行政区划，也没有重新定义片区算法。
    labels={0:'牛首山',1:'夫子庙·秦淮',3:'红山动物园·神策门',4:'明孝陵·植物园',5:'中山陵·美龄宫',6:'南京博物院·明故宫',7:'总统府·长江路·新街口',10:'老门东·中华门·大报恩寺',14:'玄武湖西岸·鸡鸣寺',15:'栖霞山',17:'阅江楼·下关',21:'欢乐谷',23:'江东门·云锦',27:'园博园·汤山西部',28:'朝天宫·莫愁湖',30:'玄武湖东岸·紫金山西麓',31:'金陵小城',33:'长江大桥南岸',36:'灵谷',38:'银杏湖',41:'河西奥体·南京眼',45:'幕燕滨江',46:'汤山紫清湖',47:'鼓楼·颐和路·狮子桥',63:'清凉山·石头城',65:'珍珠泉',67:'燕子矶',70:'老山·不老村',75:'紫金山山顶',76:'高淳老街',93:'河西滨江',129:'百家湖'}
    names={'version':'2026-10-19-c4-v1','scope':'Codex依据自动聚类成员起草，待用户审阅；只影响展示名称，不是行政边界。','areas':[{'anchor_id':i,'name':n} for i,n in labels.items()]}
    (city.assets_dir/'area_names.json').write_text(json.dumps(names,ensure_ascii=False,indent=2)+'\n')
    print({'review_entries':len(places),'reviewed_pois':len({p['id'] for p in places}),'excluded':len(exclusion)+1,'draft_area_names':len(labels)})

if __name__=='__main__':
    main()
