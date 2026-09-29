"""
QQ音乐模块
签名算法: SHA1 + 混淆 + base64 (zzc开头)
包含: 设备信息, QIMEI, sign, build_comm, sign_request, info/url/lyric/refresh
"""
import base64 as _base64
import binascii
import hashlib
import random
import re
import string
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .core import (
    config, cache, stats_manager, createLogger,
    FailedException, SongInfo, UrlResponse, Lyric,
    send_http_request, getExpireTime,
    get_fallback_chain, parse_quality_from_url, compare_quality,
)
from .encode import md5, json_dumps, json_loads, b64encode
from .crypto import tripledes_key_setup, tripledes_crypt, DECRYPT
import zlib

logger = createLogger("TX")

# ============================================================
# 签名算法 (SHA1 + 混淆)
# ============================================================
_PART_1_INDEXES = list(filter(lambda x: x < 40, [23, 14, 6, 36, 16, 40, 7, 19]))
_PART_2_INDEXES = [16, 1, 32, 12, 19, 27, 8, 5]
_SCRAMBLE_VALUES = [
    89, 39, 179, 150, 218, 82, 58, 252, 177, 52,
    186, 123, 120, 64, 242, 133, 143, 161, 121, 179,
]

def tx_sign(payload: str) -> str:
    """QQ音乐签名算法"""
    sha1_hash = hashlib.sha1(payload.encode("utf-8")).hexdigest().upper()
    part1 = "".join(sha1_hash[i] for i in _PART_1_INDEXES)
    part2 = "".join(sha1_hash[i] for i in _PART_2_INDEXES)
    part3 = bytearray(20)
    for i, v in enumerate(_SCRAMBLE_VALUES):
        value = v ^ int(sha1_hash[i * 2: i * 2 + 2], 16)
        part3[i] = value
    b64_part = re.sub(r"[\\/+=]", "", b64encode(bytes(part3)))
    return f"zzc{part1}{b64_part}{part2}".lower()

# ============================================================
# 设备信息
# ============================================================
class QimeiResult(TypedDict):
    q16: str
    q36: str

def _random_imei() -> str:
    imei = []
    sum_ = 0
    for i in range(14):
        num = random.randint(0, 9)
        if (i + 2) % 2 == 0:
            num *= 2
            if num >= 10:
                num = (num % 10) + 1
        sum_ += num
        imei.append(str(num))
    ctrl_digit = (sum_ * 9) % 10
    imei.append(str(ctrl_digit))
    return "".join(imei)

@dataclass
class OSVersion:
    incremental: str = "5891938"
    release: str = "10"
    codename: str = "REL"
    sdk: int = 29

@dataclass
class Device:
    display: str = field(default_factory=lambda: f"QMAPI.{random.randint(100000, 999999)}.001")
    product: str = "iarim"
    device: str = "sagit"
    board: str = "eomam"
    model: str = "MI 6"
    fingerprint: str = field(default_factory=lambda: f"xiaomi/iarim/sagit:10/eomam.200122.001/{random.randint(1000000, 9999999)}:user/release-keys")
    boot_id: str = field(default_factory=lambda: str(uuid4()))
    proc_version: str = field(default_factory=lambda: f"Linux 5.4.0-54-generic-{''.join(random.choices(string.ascii_letters + string.digits, k=8))} (android-build@google.com)")
    imei: str = field(default_factory=_random_imei)
    brand: str = "Xiaomi"
    bootloader: str = "U-boot"
    base_band: str = ""
    version: OSVersion = field(default_factory=OSVersion)
    sim_info: str = "T-Mobile"
    os_type: str = "android"
    mac_address: str = "00:50:56:C0:00:08"
    wifi_bssid: str = "00:50:56:C0:00:08"
    wifi_ssid: str = "<unknown ssid>"
    imsi_md5: list = field(default_factory=lambda: list(hashlib.md5(bytes([random.randint(0, 255) for _ in range(16)])).digest()))
    android_id: str = field(default_factory=lambda: binascii.hexlify(bytes([random.randint(0, 255) for _ in range(8)])).decode("utf-8"))
    apn: str = "wifi"
    vendor_name: str = "MIUI"
    vendor_os_name: str = "qmapi"
    qimei: Optional[QimeiResult] = None

