# Edge Panel Python Backend

这是 `flutter_desktop_panel` 的后端服务，基于 FastAPI + Socket.IO 构建。它为前端面板提供天气、事件和消息数据，负责图片上传、消息持久化，并托管 `web/` 下的静态页面。

## 功能

- 聚合天气数据：当前天气、分钟级降水和空气质量。
- 提供消息接口：文本消息、图片消息、通知消息、单条删除和清空。
- 消息管理页使用四位数字密码鉴权，管理操作由后端 Bearer 会话令牌保护。
- 支持图片上传，并在本地保存原始文件或转换后的 JPEG 文件。
- 通过 Socket.IO 推送 `messages_updated`，让前端在消息变更后立即刷新。
- 提供静态前端页面：`web/index.html` 和 `web/manage.html`。

## 目录结构

- `app/main.py`：FastAPI 入口，定义 HTTP 接口、Socket.IO 事件和静态资源挂载。
- `app/message_service.py`：SQLite 消息存储与读写逻辑。
- `app/weather_service.py`：QWeather 请求与缓存逻辑。
- `app/event_service.py`：事件服务，目前返回占位数据，后续可接入独立事件源。
- `web/index.html`：消息展示与发送页面。
- `web/manage.html`：带密码登录界面的消息管理页面。
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

### 天气相关

| 变量 | 说明 | 是否必填 | 默认值 |
|------|------|----------|--------|
| `QWEATHER_LOCATION` | 天气查询经纬度（经度,纬度） | 否 | `116.41,39.92` |
| `QWEATHER_KID` | QWeather key id | 是（天气功能） | — |
| `QWEATHER_PROJECT_ID` | QWeather project id | 是（天气功能） | — |
| `QWEATHER_PRIVATE_KEY_FILE` | QWeather Ed25519 私钥文件路径 | 否 | `app/secrets/ed25519-private.pem` |

### 管理鉴权、图片与服务地址

| 变量 | 说明 | 是否必填 | 默认值 |
|------|------|----------|------|
| `PORT` | 服务监听端口 | 否 | `5000` |
| `PUBLIC_BASE_URL` | 对外可访问地址，用于生成图片 URL | 否 | — |
| `LOCAL_BASE_URL` | 本机访问地址，`PUBLIC_BASE_URL` 未设置时作为回退 | 否 | `http://127.0.0.1:5000` |
| `MANAGE_PASSWORD` | 消息管理页密码，必须是四位 ASCII 数字 | 否 | `1234` |

本地运行与 Docker 使用相同的环境变量名。使用 `docker compose` 时，可在项目根目录创建 `.env` 文件。部署时请务必修改默认管理密码，例如 `MANAGE_PASSWORD=7391`；格式不符合要求时服务会拒绝启动。

## 接口说明

### 公共消息接口

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

### 管理鉴权接口

- `POST /api/manage/login`：提交 `{ "password": "四位密码" }`，成功后返回 Bearer 令牌
- `GET /api/messages/deleted`：获取最近 20 条历史消息，需要管理令牌
- `DELETE /api/messages/{message_id}`：删除单条消息，需要管理令牌
- `POST /api/messages/deleted/{message_id}/restore`：恢复历史消息，需要管理令牌
- `POST /api/messages/clear`：清空当前消息，需要管理令牌；删除内容会进入历史消息

```bash
curl -X POST 'http://127.0.0.1:5000/api/manage/login' \
  -H 'Content-Type: application/json' \
  -d '{"password":"7391"}'

curl 'http://127.0.0.1:5000/api/messages/deleted' \
  -H 'Authorization: Bearer <登录接口返回的 token>'
```

管理令牌保存在服务内存中，有效期为 8 小时，服务重启后自动失效。浏览器管理页将令牌保存在当前标签页的 `sessionStorage` 中；退出管理或关闭标签页后需要重新验证。

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
- 事件数据当前由 `app/event_service.py` 提供固定占位 payload，后续可接入独立事件源。
- `web/` 下的静态页面由后端直接挂载，上传后的图片也通过 `/uploads/` 对外访问。
- 主页面不再提供清空消息按钮；进入管理页后必须先输入四位密码，删除、恢复及历史记录操作也会在后端验证管理令牌。

## 说明

- 首次运行前，请确保 QWeather 私钥文件可用，或通过 `QWEATHER_PRIVATE_KEY_FILE` 指定路径。
- 如果你希望前端展示的图片地址使用公网域名，请设置 `PUBLIC_BASE_URL`。
- 四位密码的组合空间有限，公网部署时还应使用 HTTPS，并通过反向代理限制管理入口的访问频率或来源。
