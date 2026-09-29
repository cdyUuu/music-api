"""
酷狗音乐模块
包含: info(v3/album_audio/audio), url(tracker.kugou.com/v5/url), lyric, refresh, 签名工具
"""
import time
import random
import re
import zlib
from typing import Dict, Optional

from .core import (
    config, cache, stats_manager, createLogger,
    FailedException, SongInfo, UrlResponse, Lyric,
    send_http_request, getExpireTime,
    get_fallback_chain, parse_quality_from_url, compare_quality,
)
from .encode import md5, json_dumps, json_loads, b64decode, encodeURI

logger = createLogger("KG")

# ============================================================
# 工具常量
# ============================================================
KG_TOOLS = {
    "mid": "musicapi",
    "qualityHashMap": {
        "128k": "hash_128", "320k": "hash_320", "flac": "hash_flac",
        "hires": "hash_high", "atmos": "hash_128", "master": "hash_128",
    },
    "qualityMap": {
        "128k": "128", "320k": "320", "flac": "flac",
        "hires": "high", "atmos": "viper_atmos", "master": "viper_clear",
    },
}

KG_API_KEY = "OIlwieks28dk2k092lksi2UIkp"
KG_KEY_SALT = "57ae12eb6890223e355ccfcb74edf70d"

def _sort_dict(d: Dict) -> Dict:
    return dict(sorted(d.items()))

def _build_signature_params(d: Dict, body: str = "") -> str:
    return "".join([f"{k}={v}" for k, v in d.items()]) + body

def _build_request_params(d: Dict) -> str:
    return "&".join([f"{k}={v}" for k, v in d.items()])

def kg_sign(params: Dict, body: str = "") -> str:
    if isinstance(body, dict):
        body = json_dumps(body)
    params = _sort_dict(params)
    params_str = _build_signature_params(params, body)
    return md5(KG_API_KEY + params_str + KG_API_KEY)

async def kg_sign_request(url: str, params: Dict, options: Optional[Dict] = None):
    options = options or {}
    body_content = options.get("body") or options.get("data") or options.get("json") or ""
    params["signature"] = kg_sign(params, body_content)
    full_url = url + "?" + _build_request_params(params)
    return await send_http_request(full_url, headers=options.get("headers", {}))

def get_key(hash_: str, user_info: Dict) -> str:
    return md5(hash_.lower() + KG_KEY_SALT + "1005" + KG_TOOLS["mid"] + user_info["userid"])

def _format_play_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"