_DEVICE_CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "device.json"

def _get_cached_device() -> Device:
    if _DEVICE_CACHE.exists():
        try:
            device_data = json_loads(_DEVICE_CACHE.read_text())
            device_data["version"] = OSVersion(**device_data["version"])
            return Device(**device_data)
        except:
            pass
    device = Device()
    _save_device(device)
    return device

def _save_device(device: Device):
    _DEVICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _DEVICE_CACHE.write_text(json_dumps(asdict(device), indent_2=True))

# ============================================================
# QIMEI 获取
# ============================================================
_QIMEI_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDEIxgwoutfwoJxcGQeedgP7FG9qaIuS0qzfR8gWkrkTZKM2iWHn2ajQpBRZjMSoSf6+KJGvar2ORhBfpDXyVtZCKpqLQ+FLkpncClKVIrBwv6PHyUvuCb0rIarmgDnzkfQAqVufEtR64iazGDKatvJ9y6B9NMbHddGSAUmRTCrHQIDAQAB
-----END PUBLIC KEY-----"""
_QIMEI_SECRET = "ZdJqM15EeO2zWc08"
_QIMEI_APP_KEY = "0AND0HD6FE4HY80F"

def _qimei_rsa_encrypt(content: bytes) -> bytes:
    key = serialization.load_pem_public_key(_QIMEI_PUBLIC_KEY.encode())
    return key.encrypt(content, padding.PKCS1v15())

def _qimei_aes_encrypt(key: bytes, content: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    padding_size = 16 - len(content) % 16
    encryptor = cipher.encryptor()
    return encryptor.update(content + (padding_size * chr(padding_size)).encode()) + encryptor.finalize()

def _random_beacon_id() -> str:
    beacon_id = ""
    time_month = datetime.now().strftime("%Y-%m-") + "01"
    rand1 = random.randint(100000, 999999)
    rand2 = random.randint(100000000, 999999999)
    for i in range(1, 41):
        if i in [1, 2, 13, 14, 17, 18, 21, 22, 25, 26, 29, 30, 33, 34, 37, 38]:
            beacon_id += f"k{i}:{time_month}{rand1}.{rand2}"
        elif i == 3:
            beacon_id += "k3:0000000000000000"
        elif i == 4:
            beacon_id += f"k4:{''.join(random.choices('123456789abcdef', k=16))}"
        else:
            beacon_id += f"k{i}:{random.randint(0, 9999)}"
        beacon_id += ";"
    return beacon_id

async def _get_qimei(version: str = "14.9.0.8") -> QimeiResult:
    device = _get_cached_device()
    if device.qimei:
        return device.qimei
    try:
        reserved = {
            "harmony": "0", "clone": "0", "containe": "",
            "oz": "UhYmelwouA+V2nPWbOvLTgN2/m8jwGB+yUB5v9tysQg=",
            "oo": "Xecjt+9S1+f8Pz2VLSxgpw==",
            "kelong": "0",
            "uptimes": (datetime.now() - timedelta(seconds=random.randint(0, 14400))).strftime("%Y-%m-%d %H:%M:%S"),
            "multiUser": "0",
            "bod": device.brand, "dv": device.device,
            "firstLevel": "", "manufact": device.brand,
            "name": device.model, "host": "se.infra",
            "kernel": device.proc_version,
        }
        payload = {
            "androidId": device.android_id, "platformId": 1,
            "appKey": _QIMEI_APP_KEY, "appVersion": version,
            "beaconIdSrc": _random_beacon_id(),
            "brand": device.brand, "channelId": "10003505",
            "cid": "", "imei": device.imei, "imsi": "", "mac": "",
            "model": device.model, "networkType": "unknown",
            "oaid": "", "osVersion": f"Android {device.version.release},level {device.version.sdk}",
            "qimei": "", "qimei36": "", "sdkVersion": "1.2.13.6",
            "targetSdkVersion": "33", "audit": "", "userId": "{}",
            "packageId": "com.tencent.qqmusic", "deviceType": "Phone",
            "sdkName": "", "reserved": json_dumps(reserved),
        }
        crypt_key = "".join(random.choices("adbcdef1234567890", k=16))
        nonce = "".join(random.choices("adbcdef1234567890", k=16))
        ts = int(_time.time())
        key = _base64.b64encode(_qimei_rsa_encrypt(crypt_key.encode())).decode()
        params = _base64.b64encode(_qimei_aes_encrypt(crypt_key.encode(), json_dumps(payload).encode())).decode()
        extra = '{"appKey":"' + _QIMEI_APP_KEY + '"}'
        sign = md5(key, params, str(ts * 1000), nonce, _QIMEI_SECRET, extra)
        resp = await send_http_request(
            "https://api.tencentmusic.com/tme/trpc/proxy",
            method="POST",
            headers={
                "Host": "api.tencentmusic.com",
                "method": "GetQimei",
                "service": "trpc.tme_datasvr.qimeiproxy.QimeiProxy",
                "appid": "qimei_qq_android",
                "sign": md5("qimei_qq_androidpzAuCmaFAaFaHrdakPjLIEqKrGnSOOvH", str(ts)),
                "User-Agent": "QQMusic",
                "timestamp": str(ts),
            },
            json_data={
                "app": 0, "os": 1,
                "qimeiParams": {"key": key, "params": params, "time": str(ts), "nonce": nonce, "sign": sign, "extra": extra},
            },
        )
        text = await resp.text()
        data = json_loads(json_loads(text)["data"])["data"]
        device.qimei = QimeiResult(q16=data["q16"], q36=data["q36"])
        _save_device(device)
        return device.qimei
    except Exception as e:
        logger.error(f"获取QIMEI失败: {e}")
        return QimeiResult(q16="", q36="6c9d3cd110abca9b16311cee10001e717614")

# ============================================================
# build_comm & sign_request
# ============================================================
async def build_comm(user_info: Optional[Dict] = None) -> Dict[str, Any]:
    device = _get_cached_device()
    qimei = await _get_qimei()
    common = {
        "v": 14090008, "ct": 11, "cv": 14090008,
        "chid": "2005000982",
        "QIMEI": qimei["q16"], "QIMEI36": qimei["q36"],
        "tmeAppID": "qqmusic", "format": "json",
        "inCharset": "utf-8", "outCharset": "utf-8",
    }
    if user_info and user_info.get("uin") and user_info.get("token"):
        tmeLoginType = 1 if str(user_info["token"]).startswith("W_X_") else 2
        common.update({
            "qq": str(user_info["uin"]),
            "authst": user_info["token"],
            "tmeLoginType": tmeLoginType,
        })
    common.update({
        "OpenUDID": "ffffffffbff94f7d000000000033c587",
        "udid": "ffffffffbff94f7d000000000033c587",
        "os_ver": device.version.release,
        "aid": "d2550265db4ce5c4",
        "phonetype": device.model,
        "devicelevel": device.version.sdk,
        "newdevicelevel": device.version.sdk,
        "nettype": "1030",
        "rom": device.fingerprint,
        "OpenUDID2": "ffffffffbff94f7d000001999ff7d5bf",
    })
    return common

async def sign_request(data: Dict) -> Dict:
    """发送签名请求到QQ音乐API - data包含comm和req/request"""
    body = json_dumps(data)
    s = tx_sign(body)
    resp = await send_http_request(
        f"https://u.y.qq.com/cgi-bin/musics.fcg?sign={s}",
        method="POST",
        data=body,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
    )
    text = await resp.text()
    return json_loads(text)

# ============================================================
# 辅助函数
# ============================================================
def _format_singer(singer_list: list) -> str:
    return ",".join(s.get("name", "") for s in singer_list if isinstance(s, dict))

def _format_play_time(interval: int) -> str:
    m = int(interval // 60)
    s = int(interval % 60)
    return f"{m:02d}:{s:02d}"

# ============================================================
# 歌曲信息
# ============================================================
async def _id_get_info(songid: int) -> SongInfo:
    comm = await build_comm()
    req_body = {
        "comm": comm,
        "req": {
            "module": "music.trackInfo.UniformRuleCtrl",
            "method": "CgiGetTrackInfo",
            "param": {"types": [1], "ids": [songid], "ctx": 0},
        },
    }
    resp = await sign_request(req_body)
    if resp.get("code") != 0 or resp.get("req", {}).get("code") != 0:
        raise FailedException("获取音乐信息失败")
    info = resp["req"]["data"]["tracks"][0]
    return SongInfo(
        songId=str(info.get("id", "")),
        songName=(info.get("title", "") + info.get("subtitle", "")),
        artistName=_format_singer(info.get("singer", [])),
        albumName=(info.get("album", {}).get("title", "") + info.get("album", {}).get("subtitle", "")),
        albumId=str(info.get("album", {}).get("id", "")),
        duration=_format_play_time(info.get("interval", 0)) if info.get("interval") is not None else None,
        coverUrl=info.get("album", {}).get("cover", {}).get("medium", "") if isinstance(info.get("album", {}).get("cover"), dict) else "",
    ), info

async def _mid_get_info(mid: str) -> SongInfo:
    comm = await build_comm()
    req_body = {
        "comm": comm,
        "req": {
            "method": "get_song_detail_yqq",
            "param": {"song_type": 0, "song_mid": mid},
            "module": "music.pf_song_detail_svr",
        },
    }
    resp = await sign_request(req_body)
    if resp.get("code") != 0 or resp.get("req", {}).get("code") != 0:
        raise FailedException("获取音乐信息失败")
    info = resp["req"]["data"]["track_info"]
    return SongInfo(
        songId=str(info.get("id", "")),
        songName=(info.get("title", "") + info.get("subtitle", "")),
        artistName=_format_singer(info.get("singer", [])),
        albumName=(info.get("album", {}).get("title", "") + info.get("album", {}).get("subtitle", "")),
        albumId=str(info.get("album", {}).get("id", "")),
        duration=_format_play_time(info.get("interval", 0)) if info.get("interval") is not None else None,
        coverUrl=info.get("album", {}).get("cover", {}).get("medium", "") if isinstance(info.get("album", {}).get("cover"), dict) else "",
    ), info

async def get_song_info(song_id: str) -> SongInfo:
    cache_key = f"info_tx_{song_id}"
    cached = cache.get("info", cache_key)
    if cached:
        return SongInfo(**cached)

    try:
        if str(song_id).isdigit():
            info, raw = await _id_get_info(int(song_id))
        else:
            info, raw = await _mid_get_info(song_id)
        # 保存额外信息
        info._tx_songMid = raw.get("mid", song_id)
        info._tx_mediaMid = raw.get("file", {}).get("media_mid") or raw.get("mid", song_id)
        cache.set("info", cache_key, info.model_dump(), getExpireTime("tx", "info"))
        await stats_manager.increment("tx", "info", True)
        return info
    except FailedException:
        await stats_manager.increment("tx", "info", False)
        raise
    except Exception as e:
        logger.error(f"获取QQ音乐信息失败: {e}")
        await stats_manager.increment("tx", "info", False)
        raise FailedException(f"获取音乐信息失败: {e}")

# ============================================================
# 播放地址
# ============================================================
_QUALITY_MAP = {
    "128k": {"e": ".mp3", "h": "M500"},
    "320k": {"e": ".mp3", "h": "M800"},
    "flac": {"e": ".flac", "h": "F000"},
    "hires": {"e": ".flac", "h": "RS01"},
    "atmos": {"e": ".flac", "h": "Q000"},
    "atmos_plus": {"e": ".flac", "h": "Q001"},
    "master": {"e": ".flac", "h": "AI00"},
}

_GUID_LIST = [
    "SM-G998B", "iPhone14,2", "Xiaomi12", "Pixel6", "OnePlus9",
    "RedmiK40", "RealmeGT", "VivoX70", "OppoFindX3", "HuaweiP50",
    "Nokia8.3", "SonyXperia1III", "LG_V60", "AsusROG5", "ZTE_Axon30",
    "MotorolaEdge20", "Fairphone4", "NothingPhone1", "PocoF3", "BlackShark4",
    "LenovoLegion", "NubiaZ30", "Meizu18", "GioneeM12", "TCL20Pro",
    "Android_9F3A2B1C", "Phone_7E8D5F4G", "Device_1A2B3C4D",
    "Client_5E6F7G8H", "Music_9I0J1K2L",
]

async def _get_url_once(song_id: str, quality: str, user_info: Dict, info: SongInfo) -> Optional[str]:
    """
    单次尝试获取指定音质的播放 URL。成功返回完整 URL（含 CDN 前缀），失败返回 None。
    不再抛 FailedException，由调用方聚合处理。
    """
    songMid = getattr(info, "_tx_songMid", song_id)
    mediaMid = getattr(info, "_tx_mediaMid", songMid)

    comm = await build_comm(user_info)
    comm["ct"] = 20
    guid = random.choice(_GUID_LIST)
    q = _QUALITY_MAP.get(quality, _QUALITY_MAP["320k"])

    try:
        resp = await sign_request({
            "comm": comm,
            "request": {
                "module": "vkey.GetVkeyServer",
                "method": "CgiGetVkey",
                "param": {
                    "guid": guid,
                    "uin": user_info["uin"],
                    "songtype": [1],
                    "songmid": [songMid],
                    "filename": [f"{q['h']}{mediaMid}{q['e']}"],
                    "loginflag": 1,
                    "platform": "20",
                },
            },
        })
    except Exception as e:
        logger.warning(f"tx _get_url_once 请求失败 quality={quality}: {e}")
        return None

    midurlinfo = resp.get("request", {}).get("data", {}).get("midurlinfo", [])
    if not midurlinfo:
        logger.info(
            f"[QUALITY] source=tx songId={song_id} "
            f"requested={quality} sent_level={q['h']} "
            f"returned_code={resp.get('request', {}).get('code')} purl=null"
        )
        return None
    purl = str(midurlinfo[0].get("purl") or "")
    if not purl:
        logger.info(
            f"[QUALITY] source=tx songId={song_id} "
            f"requested={quality} sent_level={q['h']} "
            f"returned_code={resp.get('request', {}).get('code')} purl=empty"
        )
        return None
    cdn_list = config.get_tx_cdn_list()
    play_url = random.choice(cdn_list) + purl if cdn_list else purl
    return play_url


async def get_url(song_id: str, quality: str = "320k") -> UrlResponse:
    """
    逐级降级获取播放 URL。
    规则：从请求音质开始，按 FALLBACK_ORDER 依次往下试，直到成功。
    对每个音质档位选择合适的账号（高音质优先 SVIP）。
    """
    info = await get_song_info(song_id)
    users = config.get_platform_users("tx")
    if not users:
        raise FailedException("未配置QQ音乐账号")

    # 取逐级降级链
    chain = get_fallback_chain(quality)
    tried = []

    try:
        for try_q in chain:
            tried.append(try_q)
            q = _QUALITY_MAP.get(try_q, _QUALITY_MAP["320k"])
            sent_level = q["h"]

            # 高音质（flac 及以上）优先用 SVIP 账号
            if try_q not in ["128k", "320k", "flac", "hires"]:
                svip_users = [u for u in users if u.get("vipType") == "svip"]
                user_info = random.choice(svip_users) if svip_users else random.choice(users)
            else:
                user_info = random.choice(users)

            play_url = await _get_url_once(song_id, try_q, user_info, info)
            if not play_url:
                continue

            # 从 URL 解析实际音质（QQ CDN URL 中带文件名前缀如 AI00/M800/F000）
            actual_q = parse_quality_from_url(play_url, "tx")
            if not actual_q or actual_q == "unknown":
                actual_q = try_q

            direction = compare_quality(try_q, actual_q)
            is_fallback = (try_q != quality)
            is_upgraded = (direction == "upgraded")

            logger.info(
                f"[QUALITY] source=tx songId={song_id} "
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

            # 缓存键用"实际音质"，避免污染
            cache_key = f"url_tx_{song_id}_{actual_q}"
            cache.set("url", cache_key, result.model_dump(), getExpireTime("tx", "url"))
            if actual_q == quality:
                req_cache_key = f"url_tx_{song_id}_{quality}"
                cache.set("url", req_cache_key, result.model_dump(), getExpireTime("tx", "url"))

            await stats_manager.increment("tx", "url", True)
            return result

        # 所有档位失败
        logger.warning(
            f"[QUALITY] source=tx songId={song_id} requested={quality} "
            f"tried={tried} ALL_FAILED"
        )
        await stats_manager.increment("tx", "url", False)
        raise FailedException(f"所有音质({','.join(tried)})均获取失败")
    except FailedException:
        await stats_manager.increment("tx", "url", False)
        raise
    except Exception as e:
        logger.error(f"获取QQ音乐播放地址失败: {e}")
        await stats_manager.increment("tx", "url", False)
        raise FailedException(f"获取播放地址失败: {e}")

# ============================================================
# 歌词
# ============================================================
def qrc_decrypt(encrypted_qrc):
    if not encrypted_qrc:
        return ""
    try:
        if isinstance(encrypted_qrc, str):
            encrypted_qrc = bytearray.fromhex(encrypted_qrc)
        else:
            encrypted_qrc = bytearray(encrypted_qrc)
        data = bytearray()
        schedule = tripledes_key_setup(b"!@#)(*$%123ZXC!@!@#)(NHL", DECRYPT)
        for i in range(0, len(encrypted_qrc), 8):
            data += tripledes_crypt(encrypted_qrc[i:i+8], schedule)
        return zlib.decompress(bytes(data)).decode("utf-8")
    except Exception as e:
        logger.warning(f"QRC解密失败: {e}")
        return ""

def qrc_xml_to_lrc(xml_str):
    """QRC XML → LRC：提取全部 LyricContent，兼容字面换行与 &#10; 实体编码"""
    if not xml_str:
        return ""
    contents = []
    # 双引号属性（内容可含字面换行），提取完成后再解码实体，避免 &quot; 提前截断
    for m in re.finditer(r'LyricContent\s*=\s*"([\s\S]*?)"', xml_str):
        contents.append(m.group(1))
    if not contents:
        for m in re.finditer(r"LyricContent\s*=\s*'([\s\S]*?)'", xml_str):
            contents.append(m.group(1))
    content = "\n".join(contents) if contents else xml_str
    content = _decode_xml_entities(content)
    return qrc_time_to_lrc(content)

