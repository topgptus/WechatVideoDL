# PR #12 上游集成与验证说明

本分支直接从原作者仓库已合并 #4～#11 的最新 main 创建，不直接合并旧 #12 的重复历史。
保留上游的全部既有测试与文档，按功能移植剩余端到端逻辑：

- 多链接批量采集、持久队列、与单任务统一的模式和提示词开关、暂停/恢复/重试。
- 管理员网页导入共享元宝 Cookie/Session，并保持分用户任务与成果权限隔离。
- 工作台和历史的任务模式、真实进度、授权播放代理、批量历史操作之间的接口接线。
- 本地 SenseVoice 长视频续跑与识别统计；按当前批次权限一键导出全部现有成果 ZIP。

## 安全原则

- 保留原作者通用 Docker Compose，不包含个人域名、NPM/Cloudflare 网络、生产服务端口特例。
- 不提交私有 .env、数据目录、数据库、Cookie、Session、Token 或媒体成果。
- 默认不启用 SenseVoice；管理员凭据导入经登录、CSRF、文件校验和解析期间互斥锁。
- ZIP 只取拥有者可见批次、限制路径及扩展名、拒绝符号链接与目录穿越，并控制文件总量、磁盘空余量。
- 兼容旧 API：未指定格式默认 TXT，新页面显式采用 Markdown；保留轮询间隔及 Retry-After。

## 验证

- 全部上游测试与本轮新增测试都在无网络、只读代码挂载的 Docker Python 容器中运行。
- 运行 JavaScript/Python 语法检查、Docker Compose 配置验证和独立构建检查。
- 从最新 main 对照改动文件清单与测试结果，防止旧 PR 删除上游已合并的内容。
- 不部署至用户的正式或测试服务器；上游合并单独确认。


## 本轮 SenseVoice 可选安装增量

- 新增可选 `docker-compose.sensevoice.yml` 和 `Dockerfile.sensevoice`，支持 Linux Docker 离线 worker。
- `scripts/install_sensevoice_model.py` 以官方固定 Release URL 下载并校验 archive，无执行远端脚本；模型不进入 Git。
- `scripts/setup_sensevoice_docker.sh` 为宿主管理员按需执行的安装引导，默认不更改 app，`--enable-app` 在 worker 就绪后才修改私有配置。
- 管理员设置页面新增「语音识别」只读就绪检查，检查实际心跳，不展示服务器路径、账户或 Session。
- 不给 WebUI 赋予 Docker Socket、宿主机命令执行、模型下载或额外网络服务权限；Mac 现有 worker 保留。

## GitHub 自动回归检查

为减少人工单点验证，新增只读 GitHub Actions 工作流。
在 Pull Request 和 main 推送时运行 Python 测试、语法和双 Compose 配置检查。
使用 pull_request（不是 pull_request_target）与 contents: read，不读取真实凭据或生产数据。
模型文件和模型下载均不是自动测试的前置条件。
