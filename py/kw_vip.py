#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" 酷我音乐下载器（增强优化版 v2） """
import os
import re
import ast
import sys
import json
import time
import base64
import zlib
import logging
import threading
from urllib.parse import urlparse, parse_qs, unquote, urlencode
import requests

SAVE_DIR = "./downloads"
CACHE_TTL = 1800
CACHE_FILE = ""

QUALITIES = {
    "128k": {"br": "128kmp3", "format": "mp3", "desc": "标准 128k", "min_kbps": 96, "max_kbps": 192, "target_kbps": 128},
    "192k": {"br": "192kmp3", "format": "mp3", "desc": "中品 192k", "min_kbps": 161, "max_kbps": 255, "target_kbps": 192},
    "320k": {"br": "320kmp3", "format": "mp3", "desc": "高品 320k", "min_kbps": 256, "max_kbps": 360, "target_kbps": 320},
    "flac": {"br": "2000kflac", "format": "flac", "desc": "无损 FLAC", "min_kbps": 800, "max_kbps": 3000, "target_kbps": 2000},
    "hires": {"br": "4000kflac", "format": "flac", "desc": "Hi-Res 母带", "min_kbps": 3000, "max_kbps": 20000, "target_kbps": 4000},
    "hifi": {"br": "6000kflac", "format": "mflac", "desc": "超高音质(加密mflac)", "min_kbps": 4000, "max_kbps": 20000, "target_kbps": 6000},
    "master": {"br": "4000kflac", "format": "flac", "desc": "母带(Hi-Res)", "min_kbps": 3000, "max_kbps": 20000, "target_kbps": 4000},
    "atmos": {"br": "20501kzpga", "format": "mflac", "desc": "臻品全景声(加密mflac)", "min_kbps": 3000, "max_kbps": 23000, "target_kbps": 20501},
    "zpga": {"br": "20501kzpga", "format": "mflac", "desc": "臻品全景声(加密mflac)", "min_kbps": 3000, "max_kbps": 23000, "target_kbps": 20501},
    "sur": {"br": "20501kzpga", "format": "mflac", "desc": "臻品全景音(酷我12.0加密)", "min_kbps": 3000, "max_kbps": 23000, "target_kbps": 20501},
    "atmos_plus": {"br": "24900kszpga", "format": "mflac", "desc": "臻品全景声 Atmos+ 极致(加密mflac)", "min_kbps": 22000, "max_kbps": 26000, "target_kbps": 24900},
    "zp": {"br": "20900kzply", "format": "mflac", "desc": "臻品母带(加密mgg)", "min_kbps": 3000, "max_kbps": 25000, "target_kbps": 20900},
    "jymaster": {"br": "28000kzlmaster", "format": "mgg", "desc": "臻品母带极致 酷我12.0(加密mgg)", "min_kbps": 24000, "max_kbps": 32000, "target_kbps": 28000},
}

QUALITIES_ALIAS = {
    "atmos+": "atmos_plus", "atmosplus": "atmos_plus", "atmos2": "atmos_plus",
    "sur+": "atmos_plus",
    "zp+": "jymaster", "zpplus": "jymaster", "zp2": "jymaster",
}

_kw_logger = logging.getLogger("kw")
if not _kw_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _kw_logger.addHandler(_h)
_kw_logger.setLevel(logging.INFO)

def resolve_quality(quality):
    if not quality:
        return quality
    q = str(quality).strip().lower()
    if q in QUALITIES_ALIAS:
        return QUALITIES_ALIAS[q]
    if q in QUALITIES:
        return q
    raw = q.replace("+", "plus").replace("-", "").replace("_", "")
    for k, v in QUALITIES_ALIAS.items():
        kk = k.replace("+", "plus").replace("-", "").replace("_", "")
        if kk == raw:
            return v
    return q

BR_CHAIN = {
    "128k": ["128kmp3"],
    "192k": ["192kmp3", "128kmp3"],
    "320k": ["320kmp3", "192kmp3", "128kmp3"],
    "flac": ["2000kflac", "320kmp3", "192kmp3"],
    "hires": ["4000kflac", "2000kflac", "320kmp3"],
    "hifi": ["6000kflac", "4000kflac", "2000kflac", "320kmp3"],
    "atmos": ["20501kzpga", "2000kflac", "320kmp3"],
    "master": ["4000kflac", "2000kflac", "320kmp3"],
    "zpga": ["20501kzpga", "2000kflac", "320kmp3"],
    "sur": ["20501kzpga", "2000kflac", "320kmp3"],
    "atmos_plus": ["24900kszpga", "20501kzpga", "2000kflac", "320kmp3"],
    "zp": ["20900kzply", "2000kflac", "320kmp3"],
    "jymaster": ["28000kzlmaster", "20900kzply", "2000kflac", "320kmp3"],
}

LOSSLESS_PLUS = {"flac", "hires", "hifi", "atmos", "master", "zp", "zpga", "sur", "atmos_plus", "jymaster"}
LOSSLESS_FMTS = {"flac", "mgg", "mflac"}
ENCRYPTED_FMTS = {"mgg", "mflac"}
ALL_QUALITY_KEYS = list(QUALITIES.keys()) + list(QUALITIES_ALIAS.keys())

