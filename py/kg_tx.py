#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
酷狗音乐 + QQ音乐 API CLI 入口
用法: python kg_tx.py <source> <action> <songId> [quality]
source: kg | tx
action: url | info | lyric | refresh
"""
import sys
import os
import json
import asyncio

PY_DIR = os.path.dirname(os.path.abspath(__file__))
if PY_DIR not in sys.path:
    sys.path.insert(0, PY_DIR)

from lx import kg, tx, core

def print_json(data):
    print(json.dumps(data, ensure_ascii=False))

async def run_refresh(source):
    mod = kg if source == "kg" else tx
    platform_key = "kg" if source == "kg" else "tx"
    users = core.config.get("modules", "platform", platform_key, "users", default=[])
    updated = []
    for user in users:
        new_user = await mod.refresh_login(dict(user))
        updated.append(new_user)
    core.config.set_nested("modules", "platform", platform_key, "users", updated)
    return {"code": 200, "message": f"{source} 刷新完成", "users": updated}

async def run_action(source, action, song_id, quality="320k"):
    try:
        mod = kg if source == "kg" else tx
        if action == "url":
            result = await mod.get_url(song_id, quality)
            return {
                "code": 200, "message": "成功", "url": result.url,
                "quality": result.quality,
                "requested": result.requested,
                "fallback": result.fallback,
                "upgraded": result.upgraded,
                "direction": result.direction,
                "fallback_chain": result.fallback_chain,
                "sent_level": result.sent_level,
                "returned_level": result.returned_level,
                "url_feature": result.url_feature,
            }
        elif action == "info":
            result = await mod.get_song_info(song_id)
            return {"code": 200, "message": "成功", "data": result.model_dump()}
        elif action == "lyric":
            result = await mod.get_lyric(song_id)
            return {"code": 200, "message": "成功", "data": result.model_dump()}
        elif action == "refresh":
            return await run_refresh(source)
        else:
            return {"code": 400, "message": f"不支持的action: {action}"}
    except core.FailedException as e:
        return {"code": 500, "message": e.message}
    except Exception as e:
        return {"code": 500, "message": str(e)}

def main():
    if len(sys.argv) < 3:
        print_json({
            "code": 400,
            "message": "参数不足",
            "usage": "python kg_tx.py <source> <action> [songId] [quality]",
            "source": "kg | tx",
            "action": "url | info | lyric | refresh",
        })
        sys.exit(1)

    source = sys.argv[1].lower()
    action = sys.argv[2].lower()

    if source not in ("kg", "tx"):
        print_json({"code": 400, "message": f"不支持的source: {source}"})
        sys.exit(1)

    if action == "refresh":
        result = asyncio.run(run_refresh(source))
    else:
        if len(sys.argv) < 4:
            print_json({"code": 400, "message": "缺少songId参数"})
            sys.exit(1)
        song_id = sys.argv[3]
        quality = sys.argv[4] if len(sys.argv) > 4 else "320k"
        if source == "kg":
            song_id = song_id.lower()
        result = asyncio.run(run_action(source, action, song_id, quality))

    asyncio.run(core.http.close())
    print_json(result)

if __name__ == "__main__":
    main()