def _decode_xml_entities(text):
    """解码歌词内容中的XML实体：换行/回车/引号/尖括号/amp（amp最后解码）"""
    if "&" in text:
        text = re.sub(r"&#0*(?:10|x0*a);", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"&#0*(?:13|x0*d);", "\n", text, flags=re.IGNORECASE)
        text = text.replace("&quot;", '"').replace("&apos;", "'")
        text = text.replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&amp;", "&")
    return text.replace("\r\n", "\n").replace("\r", "\n")

def _ms_to_lrc_timestamp(start_ms):
    """毫秒 → [mm:ss.xx]，整数运算四舍五入（605ms → 00:00.61，避免浮点银行家舍入）"""
    start_ms = int(start_ms)
    minutes, rem = divmod(start_ms, 60000)
    seconds, rem = divmod(rem, 1000)
    centis = (rem + 5) // 10
    if centis >= 100:
        seconds += 1
        centis -= 100
    if seconds >= 60:
        minutes += 1
        seconds -= 60
    return f"[{minutes:02d}:{seconds:02d}.{centis:02d}]"

def qrc_time_to_lrc(content):
    """QRC逐行格式 → LRC：[605,815] → [00:00.61]，去掉逐字标注 黑(605,165) → 黑
    支持同一行多个 [start,dur] 块（非标准但存在），各自拆成独立LRC行"""
    if not content:
        return ""
    lines = content.split("\n")
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        blocks = list(re.finditer(r'\[(\d+),(\d+)\]', line))
        if blocks:
            if line.startswith(blocks[0].group(0)):
                # 行首时间戳块：按块拆分（普通LRC行 [00:12.34] 不会匹配此模式）
                for i, m in enumerate(blocks):
                    seg_end = blocks[i + 1].start() if i + 1 < len(blocks) else len(line)
                    text = re.sub(r'\(\d+(?:,\d+)+\)', '', line[m.end():seg_end]).strip()
                    if text:
                        result.append(_ms_to_lrc_timestamp(int(m.group(1))) + text)
                continue
            # 时间戳块不在行首的怪异行：仅去逐字标注保留原样
            cleaned = re.sub(r'\(\d+(?:,\d+)+\)', '', line).strip()
            result.append(cleaned if cleaned else line)
            continue
        if re.search(r'\(\d+(?:,\d+)+\)', line):
            # 无行时间戳但带逐字标注的残留行：仅去标注，保留原行（含[00:12.34]等LRC时间戳）
            cleaned = re.sub(r'\(\d+(?:,\d+)+\)', '', line).strip()
            result.append(cleaned if cleaned else line)
        else:
            # [ti:] [ar:] [offset:] 等元数据行与普通LRC行原样保留
            result.append(line)
    return "\n".join(result)

