# SenseVoice 可选安装与运行

SenseVoice 已集成到 WechatVideoDL 的任务模式和队列中，但**正常安装默认不运行本地语音识别**：

```dotenv
LOCAL_TRANSCRIPTION_ENABLED=false
LOCAL_TRANSCRIPTION_MODEL=sensevoice
```

不使用语音转写的用户无需安装模型，视频下载、AI 解析等原有功能保持可用。

## 推荐：Linux / Docker 一条命令安装

在服务器上确认 Docker Compose V2、Python 3、HTTPS 网络和空余资源已就绪；
在配置好私有 `.env` 的 `server_docker` 目录执行：

```bash
./scripts/setup_sensevoice_docker.sh --enable-app
```

脚本按顺序完成：

1. 核查 Compose 配置、私有 `.env`、现有 worker 是否占用同一队列；如已有其他 worker 在线，则中止，避免重复消费任务。
2. **按需**从 [sherpa-onnx 官方 GitHub Release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) 获取 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`。
3. 仅提取官方包中的 `model.int8.onnx`、`tokens.txt`、`LICENSE` 和可选 `README.md`，对成员名称、大小、数量和符号链接做检查；临时下载完成才原子安装到 `server_docker/models/sensevoice/`，默认没有模型缓存时需要联网。
4. 构建**可选**的 SenseVoice Docker 镜像，安装固定版本的 sherpa-onnx 和 NumPy；启动不联网的 worker。worker 只挂载视频目录、转写队列和只读模型，不挂载 Cookie、Session、数据库或 Docker socket。
5. 等待健康心跳验证通过。只有传入 `--enable-app` 才会私密备份 `.env`、开启 `LOCAL_TRANSCRIPTION_ENABLED=true` 并更新主应用容器。

第一次只想下载并测试 worker、**不改变主程序**：

```bash
./scripts/setup_sensevoice_docker.sh
```

模型下载需要数百 MB；首次 worker 镜像构建需要额外时间、磁盘和内存。源站不可达时可以在可信设备下载官方 archive，再离线安装：

```bash
python3 scripts/install_sensevoice_model.py --archive /path/to/official-model.tar.bz2 --target models/sensevoice
```

强烈建议传入事先从可信渠道核对的 `--sha256 <64位SHA256>`，尤其在跨机器传输模型包时。
脚本不在下载过程中动态运行远端脚本，不自动执行 tar 包内文件。模型版权和许可由上游包内 LICENSE 说明。

### Compose 与权限隔离

可选覆盖配置：`docker-compose.sensevoice.yml`，启用 `sensevoice` profile。
worker `network_mode: none`、只读根文件系统、丢弃全部 Linux capabilities、禁止提权，
仅有 `/data/downloads` 与 `/data/transcription_queue` 两个必要读写挂载，以及只读 `/models/sensevoice`。
主程序的原 Compose **完全不需要**新增公网服务或 Docker 管理权限。
Worker 的容器内 root 仅用于访问与现有 app 容器共享的私有文件；通过挂载边界阻止它读取 `/data` 中的账户和元宝凭据。

已完成启动的 worker 通过本地共享队列 `worker.status.json` 心跳宣告就绪；
设置 → 语音识别提供管理员可见的状态刷新，普通用户不能调用该诊断接口。
如果状态显示未就绪：先检查模型目录、容器日志和 Compose，**不要**把 `true` 当作安装成功。

```bash
docker compose -f docker-compose.yml -f docker-compose.sensevoice.yml --profile sensevoice logs --tail=100 sensevoice
```

如果原本已通过 macOS `launchd` 运行 SenseVoice，请继续使用原有的
`scripts/setup_host_transcriber.py` 配置，不要在同一 `data/transcription_queue` 上再启动 Docker worker。
同一份共享队列只能有一个 active worker。

## 关闭、升级与回滚

- 关闭转写：将私有 `.env` 中 `LOCAL_TRANSCRIPTION_ENABLED=false`，然后仅更新主应用容器；普通视频下载继续可用。
- 停止可选 worker：使用上述两个 Compose 文件运行 `stop sensevoice`，不会停止普通 app/browserless。
- 删除可选镜像与模型请由服务器管理员单独决定；不得使用 `docker compose down -v` 清理用户任务数据。
- 安装脚本将 `.env` 备份保存在 Git 忽略的私有 `data/.maintenance/` 下，保留 0600 权限；绝不上传 GitHub。

## 测试范围

CI/单元测试模拟模型包、权限及心跳，不能代替在特定 CPU 架构上使用真实模型的语音识别验收。
建议首次启动时用一条获得授权的短视频测试转写、逐字稿下载和任务历史归属。

官方模型说明：[sherpa-onnx 预训练 SenseVoice](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)。
