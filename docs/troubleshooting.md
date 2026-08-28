# 排障指南

## 找不到 `copala`

确认虚拟环境已激活并执行 `python -m pip install -e .`。也可以用 `python -m copala --help` 验证模块入口。

## Provider 提示缺少 API Key

运行 `copala status`，检查所选模型前缀、`agents.defaults.provider` 和对应 `providers.*.apiKey` 是否一致。不要把密钥粘贴到 Issue 或日志。

## 飞书 OAuth 回调连接失败

运行 `copala calendar feishu-auth-login` 后再打开命令输出的授权链接。确保回调端口未被占用，开放平台登记的回调 URI 与配置完全一致。若 URL 已包含 `code`，可使用 `copala calendar feishu-auth-code CODE`。

## CLI 能读取飞书，Gateway 不能

两个进程可能使用了不同的 `--config`、`--workspace` 或旧进程配置。运行 `copala calendar status`，确认 Token 文件与工作区，然后重启 Gateway。

## 日历为空

扩大查询范围并明确来源：

```bash
copala calendar list --source feishu --days 30
copala calendar calendars --platform feishu
```

个人私人日历通常需要 `authMode: "user"`；`tenant` Token 只能访问应用有权限的日历。

## 计划缺少实时路线或天气

检查 `smartReminders.amap.enabled`、高德 Key 和网络连接。无凭证或外部调用失败时，Copala 会保留其他信息并显示降级结果，这是预期行为。

## Matrix 图片或富文本异常

安全净化只允许 Matrix 的 `mxc://` 图片源；HTTP 图片、事件属性和脚本内容会被移除。确认安装了 `.[matrix]` 依赖。

## 测试导入旧源码

迁移到 `src/` 后重新执行可编辑安装：

```bash
python -m pip install -e ".[dev]"
python -c "import copala; print(copala.__file__)"
```

路径应指向当前仓库的 `src/copala`。