def normalize_tx_lyric(raw):
    """QQ歌词归一化为LRC：hex密文 → 解密 → XML/裸QRC → LRC；普通LRC原样返回"""
    if not raw:
        return ""
    raw = raw.strip()
    if re.fullmatch(r'[0-9A-Fa-f]+', raw):
        decrypted = qrc_decrypt(raw)
        if decrypted:
            raw = decrypted.strip()
    if raw.lstrip().startswith("<?xml") or "<Lyric_" in raw or "<QrcInfos" in raw:
        return qrc_xml_to_lrc(raw)
    if re.search(r'\[\d+,\d+\]', raw) or re.search(r'\(\d+(?:,\d+)+\)', raw):
        return qrc_time_to_lrc(raw)
    return raw

async def get_lyric(song_id: str) -> Lyric:
    # v2: 升级缓存键，旧缓存中可能存有未转换的QRC原始数据
    cache_key = f"lyric2_tx_{song_id}"
    cached = cache.get("lyric", cache_key)
    if cached:
        return Lyric(**cached)

    try:
        comm = await build_comm()
        resp = await sign_request({
            "comm": comm,
            "req": {
                "module": "music.musichallSong.PlayLyricInfo",
                "method": "GetPlayLyricInfo",
                "param": {
                    "songID": int(song_id) if str(song_id).isdigit() else 0,
                    "songMid": song_id,
                    "qrc": 1, "trans": 1,
                },
            },
        })
        lyric_data = resp.get("req", {}).get("data", {})
        if not lyric_data:
            raise FailedException("未检索到歌词")
        raw_lyric = lyric_data.get("lyric", "") or ""
        lyric = normalize_tx_lyric(raw_lyric)
        raw_trans = lyric_data.get("trans", "") or ""
        trans = normalize_tx_lyric(raw_trans)
        result = Lyric(lyric=lyric, trans=trans)
        cache.set("lyric", cache_key, result.model_dump(), getExpireTime("tx", "lyric"))
        await stats_manager.increment("tx", "lyric", True)
        return result
    except FailedException:
        await stats_manager.increment("tx", "lyric", False)
        raise
    except Exception as e:
        logger.error(f"获取QQ音乐歌词失败: {e}")
        await stats_manager.increment("tx", "lyric", False)
        raise FailedException(f"获取歌词失败: {e}")

