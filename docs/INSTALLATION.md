# 安装与验证经验

## 为什么要构建合并目录

Codex 桌面端右下角模型菜单从它使用的目录读取模型。单纯在用户级 config.toml 增加 model_providers、model_catalog_url 或本地 model-catalogs 文件，不保证官方模型与第三方模型在同一个菜单出现，更不会自动给每个条目切换 Provider。此工具生成合并目录，并为新增模型明确指定 Provider。官方模型和认证路径继续保留。

## 连接信息和密钥

在连接 JSON 中只写 env_key 名称。连接内容的 id 必须以 local- 开头，base_url 一般以 /v1 结尾，wire_api 要与服务实际支持的协议一致。/v1/models 缺少 context_window 时，提供 model_specs。上下文长度是服务能力声明，不应从模型名称推测。若某模型实际只支持 Chat Completions，应配置 wire_api 为 chat_completions 并单独验证工具调用和指令兼容性。

Windows 的图形应用有时看不到启动终端临时设置的变量。本派生版在当前进程环境没有值时，可读取 Windows 用户级环境变量。设置后重新启动桌面端。不要把密钥放在连接文件、TOML、构建输出、命令行参数或公开 issue 中。

## Windows 上的实际观察

2026-10-22 的本机试验使用 Codex 桌面版 26.1016.6573 和 CLI 0.163.0-alpha.11。构建结果合并了 10 个官方 GPT 条目和两个第三方条目 claude-sonnet-4-6、claude-opus-5-5。原生适配器的隔离验证通过；通过适配器发出的简单请求中，GPT 和 Sonnet 正常返回。Opus 请求收到服务端响应，但一次要求仅输出 42 的测试返回了无关搜索内容，因此只能说明链路可达，不能确认该服务端的 Opus 路由和行为可靠。

第三方服务的 /v1/models 在当时会拒绝 Python 默认 User-Agent，表现为 HTTP 1010。本派生版给发现和推理请求设置了明确的 User-Agent。此经验只说明这一服务的当时行为，并不保证其他服务兼容。

安装器曾在 Windows 的 Provider 缺省值为 null 时失败；派生版处理了这个边界。安装前必须正常退出 Codex，随后安装已验证的构建目录；运行中的桌面进程会阻止安装。保留构建目录以供恢复。

## 升级与恢复

本仓库不发布更新清单，也不配置自动更新通道。不要打开 monitor 自动更新。Codex 升级后重新构建、隔离验证并安装。若需撤销，退出 Codex 后运行 python patch.py restore <build-directory>。

## 公开仓库边界

此仓库只放通用源代码、无密钥示例和验证结论。本机用户配置、凭据、连接文件、构建目录、安装包以及真实接口地址均不属于可发布材料。AGENTS.md 的 subagent 调度和模型菜单接入属于不同机制，可以独立配置。
