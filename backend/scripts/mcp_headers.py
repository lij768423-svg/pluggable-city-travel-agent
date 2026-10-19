"""仅供受信任MCP客户端headers helper使用；禁止直接在聊天/终端打印运行结果。"""
import json
from pathlib import Path
from dotenv import dotenv_values

secret = dotenv_values(Path(__file__).resolve().parents[1] / '.env').get('TTA_MCP_TOKEN', '')
if not secret or len(secret) < 24:
    raise SystemExit('MCP credential unavailable')
print(json.dumps({'Authorization': f'Bearer {secret}'}))
