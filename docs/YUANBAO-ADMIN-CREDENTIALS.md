# 元宝统一登录配置（管理员网页导入）

## 产品约定

WeChatVideoDL 的用户隔离仍以 WebUI 用户 ID 为准：个人视频任务与成果不会混在一起。
仅**登录元宝网页的 Cookie + Session** 为全站共享资源，由所有管理员在 **设置 → 元宝连接** 维护。
普通用户不可查看或上传这两个私密文件，但有权限使用后台配置创建和执行解析任务。
当前不提供独立用户的元宝账号/登录态；如使用量增长，可另外设计 per-user 凭据隔离。

## 管理员步骤

1. 登录站点，进入「设置 → 元宝连接」。
2. 在「Cookie 文件」选择 Netscape 文本格式的 `cookies.txt` 文件，点击「导入／更新 Cookie」。最大 512 KB。元宝需要已有、未过期的 `yuanbao.tencent.com` 或上层腾讯域名 Cookie。导入提示数量仅是**语法与到期日期检查**，并不保证登录仍然有效。
3. 可单独在「Session 状态」导入 `JSON` 文件，最多 1 MB。支持的键为 `ua`、`platform`、`language`、`cookie`、`localStorage`、`sessionStorage`。不需要 Session 时允许不导入，是否能正确登录须以实际诊断为准。
4. 点击「检查元宝连接」做真实网页端只读登录与输入框诊断。诊断只返回布尔状态，不回传 Cookie/Session 或聊天正文。

## Session 结构示例（不要填入真实私密值再公开）

```json
{
  "ua": "browser-user-agent",
  "localStorage": {"sample-name": "sample-value"},
  "sessionStorage": {"sample-name": "sample-value"},
  "cookie": "sample-cookie=sample-value"
}
```

仅结构示意；实际可用字段取决于你所使用的元宝登录导出方式。`cookie` 字段是选填项；若仅有 Cookie 文件，可以不提供 Session。

## 安全与兼容性

- `Cookie` 和 `Session` 继续使用已配置的 `/data/...` 私有文件路径；原来的文件无需重新导入，新版本读取仍兼容。文件模式 `0600`，通过原子替换更新，导入失败不会覆盖旧文件。
- 不提供凭据下载、查看明文或应用层日志输出。网页只显示是否存在并通过结构校验、有效 Cookie 数量及诊断状态。
- 只允许后台管理员导入。接口采用登录验证 + CSRF 保护；导入前若任务正在解析，返回提醒，不会干扰当前任务。
- 备份原数据目录时须将这些敏感文件排除在公开归档之外。个人用户历史数据授权与超级管理员保护不因共享元宝登录而改变。
- 不自动复制正式站点 Cookie/Session 到 staging。测试环境需管理员主动从自己的设备导入授权登录状态。
