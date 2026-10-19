# WechatVideoDL · Docker 部署指南

本目录是 WechatVideoDL 推荐的 Ubuntu / Debian 服务器版本：

```text
Flask + SQLite + yt-dlp
        │
        ▼
Browserless Headless Chromium
        │
        ├── Tencent Yuanbao
        └── WeChat Channels Preview
```

它不依赖 Playwright SDK，也不需要桌面浏览器。

![WebUI](../docs/images/webui-dark.png)

当前 Docker 版对应最新版 WebUI，已经包含：

- 最多 3 次自动重试；
- SQLite 历史记录与任务详情；
- Markdown / TXT 输出选择；
- 5 个可编辑 Prompt 预设；
- Light / Dark / System 三种主题；
- 历史任务按真实视频标题展示，并显示视频缩略图；
- `视频标题__000012` 统一命名 MD/TXT、MP4、JPG 与 ZIP；
- “下载全部”ZIP + MD/TXT/MP4 单独下载与安全删除；
- 原视频号链接直接打开 + “微信打开”复制并尝试唤起桌面微信；
- 反向代理真实域名、HTTPS 与 path prefix 支持；
- 非 JSON / HTML 502 代理错误的友好提示。

更多界面截图：

- [暗色工作台](../docs/images/webui-dark.png)
- [明亮工作台](../docs/images/webui-light.png)
- [历史记录](../docs/images/webui-history.png)
- [登录页](../docs/images/webui-login.png)

完整项目说明请同时阅读根目录 [`README.md`](../README.md)。

---

## 1. 最短部署流程

```bash
git clone https://github.com/topgptus/WechatVideoDL.git
cd WechatVideoDL/server_docker

./scripts/install-docker-if-missing.sh
mkdir -p data/downloads
cp .env.example .env
```

把元宝登录态放入：

```text
data/yuanbao.tencent.com_cookies.txt
data/yuanbao_session.json
```

然后：

```bash
./scripts/deploy.sh --dry-run
./scripts/deploy.sh
```

访问：

```text
http://SERVER_IP:18770/
```

---

## 多用户登录与管理员

WebUI 支持管理员和普通用户，历史任务与下载文件按提交者隔离。首次管理员沿用现有 .env 中配置的账号和密码，只在第一次启动时写入私有 auth.db；后续改密与用户管理在网页操作，管理员可以创建、禁用、重置其他用户密码和调整权限。详情见 [多用户管理与安全说明](../docs/MULTI-USER-SECURITY.md)。

## 2. Cookie 与 Session

### Cookie

Chrome 安装 **Get cookies.txt LOCALLY**：

```text
https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
```

![Cookie extension](../docs/images/cookie-extension-store.png)

登录：

```text
https://yuanbao.tencent.com/
```

导出当前 Yuanbao 站点 Netscape cookies.txt，重命名：

```text
yuanbao.tencent.com_cookies.txt
```

放到：

```text
server_docker/data/yuanbao.tencent.com_cookies.txt
```

如果已经在本目录，则：

```text
data/yuanbao.tencent.com_cookies.txt
```

权限：

```bash
chmod 600 data/yuanbao.tencent.com_cookies.txt
```

### Session

元宝登录态还可能依赖 localStorage/sessionStorage，因此推荐同时创建：

```text
data/yuanbao_session.json
```

在已登录 Yuanbao 页面 DevTools Console 执行：

```javascript
(() => {
  const dump = (s) => Object.fromEntries(
    Array.from({ length: s.length }, (_, i) => {
      const key = s.key(i);
      return [key, s.getItem(key)];
    })
  );
  const state = {
    ua: navigator.userAgent,
    platform: navigator.platform,
    language: navigator.language,
    cookie: document.cookie,
    localStorage: dump(localStorage),
    sessionStorage: dump(sessionStorage),
  };
  copy(JSON.stringify(state, null, 2));
})();
```

把剪贴板 JSON 保存到：

```text
data/yuanbao_session.json
```

权限：

```bash
chmod 600 data/yuanbao_session.json
```

Cookie / Session 等同登录凭据，不要提交 GitHub。

---

## 3. `.env`

