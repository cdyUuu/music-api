#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐 API CLI 入口
网易云音乐模块

用法: python wy.py <action> <songId> [quality]
action: url | info | lyric
"""
import sys
import os
import json
import argparse
import hashlib
import logging
import requests
from Crypto.Cipher import AES

# 接入统一音质工具（与 lx.core 同源，避免循环依赖）
_PY_DIR = os.path.dirname(os.path.abspath(__file__))
if _PY_DIR not in sys.path:
    sys.path.insert(0, _PY_DIR)
try:
    from lx.core import (
        get_fallback_chain, parse_quality_from_url, compare_quality, QUALITY_RANK,
    )
    _HAS_CORE = True
except Exception:
    _HAS_CORE = False
    # 退化版本：内联一份最小实现
    FALLBACK_ORDER = {
        "master":     ["hires", "flac", "320k", "128k"],
        "hires":      ["flac", "320k", "128k"],
        "flac":       ["320k", "128k"],
        "320k":       ["128k"],
        "192k":       ["128k"],
        "128k":       [],
    }
    QUALITY_RANK = {
        "128k": 1, "192k": 2, "320k": 3,
        "flac": 4, "hires": 6, "master": 9,
    }
    def get_fallback_chain(q):
        return [q] + FALLBACK_ORDER.get(q, [])
    def compare_quality(req, act):
        r = QUALITY_RANK.get(req, 0); a = QUALITY_RANK.get(act, 0)
        if a == r: return "match"
        return "fallback" if a < r else "upgraded"
    def parse_quality_from_url(url, source):
        # 网易 URL 不可靠，主要从 br 字段推断（见 get_playback 里）
        return "unknown"

_wy_logger = logging.getLogger("wy")
if not _wy_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _wy_logger.addHandler(_h)
    _wy_logger.setLevel(logging.INFO)

# 从config.json读取配置
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
WY_COOKIE = ""
if os.path.exists(CONFIG_PATH):
    try:
        _cfg = json.loads(open(CONFIG_PATH, encoding="utf-8").read())
        WY_COOKIE = _cfg.get("wy", {}).get("cookie", "")
    except:
        pass

# ============================================================
# 网易云 eapi 加密
# ============================================================
EAPI_KEY = b"e82ckenh8dichen8"

def eapi_encrypt(url, params):
    text = f"nobody{url}use{json.dumps(params)}md5forencrypt"
    digest = hashlib.md5(text.encode()).hexdigest()
    data = f"{url}-36cd479b6b5-{json.dumps(params)}-36cd479b6b5-{digest}"
    pad_len = 16 - len(data) % 16
    padded = data.encode() + bytes([pad_len] * pad_len)
    cipher = AES.new(EAPI_KEY, AES.MODE_ECB)
    return cipher.encrypt(padded).hex().upper()

# ============================================================
# 网易云音乐
# ============================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://music.163.com/",
    "Cookie": WY_COOKIE,
}

def get_playback(song_id, quality="320k"):
    """
    逐级降级获取播放 URL。
    网易的音质识别靠 br（比特率）字段：
      - 128000 → 128k
      - 192000 → 192k
      - 320000 → 320k
      - 740000~999000 → flac (lossless)
      - 1999000+ → hires
    master（jymaster）走单独接口（/api/song/enhance/player/url/v1），level=jymaster。
    """
    # 网易 level 映射（v1 接口用）
    WY_LEVEL_MAP = {
        "128k":   "standard",
        "192k":   "higher",
        "320k":   "exhigh",
        "flac":   "lossless",
        "hires":  "hires",
        "master": "jymaster",
    }
    # 旧接口 br 映射（兼容回退）
    WY_BR_MAP = {
        "128k": 128000, "192k": 192000, "320k": 320000,
        "flac": 999000, "hires": 1999000, "master": 1999000,
    }

    def _br_to_quality(br_val):
        """根据 br 数值推断音质等级"""
        try:
            b = int(br_val)
        except (TypeError, ValueError):
            return "unknown"
        if b <= 192000:
            return "128k"
        if b <= 320000:
            return "320k" if b > 192000 else "192k"
        if b < 1500000:
            return "flac"
        return "hires"

    def _try_once(try_q):
        """单次请求，返回 dict 或 None"""
        sent_level = WY_LEVEL_MAP.get(try_q, "exhigh")
        br = WY_BR_MAP.get(try_q, 320000)
        # master/hires 用 v1 接口（带 level 参数），其他用旧接口（带 br）
        if try_q in ("master", "hires", "flac"):
            url = "https://interface3.music.163.com/eapi/song/enhance/player/url/v1"
            params = {"ids": f"[{song_id}]", "level": sent_level, "encodeType": "flac"}
            encrypted = eapi_encrypt("/api/song/enhance/player/url/v1", params)
        else:
            url = "https://interface3.music.163.com/eapi/song/enhance/player/url"
            params = {"ids": f"[{song_id}]", "br": br}
            encrypted = eapi_encrypt("/api/song/enhance/player/url", params)
        try:
            resp = requests.post(url, data={"params": encrypted}, headers=HEADERS, timeout=10)
            data = resp.json()
        except Exception as e:
            _wy_logger.warning(f"wy _try_once 请求失败 quality={try_q}: {e}")
            return None
        if data.get("code") != 200 or not data.get("data"):
            _wy_logger.info(
                f"[QUALITY] source=wy songId={song_id} "
                f"requested={quality} tried={try_q} sent_level={sent_level} "
                f"returned_code={data.get('code')} data=null"
            )
            return None
        song_data = data["data"][0]
        if not song_data.get("url"):
            _wy_logger.info(
                f"[QUALITY] source=wy songId={song_id} "
                f"requested={quality} tried={try_q} sent_level={sent_level} "
                f"returned_code={data.get('code')} url=null"
            )
            return None
        # 网易响应里带 br 字段
        actual_br = song_data.get("br")
        actual_q = _br_to_quality(actual_br)
        return {
            "url": song_data["url"],
            "br": actual_br,
            "size": song_data.get("size"),
            "actual_q": actual_q,
            "sent_level": sent_level,
            "try_q": try_q,
        }

    chain = get_fallback_chain(quality)
    tried = []
    for try_q in chain:
        tried.append(try_q)
        r = _try_once(try_q)
        if not r:
            continue
        actual_q = r["actual_q"]
        if actual_q == "unknown":
            actual_q = try_q
        direction = compare_quality(try_q, actual_q)
        is_fallback = (try_q != quality)
        is_upgraded = (direction == "upgraded")

        _wy_logger.info(
            f"[QUALITY] source=wy songId={song_id} "
            f"requested={quality} tried={try_q} "
            f"sent_level={r['sent_level']} "
            f"returned_br={r['br']} returned_url_feature={actual_q} "
            f"final_quality={actual_q} direction={direction} "
            f"fallback_chain={'→'.join(tried)}"
        )
        return {
            "url": r["url"],
            "br": r["br"],
            "size": r["size"],
            "quality": actual_q,
            "requested": quality,
            "fallback": is_fallback,
            "upgraded": is_upgraded,
            "direction": direction,
            "fallback_chain": "→".join(tried),
            "sent_level": r["sent_level"],
            "returned_level": actual_q,
            "url_feature": actual_q,
        }

    _wy_logger.warning(
        f"[QUALITY] source=wy songId={song_id} requested={quality} "
        f"tried={tried} ALL_FAILED"
    )
    return {"error": f"所有音质({','.join(tried)})均获取失败"}

def get_detail(song_id):
    url = "https://interface3.music.163.com/eapi/v3/song/detail"
    params = {"c": json.dumps([{"id": song_id}])}
    encrypted = eapi_encrypt("/api/v3/song/detail", params)
    try:
        resp = requests.post(url, data={"params": encrypted}, headers=HEADERS, timeout=10)
        data = resp.json()
        if data.get("code") == 200 and data.get("songs"):
            song = data["songs"][0]
            return {
                "song": {"id": song["id"], "name": song["name"],
                         "duration_text": f"{song['dt']//1000//60}:{song['dt']//1000%60:02d}"},
                "artist": ",".join(a["name"] for a in song.get("ar", [])),
                "album": {"id": song["al"]["id"], "name": song["al"]["name"]},
                "cover_sizes": {"500": song["al"].get("picUrl", "")},
                "covers": [song["al"].get("picUrl", "")],
            }
        return {"error": "获取详情失败"}
    except Exception as e:
        return {"error": str(e)}

def _parse_yrc_lines(lines):
    lrc_lines = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        t = line.get("t", 0)
        chars = line.get("c", [])
        text = "".join(c.get("tx", "") for c in chars if isinstance(c, dict))
        # 整数运算四舍五入，避免浮点银行家舍入（605ms → 00.61）
        t = int(t)
        minutes, rem = divmod(t, 60000)
        seconds, rem = divmod(rem, 1000)
        centis = (rem + 5) // 10
        if centis >= 100:
            seconds += 1
            centis -= 100
        if seconds >= 60:
            minutes += 1
            seconds -= 60
        lrc_lines.append(f"[{minutes:02d}:{seconds:02d}.{centis:02d}]{text}")
    return lrc_lines

def yrc_to_lrc(yrc_str):
    if not yrc_str:
        return ""
    yrc_str = yrc_str.strip()
    try:
        parsed = json.loads(yrc_str)
        if isinstance(parsed, dict) and "yrc" in parsed:
            inner = parsed["yrc"].get("lyric", "")
            if inner:
                return yrc_to_lrc(inner)
        if isinstance(parsed, dict):
            lines = [parsed]
        elif isinstance(parsed, list):
            lines = parsed
        else:
            return yrc_str
        lrc_lines = _parse_yrc_lines(lines)
        return "\n".join(lrc_lines) if lrc_lines else yrc_str
    except json.JSONDecodeError:
        pass
    # 逐行JSON：每行一个JSON对象
    lrc_lines = []
    for raw_line in yrc_str.split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
            parsed_lines = _parse_yrc_lines([obj])
            lrc_lines.extend(parsed_lines)
        except json.JSONDecodeError:
            lrc_lines.append(raw_line)
    return "\n".join(lrc_lines) if lrc_lines else yrc_str

def _needs_yrc_conversion(text):
    """判断歌词内容是否为网易YRC逐行JSON格式（{"t":..,"c":[{"tx":..}]}），需要转LRC"""
    if not text:
        return False
    s = text.strip()
    if s.startswith("{"):
        # 整体为JSON对象（含 {"yrc":{...}} 包装）或逐行JSON首行为{
        return True
    if s.startswith("[") and '"t"' in s and '"tx"' in s:
        # JSON数组形式
        return True
    # 部分行是JSON（前几行命中才触发，避免误判普通LRC）
    hits = 0
    for line in s.split("\n")[:5]:
        line = line.strip()
        if line.startswith("{") and '"t"' in line:
            hits += 1
    return hits >= 2

def get_lyric(song_id):
    url = "https://interface3.music.163.com/eapi/song/lyric/v1"
    params = {"id": song_id, "cp": False, "tv": 0, "lv": 1, "rv": 0, "kv": 0, "yv": 0, "ytv": 0, "ytl": 0}
    encrypted = eapi_encrypt("/api/song/lyric/v1", params)
    try:
        resp = requests.post(url, data={"params": encrypted}, headers=HEADERS, timeout=10)
        data = resp.json()
        if data.get("code") == 200:
            original = data.get("lrc", {}).get("lyric", "")
            if original and _needs_yrc_conversion(original):
                converted = yrc_to_lrc(original)
                if converted and converted != original:
                    original = converted
            translated = data.get("tlyric", {}).get("lyric", "")
            if translated and _needs_yrc_conversion(translated):
                converted = yrc_to_lrc(translated)
                if converted and converted != translated:
                    translated = converted
            return {"original": original, "translated": translated}
        return {"error": "获取歌词失败"}
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="网易云音乐API")
    parser.add_argument("action", choices=["url", "info", "lyric"], help="操作: url/info/lyric")
    parser.add_argument("song_id", help="歌曲ID")
    parser.add_argument("quality", nargs="?", default="320k", help="音质")
    args = parser.parse_args()

    result = {}
    if args.action == "url":
        playback = get_playback(args.song_id, args.quality)
        result = {"playback": playback}
    elif args.action == "info":
        result = {"detail": get_detail(args.song_id)}
    elif args.action == "lyric":
        result = {"lyric": get_lyric(args.song_id)}

    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
