# WechatVideoDL — Apple-Design UI 规范与验收

日期：2026-10-16；范围：工作台、历史列表、详情面板、登录页；技术：Flask/Jinja + 原生 JavaScript/CSS WebUI。

## 设计目标

围绕「从视频到可追溯知识」的产品任务，保留绿色/青色品牌、创建任务→查看成果的熟悉结构，以信息层次和触控便利性优先，不追加复杂特效或变更业务逻辑。Apple HIG 用作 WebUI 设计原则，不把网页冒充原生 iOS App。

## 已核对 HIG 与主要问题

- Critical（文字可读性）：浅色原有绿色 #2DD783 对白 1.88:1、蓝色 #438CFF 对白 3.26:1、红色 #EF5B63 对白 3.31:1；低于普通文字 4.5:1 推荐门槛。参考 accessibility.md › Vision 与 color.md › Best practices。
- High（移动触控）：原历史按钮没有 44px 高的稳定触控区域，6 项操作随窄屏挤压错行。参考 buttons.md › Best practices。
- High（适配）：历史卡片宽屏右侧操作栏最多 230px，界面常出现不规则换行；手机头部导航两行且缺少底部 safe-area。参考 layout.md › Adaptability、designing-for-ios.md › Best practices。
- Medium（无障碍与状态）：缺少统一的可见键盘焦点、reduce motion/reduce transparency 处理，长标题、逐字稿以及手机键盘遮挡存在风险。参考 accessibility.md、dark-mode.md、entering-data.md。
- Craft：继续使用青绿色主操作、克制的功能层模糊，不在视频和内容卡片上滥用 Liquid Glass。参考 liquid-glass.md › The two layers。

## 语义颜色与对比

| 用途 | 浅色文字 | 背景 | 实测对比 |
|---|---|---|---|
| 主操作及成功状态的文字 | #13734B | #FFFFFF | 5.86:1 |
| 链接 | #235DBE | #FFFFFF | 6.21:1 |
| 次级文字 | #50645B | #F0F4F2 | 5.71:1 |
| 危险操作文字 | #AD2839 | #FFFFFF | 6.69:1 |

深色模式使用语义变体：链接 #91BAFE 在 #111815 上约 9.15:1；次要文字 #B5C7BC 在 #161F1B 上约 9.52:1。明亮的品牌背景仍用于主按钮，但不再直接充当白底小字号文本。

## 历史操作映射（从左到右，从上到下）

ALL → AI解析 → 旁白 → MP4 → 详情 → 删除。

- ALL：打包下载全部成果；AI解析：元宝生成的 MD 或 TXT；旁白：原始机器识别逐字稿 TXT（未经元宝润色）；MP4：真实视频文件。
- 详情：按原接口展开 AI 内容，支持 aria-expanded；删除：保留二次确认与运行任务禁用。
- 无文件时仅隐藏对应下载项，其他动作依次前移，不制造失效链接。工作台下载区同步统一为 ALL、AI解析、旁白、MP4。
- 对纯文本任务保留此前用户提出的 No VIDEO 占位文案。

## 响应式、触控、键盘与语义

- ≥901px：工作台双栏；历史图片与文字分区，操作同一栏从左向右排列。
- 641–900px：工作台改纵向；历史操作可折行，但 DOM 顺序稳定。
- ≤640px：顶部是品牌、外观与退出，底部固定工作台/历史记录两项主导航；安全区使用 env(safe-area-inset-bottom)。历史操作最多 3 列、按视觉次序折为两行，触控项不小于 44px。
- 320px 小屏有更窄媒体卡布局，表单输入在手机至少 16px，避免 iOS 输入聚焦时自动放大；手机表单聚焦时底部导航隐藏，避免覆盖键盘。
- 增加可见键盘焦点、导航 aria-current、详情 aria-expanded/aria-controls、搜索 label，以及 reduced motion、reduced transparency、increased contrast 规则。
- URL、标题、长文字保持可换行，不应出现横向滚动。

## 测试与已知边界

在隔离的 Chromium/Browserless 镜像中，完全以合成测试数据渲染，不访问用户账号或真实历史：

- 历史页面：360、390、430、768、1280 CSS px，包含明暗两种配色。
- 工作台：390、430、768、1280；登录：360、390、768、1280。合计 14 个实际浏览器视窗检查。
- 14 组均未出现文档横向溢出。完整 6 项历史操作顺序正确；手机按钮实测 44px 高。
- 单元测试及独立 Chromium 交互测试均需随代码持续运行；上述是浏览器模拟视窗测试，不声称已在用户真实 iPhone 的 Safari 中验收。

## 开发边界

没有修改元宝 Prompt、下载/转写逻辑、后端历史记录、真实下载文件、登录机制、NPM、Cloudflare 或其他 Docker 项目。Apple-Design Skill 来自 AgentDock-Mini 已安装的 managed apple-design，先阅读指导文件与 12 项针对性 HIG 参考后执行审查。
