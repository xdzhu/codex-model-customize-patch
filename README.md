# Codex 自定义模型菜单补丁

[English](README.en.md) · [安装和验证记录](docs/INSTALLATION.md) · [第三方模型输出额度](docs/OUTPUT_BUDGET.md) · [架构](docs/ARCHITECTURE.md) · [许可](LICENSE)

这是 [limyum-creator/codex-deepseek-patch](https://github.com/limyum-creator/codex-deepseek-patch) 扩展版的派生项目。它将兼容 API 的第三方模型加入 Codex 桌面端右下角模型菜单，同时保留官方 GPT 模型及其原有登录。模型 slug 保持服务端名称，不靠模型目录自动切换 Provider；构建时会明确写入每个模型到对应 Provider 的路由。

本项目使用本地原生适配器及合并后的模型目录。它不是 Codex 官方提供的第三方模型功能，也不是只改 ~/.codex/config.toml 就能实现的功能。请先阅读代码并自行决定是否信任适配器处理请求。

## 最终效果

右下角模型菜单同时显示 Codex 官方 GPT 模型和通过本补丁加入的 Claude 模型，可直接在菜单中切换。下图是本机安装后的实际界面：

![Codex 模型菜单同时显示官方 GPT 模型与第三方 Claude 模型](docs/images/model-picker-final.png)

## 快速开始

需要 Python 3.11+、已安装的 Codex 桌面端，以及支持 /v1/models 和 Responses API 的服务。以下是无密钥的通用示例；把 URL、模型 slug 和上下文长度换成服务商提供的真实值。运行命令的账户必须能读取环境变量。

```powershell
git clone https://github.com/xdzhu/codex-model-customize-patch.git
cd codex-model-customize-patch
[Environment]::SetEnvironmentVariable('THIRD_PARTY_API_KEY', '<your-key>', 'User')
```

重新打开 PowerShell，让新环境变量生效。请勿把真实密钥写到仓库、config.toml、连接 JSON 或命令历史中。上面的占位命令会把你输入的密钥留在 PowerShell 历史里；更安全的做法是使用系统凭据管理器或在交互提示中输入。

```powershell
python patch.py connect --name example --kind openai --url https://api.example.com/v1 --wire-api responses --env-key THIRD_PARTY_API_KEY --models MODEL_SLUG --context-window 200000
```

connect 会请求 /v1/models，并在本机状态目录保存连接信息。它不保存密钥值。连接文件路径会出现在命令输出中。多个模型需要不同元数据时，按 [示例](examples/connections.example.json) 编辑 model_specs，并用 --connections 指定文件。--context-window 仅在所有指定模型具有相同上下文长度时方便使用；实际值以服务商资料为准。

中科大大模型公共服务平台的五模型无密钥示例见 [USTC 连接示例](examples/ustc-connections.example.json)；该平台的发现、Responses 和工具调用验证记录见[安装和验证记录](docs/INSTALLATION.md#中科大平台五模型接入验证)。示例只保存 `USTC_API_KEY` 环境变量名。不同服务商的连接可合并在同一个私有 `connections.json` 的 `locals` 数组中。

第三方条目不能照搬官方 GPT 的身份提示。旧构建复制官方目录模板时，也复制了“基于 GPT-6”的指令；这会让第三方模型错误自称 GPT。现已在生成第三方条目时替换该提示，同时保持官方条目原样。模型的自我介绍不能证明实际路由或底层权重；应以会话中的模型 ID、适配器路由和服务商记录核对。一次多模型切换会话中，两次 DeepSeek 请求收到上游 HTTP 400。受控重放确认：跨模型传递 GLM 可见推理摘要时，适配器生成的助手消息漏写 `type: "message"`，被服务商拒绝解析；补上后完整历史请求返回 200。详情见[排查记录](docs/INSTALLATION.md#多模型会话的错误身份与-http-400)。

```powershell
python patch.py build --connections "<path-to-connections.json>" --output "<path-to-build-directory>" --verify
```

正常退出 Codex 桌面端后再运行：

```powershell
python patch.py install "<path-to-build-directory>"
```

重新打开 Codex，在右下角模型菜单选择官方模型或新增模型。默认官方模型不会被覆盖。恢复前也要退出桌面端：

```powershell
python patch.py restore "<path-to-build-directory>"
```

## 已验证范围与限制

本机曾在 Windows 桌面版 26.1016.6573、Codex CLI 0.163.0-alpha.11 上验证合并目录包含 10 个官方模型以及 claude-sonnet-4-6、claude-opus-5-5。GPT 与 Sonnet 的简单请求正常返回。Opus 可以发出请求并收到响应，但一次最小指令测试返回了无关搜索内容；这不能作为 Opus 质量或服务端路由正确性的保证。第三方能力和行为取决于服务商。具体记录见 [安装和验证记录](docs/INSTALLATION.md)。

后续增量构建保留 11 个官方目录条目（包括隐藏条目），并按 Opus 5.5、Sonnet 5.5、Sonnet 4.6 排列三个第三方模型。Sonnet 5.5 的最小 Responses 请求返回 `42`，构建的隔离验证通过；随后包含长历史恢复修复的构建已在本机安装。Opus 5.5 曾在超长会话中碰到 8192 输出额度，经过短请求边界测试，该第三方路由接受的 `max_output_tokens` 最大为 128000；归因、设置和验证边界见[第三方模型输出额度](docs/OUTPUT_BUDGET.md)。示例中的上下文长度只是占位值，实际部署时应核对服务商能力。

旧 GPT 会话切换到 Opus 时，历史恢复可能先产生多笔昂贵请求，随后最终请求仍被服务商以 HTTP 400 拒绝。新会话携带简短可读交接的方式已通过小请求验证；故障证据与处理步骤见[安装和验证记录](docs/INSTALLATION.md#重启后旧会话长时间正在思考最终收到服务商-http-400)。

Windows 安装器注册登录计划任务，重启并登录后会自动启动本地适配器；API Key 应保存在用户级环境变量中，无需每次开终端或手动运行脚本。本机已核对计划任务、适配器健康检查与持久化环境变量。官方 GPT 登录和模型继续可用。

适配器不按 Codex 桌面端版本号锁定运行：新版桌面端通常可继续使用已安装的菜单与适配器。版本变化后可先检查模型菜单、`patch.py status` 和一条简短请求；仅在请求契约发生不兼容、目录需要刷新或安装新构建时，才针对当前已签名的 Codex 版本重新构建并隔离验证。本仓库没有配置自动更新发布目录，请勿启用后台更新监视器。安装新构建时桌面端必须正常退出。构建输出与本机连接文件都可能包含敏感运行信息，不能提交到 Git。

## 来源与许可

源代码基于上游扩展版提交 d2c4a8b8b614813846b8563c1c2e98a8bbbc829d，保留原有 [MIT 许可](LICENSE) 和 [NOTICE](NOTICE)。本仓库的主要增补是环境变量取 API Key、Windows 用户环境变量读取、兼容服务的 User-Agent 以及安装器边界修复。项目与 OpenAI、Anthropic 及 API 服务商无关联。