```bash
cp .env.example .env
openssl rand -hex 24
openssl rand -hex 32
```

示例：

```dotenv
APP_PORT=18770
WEBUI_USERNAME=admin
WEBUI_PASSWORD=change-me
SECRET_KEY=replace-with-openssl-rand-hex-32
BROWSERLESS_TOKEN=replace-with-openssl-rand-hex-24

# 反代自动识别域名时留空
PUBLIC_BASE_URL=
TRUST_PROXY_HEADERS=false
PROXY_HOPS=1

MAX_PARSE_ATTEMPTS=3
MAX_PENDING_JOBS=8
POLL_INTERVAL_SECONDS=3
```

### 环境变量

| 变量 | 默认 | 用途 |
|---|---:|---|
| `APP_PORT` | `18770` | Web/API 端口 |
| `WEBUI_USERNAME` | `admin` | WebUI / API 用户名 |
| `WEBUI_PASSWORD` | - | 密码 |
| `SECRET_KEY` | - | Flask session |
| `BROWSERLESS_TOKEN` | - | Browserless token |
| `PUBLIC_BASE_URL` | 空 | 强制公共地址；留空时可跟随可信反代 |
| `TRUST_PROXY_HEADERS` | `false` | 是否信任 `X-Forwarded-*` |
| `PROXY_HOPS` | `1` | 可信 proxy 层数 |
| `MAX_PARSE_ATTEMPTS` | `3` | 最多总尝试次数 |
| `MAX_PENDING_JOBS` | `8` | 同时运行与排队任务的总上限（单 Browserless worker） |
| `POLL_INTERVAL_SECONDS` | `3` | 服务端建议的异步任务轮询间隔；返回到 JSON 与 `Retry-After`，限制 1～30 秒 |
| `BROWSERLESS_URL` | 自动 | 外部 Browserless，可选 |
| `BROWSERLESS_IMAGE` | 固定默认 | Browserless 镜像覆盖 |
| `BROWSER_TIMEOUT` | `300` | 元宝浏览器自动化最长等待（秒）；应大于视频卡片等待 110 秒及后续解析时间 |

---

### 元宝视频卡片首次响应慢

元宝视频号卡片可能需要较长时间才能生成。Browserless 函数现在最多等待约
**110 秒**（此前 65 秒），配合应用端 300 秒与 Browserless 360 秒的总超时设置。
只有超时后才会按原重试规则进入下一次尝试（默认总共最多 3 次）。

当视频卡片仍未出现时，后台历史任务会明确记录
`YUANBAO_CARD_TIMEOUT`，而非笼统的 HTTP 502。
浏览器错误只记录非敏感状态标记和错误代码，不记录网页正文。

安全提醒：旧版 Browserless 的默认调试日志会打印 `/function` 请求上下文，
可能包含 Cookie/Session。配套 Compose 将 `DEBUG=-*`，关闭该类详细日志。
此选项不会阻止 Docker 本机持有人读取已有旧日志；请妥善保护本机账户权限。

## 4. Browserless

### 已有 Browserless

部署脚本会检测正在运行的 `browserless/chrome`。兼容时直接复用，不会重启它。

### 已有但不兼容

例如 `CONNECTION_TIMEOUT` 过短，部署脚本不修改现有 Browserless，而启动项目专属容器。

### 完全没有 Browserless

使用：

```text
docker-compose.browserless.yml
```

自动创建：

```text
wx-video-download-browserless-1
```

其 `3000/tcp` 只在 Docker 内部网络暴露，不映射宿主端口。

因此新 VPS 不需要先手工安装 Browserless。

---

## 5. 安全部署

先：

```bash
./scripts/deploy.sh --dry-run
```

确认：

- Docker daemon 正确；
- DockerRootDir 正确；
- Compose 正确；
- APP_PORT 无冲突；
- Browserless 检测符合预期。

再：

```bash
./scripts/deploy.sh
```

脚本不会运行：

```text
docker system prune
docker compose down -v
```

部署只操作 `wx-video-download` Compose project，并核对部署前已有容器仍在运行。

### Snap Docker