# ============================================================
# 歌曲信息
# ============================================================
async def get_song_info(song_id: str) -> SongInfo:
    """song_id 是酷狗的hash值"""
    cache_key = f"info_kg_{song_id}"
    cached = cache.get("info", cache_key)
    if cached:
        return SongInfo(**cached)

    hash_ = song_id.lower()
    tn = int(time.time())
    url = "https://gateway.kugou.com/v3/album_audio/audio"
    headers = {
        "KG-THash": "13a3164",
        "KG-RC": "1",
        "KG-Fake": "0",
        "KG-RF": "00869891",
        "User-Agent": "Android712-AndroidPhone-11451-376-0-FeeCacheUpdate-wifi",
        "x-router": "kmr.service.kugou.com",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = {
        "area_code": "1",
        "show_privilege": "1",
        "show_album_info": "1",
        "is_publish": "",
        "appid": 1005,
        "clientver": 11451,
        "mid": "114514",
        "dfid": "-",
        "clienttime": tn,
        "key": KG_API_KEY,
        "data": [{"hash": hash_}],
    }
    try:
        resp = await send_http_request(url, method="POST", json_data=body, headers=headers)
        result = json_loads(await resp.text())
        data_list = result.get("data")
        data = data_list[0][0] if (data_list and data_list[0]) else None
        if not data:
            raise FailedException("歌曲不存在")

        audio_info = data.get("audio_info", {})
        album_info = data.get("album_info", {})
        timelength = audio_info.get("timelength")
        duration = _format_play_time(int(timelength) / 1000) if timelength else None
        cover = album_info.get("sizable_cover", "")
        cover_url = cover.format(size="500") if cover else None

        info = SongInfo(
            songId=str(data.get("audio_id", "")),
            songName=data.get("ori_audio_name", ""),
            artistName=data.get("author_name", ""),
            albumName=album_info.get("album_name", ""),
            albumId=str(album_info.get("album_id", "")),
            duration=duration,
            coverUrl=cover_url,
        )
        # 保存额外信息供url接口使用
        info._kg_hash = audio_info.get("hash", hash_)
        info._kg_album_audio_id = data.get("album_audio_id")
        info._kg_album_id = album_info.get("album_id")

        cache.set("info", cache_key, info.model_dump(), getExpireTime("kg", "info"))
        await stats_manager.increment("kg", "info", True)
        return info
    except FailedException:
        await stats_manager.increment("kg", "info", False)
        raise
    except Exception as e:
        logger.error(f"获取酷狗信息失败: {e}")
        await stats_manager.increment("kg", "info", False)
        raise FailedException(f"获取音乐信息失败: {e}")

# ============================================================
# 播放地址
# ============================================================
async def _get_url_once(song_id: str, quality: str, user_info: Dict, info: SongInfo) -> Optional[str]:
    """
    单次尝试获取指定音质的播放 URL。成功返回 URL 字符串，失败返回 None。
    不再抛 FailedException，由调用方聚合处理。
    """
    hash_ = song_id.lower()
    album_id = getattr(info, "_kg_album_id", None) or info.albumId
    album_audio_id = getattr(info, "_kg_album_audio_id", None)
    actual_hash = getattr(info, "_kg_hash", hash_)

    params = {
        "album_id": album_id,
        "userid": user_info["userid"],
        "area_code": 1,
        "hash": actual_hash,
        "mid": KG_TOOLS["mid"],
        "appid": "1005",
        "ssa_flag": "is_fromtrack",
        "clientver": "20349",
        "token": user_info["token"],
        "album_audio_id": album_audio_id,
        "behavior": "play",
        "clienttime": int(time.time()),
        "pid": "2",
        "key": get_key(actual_hash, user_info),
        "quality": KG_TOOLS["qualityMap"].get(quality, "320"),
        "version": "20349",
        "dfid": "-",
        "pidversion": 3001,
    }
    headers = {
        "User-Agent": "Android12-AndroidCar-20089-46-0-NetMusic-wifi",
        "KG-THash": "255d751",
        "KG-Rec": "1",
        "KG-RC": "1",
    }
    try:
        resp = await kg_sign_request("http://tracker.kugou.com/v5/url", params, {"headers": headers})
        body = json_loads(await resp.text())
    except Exception as e:
        logger.warning(f"kg _get_url_once 请求失败 quality={quality}: {e}")
        return None

    url_list = body.get("url")
    if not url_list:
        # 上游返回错误码（如 "ok" / "其他状态"），记录日志方便诊断
        logger.info(
            f"[QUALITY] source=kg songId={song_id} "
            f"requested={quality} sent_level={params['quality']} "
            f"returned_status={body.get('status')} url=null"
        )
        return None
    return url_list[0]


async def get_url(song_id: str, quality: str = "320k") -> UrlResponse:
    """
    逐级降级获取播放 URL。
    规则：从请求音质开始，按 FALLBACK_ORDER 依次往下试，直到成功。
    不会跳级（如 master 不会直接降到 320k，必先经过 hires/flac）。
    """
    hash_ = song_id.lower()
    info = await get_song_info(song_id)

    users = config.get_platform_users("kg")
    if not users:
        raise FailedException("未配置酷狗账号")
    user_info = random.choice(users)

    # 取逐级降级链：[quality, ...fallbacks]
    chain = get_fallback_chain(quality)
    tried = []  # 记录尝试过的音质

    try:
        for idx, try_q in enumerate(chain):
            tried.append(try_q)
            sent_level = KG_TOOLS["qualityMap"].get(try_q, "320")
            play_url = await _get_url_once(song_id, try_q, user_info, info)
            if not play_url:
                continue

            # 从 URL 解析实际音质
            actual_q = parse_quality_from_url(play_url, "kg")
            if not actual_q or actual_q == "unknown":
                # URL 无法识别时，认为就是当前请求档位
                actual_q = try_q

            # 反向升级检测：上游返回了比请求更高的音质
            direction = compare_quality(try_q, actual_q)

            # 降级标记：只要不是请求档位命中，都算降级
            is_fallback = (try_q != quality)
            is_upgraded = (direction == "upgraded")

            logger.info(
                f"[QUALITY] source=kg songId={song_id} "
                f"requested={quality} tried={try_q} "
                f"sent_level={sent_level} "
                f"returned_url_feature={actual_q} "
                f"final_quality={actual_q} "
                f"direction={direction} "
                f"fallback_chain={'→'.join(tried)}"
            )

            result = UrlResponse(
                url=play_url,
                quality=actual_q,
                requested=quality,
                fallback=is_fallback,
                upgraded=is_upgraded,
                direction=direction,
                fallback_chain="→".join(tried),
                sent_level=sent_level,
                returned_level=actual_q,
                url_feature=actual_q,
            )

            # 缓存键用"实际音质"，避免污染：下次请求 quality=actual_q 时直接命中
            cache_key = f"url_kg_{song_id}_{actual_q}"
            cache.set("url", cache_key, result.model_dump(), getExpireTime("kg", "url"))

            # 同时给"请求音质"键也写一份缓存（用户请求 quality=X 时直接命中）
            # 但只在 actual_q == quality 时才写，避免把降级结果缓存到高音质键
            if actual_q == quality:
                req_cache_key = f"url_kg_{song_id}_{quality}"
                cache.set("url", req_cache_key, result.model_dump(), getExpireTime("kg", "url"))

            await stats_manager.increment("kg", "url", True)
            return result

        # 所有档位都失败
        logger.warning(
            f"[QUALITY] source=kg songId={song_id} requested={quality} "
            f"tried={tried} ALL_FAILED"
        )
        await stats_manager.increment("kg", "url", False)
        raise FailedException(f"所有音质({','.join(tried)})均获取失败")
    except FailedException:
        await stats_manager.increment("kg", "url", False)
        raise
    except Exception as e:
        logger.error(f"获取酷狗播放地址失败: {e}")
        await stats_manager.increment("kg", "url", False)
        raise FailedException(f"获取播放地址失败: {e}")

# ============================================================
# 歌词
# ============================================================
class _KrcParser:
    def __init__(self):
        self.head_exp = r"^.*\[id:\$\w+\]\n"
        self.i = 0

    def parse(self, string):
        string = string.replace("\r", "")
        if re.match(self.head_exp, string):
            string = re.sub(self.head_exp, "", string)
        trans = re.search(r"\[language:([\w=\\/+]+)\]", string)
        rlyric = None
        tlyric = None
        if trans:
            string = re.sub(r"\[language:[\w=\\/+]+\]\n", "", string)
            decoded_trans = b64decode(trans.group(1)).decode("utf-8")
            trans_json = json_loads(decoded_trans)
            for item in trans_json["content"]:
                if item["type"] == 0:
                    rlyric = item["lyricContent"]
                elif item["type"] == 1:
                    tlyric = item["lyricContent"]
        self.i = 0
        lxlyric = re.sub(
            r"\[((\d+),\d+)\].*",
            lambda x: self._process_match(x, rlyric, tlyric),
            string,
        )
        rlyric = "\n".join(rlyric) if rlyric else ""
        tlyric = "\n".join(tlyric) if tlyric else ""
        lxlyric = re.sub(r"<(\d+,\d+),\d+>", r"<\1>", lxlyric)
        lyric = re.sub(r"<\d+,\d+>", "", lxlyric)
        return {"lyric": lyric, "tlyric": tlyric, "rlyric": rlyric, "lxlyric": lxlyric}

    def _process_match(self, match, rlyric, tlyric):
        result = re.match(r"\[((\d+),\d+)\].*", match.group(0))
        time_val = int(result.group(2))
        ms = time_val % 1000
        time_val /= 1000
        m = str(int(time_val / 60)).zfill(2)
        time_val %= 60
        s = str(int(time_val)).zfill(2)
        time_string = f"{m}:{s}.{ms}"
        if tlyric:
            transformed_t = ""
            for t in tlyric[self.i]:
                transformed_t += t
            tlyric[self.i] = transformed_t
        if rlyric:
            nr = [r for r in rlyric[self.i]]
            _tnr = "".join(nr)
            if " " in _tnr:
                rlyric[self.i] = _tnr
            else:
                nr = [r.strip() for r in rlyric[self.i]]
                rlyric[self.i] = " ".join(nr)
        if rlyric:
            rlyric[self.i] = f"[{time_string}]{rlyric[self.i] if rlyric[self.i] else ''}".replace("  ", " ")
        if tlyric:
            tlyric[self.i] = f"[{time_string}]{tlyric[self.i] if tlyric[self.i] else ''}"
        self.i += 1
        return re.sub(result.group(1), time_string, match.group(0))

_krc_parser = _KrcParser()

def _krc_decode(a: bytes) -> str:
    encrypt_key = (64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105)
    content = a[4:]
    compress_content = bytes(content[i] ^ encrypt_key[i % len(encrypt_key)] for i in range(len(content)))
    text_bytes = zlib.decompress(bytes(compress_content))
    return text_bytes.decode("utf-8")

async def get_lyric(song_id: str) -> Lyric:
    cache_key = f"lyric_kg_{song_id}"
    cached = cache.get("lyric", cache_key)
    if cached:
        return Lyric(**cached)

    try:
        info = await get_song_info(song_id)
        hash_new = getattr(info, "_kg_hash", song_id.lower())
        name = info.songName
        duration_sec = 0
        if info.duration:
            parts = info.duration.split(":")
            duration_sec = int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else 0

        search_url = encodeURI(
            "http://lyrics.kugou.com/search?ver=1&man=yes&client=pc&keyword="
            + name + "&hash=" + hash_new + "&timelength=" + str(duration_sec)
        )
        resp = await send_http_request(search_url)
        body = json_loads(await resp.text())
        if body.get("status") != 200 or not body.get("candidates"):
            raise FailedException("未检索到歌词")
        lyric_id = body["candidates"][0]["id"]
        accesskey = body["candidates"][0]["accesskey"]

        dl_url = f"http://lyrics.kugou.com/download?ver=1&client=pc&id={lyric_id}&accesskey={accesskey}"
        resp = await send_http_request(dl_url)
        body = json_loads(await resp.text())
        if body.get("status") != 200 or not body.get("content"):
            raise FailedException("歌词获取失败")

        content = b64decode(body["content"])
        text = _krc_decode(content)
        parsed = _krc_parser.parse(text)
        result = Lyric(lyric=parsed["lyric"], trans=parsed.get("tlyric", ""))
        cache.set("lyric", cache_key, result.model_dump(), getExpireTime("kg", "lyric"))
        await stats_manager.increment("kg", "lyric", True)
        return result
    except FailedException:
        await stats_manager.increment("kg", "lyric", False)
        raise
    except Exception as e:
        logger.error(f"获取酷狗歌词失败: {e}")
        await stats_manager.increment("kg", "lyric", False)
        raise FailedException(f"获取歌词失败: {e}")

# ============================================================
# 刷新登录
# ============================================================
import string
import base64 as _kg_base64
from cryptography.hazmat.primitives.asymmetric import padding as _kg_rsa_padding
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.hazmat.primitives.ciphers import Cipher as _KgCipher, algorithms as _KgAlg, modes as _KgMode
from cryptography.hazmat.primitives import padding as _kg_pad

_KG_PUBLIC_KEY_DER = _kg_base64.b64decode(
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDIAG7QOELSYoIJvTFJhMpe1s/gbjDJX51HBNnEl5HXqTW6lQ7LC8jr9fWZTwusknp+sVGzwd40MwP6U5yDE27M/X1+UR4tvOGOqp94TJtQ1EPnWGWXngpeIW5GxoQGao1rmYWAu6oi1z9XkChrsUdC6DJE5E221wf/4WLFxwAtRQIDAQAB"
)

def _kg_aes_encrypt(data_dict, key_str=None, iv_str=None):
    raw = json_dumps(data_dict).encode("utf-8")
    if key_str is None:
        temp_key = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(16)).lower()
        key_full = md5(temp_key)
        ret_key = temp_key
    else:
        key_full = md5(key_str)
        ret_key = None
    key_bytes = key_full[:32].encode("utf-8")
    iv_bytes = (iv_str if iv_str else key_full[16:32]).encode("utf-8")
    padder = _kg_pad.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    cipher = _KgCipher(_KgAlg.AES(key_bytes), _KgMode.CBC(iv_bytes))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    hex_str = encrypted.hex()
    return (hex_str, ret_key)

