# Python Backend

这是 `flutter_desktop_panel` 的后端服务，基于 FastAPI + Socket.IO 构建，负责为 Flutter 前端提供天气、事件和消息数据，并处理图片上传与消息持久化。

## 主要职责

- 提供天气数据接口，聚合当前天气、分钟级降水和空气质量信息。
- 提供事件数据接口，供前端面板展示“下一条事件”。
- 提供消息接口，支持文本消息、图片消息、通知消息和消息清空。
- 通过 Socket.IO 推送 `messages_updated`，让前端在消息变更后立即刷新。
- 将消息数据保存到 SQLite 数据库，并将上传图片保存到本地缓存目录。

## 目录结构

- `app/main.py`：FastAPI 入口，定义 HTTP 接口和 Socket.IO 事件。
- `app/message_backend.py`：消息数据库读写逻辑，使用 SQLite 存储。
- `app/weather_fetch_remote.py`：QWeather 请求与缓存逻辑。
- `app/secrets/`：QWeather 私钥文件存放目录。
- `web/`：静态站点目录，由后端挂载到根路径。

## 运行方式

### 本地运行

```bash
cd python
uv sync
uv run python app/main.py
```

服务默认监听 `http://127.0.0.1:5000`。

### Docker

```bash
cd python
docker compose up -d
```

## 环境变量

后端会读取以下环境变量：

- `LOCATION`：天气查询经纬度，默认 `116.31,40.09`
- `KID`：QWeather key id
- `PROJECT_ID`：QWeather project id
- `PUBLIC_BASE_URL`：对外可访问地址，用于生成图片 URL
- `LOCAL_BASE_URL`：本机访问地址，默认 `http://127.0.0.1:5000`

如果未设置 `PUBLIC_BASE_URL`，上传图片返回的地址会使用 `LOCAL_BASE_URL`。

## 接口说明

- `GET /api/messages`：获取消息列表
- `POST /api/messages`：创建文本、图片或通知消息记录
- `POST /api/messages/upload-image`：上传图片并生成图片消息
- `POST /api/messages/webhook/notify`：其他应用通过 webhook 创建通知消息

```bash
curl -X POST 'http://127.0.0.1:5000/api/messages/webhook/notify' \
  -H 'Content-Type: application/json' \
  -d '{
    "app_name": "OtherApp",
    "body": "这就是通知正文"
  }'
```

- `POST /api/messages/clear`：清空消息和已上传文件

## Socket.IO 事件

- `request_weather` -> 返回 `weather_data`
- `request_event` -> 返回 `event_data`
- `messages_updated` -> 消息变更通知

## 数据存储

- 消息数据库：`app/.cache/messages.db`
- 图片缓存目录：`app/.cache/uploads/`
- 天气缓存目录：`app/.cache/`

## 说明

- 首次运行前，请确保 QWeather 私钥文件已放入 `app/secrets/`，或通过环境变量提供路径。
- 后端同时提供静态资源挂载，便于前端直接访问上传的图片。
