"""
核心模块
包含: config, cache, log, http, constants, exceptions, variable, statistics, models
"""
import asyncio
import json
import os
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from loguru import logger as _loguru_logger
from pydantic import BaseModel

from . import encode

# ============================================================
# 路径配置 - 统一项目根目录
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # music-api-v2/
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / "cache"
LOG_DIR = PROJECT_ROOT / "logs"
CONFIG_PATH = PROJECT_ROOT / "config.json"

for d in (DATA_DIR, CACHE_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ============================================================
# 异常
# ============================================================
class FailedException(Exception):
    def __init__(self, message="获取失败"):
        self.message = message
        super().__init__(self.message)

class ConfigGenerateException(Exception):
    pass


# ============================================================
# 全局变量
# ============================================================
http_client: Optional[aiohttp.ClientSession] = None


# ============================================================
# 常量
# ============================================================
SOURCE_NAME = {"kw": "酷我", "kg": "酷狗", "tx": "QQ音乐", "wy": "网易云"}

QUALITY_MAP = {
    "128k": "128k", "192k": "192k", "320k": "320k",
    "flac": "flac", "flac24bit": "flac24bit",
    "hires": "hires", "atmos": "atmos", "atmos_plus": "atmos_plus",
    "master": "master",
}

# ============================================================
# 逐级降级链：从请求的音质开始，依次往下试，直到成功
# 注：相邻档位之间不允许跳级（如 master → 320k 是禁止的）
# ============================================================
FALLBACK_ORDER = {
    "master":     ["hires", "flac", "320k", "128k"],
    "atmos_plus": ["atmos", "hires", "flac", "320k", "128k"],
    "atmos":      ["hires", "flac", "320k", "128k"],
    "hires":      ["flac", "320k", "128k"],
    "flac24bit":  ["flac", "320k", "128k"],
    "flac":       ["320k", "128k"],
    "320k":       ["128k"],
    "192k":       ["128k"],
    "128k":       [],
}

# 音质等级排序（数字越大音质越高），用于判断"反向升级"
QUALITY_RANK = {
    "128k": 1, "192k": 2, "320k": 3,
    "flac": 4, "flac24bit": 5,
    "hires": 6, "atmos": 7, "atmos_plus": 8, "master": 9,
}


def get_fallback_chain(quality: str) -> list:
    """返回包含自身的完整降级链：[quality, ...fallbacks]"""
    chain = [quality]
    chain.extend(FALLBACK_ORDER.get(quality, []))
    return chain


def quality_rank(q: str) -> int:
    """返回音质等级数值，未知返回 0"""
    return QUALITY_RANK.get(q, 0)


def compare_quality(requested: str, actual: str) -> str:
    """
    比较请求音质与实际音质。
    返回:
      - "match"    一致
      - "fallback" 降级（实际 < 请求）
      - "upgraded" 反向升级（实际 > 请求）
    """
    r = quality_rank(requested)
    a = quality_rank(actual)
    if a == r:
        return "match"
    elif a < r:
        return "fallback"
    else:
        return "upgraded"


def parse_quality_from_url(url: str, source: str) -> str:
    """
    从上游返回的播放 URL 中解析实际音质。
    各平台 URL 特征：
      - kg (酷狗): qu128 / qu320 / quflac / quhigh / quviper_clear / quviper_atmos
      - tx (QQ):   M500 (128k) / M800 (320k) / F000 (flac) / RS01 (hires)
                   Q000 (atmos) / Q001 (atmos_plus) / AI00 (master)
      - wy (网易): URL 中带 /128/ /320/ /lossless/ /hires/ /jymaster/
                   或从 size 推断（由 wy.py 在拿到响应后另行处理）
      - kw (酷我): format 字段直接给出（在 info 里已解析）
      - migu:     URL 中 /PQ/ (128k) /HQ/ (320k) /SQ/ (flac) /ZQ/ (hires/flac24bit)
    返回找不到时为 "unknown"。
    """
    if not url:
        return "unknown"
    u = str(url).lower()

    if source == "kg":
        if "quviper_clear" in u or "viper_clear" in u:
            return "master"
        if "quviper_atmos" in u or "viper_atmos" in u:
            return "atmos"
        if "quhigh" in u or "/high" in u or "_high" in u:
            return "hires"
        if "quflac" in u or ".flac" in u:
            return "flac"
        if "qu320" in u or "_320" in u or "/320" in u:
            return "320k"
        if "qu128" in u or "_128" in u or "/128" in u:
            return "128k"
        # 兜底：根据扩展名
        if u.endswith(".flac"):
            return "flac"
        if u.endswith(".mp3"):
            return "320k"
        return "unknown"

    if source == "tx":
        # 文件名前缀是关键特征
        if "ai00" in u:
            return "master"
        if "q001" in u:
            return "atmos_plus"
        if "q000" in u:
            return "atmos"
        if "rs01" in u:
            return "hires"
        if "f000" in u:
            return "flac"
        if "m800" in u:
            return "320k"
        if "m500" in u:
            return "128k"
        return "unknown"

    if source == "wy":
        if "jymaster" in u or "jymaster" in u:
            return "master"
        if "hires" in u:
            return "hires"
        if "lossless" in u or "/flac" in u or ".flac" in u:
            return "flac"
        if "/320" in u or "_320" in u or "exhigh" in u:
            return "320k"
        if "/192" in u or "_192" in u:
            return "192k"
        if "/128" in u or "_128" in u or "standard" in u:
            return "128k"
        return "unknown"

    if source == "migu":
        if "/zq/" in u or "zq/" in u:
            # ZQ 既可能是 hires 也可能是 flac24bit，无法区分，统一报 hires
            return "hires"
        if "/sq/" in u or "sq/" in u or "/flac" in u:
            return "flac"
        if "/hq/" in u or "hq/" in u:
            return "320k"
        if "/pq/" in u or "pq/" in u:
            return "128k"
        return "unknown"

    if source == "kw":
        # 酷我的 URL 没有强特征，依赖 info.format/br
        return "unknown"

    return "unknown"


def getExpireTime(source: str, type: str = "url") -> int:
    return 600

def translateStrOrInt(value):
    return value


# ============================================================
# 数据模型
# ============================================================
class SongInfo(BaseModel):
    model_config = {"extra": "allow"}
    songId: str
    songName: str
    artistName: str
    albumName: str = ""
    albumId: str = ""
    duration: str = ""
    coverUrl: str = ""

class UrlResponse(BaseModel):
    model_config = {"extra": "allow"}
    url: str
    quality: str = ""          # 实际返回的音质
    requested: str = ""        # 用户请求的音质
    fallback: bool = False     # 是否发生了降级
    upgraded: bool = False     # 是否发生了反向升级
    direction: str = "match"   # match / fallback / upgraded / unknown
    fallback_chain: str = ""   # 实际尝试过的降级链路，如 "master→hires→flac"
    sent_level: str = ""       # 传给上游的 level/quality 参数
    returned_level: str = ""   # 上游返回的 level/quality 标识
    url_feature: str = ""      # URL 中的音质特征

class Lyric(BaseModel):
    lyric: str
    trans: str = ""

class KGSpecial(BaseModel):
    album_audio_id: str
    hash: str

class TXSpecial(BaseModel):
    strMediaMid: str
    songId: str


# ============================================================
# 日志 - 统一到 logs/ 目录
# ============================================================
_loguru_logger.remove()

def _log_filter(record):
    return record["level"].no >= 20  # INFO及以上

_loguru_logger.add(
    sys.stderr if False else os.devnull,  # 不输出到stderr，避免污染CLI JSON输出
    level="INFO",
)

# 文件日志 - 按天分割
_loguru_logger.add(
    str(LOG_DIR / "music-api_{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="7 days",
    level="INFO",
    encoding="utf-8",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)

import sys  # noqa: E402

def createLogger(name: str):
    return _loguru_logger.bind(name=name)


# ============================================================
# 文件缓存
# ============================================================
_cache_lock = threading.Lock()

def _cache_file(module: str, key: str) -> Path:
    safe_key = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    d = CACHE_DIR / module
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe_key}.json"

class CacheManager:
    def __init__(self):
        self.logger = createLogger("Cache")
        self.logger.info(f"文件缓存管理器已初始化，目录: {CACHE_DIR}")

    def get(self, module: str, key: str):
        try:
            f = _cache_file(module, key)
            if not f.exists():
                return None
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("expire") and data.get("time", 0) < time.time():
                f.unlink(missing_ok=True)
                return None
            return data.get("data")
        except Exception as e:
            self.logger.error(f"缓存读取错误: {e}")
            return None

    def set(self, module: str, key: str, data, expire: int = None):
        try:
            f = _cache_file(module, key)
            payload = {
                "data": data,
                "time": int(time.time()) + (expire if expire else 0),
                "expire": expire is not None and expire > 0,
            }
            with _cache_lock:
                f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            self.logger.error(f"缓存写入错误: {e}")

    def delete(self, module: str, key: str):
        try:
            f = _cache_file(module, key)
            if f.exists():
                f.unlink()
        except Exception as e:
            self.logger.error(f"缓存删除错误: {e}")

cache = CacheManager()


# ============================================================
# HTTP 客户端
# ============================================================
class _HttpClient:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
        self.logger = createLogger("HTTP")

    async def get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=15)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