# ============================================================
# 刷新登录
# ============================================================
async def refresh_login(user: Dict) -> Dict:
    uin = str(user.get("uin", ""))
    if not uin or uin == "0" or not user.get("token"):
        return user
    try:
        comm = await build_comm(user)
        resp = await sign_request({
            "comm": comm,
            "req": {
                "module": "music.login.LoginServer",
                "method": "Login",
                "param": {
                    "openid": user.get("openId", ""),
                    "access_token": user.get("accessToken", ""),
                    "refresh_token": user.get("refreshToken", ""),
                    "expired_in": 0,
                    "musicid": int(uin),
                    "musickey": user["token"],
                    "refresh_key": user.get("refreshKey", ""),
                    "loginMode": 2,
                },
            },
        })
        req_data = resp.get("req", {})
        if req_data.get("code") != 0:
            logger.warning(f"QQ账号 {uin} 刷新失败 code={req_data.get('code')}")
            return user
        data = req_data.get("data", {})
        if data.get("musickey"):
            user["token"] = data["musickey"]
            user["uin"] = str(data.get("musicid", uin))
            if data.get("openid"): user["openId"] = str(data["openid"])
            if data.get("access_token"): user["accessToken"] = str(data["access_token"])
            if data.get("refresh_key"): user["refreshKey"] = str(data["refresh_key"])
            logger.info(f"QQ账号 {uin} token刷新成功")
    except Exception as e:
        logger.error(f"QQ刷新登录异常: {e}")
    return user