若 Snap Docker 不允许 `/opt` bind mount，请把项目放在：

```text
/root/WechatVideoDL
```

或 `/home/...`。

---

## 6. 数据目录

```text
data/
├── yuanbao.tencent.com_cookies.txt
├── yuanbao_session.json
├── history.db
├── sequence.txt
└── downloads/
    ├── 视频标题__000001.md
    ├── 视频标题__000001.mp4
    ├── 视频标题__000001.jpg
    ├── 视频标题__000001.zip
    ├── wxv_000007.md        # 旧版本文件继续兼容
    └── ...
```

`history.db` 是 SQLite 单账号历史库。升级时会自动幂等新增 `video_title`、`cover_url`、`thumbnail_filename`、`bundle_filename`，旧数据不会被重建或批量改名。

容器重建不会删除 bind-mounted `data/`。

---

## 7. WebUI 新功能

![Light UI](../docs/images/webui-light.png)

![History](../docs/images/webui-history.png)

WebUI 支持：

- Light / Dark / System
- 5 个 Prompt 预设
- Prompt 选择后继续编辑
- Markdown / TXT
- 可选 MP4
- 最多 3 次自动重试
- 可读错误，不再把 HTML 错误页当 JSON
- 历史记录
- 历史详情
- 视频标题 + 16:9 缩略图
- 按标题 / ID / 链接 / Prompt 搜索
- `下载全部` ZIP，以及 MP4 / TXT / MD 单独下载
- 点击原视频号链接，或“微信打开”复制链接并尝试唤起桌面微信
- 删除历史及其 MD/TXT/MP4/JPG/ZIP 关联文件

WebUI 默认 `md`；API 没传 `output_format` 时为兼容旧客户端默认 `txt`。

缩略图采用“**封面优先，首帧 fallback**”：先获取 Browserless 返回的 `coverUrl`，失败且存在 MP4 时使用 ffmpeg 抽取首帧。缩略图失败本身不会让主解析任务失败。

---

## 8. API

### Parse

```bash
curl -u 'USERNAME:PASSWORD' \
  -X POST 'http://SERVER_IP:18770/api/parse' \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://weixin.qq.com/sph/AUjYMH2y1I",
    "preset_id": "knowledge_tags",
    "prompt": "提取相关 tag 作为知识库导入",
    "output_format": "md",
    "download": true
  }'
```

响应包含：

```text
attempts_used
max_attempts
retry_errors
title
source_url
text.download_path
text.download_url
video.download_path
video.download_url
thumbnail.download_path
thumbnail.download_url
bundle.download_path
bundle.download_url
```

### Presets

```bash
curl -u 'USERNAME:PASSWORD' http://SERVER_IP:18770/api/presets
```

### 长视频：元宝优先，本地免费转写兜底（可选）

遇到腾讯元宝明确回复“抱歉，未找到相关视频”等拒绝内容时，不能将非空回复误判为分析成功。
新任务保持现有 HTTP 202 异步接口：先由元宝获取视频卡片和真实播放地址，元宝能分析成功就直接保存。
只有元宝分析无效，且启用了本地转写组件时，才进入以下流程：

1. 优先复用同一视频号分享链接已经下载的本地 MP4，避免重新下载；
   没有本地文件时才按原视频地址下载。用户关闭 MP4 保存时，文件只临时使用，处理完成后删除。
2. Mac 宿主机通过**私有共享目录任务队列**执行免费 Whisper 中文转写；不对外监听端口，不授权 Docker Socket。
3. 将中文逐字稿按原 Prompt 交给元宝执行文本分析。长文本会分段处理再汇总。
4. 分别保留完整转写原文（TXT）与 AI 分析（MD/TXT）；**AI 分析不是逐字转写**。
   历史页与任务结果中的“本地转写耗时”来自宿主机工作进程的真实计时，
   **包括音轨提取、本地模型识别和逐字稿写入，不包括视频下载或元宝二次分析**。
   未使用本地转写的任务不显示该耗时，旧历史如果没有可信记录则保持空白。
   历史列表的“口播原文”按钮直接下载未经过元宝精修的本地识别 TXT；
   元宝根据 Prompt 生成的 MD/TXT 会重新提炼观点和组织结构，不能理解为只去掉口头禅的精修稿。
