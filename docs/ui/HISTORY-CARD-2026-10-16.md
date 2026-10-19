# 历史卡片精简优化：标题、元信息与来源入口

日期：2026-10-16。基于 AgentDock-Mini managed Apple-Design Skill，针对用户提供的桌面截图，复查了 accessibility.md、layout.md、typography.md、color.md、designing-for-ios.md、designing-for-macos.md、lists-and-tables.md、buttons.md、writing.md、feedback.md、dark-mode.md 和跨平台设计说明。

## 设计准则与具体决定

1. 标题：保留数据库与下载文件中的原始视频标题；仅在历史卡片前端展示第一个完整且长度足够的意义短句，否则截取最多 36 个 Unicode 字符并加省略号。桌面单行、手机至多两行，完整标题通过 title、ARIA 和详情可查。此方案不调用 AI 重新概括，不新增存储字段。依据 lists-and-tables.md › Content（列表文字简短易扫读）与 layout.md › Visual hierarchy（重要信息首先呈现）。
2. 元信息：状态、北京时间创建时间、本地转写耗时、完整任务 ID 的顺序固定。任务 ID 变为更低视觉权重的等宽小字，鼠标悬停或读屏可识别其含义。依据 typography.md › Conveying hierarchy；accessibility.md › Vision。
3. 原视频来源与 Prompt：默认卡片不再常驻显示；点击已有“详情”后展示完整标题、实际输入链接、浏览器打开、复制链接、完整 Prompt 和已保存的 AI 内容，避免卡片过高。依据 layout.md › Visual hierarchy（渐进披露）与 writing.md › Best practices。
4. 旧“微信打开”原理：先尝试 navigator.clipboard.writeText(真实输入 URL)，再导航至没有目标视频参数的通用 weixin:// 协议。macOS 浏览器往往无法直接交给微信，且并不能直接定位特定视频。现替换为“复制链接”（成功反馈“已复制”，无剪贴板权限时使用可手动复制的提示框）；另保留“浏览器打开”真实 HTTPS 视频号链接。这一修复同时覆盖工作台结果和历史详情。
5. 原有六项下载/操作 ALL、AI解析、旁白、MP4、详情、删除及 API/删除确认逻辑保持不变。搜索继续使用完整标题、原 Prompt 和 URL。

## UI 验收

仅使用隔离 Chromium + 完全合成任务数据（其中标题来自用户截图），无真实微信会话、账号或视频内容。实际浏览器覆盖 320/375/390/430/768/1280 CSS px 与浅/深色组合。原长标题被缩为“上半年结婚对数只有327万对…”。单任务折叠卡片桌面（1280px）约 148px，390px 手机约 213px；页面无横向滚动。详情完整保留原标题与 Prompt；复制链接可准确复制原 HTTPS URL；javascript: 来源不会生成可点击链接。

## 边界与后续

本轮仅修改历史卡片前端结构、通用来源链接按钮及对应样式/测试。不改变视频下载、元宝/SenseVoice 转写、Prompt、文件、历史数据库与认证系统。Apple HIG 指引用于 WebUI 的设计原则，不声称网页是原生 macOS/iOS 客户端。iPhone Safari 的真实设备触控表现仍需用户现场确认。