# 酷我服务端风控降级时返回的试听/预览码率形态（匿名/非VIP时大量出现）
# br=6   老版试听标记
# br=1   新版 source 试听（format=mp3，30s 片段）
# br=48  付费歌试听（format=aac/m4a）
# br=100 免费歌假无损（format=ogg，不是无损）
PREVIEW_BITRATES = {1, 6, 48, 100}
# 出现即视为试听的格式（酷我正常无损/MP3 不会用 aac 直链）
PREVIEW_FORMATS = {"aac"}

# 最近一次 get_url 失败的详细原因（供 main/download 提示用户）
LAST_FAIL_REASON = ""

CHANNELS_STAGE_PREMIUM = [
    {"name": "car_conv2", "url": "http://anymatch.kuwo.cn/mobi.s", "type": "convert_url2", "source": "kwplayercar_ar_6.0.0.9_B_jiakong_vh.apk"},
    {"name": "mobi_conv2", "url": "http://mobi.kuwo.cn/mobi.s", "type": "convert_url2", "source": "kwplayer_ar_8.5.5.0_apk_keluze.apk"},
]
CHANNELS_STAGE_STABLE = [
    # nuoweida 老版播放器 source：实测对含 flac/320k 授权的歌曲可直接返回无损/高品直链（匿名可用）
    {"name": "mobi_nuoweida", "url": "https://mobi.kuwo.cn/mobi.s", "type": "convert_url_with_sign", "source": "kwplayer_ar_4.4.2.7_B_nuoweida_vh.apk"},
    {"name": "nmobi_nuoweida", "url": "https://nmobi.kuwo.cn/mobi.s", "type": "convert_url_with_sign", "source": "kwplayer_ar_4.4.2.7_B_nuoweida_vh.apk"},
    {"name": "mobi_with_sign", "url": "https://mobi.kuwo.cn/mobi.s", "type": "convert_url_with_sign", "source": "kwplayer_ar_8.5.5.0_apk_keluze.apk"},
    {"name": "car_sign_6005", "url": "http://nmobi.kuwo.cn/mobi.s", "type": "convert_url_with_sign", "source": "kwplayercar_ar_6.0.0.5_B_jiakong_vh.apk"},
    {"name": "car_sign_6100", "url": "http://nmobi.kuwo.cn/mobi.s", "type": "convert_url_with_sign", "source": "kwplayercar_ar_6.1.0.0_B_jiakong_vh.apk"},
]
CHANNELS_STAGE_FALLBACK = [
    {"name": "sign_mobi_http", "url": "http://mobi.kuwo.cn/mobi.s", "type": "convert_url_with_sign", "source": "kwplayer_ar_8.5.5.0_apk_keluze.apk"},
    # anti.s 老接口：无损已被拒，但 mp3 档仍可用（酷我反代直链），作为 mp3 兜底
    {"name": "anti_mp3", "url": "https://antiserver.kuwo.cn/anti.s", "type": "convert_url3"},
]
CHANNELS = CHANNELS_STAGE_PREMIUM + CHANNELS_STAGE_STABLE + CHANNELS_STAGE_FALLBACK

UA_MOBI = "okhttp/3.10.0"
UA_WEB = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
TIMEOUT = 15

class UrlCache:
    def __init__(self, ttl=CACHE_TTL, cache_file=CACHE_FILE):
        self.ttl = ttl
        self.cache_file = cache_file
        self._data = {}
        self._lock = threading.Lock()
        if self.cache_file:
            self._load()

    def _load(self):
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            self._data = {k: v for k, v in raw.items() if now - v.get("ts", 0) < self.ttl}
        except Exception:
            self._data = {}

    def _save(self):
        if not self.cache_file:
            return
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
        except Exception:
            pass

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item and time.time() - item.get("ts", 0) < self.ttl:
                return item.get("value")
            if item:
                self._data.pop(key, None)
            return None

    def set(self, key, value):
        with self._lock:
            self._data[key] = {"value": value, "ts": time.time()}
            self._save()

_cache = UrlCache()

def json_like(text):
    try:
        return json.loads(text)
    except Exception:
        try:
            return ast.literal_eval(text)
        except Exception:
            t = text.strip().lstrip("\ufeff")
            try:
                return json.loads(t)
            except Exception:
                return None

def normalize_fmt(fmt):
    fmt = str(fmt or "").lower().strip()
    fmt = re.sub(r"^\d+k", "", fmt)
    return fmt

def _extract_ekey_from_url(url):
    result = {}
    if not url:
        return result
    try:
        q = parse_qs(urlparse(url).query)
        for k in ("ekey", "album_id", "encrypt", "sign", "key"):
            if k in q:
                v = q[k][0]
                if len(v) > 3:
                    result[k] = unquote(v)
    except Exception:
        pass
    return result

def _extract_ekey_deep(data, depth=0, max_depth=5):
    found = {}
    if depth > max_depth or data is None:
        return found
    EKEY_KEYS = ("ekey", "album_id", "albumid", "secret", "seckey", "decrypt_key", "decryptkey", "mkey", "music_key", "k", "key")
    if isinstance(data, dict):
        for k, v in data.items():
            lk = str(k).lower()
            if lk in EKEY_KEYS and isinstance(v, str) and len(v) >= 8:
                found[lk] = v
            if isinstance(v, str) and 16 <= len(v) <= 512:
                if lk in ("data", "info", "extra", "msg", "message", "result"):
                    try:
                        sub = json_like(v)
                        if sub:
                            found.update(_extract_ekey_deep(sub, depth + 1, max_depth))
                    except Exception:
                        pass
            found.update(_extract_ekey_deep(v, depth + 1, max_depth))
    elif isinstance(data, list):
        for item in data:
            found.update(_extract_ekey_deep(item, depth + 1, max_depth))
    return found