5. 若下载成功但 AI 分析未完成，前端显示“部分完成”；保留成功的 MP4 或逐字稿，
   不会用“未找到视频”冒充正常分析结果。

此功能默认**关闭**，仅部署了宿主机服务并完成验证的 Mac 可设置
`LOCAL_TRANSCRIPTION_ENABLED=true`。示例安装步骤（只面向 macOS）：

```bash
# 在 Mac 宿主机，先安装免费 openai-whisper、FFmpeg，并缓存 base 模型；
# Whisper Python 应使用实际安装该库的 Python 可执行文件。
python3 scripts/setup_host_transcriber.py   --data-dir ./data --python /path/to/whisper-python
python3 scripts/setup_host_transcriber.py   --data-dir ./data --python /path/to/whisper-python --install
# 确认 data/transcription_queue/worker.status.json 有最新心跳后，
# 编辑私有 .env 设 LOCAL_TRANSCRIPTION_ENABLED=true，再用 deploy.sh 部署。
```

恢复/卸载（不删除已有 MP4、Markdown 或逐字稿）：

```bash
python3 scripts/setup_host_transcriber.py   --data-dir ./data --python /path/to/whisper-python --uninstall
# 同时把 .env 的 LOCAL_TRANSCRIPTION_ENABLED 改回 false。
```

**运行限制**：当前本地 worker 默认 `base` 模型，依赖 Mac Metal/MPS 及本地可用模型；
宿主机转写长视频可能耗时数分钟到几十分钟，且模型转写可能出现错字，须核对关键原话。
AI 分析步骤仍会把**转写文本发送给腾讯元宝**，因此不是全程离线。

**SenseVoice INT8 中文转写（可选、默认不开启）**：可以在本地环境选择 SenseVoice INT8 与 Whisper `base` 对照，
根据实际语音和硬件评估准确率、标点与速度。
此路线用 sherpa-onnx 的**隔离 Python 环境和预先缓存的量化模型**运行，
不下载模型、不开放网络端口，无需额外的设备管理连接器。
分段采用约 20 秒窗口、1 秒重叠，保存带时间戳的未经 AI 改写的原始文本。
质量状态为未人工核对（`unverified`），不能保证全量转写完全正确。

生产环境首次启用时应先验证宿主机中的隔离依赖和模型，再安装
`--model sensevoice` 的独立 LaunchAgent，并把私有 `.env` 中
`LOCAL_TRANSCRIPTION_MODEL` 改为 `sensevoice`。在这之前默认仍为 `base`。
安装示例（以下路径全部是占位符，不是真实设备路径）：

```bash
python3 scripts/setup_host_transcriber.py \
  --data-dir ./data --python /path/to/whisper-python \
  --model sensevoice \
  --sensevoice-python /private/sensevoice-venv/bin/python \
  --sensevoice-model-dir /private/sensevoice-model \
  --fallback-base
# 上一条无 --install 为预演；检查通过后在原命令末尾加 --install。
# 必须先确认宿主 worker 心跳、模型可用和真实样例输出，再切换 App 的 .env。
```

如 SenseVoice 异常且显式设置了 `--fallback-base`，宿主工作进程会退回
旧 Whisper `base` 转写；不会自动修改 NPM、其他 Docker 容器或用户历史记录。
任务执行的临时 FLAC 音频和中间识别片段位于受限临时目录，成功或失败后清理。
**用户上传的视频在本地被转写，而转写后的文字仍按用户原有 Prompt 交给元宝分析。**

生产任务进程或 Mac 重启时，和现有异步队列一样不承诺自动续跑；已存储的独立成果不会被故意删除。
宿主机 worker 私有目录文件权限为 0600/0700，日志不会记录完整转写文本或登录 Cookie。

### 异步提交与进度查询（HTTP 202）

