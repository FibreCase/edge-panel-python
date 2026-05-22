import base64
import gzip
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

api_host = "https://kx3yfp4b97.re.qweatherapi.com"
private_key_file = Path(__file__).with_name("secrets") / "ed25519-private.pem"

cache_dir = Path(__file__).with_name(".cache")

weather_cache_file = cache_dir / "weather.tmp"
precipitation_cache_file = cache_dir / "precipitation.tmp"
airquality_cache_file = cache_dir / "airquality.tmp"


def _base64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_private_key_path(private_key_path=None):
    path = Path(private_key_path or os.getenv("QWEATHER_PRIVATE_KEY_FILE") or private_key_file)
    if not path.exists():
        raise ValueError(f"Missing QWeather private key file: {path}")
    return path


def _build_jwt(private_key_path=None, kid=None, project_id=None, iat=None, exp=None):
    kid = kid or os.getenv("QWEATHER_KID")
    project_id = project_id or os.getenv("QWEATHER_PROJECT_ID")
    if not kid:
        raise ValueError("Missing QWeather kid. Set QWEATHER_KID or pass kid explicitly.")
    if not project_id:
        raise ValueError("Missing QWeather project id. Set QWEATHER_PROJECT_ID, or pass project_id explicitly.")

    private_key_path = _load_private_key_path(private_key_path)
    iat = int(iat if iat is not None else time.time()) - 30
    exp = int(exp if exp is not None else iat + 900)

    header = {"alg": "EdDSA", "kid": kid}
    payload = {"sub": project_id, "iat": iat, "exp": exp}
    header_payload = f"{_base64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))}.{_base64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))}"

    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as temp_file:
        temp_file.write(header_payload.encode("utf-8"))
        temp_file_path = Path(temp_file.name)

    try:
        try:
            sign_result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-sign",
                    "-inkey",
                    str(private_key_path),
                    "-rawin",
                    "-in",
                    str(temp_file_path),
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("openssl is required to sign the QWeather JWT") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace").strip() if exc.stderr else ""
            raise RuntimeError(f"Failed to sign QWeather JWT: {stderr or exc}") from exc
    finally:
        temp_file_path.unlink(missing_ok=True)

    return f"{header_payload}.{_base64url_encode(sign_result.stdout)}"


def _load_cache(file_path):
    """加载缓存数据"""
    try:
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return None


def _save_cache(file_path, data):
    """保存数据到缓存"""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except IOError as e:
        print(f"Warning: Failed to save cache: {e}")


def _is_cache_valid(cache_data, max_age_seconds=1800):
    """检查缓存是否有效（默认30分钟）"""
    if not cache_data or "cached_at" not in cache_data:
        return False
    
    try:
        # 比较缓存创建时间与当前时间
        cached_at = cache_data["cached_at"]
        current_time = time.time()
        # 检查缓存是否在 max_age_seconds 内
        return (current_time - cached_at) < max_age_seconds
    except (ValueError, KeyError, TypeError):
        return False


def _request_current_weather(location="116.41,39.92", token=None, private_key_path=None, kid=None, project_id=None):
    
    token = token or _build_jwt(private_key_path=private_key_path, kid=kid, project_id=project_id)

    request_url = f"{api_host}/v7/grid-weather/now?{urlencode({'location': location, 'lang': 'en'})}"
    request = Request(
        request_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Encoding": "gzip, deflate",
        }
    )

    try:
        with urlopen(request, timeout=10) as response:
            data = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                data = gzip.decompress(data)
            payload = json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"QWeather HTTP error: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"QWeather request failed: {exc.reason}") from exc

    if payload.get("code") != "200":
        raise RuntimeError(f"QWeather API error: {payload}")

    now = payload.get("now", {})
    return {
        "code": payload.get("code"),
        "updateTime": payload.get("updateTime"),
        "now": {
            "obsTime": now.get("obsTime"),
            "temp": now.get("temp"),
            "icon": now.get("icon"),
            "text": now.get("text"),
            "wind360": now.get("wind360"),
            "windDir": now.get("windDir"),
            "windScale": now.get("windScale"),
            "windSpeed": now.get("windSpeed"),
            "humidity": now.get("humidity"),
            "precip": now.get("precip"),
            "pressure": now.get("pressure"),
            # "cloud": now.get("cloud"),
            # "dew": now.get("dew"),
        },
        "refer": payload.get("refer", {}),
    }