def estimate_quality_by_size(size_bytes, duration_sec):
    result = {
        "estimated_kbps": 0,
        "quality_level": "unknown",
        "quality_desc": "未知音质",
        "size_mb": 0,
        "duration_sec": duration_sec or 0,
        "match_target": "",
    }
    if not size_bytes or not duration_sec or duration_sec <= 0:
        return result
    size_mb = round(size_bytes / 1024 / 1024, 2)
    estimated_kbps = round((size_bytes * 8) / 1000 / duration_sec, 1)
    result["size_mb"] = size_mb
    result["estimated_kbps"] = estimated_kbps
    if estimated_kbps < 161:
        result["quality_level"] = "128k"
        result["quality_desc"] = "标准音质 (~128kbps)"
    elif estimated_kbps < 256:
        result["quality_level"] = "192k"
        result["quality_desc"] = "中品音质 (~192kbps)"
    elif estimated_kbps < 800:
        result["quality_level"] = "320k"
        result["quality_desc"] = "高品音质 (~320kbps)"
    elif estimated_kbps < 3000:
        result["quality_level"] = "flac"
        result["quality_desc"] = "无损 FLAC (~2000kbps)"
    elif estimated_kbps < 8000:
        result["quality_level"] = "hires"
        result["quality_desc"] = "Hi-Res / 母带 (~4000kbps+)"
    elif estimated_kbps < 10000:
        result["quality_level"] = "hifi"
        result["quality_desc"] = "超高音质 加密mflac (~6000kbps+)"
    elif estimated_kbps < 23000:
        result["quality_level"] = "zpga"
        result["quality_desc"] = "臻品全景声 / 全景音 atmos/sur (~20000kbps+)"
    elif estimated_kbps < 26000:
        result["quality_level"] = "atmos_plus"
        result["quality_desc"] = "臻品全景声 Atmos+ 极致 (~24900kbps+)"
    elif estimated_kbps < 28000:
        result["quality_level"] = "zp"
        result["quality_desc"] = "臻品母带 zply (~20900kbps+)"
    else:
        result["quality_level"] = "jymaster"
        result["quality_desc"] = "臻品母带极致 jymaster (~28000kbps+)"
    return result

def verify_quality_match(target_quality, estimated_kbps, actual_format):
    target_quality = resolve_quality(target_quality)
    if target_quality not in QUALITIES:
        return True, ""
    cfg = QUALITIES[target_quality]
    want_fmt = normalize_fmt(cfg["format"])
    actual_fmt_n = normalize_fmt(actual_format)
    fmt_ok = (want_fmt == actual_fmt_n) or \
        (want_fmt == "flac" and actual_fmt_n in LOSSLESS_FMTS) or \
        (want_fmt == "mflac" and actual_fmt_n in ENCRYPTED_FMTS)
    if not fmt_ok:
        return False, f"格式不匹配: 期望 {want_fmt}, 实际 {actual_fmt_n}"
    return True, ""

def _get_content_length(url):
    try:
        r = requests.head(url, headers={"User-Agent": UA_MOBI}, timeout=TIMEOUT, allow_redirects=True)
        cl = r.headers.get("Content-Length")
        if cl:
            return int(cl)
    except Exception:
        pass
    return None

H5_LRC_ENDPOINT = "http://m.kuwo.cn/newh5/singles/songinfoandlrc"
APP_LRCX_ENDPOINT = "http://mobi.kuwo.cn/mobi.s"
NMOBI_LRCX_ENDPOINT = "http://nmobi.kuwo.cn/mobi.s"
_PIC_CDN = [
    "https://img1.kwcdn.kuwo.cn",
    "https://img2.kwcdn.kuwo.cn",
    "https://img3.kwcdn.kuwo.cn",
]
_ALBUM_PIC_SIZES = (100, 300, 500, 800)

