"""C3 浏览器回归的真实 meta 夹具；不规划、不联网，剔除地图 key 并关闭登录门槛。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['TTA_CITIES'] = 'hangzhou,nanjing'
from app.api import meta  # noqa: E402
from app import config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=config.DATA_DIR / 'city-c3-20261019')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for city in ('hangzhou', 'nanjing'):
        payload = meta(city)
        payload['map'] = {'provider': 'leaflet', 'amap_js_key': None}
        payload['access'] = {'login_to_plan': False, 'invite_required': False}
        (args.output / f'meta-{city}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
