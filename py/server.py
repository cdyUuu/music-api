#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音乐 API Python 常驻服务
通过 Unix Socket 与 PHP 通信，避免每次请求 fork 新进程。

启动: python py/server.py
Socket: /run/music-api/music-api.sock
"""
import os
import sys
import json
import asyncio
import logging
from pathlib import Path

PY_DIR = os.path.dirname(os.path.abspath(__file__))
if PY_DIR not in sys.path:
    sys.path.insert(0, PY_DIR)

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import uvicorn

from lx import kg, tx, core
import wy as wy_mod

# 酷我是纯同步模块，按需导入
try:
    import kw_vip as kw_mod
    _KW_AVAILABLE = True
except Exception as e:
    _KW_AVAILABLE = False
    _KW_IMPORT_ERROR = str(e)

SOCKET_PATH = os.environ.get("MUSIC_API_SOCKET", "/run/music-api/music-api.sock")
PROJECT_ROOT = Path(PY_DIR).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("server")

app = FastAPI(title="Music API Python Service", docs_url=None, redoc_url=None)


def _error(code: int, message: str, **extra) -> dict:
    return {"code": code, "message": message, **extra}


# ============================================================
# 酷狗 / QQ音乐（async）
# ============================================================
async def _kg_tx_handle(source: str, action: str, song_id: str, quality: str, nocache: bool = False):
    mod = kg if source == "kg" else tx
    try:
        if action == "url":
            # nocache: 直接调底层，不走 cache.get
            if not nocache:
                cache_key = f"url_{source}_{song_id}_{quality}"
                cached = core.cache.get("url", cache_key)
                if cached:
                    return cached
            result = await mod.get_url(song_id, quality)
            resp = {"code": 200, "message": "成功", "url": result.url,
                    "quality": result.quality,
                    "requested": result.requested,
                    "fallback": result.fallback,
                    "upgraded": result.upgraded,
                    "direction": result.direction,
                    "fallback_chain": result.fallback_chain,
                    "sent_level": result.sent_level,
                    "returned_level": result.returned_level,
                    "url_feature": result.url_feature}
            return resp
        elif action == "info":
            result = await mod.get_song_info(song_id)
            return {"code": 200, "message": "成功", "data": result.model_dump()}
        elif action == "lyric":
            result = await mod.get_lyric(song_id)
            return {"code": 200, "message": "成功", "data": result.model_dump()}
        elif action == "refresh":
            users = core.config.get("modules", "platform", source, "users", default=[])
            updated = []
            for user in users:
                new_user = await mod.refresh_login(dict(user))
                updated.append(new_user)
            core.config.set_nested("modules", "platform", source, "users", updated)
            return {"code": 200, "message": f"{source} 刷新完成", "users": updated}
        else:
            return _error(400, f"不支持的action: {action}")
    except core.FailedException as e:
        return _error(500, e.message)
    except Exception as e:
        logger.exception(f"{source} {action} 异常")
        return _error(500, str(e))


# ============================================================
# 网易云（同步，放线程池）
# ============================================================
def _wy_sync(action: str, song_id: str, quality: str, nocache: bool = False) -> dict:
    try:
        if action == "url":
            if not nocache:
                cache_key = f"url_wy_{song_id}_{quality}"
                cached = core.cache.get("url", cache_key)
                if cached:
                    return cached
            playback = wy_mod.get_playback(song_id, quality)
            if "error" in playback:
                return _error(500, playback["error"])
            # 缓存以"实际音质"为键
            actual_q = playback.get("quality", quality)
            cache_payload = {"code": 200, "message": "成功", "playback": playback}
            core.cache.set("url", f"url_wy_{song_id}_{actual_q}", cache_payload, 600)
            if actual_q == quality:
                core.cache.set("url", f"url_wy_{song_id}_{quality}", cache_payload, 600)
            return cache_payload
        elif action == "info":
            return {"detail": wy_mod.get_detail(song_id)}
        elif action == "lyric":
            return {"lyric": wy_mod.get_lyric(song_id)}
        else:
            return _error(400, f"不支持的action: {action}")
    except Exception as e:
        logger.exception(f"wy {action} 异常")
        return _error(500, str(e))


# ============================================================
# 酷我（同步，放线程池）
# ============================================================
def _kw_detect_quality(info, fallback_quality):
    """
    从酷我返回的 info dict 推断实际音质。
    简化版（VIP 检测系统已移除）：用 br 数值 + format 判断。
    """
    if not info:
        return fallback_quality
    fmt = info.get("format") or ""
    fmt_l = str(fmt).lower()
    br_raw = info.get("br")
    # br=6 是试听片段
    if str(br_raw) == "6":
        return "128k"
    # 解析 br 数值
    br_val = None
    try:
        if isinstance(br_raw, str) and br_raw.endswith(("k", "K")):
            br_val = float(br_raw[:-1])
        elif br_raw is not None:
            br_val = float(str(br_raw).replace("k", "").replace("K", ""))
    except Exception:
        br_val = None
    if br_val is not None:
        if br_val < 161: return "128k"
        if br_val < 256: return "192k"
        if br_val < 800: return "320k"
        if br_val < 3000: return "flac"
        if br_val < 8000: return "hires"
        if br_val < 23000: return "atmos"
        if br_val < 26000: return "atmos_plus"
        return "master"
    # 兜底用 format
    if fmt_l == "flac": return "flac"
    if fmt_l in ("mflac", "mgg"): return "atmos"
    if fmt_l == "mp3": return "320k"
    return fallback_quality


def _kw_sync(song_id: str, quality: str, nocache: bool = False) -> dict:
    if not _KW_AVAILABLE:
        return _error(500, f"酷我模块加载失败: {_KW_IMPORT_ERROR}")
    try:
        if not nocache:
            cache_key = f"url_kw_{song_id}_{quality}"
            cached = core.cache.get("url", cache_key)
            if cached:
                return cached
        info, channel = kw_mod.get_url(song_id, quality, duration_sec=None)
        if info and info.get("url"):
            # 推断实际音质
            actual_q = _kw_detect_quality(info, quality)
            _rank = {"128k":1,"192k":2,"320k":3,"flac":4,"flac24bit":5,
                     "hires":6,"atmos":7,"atmos_plus":8,"master":9,
                     "jymaster":9,"hifi":7,"zpga":7,"sur":7,"zp":8}
            r_req = _rank.get(quality, 0)
            r_act = _rank.get(actual_q, 0)
            if r_act == r_req:
                direction = "match"
            elif r_act < r_req:
                direction = "fallback"
            else:
                direction = "upgraded"

            result = dict(info) if isinstance(info, dict) else {}
            result.update({
                "code": 200, "message": "成功",
                "rid": song_id, "quality": actual_q, "requested": quality,
                "fallback": (actual_q != quality),
                "upgraded": direction == "upgraded",
                "direction": direction,
                "channel": channel,
                "url": info["url"], "format": info["format"],
                "br": info.get("br"), "br_req": info.get("br_req"),
                "ekey": info.get("ekey") or "",
                "album_id": info.get("album_id") or "",
                "content_length": info.get("content_length"),
                "estimated_kbps_by_head": info.get("estimated_kbps_by_head"),
                "is_encrypted": info["format"] in kw_mod.ENCRYPTED_FMTS,
            })
            # 缓存以实际音质为键
            core.cache.set("url", f"url_kw_{song_id}_{actual_q}", result, 600)
            if actual_q == quality:
                core.cache.set("url", f"url_kw_{song_id}_{quality}", result, 600)
            return result
        else:
            return _error(500, "获取直链失败", rid=song_id, quality=quality)
    except Exception as e:
        logger.exception(f"kw url 异常")
        return _error(500, str(e), rid=song_id, quality=quality)


# ============================================================
# 路由
# ============================================================
@app.get("/health")
async def health():
    return {
        "code": 200,
        "status": "ok",
        "python": sys.version.split()[0],
        "kw_available": _KW_AVAILABLE,
    }


@app.get("/url")
async def get_url(
    source: str = Query(...),
    songId: str = Query(...),
    quality: str = Query("320k"),
    nocache: int = Query(0),
):
    source = source.lower()
    no_cache = bool(nocache)
    if source in ("kg", "tx"):
        if source == "kg":
            songId = songId.lower()
        return await _kg_tx_handle(source, "url", songId, quality, nocache=no_cache)
    elif source == "wy":
        return await asyncio.to_thread(_wy_sync, "url", songId, quality, no_cache)
    elif source == "kw":
        return await asyncio.to_thread(_kw_sync, songId, quality, no_cache)
    else:
        return _error(400, f"不支持的source: {source}")


@app.get("/info")
async def get_info(
    source: str = Query(...),
    songId: str = Query(...),
):
    source = source.lower()
    if source in ("kg", "tx"):
        if source == "kg":
            songId = songId.lower()
        return await _kg_tx_handle(source, "info", songId, "320k")
    elif source == "wy":
        return await asyncio.to_thread(_wy_sync, "info", songId, "320k")
    elif source == "kw":
        return _error(400, "酷我info由PHP原生处理")
    else:
        return _error(400, f"不支持的source: {source}")


@app.get("/lyric")
async def get_lyric(
    source: str = Query(...),
    songId: str = Query(...),
):
    source = source.lower()
    if source in ("kg", "tx"):
        if source == "kg":
            songId = songId.lower()
        return await _kg_tx_handle(source, "lyric", songId, "320k")
    elif source == "wy":
        return await asyncio.to_thread(_wy_sync, "lyric", songId, "320k")
    elif source == "kw":
        return _error(400, "酷我lyric由PHP原生处理")
    else:
        return _error(400, f"不支持的source: {source}")


@app.get("/refresh")
async def refresh(source: str = Query(...)):
    source = source.lower()
    if source in ("kg", "tx"):
        return await _kg_tx_handle(source, "refresh", "", "320k")
    return _error(400, f"不支持的source: {source}")


# ============================================================
# 启动
# ============================================================
def _ensure_socket_dir():
    sock_dir = os.path.dirname(SOCKET_PATH)
    if sock_dir and not os.path.exists(sock_dir):
        try:
            os.makedirs(sock_dir, exist_ok=True)
        except PermissionError:
            logger.warning(f"无法创建 {sock_dir}，将使用项目目录下的 socket")
            return str(PROJECT_ROOT / "data" / "music-api.sock")
    return SOCKET_PATH


if __name__ == "__main__":
    socket_path = _ensure_socket_dir()
    # 清理旧 socket
    if os.path.exists(socket_path):
        try:
            os.unlink(socket_path)
        except OSError:
            pass

    logger.info(f"启动 Python 常驻服务，socket: {socket_path}")

    config = uvicorn.Config(
        app,
        uds=socket_path,
        host=None,
        port=None,
        log_level="info",
        access_log=True,
        timeout_keep_alive=30,
    )
    server = uvicorn.Server(config)

    # 启动后设置 socket 权限
    async def _set_socket_perm():
        # 等待 socket 创建
        for _ in range(50):
            if os.path.exists(socket_path):
                try:
                    os.chmod(socket_path, 0o660)
                    # 尝试设置属主为 www-data（需要root，失败忽略）
                    try:
                        import pwd
                        uid = pwd.getpwnam("www-data").pw_uid
                        gid = pwd.getpwnam("www-data").pw_gid
                        os.chown(socket_path, uid, gid)
                    except (KeyError, PermissionError):
                        pass
                except OSError:
                    pass
                break
            await asyncio.sleep(0.1)

    async def main():
        task = asyncio.create_task(_set_socket_perm())
        await server.serve()
        task.cancel()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        if os.path.exists(socket_path):
            try:
                os.unlink(socket_path)
            except OSError:
                pass