def _sec_to_lrc_time(sec_str):
    try:
        sec = max(0.0, float(sec_str))
    except Exception:
        return "[00:00.00]"
    mm = int(sec // 60)
    ss = sec - mm * 60
    return f"[{mm:02d}:{ss:05.2f}]"

def _lrclist_to_lrc_text(lrclist):
    if not lrclist:
        return ""
    lines = []
    for row in lrclist:
        if not isinstance(row, dict):
            continue
        tm = row.get("time", "0")
        lyric = row.get("lineLyric") or row.get("lyric") or ""
        lines.append(f"{_sec_to_lrc_time(tm)}{lyric}")
    return "\n".join(lines)

def _try_decompress_lrcx_body(body):
    if not body:
        return "", "none"
    candidates = []
    for skip in range(0, min(16, len(body))):
        candidates.append(body[skip:])
    for frag in candidates:
        for wbits in (zlib.MAX_WBITS | 16, zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                dec = zlib.decompress(frag, wbits)
                text = dec.decode("utf-8", errors="replace")
                if len(text.strip()) > 20:
                    return text, "krc"
            except Exception:
                pass
    return base64.b64encode(body).decode("ascii"), "lrcx_binary"

def _build_url_with_params(base, params):
    return f"{base}?{urlencode(params)}"

def get_extra(rid, duration_sec=None):
    if not rid:
        return {}
    result = {
        "lrc_line_url": "",
        "lrc_word_url": "",
        "lrc_word_url_car": "",
        "lrc_line": [],
        "lrc": "",
        "lrc_word": "",
        "lrc_word_format": "none",
        "album": "",
        "album_id": "",
        "artist": "",
        "song_name": "",
        "album_pic": "",
        "album_pic_100": "",
        "album_pic_300": "",
        "album_pic_500": "",
        "album_pic_800": "",
        "artist_pic": "",
    }
    rid_s = str(rid)
    h5_params = {"musicId": rid_s, "httpsStatus": "1"}
    result["lrc_line_url"] = _build_url_with_params(H5_LRC_ENDPOINT, h5_params)
    lrcx_params = {"type": "lyric", "rid": rid_s, "format": "json", "source": "kwplayer_ar_8.5.5.0_apk_keluze.apk", "user": "0", "network": "WIFI", "f": "web"}
    result["lrc_word_url"] = _build_url_with_params(APP_LRCX_ENDPOINT, lrcx_params)
    lrcx_car_params = dict(lrcx_params)
    lrcx_car_params["source"] = "kwplayercar_ar_6.0.0.5_B_jiakong_vh.apk"
    result["lrc_word_url_car"] = _build_url_with_params(NMOBI_LRCX_ENDPOINT, lrcx_car_params)
    songinfo = {}
    lrclist = []
    try:
        r = requests.get(H5_LRC_ENDPOINT, params=h5_params, headers={"User-Agent": UA_WEB}, timeout=TIMEOUT)
        d = r.json() if r.status_code == 200 else None
        data = (d or {}).get("data")
        if isinstance(data, dict):
            songinfo = data.get("songinfo") or {}
            lrclist = data.get("lrclist") or []
    except Exception:
        pass
    if songinfo:
        result["album"] = str(songinfo.get("album") or "")
        result["album_id"] = str(songinfo.get("albumId") or "")
        result["artist"] = str(songinfo.get("artist") or "")
        result["song_name"] = str(songinfo.get("songName") or "")
    if lrclist:
        result["lrc_line"] = lrclist
        result["lrc"] = _lrclist_to_lrc_text(lrclist)
    try:
        r = requests.get(APP_LRCX_ENDPOINT, params=lrcx_params, headers={"User-Agent": UA_MOBI}, timeout=TIMEOUT)
        raw = r.content if r.status_code == 200 else b""
        if raw:
            body = raw
            for pat in (b"\n\n", b"\r\n\r\n"):
                pos = raw.find(pat)
                if pos > 0:
                    body = raw[pos + len(pat):]
                    break
            word_text, word_fmt = _try_decompress_lrcx_body(body)
            result["lrc_word"] = word_text
            result["lrc_word_format"] = word_fmt
    except Exception:
        pass
    pic_raw = str(songinfo.get("pic") or "").strip()

    def _normalize_pic(url, size=None):
        u = (url or "").strip()
        if not u:
            return ""
        if u.startswith("//"):
            u = "https:" + u
        if not u.startswith(("http://", "https://")):
            u = _PIC_CDN[0] + ("/" if not u.startswith("/") else "") + u
        if size and "/star/" in u:
            for sub in ("albumcover", "starheads", "userpl2015"):
                pat_old = f"/{sub}/"
                if pat_old in u:
                    idx = u.find(pat_old) + len(pat_old)
                    tail = u[idx:]
                    m = re.match(r"^(\d+)/", tail)
                    if m:
                        tail = tail.replace(f"{m.group(1)}/", f"{size}/", 1)
                    u = u[:idx] + tail
                    break
        return u

    if pic_raw:
        for sz in _ALBUM_PIC_SIZES:
            result[f"album_pic_{sz}"] = _normalize_pic(pic_raw, size=sz)
    else:
        aid = result["album_id"] or rid_s
        a = aid[-2] if len(aid) >= 2 else "0"
        b = aid[-1] if len(aid) >= 1 else "0"
        prefix_cdn = _PIC_CDN[0]
        for sz in _ALBUM_PIC_SIZES:
            result[f"album_pic_{sz}"] = (f"{prefix_cdn}/star/albumcover/{sz}/{a}/{b}/{aid}.jpg")
    result["album_pic"] = result.get("album_pic_500") or ""
    artist_id = str(songinfo.get("artistId") or "0")
    if artist_id and artist_id not in ("0", ""):
        aid2 = artist_id
        a = aid2[-2] if len(aid2) >= 2 else "0"
        b = aid2[-1] if len(aid2) >= 1 else "0"
        result["artist_pic"] = (f"{_PIC_CDN[0]}/star/starheads/500/{a}/{b}/{aid2}.jpg")
    elif pic_raw:
        result["artist_pic"] = _normalize_pic(pic_raw, size=500)
    return result

def search(keyword, page=1, rn=20):
    url = "http://search.kuwo.cn/r.s"
    params = {
        "client": "kt",
        "all": keyword,
        "pn": page,
        "rn": rn,
        "uid": "794762570",
        "ver": "kwplayer_ar_9.2.2.1",
        "vipver": "1",
        "show_copyright_off": "1",
        "newver": "1",
        "ft": "music",
        "cluster": "0",
        "strategy": "2012",
        "encoding": "utf8",
        "rformat": "json",
        "mobi": "1",
        "issubtitle": "1",
    }
    r = requests.get(url, params=params, headers={"User-Agent": UA_WEB}, timeout=TIMEOUT)
    try:
        data = json_like(r.text)
    except Exception:
        return []
    result = []
    for item in data.get("abslist", []):
        mrid = item.get("MUSICRID", "")
        rid = re.sub(r"\D", "", mrid)
        dur = item.get("DURATION", "")
        try:
            dur_sec = int(float(dur)) if dur else 0
        except Exception:
            dur_sec = 0
        result.append({
            "rid": rid,
            "name": item.get("NAME", ""),
            "artist": item.get("ARTIST", ""),
            "album": item.get("ALBUM", ""),
            "duration": dur,
            "duration_sec": dur_sec,
            "pay": item.get("PAY", ""),
            "minfo": item.get("MINFO", ""),
            "n_minfo": item.get("N_MINFO", ""),
        })
    return result

def _try_channel(ch, rid, q):
    ua = UA_MOBI
    info = None
    if ch["type"] == "convert_url_with_sign":
        params = {
            "user": "0",
            "source": ch["source"],
            "type": ch["type"],
            "br": q["br"],
            "format": q["format"],
            "sig": "0",
            "rid": rid,
            "network": "WIFI",
            "f": "web",
        }
        r = requests.get(ch["url"], params=params, headers={"User-Agent": ua}, timeout=TIMEOUT)
        d = r.json()
        if d.get("code") == 200 and d.get("data", {}).get("url"):
            info = {
                "br": d["data"].get("bitrate"),
                "format": d["data"].get("format"),
                "url": d["data"]["url"],
                "ekey": d["data"].get("ekey") or "",
                "_raw_resp": d,
            }
    elif ch["type"] == "convert_url2":
        params = {
            "user": "0",
            "source": ch["source"],
            "type": ch["type"],
            "br": q["br"],
            "format": q["format"],
            "rid": rid,
            "network": "WIFI",
            "f": "web",
            "mode": "download",
        }
        r = requests.get(ch["url"], params=params, headers={"User-Agent": ua}, timeout=TIMEOUT)
        d = {}
        for line in r.text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
        if d.get("url") and not d["url"].startswith("None"):
            info = {
                "br": d.get("bitrate"),
                "format": d.get("format"),
                "url": d["url"],
                "ekey": d.get("ekey") or "",
                "_raw_resp": d,
            }
    elif ch["type"] == "convert_url3":
        # anti.s 老接口：仅 mp3 档可用（flac 返回 refuse），返回 JSON 或纯文本 URL
        if normalize_fmt(q.get("format")) not in ("mp3",):
            return None
        params = {
            "type": "convert_url3",
            "rid": rid,
            "format": q.get("format") or "mp3",
            "response": "url",
        }
        r = requests.get(ch["url"], params=params, headers={"User-Agent": UA_MOBI}, timeout=TIMEOUT)
        d = {}
        try:
            d = r.json()
        except Exception:
            text = (r.text or "").strip()
            if text.startswith("http"):
                d = {"code": 200, "url": text}
        if d.get("code") == 200 and d.get("url"):
            info = {
                "br": 128,
                "format": "mp3",
                "url": d["url"],
                "ekey": "",
                "_raw_resp": d,
            }
    if not info:
        return None
    ekey_final = info.get("ekey") or ""
    raw = info.pop("_raw_resp", {})
    url_ek = _extract_ekey_from_url(info.get("url", ""))
    if not ekey_final and url_ek.get("ekey"):
        ekey_final = url_ek["ekey"]
    if not ekey_final and raw:
        extra_ek = _extract_ekey_deep(raw)
        if extra_ek.get("ekey"):
            ekey_final = extra_ek["ekey"]
        elif extra_ek.get("album_id"):
            ekey_final = extra_ek["album_id"]
    info["ekey"] = ekey_final
    return info

def _classify_result(info, br_str, quality_cfg):
    """判断 _try_channel 返回的结果是否合格。

    核心目的：酷我服务端在匿名/非VIP时会静默降级——
      * 请求无损 → 返回 48k aac / 100k ogg / 128k mp3（试听或假无损）
      * 请求 320k → 只给 128k mp3
    旧代码只拦 br=6 一种试听形态，其余全部当作成功接受，
    导致"无损只下到最低音质+试听"。这里按格式+码率双重校验拒绝降级结果。
    返回 (accept: bool, reason: str)
    """
    try:
        br = int(info.get("br") or 0)
    except Exception:
        br = 0
    fmt = normalize_fmt(info.get("format"))
    if not fmt:
        return False, f"无格式信息(br={br})"
    # 1) 已知试听码率形态
    if br in PREVIEW_BITRATES:
        return False, f"服务端返回试听(br={br}, fmt={fmt})"
    # 2) aac/m4a 直链一律视为试听（酷我正常无损/MP3 不会返回 aac）
    if fmt in PREVIEW_FORMATS:
        return False, f"服务端返回试听(fmt={fmt}, br={br})"
    # 3) 无损请求必须真的拿到无损格式
    br_is_lossless = any(
        ext in str(br_str).lower() for ext in ('flac', 'mflac', 'mgg', 'zply', 'zpga', 'szpga', 'zlmaster')
    )
    if br_is_lossless:
        if fmt not in LOSSLESS_FMTS:
            return False, f"无损请求被降级为{fmt}(br={br})"
        if br and br < 800:
            return False, f"无损请求返回低码率(br={br}, fmt={fmt})"
    # 4) MP3 档码率必须达到该档下限（QUALITIES[min_kbps]）
    target_min = quality_cfg.get("min_kbps")
    if target_min and br and br < target_min:
        return False, f"请求{quality_cfg.get('desc')}但服务端只给{br}k({fmt})"
    return True, ""

def get_url(rid, quality, duration_sec=None, strict_high_end=None):
    if quality not in QUALITIES:
        quality = resolve_quality(quality)
    if quality not in QUALITIES:
        raise ValueError(f"未知音质: {quality}")
    cache_key = f"{rid}:{quality}"
    cached = _cache.get(cache_key)
    if cached:
        return cached, "cache"
    want_lossless = quality in LOSSLESS_PLUS
    cfg = QUALITIES[quality]
    target_wants_plain_flac = (cfg.get("format") == "flac")
    _extra_loaded = [None]

    def _load_extra_once():
        if _extra_loaded[0] is None:
            try:
                _extra_loaded[0] = get_extra(rid, duration_sec=duration_sec) or {}
            except Exception:
                _extra_loaded[0] = {}
        return _extra_loaded[0]

    def _finalize(result_ch, do_cache=True):
        if result_ch is None:
            return None, None
        res, chname = result_ch
        extra = _load_extra_once()
        if extra:
            for ek, ev in extra.items():
                res.setdefault(ek, ev)
        if do_cache:
            _cache.set(cache_key, res)
        return res, chname

    chain = BR_CHAIN.get(quality)
    if not chain:
        if quality in ("atmos_plus", "sur"):
            chain = BR_CHAIN.get("zpga", [])
        elif quality == "jymaster":
            chain = BR_CHAIN.get("zp", [])
        elif quality == "hifi":
            chain = BR_CHAIN.get("hires", [])
        else:
            chain = [cfg["br"]]

    def _parse_br_entry(br_str):
        br_str = str(br_str)
        k_idx = br_str.lower().rfind("k")
        if k_idx < 0:
            return br_str, cfg["format"]
        fmt = br_str[k_idx+1:]
        if not fmt:
            return br_str, cfg["format"]
        fmt_l = fmt.lower()
        if fmt_l in ("zply", "mgg"):
            return br_str, "mgg"
        if fmt_l in ("zpga", "szpga", "mflac", "zlmaster"):
            return br_str, "mflac"
        if fmt_l in ("flac",):
            return br_str, "flac"
        if fmt_l in ("mp3",):
            return br_str, "mp3"
        return br_str, fmt_l

    all_stages = [
        ("premium", CHANNELS_STAGE_PREMIUM),
        ("stable", CHANNELS_STAGE_STABLE),
        ("fallback", CHANNELS_STAGE_FALLBACK),
    ]
    tried_br = []
    _reject_reasons = []
    for idx, br_entry in enumerate(chain):
        br_str, br_fmt = _parse_br_entry(br_entry)
        tried_br.append(br_str)
        q_cfg = dict(cfg)
        q_cfg["br"] = br_str
        q_cfg["format"] = br_fmt
        _kw_logger.info(
            f"[QUALITY] source=kw rid={rid} requested={quality} "
            f"trying_br={br_str} format={br_fmt} idx={idx}"
        )
        for stage_name, stage_channels in all_stages:
            for ch in stage_channels:
                try:
                    info = _try_channel(ch, rid, q_cfg)
                except Exception as e:
                    _kw_logger.warning(
                        f"[QUALITY] source=kw rid={rid} br={br_str} "
                        f"channel={ch['name']} EXCEPTION: {e}"
                    )
                    info = None
                if not info:
                    continue
                fmt = normalize_fmt(info.get("format"))
                accept, reason = _classify_result(info, br_str, q_cfg)
                if not accept:
                    _reject_reasons.append(f"{ch['name']}:{reason}")
                    _kw_logger.info(
                        f"[QUALITY] source=kw rid={rid} br={br_str} "
                        f"channel={ch['name']} REJECTED({reason})"
                    )
                    continue
                current_br_is_flac = br_str.lower().endswith("flac")
                if target_wants_plain_flac and current_br_is_flac and fmt in ENCRYPTED_FMTS:
                    _reject_reasons.append(f"{ch['name']}:明文无损请求返回加密{fmt}")
                    _kw_logger.info(
                        f"[QUALITY] source=kw rid={rid} br={br_str} "
                        f"channel={ch['name']} REJECTED(encrypted {fmt} returned for plain flac request)"
                    )
                    continue
                result = {
                    "url": info["url"],
                    "format": fmt,
                    "br": info.get("br"),
                    "br_req": br_str,
                    "ekey": info.get("ekey") or "",
                    "album_id": info.get("album_id") or "",
                    "stage": stage_name,
                }
                _kw_logger.info(
                    f"[QUALITY] source=kw rid={rid} requested={quality} "
                    f"tried_br={'→'.join(tried_br)} "
                    f"channel={ch['name']} stage={stage_name} "
                    f"ACCEPTED format={fmt} br={info.get('br')}"
                )
                return _finalize((result, ch["name"]))
        _kw_logger.info(
            f"[QUALITY] source=kw rid={rid} br={br_str} ALL_CHANNELS_FAILED, "
            f"falling back to next br in chain"
        )
    _kw_logger.warning(
        f"[QUALITY] source=kw rid={rid} requested={quality} "
        f"tried_br={'→'.join(tried_br)} ALL_FAILED"
    )
    global LAST_FAIL_REASON
    if _reject_reasons:
        LAST_FAIL_REASON = (
            f"请求{quality}被酷我服务端降级/拒绝，各通道原因: "
            + "; ".join(dict.fromkeys(_reject_reasons))[:400]
            + "。该歌曲可能未开放此音质（无 flac/320k 授权，或仅提供 mgg 加密格式），"
              "可尝试 128k/320k 或换一个版本/歌手演唱的歌曲。"
        )
    else:
        LAST_FAIL_REASON = (
            f"请求{quality}所有通道均无有效返回（网络/风控/歌曲授权）。"
            "可尝试 128k/320k，或检查歌曲是否为本区可播放版本。"
        )
    _kw_logger.warning(f"[QUALITY] source=kw FAIL_REASON: {LAST_FAIL_REASON}")
    return None, None

def download(rid, quality, save_dir=SAVE_DIR, name=None, artist=None, duration_sec=None):
    info, channel = get_url(rid, quality, duration_sec=duration_sec)
    if not info:
        return None, {"error": LAST_FAIL_REASON or "所有渠道获取直链失败（歌曲无该音质授权或网络异常）"}
    os.makedirs(save_dir, exist_ok=True)
    ext_map = {"flac": "flac", "mp3": "mp3", "ogg": "ogg", "aac": "m4a", "mgg": "mgg", "mflac": "mflac"}
    real_ext = ext_map.get(info.get("format"), info.get("format"))
    fname = f"{name or rid} - {artist or ''} [{quality}].{real_ext}".replace("/", "_")
    path = os.path.join(save_dir, fname)
    url = info["url"].split("?")[0]
    if not url:
        url = info["url"]
    r = requests.get(info["url"], headers={"User-Agent": UA_MOBI}, timeout=60, stream=True)
    r.raise_for_status()
    total_bytes = 0
    with open(path, "wb") as f:
        for chunk in r.iter_content(65536):
            if chunk:
                f.write(chunk)
                total_bytes += len(chunk)
    size = os.path.getsize(path)
    ekey_saved = ""
    ekey_source = ""
    if info.get("format") in ENCRYPTED_FMTS:
        ekey = info.get("ekey") or ""
        if not ekey:
            ekey = _force_fetch_ekey(rid, quality)
            ekey_source = " (force_fetch)"
        if ekey:
            ekey_path = os.path.splitext(path)[0] + ".ekey"
            with open(ekey_path, "w", encoding="utf-8") as f:
                f.write(ekey)
            ekey_saved = ekey_path
        info["ekey"] = ekey
    _cache.set(f"{rid}:{quality}", info)
    real_fmt_desc = detect_format(path)
    qe = estimate_quality_by_size(size, duration_sec)
    ok, reason = verify_quality_match(quality, qe.get("estimated_kbps", 0), info.get("format"))
    warning = ""
    if size < 100 * 1024:
        warning = f"疑似试听片段 ({size//1024}KB，非完整歌曲)"
    elif not ok:
        warning = reason
    detail = {
        "format_desc": real_fmt_desc,
        "size": size,
        "size_mb": round(size / 1024 / 1024, 2),
        "quality_estimate": qe,
        "ekey": info.get("ekey") or "",
        "ekey_saved": ekey_saved,
        "channel": channel,
        "warning": warning,
        "match_target": quality if ok else "",
        "ekey_source": ekey_source,
    }
    return path, detail

def _force_fetch_ekey(rid, quality):
    quality = resolve_quality(quality)
    if quality not in QUALITIES:
        return ""
    cfg = QUALITIES[quality]
    if normalize_fmt(cfg["format"]) not in ENCRYPTED_FMTS and \
            quality not in ("zp", "zpga", "atmos", "sur", "atmos_plus", "jymaster", "hifi"):
        return ""
    sign_channels = [c for c in CHANNELS if c["type"] == "convert_url_with_sign"]
    for ch in sign_channels:
        try:
            info = _try_channel(ch, rid, cfg)
            if info and info.get("ekey"):
                return info["ekey"]
        except Exception:
            continue
    return ""

def detect_format(path):
    try:
        with open(path, "rb") as f:
            head = f.read(32)
    except Exception:
        return "无法读取文件"
    if not head:
        return "空文件"
    if head[:4] == b"fLaC":
        return "FLAC 无损"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "MP3"
    if head[:4] == b"OggS":
        return "OGG"
    if head[4:8] == b"ftyp":
        return "M4A/AAC"
    if head[:4] == b"MFLA" or head[:5] == b"MFLAC":
        return "MFLAC 加密(酷我臻品)"
    if head[:4] == b"MGG\x01" or head[:3] == b"MGG":
        return "MGG 加密(臻品母带)"
    if head[:1] == b"\x22" or head[:2] == b"\"-":
        return "MGG 加密(臻品母带/变体)"
    if head[:2] == b"PK":
        return "ZIP/压缩包(错误)"
    return f"未知(头:{head[:12].hex()})"

def main():
    args = sys.argv[1:]
    json_mode = "--json" in args
    ekey_only = "--ekey" in args
    download_mode = "--download" in args
    for flag in ("--json", "--ekey", "--download"):
        if flag in args:
            args.remove(flag)
    if args and args[0].isdigit():
        rid = args[0]
        quality = args[1] if len(args) > 1 else "flac"
        duration_sec = None
        if len(args) > 2 and args[2].isdigit():
            duration_sec = int(args[2])
        info, channel = get_url(rid, quality, duration_sec=duration_sec)
        if info:
            if ekey_only:
                ekey = info.get("ekey") or _force_fetch_ekey(rid, quality)
                if ekey:
                    print(ekey)
                else:
                    print("该音质/渠道未返回 ekey（可能为明文格式）", file=sys.stderr)
                    sys.exit(1)
                return
            result = dict(info)
            if isinstance(info, dict):
                pass
            result.update({
                "rid": rid,
                "quality": quality,
                "channel": channel,
                "url": info["url"],
                "format": info["format"],
                "br": info.get("br"),
                "br_req": info.get("br_req"),
                "ekey": info.get("ekey") or "",
                "album_id": info.get("album_id") or "",
                "content_length": info.get("content_length"),
                "estimated_kbps_by_head": info.get("estimated_kbps_by_head"),
                "is_encrypted": info["format"] in ENCRYPTED_FMTS,
            })
            if download_mode:
                path, detail = download(rid, quality, duration_sec=duration_sec)
                if path:
                    result["downloaded_path"] = path
                    result.update(detail)
                else:
                    result.update(detail)
            if json_mode:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(result["url"])
                if result["ekey"]:
                    print(f"ekey: {result['ekey']}", file=sys.stderr)
                if result.get("estimated_kbps_by_head"):
                    print(f"预估比特率(HEAD): {result['estimated_kbps_by_head']}kbps", file=sys.stderr)
                if download_mode and result.get("downloaded_path"):
                    print(f"下载完成: {result['downloaded_path']}", file=sys.stderr)
                    qe = result.get("quality_estimate", {})
                    if qe:
                        print(f"音质判定: {qe.get('quality_desc')} "
                              f"(约{qe.get('estimated_kbps')}kbps, "
                              f"{qe.get('size_mb')}MB)", file=sys.stderr)
        else:
            err = {"error": LAST_FAIL_REASON or "获取直链失败", "rid": rid, "quality": quality}
            if json_mode:
                print(json.dumps(err, ensure_ascii=False))
            else:
                print(f"获取直链失败：{err['error']}", file=sys.stderr)
                sys.exit(1)
        return
    if len(args) < 1:
        print("用法: python kw_vip.py 关键词 [音质] [序号] [--json]")
        print(" 或: python kw_vip.py RID [音质] [时长秒] [--json] [--ekey] [--download]")
        print(f"音质可选: {list(QUALITIES)}（默认 flac）")
        return
    keyword = args[0]
    quality = args[1] if len(args) > 1 else "flac"
    idx = int(args[2]) if len(args) > 2 else 1
    songs = search(keyword)
    if not songs:
        print("未搜索到结果")
        return
    if json_mode:
        out = {
            "keyword": keyword,
            "total": len(songs),
            "list": songs,
        }
        print(json.dumps(out, ensure_ascii=False))
        return
    print(f"\n「{keyword}」搜索结果：")
    for i, s in enumerate(songs, 1):
        pay = "付费" if s["pay"] not in ("", "0", None) else "免费"
        tags = []
        nminfo = s["n_minfo"] or s["minfo"]
        if "format:flac" in nminfo:
            tags.append("无损")
        if "bitrate:4000" in nminfo or "bitrate:4000k" in nminfo:
            tags.append("Hi-Res")
        if "format:mflac" in nminfo or "format:mgg" in nminfo:
            tags.append("臻品")
        tag = ("[" + "+".join(tags) + "]") if tags else "[低音质]"
        dur = s.get("duration") or "-"
        print(f" {i:>2}. {s['name']:<20} - {s['artist']:<12} "
              f"{tag:<12} rid={s['rid']:<10} 时长={dur}s ({pay})")
    if idx < 1 or idx > len(songs):
        print("序号超出范围")
        return
    song = songs[idx - 1]
    print(f"\n下载: {song['name']} - {song['artist']} 音质: {quality} "
          f"时长: {song.get('duration_sec') or '?'}s")
    path, detail = download(song["rid"], quality, name=song["name"], artist=song["artist"],
                            duration_sec=song.get("duration_sec"))
    if path:
        qe = detail.get("quality_estimate", {})
        print(f"完成: {path}")
        print(f"真实格式: {detail['format_desc']}")
        print(f"文件大小: {detail.get('size_mb')}MB ({detail.get('size')} bytes)")
        if qe:
            print(f"音质判定: {qe.get('quality_desc')} "
                  f"[估算约 {qe.get('estimated_kbps')} kbps]")
        if detail.get("warning"):
            print(f"⚠️ 警告: {detail['warning']}")
        elif detail.get("match_target"):
            print(f"✅ 匹配目标音质: {detail['match_target']}")
        if detail.get("ekey_saved"):
            print(f"🔑 ekey 已保存: {detail['ekey_saved']}")
        elif detail.get("ekey"):
            print(f"🔑 ekey: {detail['ekey']}")
    else:
        print(f"失败: {detail.get('error')}")

if __name__ == "__main__":
    main()