http = _HttpClient()

async def send_http_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Any = None,
    json_data: Any = None,
    timeout: int = 15,
) -> aiohttp.ClientResponse:
    session = await http.get_session()
    kwargs = {"timeout": aiohttp.ClientTimeout(total=timeout)}
    if headers:
        kwargs["headers"] = headers
    if params:
        kwargs["params"] = params
    if data is not None:
        kwargs["data"] = data
    if json_data is not None:
        kwargs["json"] = json_data
    return await session.request(method, url, **kwargs)


# ============================================================
# 统计
# ============================================================
class StatsManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict[str, int]] = {}

    async def increment(self, source: str, endpoint: str, success: bool):
        with self._lock:
            key = f"{source}_{endpoint}"
            if key not in self._stats:
                self._stats[key] = {"success": 0, "fail": 0}
            if success:
                self._stats[key]["success"] += 1
            else:
                self._stats[key]["fail"] += 1

    def get_stats(self):
        with self._lock:
            return dict(self._stats)

stats_manager = StatsManager()


# ============================================================
# 配置管理 - 统一读取根目录 config.json
# ============================================================
_DEFAULT_CONFIG = {
    "script": {
        "name": "Music API",
        "description": "多平台音乐解析服务",
        "support_qualitys": {
            "kg": ["128k", "320k", "flac", "flac24bit", "hires"],
            "tx": ["128k", "320k", "flac", "flac24bit", "hires"],
            "wy": ["128k", "320k", "flac", "hires"],
            "kw": ["128k", "320k", "flac", "hires"],
            "migu": ["128k", "320k", "flac", "flac24bit"],
        },
        "version": "v2.0",
    },
    "modules": {
        "platform": {
            "kg": {
                "mid": ["musicapi"],
                "users": [{"userid": "", "token": "", "refreshLogin": False}],
            },
            "tx": {
                "users": [{
                    "uin": "", "token": "", "refreshKey": "",
                    "openId": "", "accessToken": "", "refreshToken": "",
                    "vipType": "", "refreshLogin": False,
                }],
                "cdn_list": ["http://ws.stream.music.qq.com/"],
            },
        }
    },
    "migu": {
        "accounts": [""],
        "decrypt_key": "Jk8qzuePiJ1qE3mDYhLQ3T73DtDoAhLP",
    },
    "wy": {"cookie": ""},
}

