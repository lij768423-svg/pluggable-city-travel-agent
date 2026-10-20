"""逐城生成模型资产；复用有效响应、逐批检查点、禁止自动重试。"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
from datetime import datetime, timezone
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config
from app.cities import city_config
from app.llm import OpenAICompatLLM, chat_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--city', required=True)
    parser.add_argument('--stage', choices=('tags', 'stay', 'both'), default='both')
    parser.add_argument('--thinking', choices=('disabled', 'adaptive'), default='adaptive')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    settings = city_config(args.city)
    frame = pd.read_parquet(settings.data_dir / 'attractions.parquet')
    rows = frame.to_dict('records')
    estimate = {'city': settings.key, 'pois': len(rows), 'tag_requests': math.ceil(len(rows) / 20),
                'stay_requests': 3 * math.ceil(len(rows) / 12), 'automatic_retries': 0,
                'thinking': args.thinking, 'reasoning_effort': 'low'}
    print(json.dumps(estimate, ensure_ascii=False), flush=True)
    if args.dry_run:
        return 0
    output = settings.data_dir / 'model-assets-run.json'
    record = json.loads(output.read_text()) if output.exists() else {'stages': {}}
    session = {'started_at': datetime.now(timezone.utc).isoformat(), 'estimate': estimate}
    record.setdefault('sessions', []).append(session)
    def save():
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    if args.stage in ('tags', 'both'):
        from scripts import tag_pois
        from app.data import TYPE_RULES
        path = settings.data_dir / 'tag-checkpoint.json'
        fingerprint = hashlib.sha256(frame.to_json().encode()).hexdigest()
        checkpoint = json.loads(path.read_text()) if path.exists() else {'input_sha256': fingerprint, 'batches': {}}
        if checkpoint['input_sha256'] != fingerprint:
            raise ValueError('标签检查点与输入不一致')
        llm = OpenAICompatLLM(retry_attempts=1, sdk_max_retries=0)
        tag_pois.get_llm = lambda: llm
        tag_pois.chat_json = lambda *a, **kw: chat_json(*a, **{**kw, 'retries': 0})
        failures = []
        for start in range(0, len(rows), 20):
            key = str(start // 20 + 1)
            batch = rows[start:start + 20]
            if key in checkpoint['batches']:
                continue
            # 只读旧 adaptive 缓存，损坏响应不被当作成功；没有联网重试。
            previous_offline = os.environ.get('TTA_OFFLINE')
            config.LLM_THINKING = 'adaptive'
            llm.reasoning_effort = None
            os.environ['TTA_OFFLINE'] = '1'
            try:
                tags = tag_pois.tag_batch(batch, settings.key)
                source = 'existing_adaptive_cache'
            except (ValueError, RuntimeError):
                tags = None
            finally:
                if previous_offline is None:
                    os.environ.pop('TTA_OFFLINE', None)
                else:
                    os.environ['TTA_OFFLINE'] = previous_offline
            try:
                if tags is None:
                    config.LLM_THINKING = args.thinking
                    llm.reasoning_effort = 'low'
                    tags = tag_pois.tag_batch(batch, settings.key)
                    source = args.thinking
                ids = [tag['id'] for tag in tags]
                if len(ids) != len(set(ids)) or set(ids) != {int(row['id']) for row in batch}:
                    raise ValueError('标签响应ID缺失、重复或越界')
                checkpoint['batches'][key] = {'source': source, 'tags': tags}
                path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + '\n')
                print(f'tags batch={key}/{estimate["tag_requests"]} source={source}', flush=True)
            except Exception as error:
                failures.append({'batch': key, 'error_type': type(error).__name__, 'http_status': getattr(error, 'status_code', None)})
                break
        session['tags'] = {'request_attempts': llm.request_attempts, 'usage': llm.usage.as_dict(), 'failures': failures,
                           'response_models': sorted(llm.response_models)}
        save()
        if failures:
            print(json.dumps(failures, ensure_ascii=False), flush=True)
            return 1
        tags = {tag['id']: tag for entry in checkpoint['batches'].values() for tag in entry['tags']}
        for row in rows:
            if not tags[row['id']]['interests']:
                interests, stamina, indoor = TYPE_RULES.get(row['type'], ([], 2, False))
                tags[row['id']].update(interests=interests, stamina=stamina, indoor=indoor, source='rule')
        tagged = pd.DataFrame([tags[row['id']] for row in rows])
        tagged.to_parquet(settings.data_dir / 'attraction_tags.parquet', index=False)
        sample_ids = random.Random(42).sample(list(frame.id), max(1, len(rows) // 10))
        sample = tagged[tagged.id.isin(sample_ids)].merge(frame[['id','name','type']], on='id')
        sample['manual_review_status'] = 'pending'
        audit = settings.data_dir / 'tag_audit'
        audit.mkdir(exist_ok=True)
        sample.to_csv(audit / 'sample.csv', index=False, encoding='utf-8-sig')
    if args.stage in ('stay', 'both'):
        from scripts import estimate_stay
        config.LLM_THINKING = args.thinking
        sys.argv = ['estimate_stay.py', '--city', settings.key, '--batch-size', '12', '--reasoning-effort', 'low']
        status = estimate_stay.main()
        session['stay'] = {'exit_code': status, 'checkpoint': str(settings.data_dir / 'stay-reference-v1/checkpoint.json')}
        save()
        if status:
            return status
    session['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
