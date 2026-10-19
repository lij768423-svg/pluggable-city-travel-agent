"""SSH stdio客户端转接：业务仍由同进程HTTP MCP处理，凭据只在Home Serve读取。"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import time
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values


def relay(source, sink, client: httpx.Client, credential: str, port: int = 18687, *, client_token: str | None = None, profile: str = "default"):
    protocol = None
    for line in source:
        identifier = None
        try:
            if len(line.encode()) > 65536:
                raise ValueError()
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError()
            identifier = message.get('id')
        except (ValueError, UnicodeError):
            sink.write(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON-RPC request'}}) + '\n')
            sink.flush()
            continue
        headers = {'Authorization': f'Bearer {credential}', 'Accept': 'application/json, text/event-stream',
                   'Content-Type': 'application/json'}
        if client_token:
            headers.update({"Authorization": f"Bearer {client_token}", "X-Travel-Transport": credential, "X-Travel-Client": profile})
        if protocol:
            headers['MCP-Protocol-Version'] = protocol
        try:
            response = client.post(f'http://127.0.0.1:{port}/mcp', json=message, headers=headers)
            response.raise_for_status()
            if not response.content:
                continue
            payload = response.json()
            if message.get('method') == 'initialize':
                protocol = payload.get('result', {}).get('protocolVersion')
        except Exception:
            if 'id' not in message:
                continue
            payload = {'jsonrpc': '2.0', 'id': identifier,
                       'error': {'code': -32000, 'message': 'Private travel MCP connection unavailable'}}
        sink.write(json.dumps(payload, ensure_ascii=False) + '\n')
        sink.flush()


def client_credential(profile: str, directory: Path | None = None) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", profile):
        raise ValueError("Invalid client profile")
    directory = directory or Path.home() / ".local/state/thesis-travel-agent/mcp-clients"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{profile}.token"
    try:
        # 排他创建避免两个客户端首次启动时保存不同凭据；锁内一次写完。
        import fcntl
        with open(path, "a+", opener=lambda name, flags: os.open(name, flags, 0o600)) as target:
            fcntl.flock(target, fcntl.LOCK_EX)
            target.seek(0)
            token = target.read().strip()
            if not token:
                token = secrets.token_urlsafe(32)
                target.write(token + "\n")
                target.flush()
            os.chmod(path, 0o600)
    except OSError:
        raise RuntimeError("Cannot save private MCP authorization") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise RuntimeError("Invalid stored MCP authorization")
    return token


def login(client, secret: str, token: str, profile: str, port: int = 18687):
    headers = {"Authorization": f"Bearer {token}", "X-Travel-Transport": secret,
               "X-Travel-Client": profile, "Accept": "application/json, text/event-stream"}
    initialized = client.post(f"http://127.0.0.1:{port}/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "travel-login", "version": "1"}}})
    initialized.raise_for_status()
    headers["MCP-Protocol-Version"] = initialized.json()["result"]["protocolVersion"]
    deadline = time.monotonic() + 610
    shown = False
    while time.monotonic() < deadline:
        response = client.post(f"http://127.0.0.1:{port}/mcp", headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "connect_account", "arguments": {}}})
        response.raise_for_status()
        payload = response.json()["result"]["structuredContent"]
        if payload["status"] == "connected":
            print("MCP account connected. You can close this setup window.", flush=True)
            return
        if not shown:
            print("Open this link to log in and authorize MCP (valid for 10 minutes):", flush=True)
            print(payload["verification_url"], flush=True)
            shown = True
        time.sleep(3)
    raise RuntimeError("Login expired; run setup again")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default", help="同一安装保持不变的连接名")
    parser.add_argument("--login", action="store_true", help="首次配置时打开打印的链接登录并授权")
    args = parser.parse_args()
    secret = dotenv_values(Path(__file__).resolve().parents[1] / '.env').get('TTA_MCP_TOKEN', '')
    if not secret or len(secret) < 24:
        print('Private travel MCP credential unavailable', file=sys.stderr)
        raise SystemExit(1)
    with httpx.Client(timeout=65, trust_env=False) as client:
        token = client_credential(args.profile)
        if args.login:
            login(client, secret, token, args.profile)
        else:
            relay(sys.stdin, sys.stdout, client, secret, client_token=token, profile=args.profile)


if __name__ == '__main__':
    main()
