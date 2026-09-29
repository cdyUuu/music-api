import base64
import hashlib
import json as _json
from typing import Any, Dict, Union
from urllib.parse import quote, unquote, urlparse

import orjson

def b64encode(data_bytes):
    return base64.b64encode(data_bytes).decode("utf-8")

def b64decode(data):
    return base64.b64decode(data)

def sort_dict(dictionary):
    return {k: v for k, v in sorted(dictionary.items())}

def merge_dict(dict1, dict2):
    merged = dict2.copy()
    merged.update(dict1)
    return merged

class JSONDecodeError(ValueError):
    pass

def json_loads(s):
    try:
        return orjson.loads(s)
    except orjson.JSONDecodeError as e:
        raise JSONDecodeError(f"JSON 解析错误: {e}")

def json_dumps(obj, indent_2=False):
    options = orjson.OPT_INDENT_2 if indent_2 else 0
    return orjson.dumps(obj, option=options).decode()

def md5(*strings: Union[str, bytes]):
    h = hashlib.md5()
    for item in strings:
        if isinstance(item, bytes):
            h.update(item)
        elif isinstance(item, str):
            h.update(item.encode())
        else:
            raise ValueError(f"Unsupported type: {type(item)}")
    return h.hexdigest()

def encodeURIComponent(component):
    if isinstance(component, str):
        component = component.encode("utf-8")
    return quote(component)

def decodeURIComponent(component):
    return unquote(component)

def encodeURI(uri):
    parse_result = urlparse(uri)
    params = {}
    for q in parse_result.query.split("&"):
        k, v = q.split("=")
        params[k] = encodeURIComponent(v)
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return parse_result._replace(query=query).geturl()