def _kg_aes_decrypt(hex_str, key_str):
    key_full = md5(key_str)
    key_bytes = key_full[:32].encode("utf-8")
    iv_bytes = key_full[16:32].encode("utf-8")
    cipher = _KgCipher(_KgAlg.AES(key_bytes), _KgMode.CBC(iv_bytes))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(bytes.fromhex(hex_str)) + decryptor.finalize()
    unpadder = _kg_pad.PKCS7(128).unpadder()
    raw = unpadder.update(decrypted) + unpadder.finalize()
    return json_loads(raw.decode("utf-8"))

def _kg_rsa_encrypt(data_dict):
    raw = json_dumps(data_dict).encode("utf-8")
    pub_key = load_der_public_key(_KG_PUBLIC_KEY_DER)
    encrypted = pub_key.encrypt(raw, _kg_rsa_padding.PKCS1v15())
    return encrypted.hex()

async def refresh_login(user: Dict) -> Dict:
    userid = str(user.get("userid", ""))
    token = user.get("token", "")
    if not userid or userid == "0" or not token:
        return user
    try:
        clienttime = int(time.time())
        clienttime_ms = clienttime * 1000

        p3, _ = _kg_aes_encrypt(
            {"clienttime": clienttime, "token": token},
            key_str="90b8382a1bb4ccdcf063102053fd75b8",
            iv_str="f063102053fd75b8",
        )
        encrypt_params, temp_key = _kg_aes_encrypt({})
        pk = _kg_rsa_encrypt({"clienttime_ms": clienttime_ms, "key": temp_key})

        url = "http://gateway.kugou.com/v5/login_by_token"
        query = _build_request_params({
            "dfid": "-", "uuid": "-", "appid": "1005",
            "mid": KG_TOOLS["mid"], "clientver": "20349", "clienttime": str(clienttime),
        })
        body = json_dumps({
            "p3": p3, "params": encrypt_params, "userid": userid,
            "need_toneinfo": 1, "clienttime_ms": clienttime_ms,
            "dfid": "-", "dev": "SDY-AN00", "plat": 1, "pk": pk,
            "t1": "0", "gitversion": "a23c277", "t2": "0",
            "t3": "MCwwLDEsMSwwLDYsMSw2LDA=",
        })
        headers = {
            "Content-Type": "application/json",
            "SUPPORT-CALM": "1", "KG-THash": "6a6a1ba",
            "x-router": "login.user.kugou.com",
            "User-Agent": "Android12-AndroidPhone-20349-201-0-ting-LOGIN-wifi",
            "KG-RC": "1",
        }
        resp = await send_http_request(url + "?" + query, method="POST", body=body, headers=headers)
        resp_data = json_loads(await resp.text())

        if resp_data.get("error_code") != 0:
            logger.warning(f"酷狗刷新失败 code={resp_data.get('error_code')}")
            return user

        decrypted = _kg_aes_decrypt(resp_data["data"]["secu_params"], temp_key)
        new_token = decrypted.get("token", "")
        if new_token:
            user["token"] = new_token
            logger.info(f"酷狗账号 {userid} token刷新成功")
    except Exception as e:
        logger.error(f"酷狗刷新登录异常: {e}")
    return user
