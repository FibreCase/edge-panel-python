# Edge Panel Python Backend

这是 `flutter_desktop_panel` 的后端服务，基于 FastAPI + Socket.IO 构建。它为前端面板提供天气、事件和消息数据，负责图片上传、消息持久化，并托管 `web/` 下的静态页面。

## 功能

- 聚合天气数据：当前天气、分钟级降水和空气质量。
- 提供消息接口：文本消息、图片消息、通知消息、单条删除和清空。
- 支持图片上传，并在本地保存原始文件或转换后的 JPEG 文件。
- 通过 Socket.IO 推送 `messages_updated`，让前端在消息变更后立即刷新。
- 提供静态前端页面：`web/index.html` 和 `web/manage.html`。

## 目录结构

- `app/main.py`：FastAPI 入口，定义 HTTP 接口、Socket.IO 事件和静态资源挂载。
- `app/message_service.py`：SQLite 消息存储与读写逻辑。
- `app/weather_service.py`：QWeather 请求与缓存逻辑。
- `app/event_service.py`：预留事件服务文件，目前为空。
- `web/index.html`：消息展示与发送页面。
- `web/manage.html`：消息管理页面。
- `web/sw.js`：静态资源缓存的 Service Worker。

## 运行方式

### 本地运行

```bash
uv sync
uv run python app/main.py
```

服务默认监听 `http://127.0.0.1:5000`。

### Docker

```bash
docker compose up -d
```

## 环境变量

后端会读取以下环境变量：

- `LOCATION`：天气查询经纬度，默认 `0, 0`
- `KID`：QWeather key id，对应 `QWEATHER_KID`
- `PROJECT_ID`：QWeather project id，对应 `QWEATHER_PROJECT_ID`
- `QWEATHER_PRIVATE_KEY_FILE`：QWeather Ed25519 私钥文件路径，默认 `app/secrets/ed25519-private.pem`
- `PUBLIC_BASE_URL`：对外可访问地址，用于生成图片 URL
- `LOCAL_BASE_URL`：本机访问地址，默认 `http://127.0.0.1:5000`

如果未设置 `PUBLIC_BASE_URL`，上传图片返回的地址会使用 `LOCAL_BASE_URL`。

## 接口说明

- `GET /api/messages`：获取消息列表
- `GET /api/messages/deleted`：获取最近 20 条历史消息
- `POST /api/messages`：创建文本、图片或通知消息记录
- `POST /api/messages/upload-image`：上传图片并生成图片消息
- `POST /api/messages/webhook/notify`：其他应用通过 webhook 创建通知消息
- `POST /api/messages/deleted/{message_id}/restore`：恢复历史消息

```bash
curl -X POST 'http://127.0.0.1:5000/api/messages/webhook/notify' \
  -H 'Content-Type: application/json' \
  -d '{
    "app_name": "OtherApp",
    "body": "这就是通知正文"
  }'
```

- `POST /api/messages/clear`：清空当前消息，删除内容会进入历史消息，最多保留最近 20 条

## Socket.IO 事件

- `request_weather` -> 返回 `weather_data`
- `request_event` -> 返回 `event_data`
- `messages_updated` -> 消息变更通知

## 数据存储

- 消息数据库：`app/.cache/messages.db`
- 图片缓存目录：`app/.cache/uploads/`
- 天气缓存目录：`app/.cache/`

## 实现说明

- 图片上传支持常见图片格式，也会将 HEIC / HEIF 转换为 JPEG 后保存。
- 天气数据通过 QWeather API 拉取，并在本地缓存以减少重复请求。
- 事件数据当前由 `app/main.py` 中的固定 payload 提供，后续可接入独立事件源。
- `web/` 下的静态页面由后端直接挂载，上传后的图片也通过 `/uploads/` 对外访问。

## 说明

- 首次运行前，请确保 QWeather 私钥文件可用，或通过 `QWEATHER_PRIVATE_KEY_FILE` 指定路径。
- 如果你希望前端展示的图片地址使用公网域名，请设置 `PUBLIC_BASE_URL`。