`POST /api/parse` 在通过输入验证并成功入队后**立即返回 HTTP 202**，包含
`id`、`poll_url`、`poll_interval_seconds` 和初始的 `queued` / `running` 状态，不再等待元宝解析或 MP4 下载。
响应同时带 `Location` 与 `Retry-After`；客户端应优先按照服务端返回的轮询间隔调用
`GET /api/history/<id>`，不要把 3 秒写死在自己的实现里。状态依次为
`queued`、`running`、`completed` 或 `failed`。完成后，`job.text.download_path`、
`job.video.download_path`、缩略图和 bundle 路径提供实际成果。

队列满时返回 JSON `429 QUEUE_FULL`，并同样带 `Retry-After`，客户端可据此延后重试。
单进程 Gunicorn（Dockerfile 默认 `-w 1`）使用一条后台线程串行执行，最多允许
`MAX_PENDING_JOBS` 个正在运行或排队的任务。运行中的任务不可删除（`409 JOB_ACTIVE`）。
浏览器断线不会中止后台任务，但**容器重启或进程退出不会自动恢复进行中的任务**：启动时这些任务会标记
`INTERRUPTED`，用户可在历史记录核实后重新提交。为避免任务丢失，不要在运行任务时部署升级；
需要跨容器重启可靠恢复的场景应另用持久化工作队列。

### History

```bash
curl -u 'USERNAME:PASSWORD' http://SERVER_IP:18770/api/history
curl -u 'USERNAME:PASSWORD' http://SERVER_IP:18770/api/history/wxv_000001
```

### 下载全部 ZIP

```bash
curl -u 'USERNAME:PASSWORD' \
  -OJ \
  http://SERVER_IP:18770/api/history/wxv_000001/bundle
```

ZIP 会使用 `视频标题__000001.zip`，内部文件也统一为同一个标题 stem。首次请求按需生成；组成附件发生变化时会自动重建缓存。

### Delete

```bash
curl -u 'USERNAME:PASSWORD' \
  -X DELETE \
  http://SERVER_IP:18770/api/history/wxv_000001
```

---

## 9. Retry

默认：

```dotenv
MAX_PARSE_ATTEMPTS=3
```

自动重试 Browserless 临时失败、页面超时、临时无法拿到 preview/video URL、部分 5xx / gateway、yt-dlp 临时网络错误。

URL 错误、鉴权错误、Cookie/Session 明确失效、无效格式不做无意义重试。

同一用户请求始终只有一个 history row；重试前会清理该任务产生的 partial artifacts。

---

## 10. 反代

### 推荐：自动跟随真实域名

`.env`：

```dotenv
PUBLIC_BASE_URL=
TRUST_PROXY_HEADERS=true
PROXY_HOPS=1
```

Nginx：

```nginx
location / {
    proxy_pass http://127.0.0.1:18770;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Caddy：

```caddyfile
video.example.com {
    reverse_proxy 127.0.0.1:18770
}
```

### 强制域名

```dotenv
PUBLIC_BASE_URL=https://video.example.com
```

设置后优先级最高。

API 同时返回相对 `download_path`，WebUI优先相对路径，因此反代域名和 path prefix 更稳定。

---

## 11. 健康检查与日志

```bash
curl http://127.0.0.1:18770/health
```

日志：

```bash
docker logs -f wx-video-download-app-1
docker logs -f wx-video-download-browserless-1
```

容器：

```bash
docker compose -p wx-video-download \
  -f docker-compose.yml \
  -f docker-compose.browserless.yml ps
```

---

## 12. 更新

```bash
git pull
./scripts/deploy.sh --dry-run
./scripts/deploy.sh
```

建议先：

```bash
tar -czf wxvideodl-data-backup.tgz data/
```

---

## 13. 测试

在安装 Python requirements 的环境中：

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

从仓库根目录执行 shell 测试：

```bash
for t in server_docker/tests/test_*.sh; do
  bash "$t"
done
```

测试覆盖 retry、history、delete、TXT/MD、proxy URL、安全路径、UI contract 和 Docker 部署保护。

---

## 14. 安全

不要提交：

```text
.env
data/
*.db
yuanbao.tencent.com_cookies.txt
yuanbao_session.json
downloads/
```

公网部署请使用 HTTPS 和强密码，并考虑限制来源 IP、VPN、Tailscale / ZeroTier 等。