def fetch_current_weather(location="116.41,39.92", token=None, private_key_path=None, kid=None, project_id=None):
    """
    获取当前天气信息，支持缓存。
    如果缓存未超过 30 分钟，直接返回缓存数据；否则重新请求。
    """
    # 尝试加载缓存
    weather_cached_data = _load_cache(weather_cache_file)
    
    # 检查缓存是否有效
    if weather_cached_data and _is_cache_valid(weather_cached_data):
        return weather_cached_data
    
    # 缓存失效或不存在，重新请求
    weather_data = _request_current_weather(
        location=location,
        token=token,
        private_key_path=private_key_path,
        kid=kid,
        project_id=project_id
    )
    
    # 添加缓存时间戳
    weather_data["cached_at"] = time.time()
    
    # 保存到缓存
    _save_cache(weather_cache_file, weather_data)
    
    return weather_data


def _request_minutely_precipitation(location="116.41,39.92", token=None, private_key_path=None, kid=None, project_id=None):
    token = token or _build_jwt(private_key_path=private_key_path, kid=kid, project_id=project_id)

    request_url = f"{api_host}/v7/minutely/5m?{urlencode({'location': location, 'lang': 'zh-hans'})}"
    request = Request(
        request_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Encoding": "gzip, deflate",
        }
    )

    try:
        with urlopen(request, timeout=10) as response:
            data = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                data = gzip.decompress(data)
            payload = json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"QWeather HTTP error: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"QWeather request failed: {exc.reason}") from exc

    if payload.get("code") != "200":
        raise RuntimeError(f"QWeather API error: {payload}")

    return {
        "code": payload.get("code"),
        "updateTime": payload.get("updateTime"),
        "summary": payload.get("summary"),
        "minutely": payload.get("minutely", []),
    }

def fetch_minutely_precipitation(location="116.41,39.92", token=None, private_key_path=None, kid=None, project_id=None):
    """
    获取分钟级降水信息，支持缓存。
    如果缓存未超过 10 分钟，直接返回缓存数据；否则重新请求。
    """
    # 尝试加载缓存
    precipitation_cached_data = _load_cache(precipitation_cache_file)
    
    if precipitation_cached_data and precipitation_cached_data.get("summary", "") != "未来两小时无降水":
        max_age_seconds = 300
    else:
        max_age_seconds = 3600
    
    # 检查缓存是否有效
    if precipitation_cached_data and _is_cache_valid(precipitation_cached_data, max_age_seconds=max_age_seconds):
        return precipitation_cached_data
    
    # 缓存失效或不存在，重新请求
    precipitation_data = _request_minutely_precipitation(
        location=location,
        token=token,
        private_key_path=private_key_path,
        kid=kid,
        project_id=project_id
    )
    
    # 添加缓存时间戳
    precipitation_data["cached_at"] = time.time()
    
    # 保存到缓存
    _save_cache(precipitation_cache_file, precipitation_data)
    
    return precipitation_data

def _request_air_quality(location="116.41,39.92", token=None, private_key_path=None, kid=None, project_id=None):
    token = token or _build_jwt(private_key_path=private_key_path, kid=kid, project_id=project_id)

    longitude, latitude = location.split(",")
    request_url = f"{api_host}/airquality/v1/current/{latitude}/{longitude}?lang=zh-hans"
    request = Request(
        request_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Encoding": "gzip, deflate",
        }
    )

    try:
        with urlopen(request, timeout=10) as response:
            data = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                data = gzip.decompress(data)
            payload = json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"QWeather HTTP error: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"QWeather request failed: {exc.reason}") from exc

    # Air quality API returns indexes array instead of code field
    indexes = payload.get("indexes", [])
    if not indexes:
        raise RuntimeError(f"QWeather API error: No air quality data returned")

    index = indexes[0]  # Get the first (main) index
    return {
        "aqi": index.get("aqi"),
        "category": index.get("category"),
    }
    
def fetch_air_quality(location="116.41,39.92", token=None, private_key_path=None, kid=None, project_id=None):
    """
    获取当前空气质量信息，支持缓存。
    如果缓存未超过 30 分钟，直接返回缓存数据；否则重新请求。
    """
    # 尝试加载缓存
    airquality_cached_data = _load_cache(airquality_cache_file)
    
    # 检查缓存是否有效
    if airquality_cached_data and _is_cache_valid(airquality_cached_data):
        return airquality_cached_data
    
    # 缓存失效或不存在，重新请求
    airquality_data = _request_air_quality(
        location=location,
        token=token,
        private_key_path=private_key_path,
        kid=kid,
        project_id=project_id
    )
    
    # 添加缓存时间戳
    airquality_data["cached_at"] = time.time()
    
    # 保存到缓存
    _save_cache(airquality_cache_file, airquality_data)
    
    return airquality_data

