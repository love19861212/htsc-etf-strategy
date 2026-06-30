#!/usr/bin/env python3
"""ETF 战报发送器 (绕开 OpenClaw routing, 用 etf-strategy bot tenant token)

用法: python3 send_zhanbao.py <text-file>
  - text-file: 包含战报正文的纯文本文件 (UTF-8)

调飞书 API:
  1. POST /auth/v3/tenant_access_token/internal 拿 tenant_access_token
  2. POST /im/v1/messages?receive_id_type=open_id 发 DM 给 官人

环境变量 / 默认值:
  - 官人 open_id: ou_484e15906a89dc0991c007e1d1a06bc4 (etf-strategy app 专属)
  - bot 凭据: 从 /root/.openclaw/agents/etf-strategy/secrets.json 读
"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

SECRETS = Path("/root/.openclaw/agents/etf-strategy/secrets.json")
USER_OPEN_ID = "ou_484e15906a89dc0991c007e1d1a06bc4"
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"


def get_token(app_id, app_secret):
    data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("code") != 0:
        raise RuntimeError(f"Failed to get token: {result}")
    return result["tenant_access_token"]


def send_dm(token, receive_id, text):
    data = json.dumps({
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text})
    }).encode("utf-8")
    req = urllib.request.Request(
        SEND_URL,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": e.read().decode("utf-8", errors="replace")[:500]}


def main():
    if len(sys.argv) != 2:
        print("Usage: send_zhanbao.py <text-file>", file=sys.stderr)
        sys.exit(2)
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    secrets = json.loads(SECRETS.read_text(encoding="utf-8"))
    app_id = secrets["FEISHU_ETF_APP_ID"]
    app_secret = secrets["FEISHU_ETF_APP_SECRET"]
    token = get_token(app_id, app_secret)
    result = send_dm(token, USER_OPEN_ID, text)
    print(json.dumps(result, ensure_ascii=False))
    if result.get("code") != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