class ConfigManager:
    def __init__(self):
        self.config_path = CONFIG_PATH
        self.config: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self.logger = createLogger("Config")
        self._load()

    def _load(self):
        try:
            if self.config_path.exists():
                self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.logger.info(f"配置文件加载成功: {self.config_path}")
            else:
                self._generate_default()
        except Exception as e:
            self.logger.error(f"配置加载失败: {e}，使用默认配置")
            self.config = dict(_DEFAULT_CONFIG)

    def _generate_default(self):
        try:
            with self._lock:
                self.config_path.write_text(
                    json.dumps(_DEFAULT_CONFIG, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                self.config = dict(_DEFAULT_CONFIG)
                self.logger.warning(f"已创建默认配置文件: {self.config_path}")
        except Exception as e:
            self.logger.error(f"配置生成失败: {e}")
            raise ConfigGenerateException(e)

    def get(self, *keys, default=None):
        """安全获取嵌套配置: config.get('modules', 'platform', 'kg', 'users')"""
        val = self.config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def get_platform_users(self, source: str) -> List[Dict]:
        return self.get("modules", "platform", source, "users", default=[])

    def set_nested(self, *keys_and_value):
        """更新嵌套配置并保存: set_nested('modules','platform','kg','users', new_users)"""
        if len(keys_and_value) < 2:
            return
        keys = keys_and_value[:-1]
        value = keys_and_value[-1]
        node = self.config
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value
        self.save()

    def save(self):
        with self._lock:
            self.config_path.write_text(
                json.dumps(self.config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def get_tx_cdn_list(self) -> List[str]:
        return self.get("modules", "platform", "tx", "cdn_list", default=["http://ws.stream.music.qq.com/"])

    def get_kg_mid(self) -> List[str]:
        return self.get("modules", "platform", "kg", "mid", default=["musicapi"])

config = ConfigManager()
