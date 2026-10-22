# Codex custom model menu patch

[中文](README.md) · [Installation notes](docs/INSTALLATION.md) · [Output budget findings](docs/OUTPUT_BUDGET.md) · [Architecture](docs/ARCHITECTURE.md) · [MIT](LICENSE)

This is a derivative of the extension edition of [limyum-creator/codex-deepseek-patch](https://github.com/limyum-creator/codex-deepseek-patch). It merges compatible third-party models into the Codex desktop model picker while keeping official GPT models and their existing authentication. Each model slug remains unchanged and is explicitly routed to its provider in the generated catalog. Merely adding a model_providers entry to config.toml does not create a combined desktop menu.

## Final result

The Codex desktop model picker shows official GPT models alongside the Claude models added by this patch, with both available for selection. This screenshot is from the installed local build:

![Codex model picker showing official GPT and third-party Claude models together](docs/images/model-picker-final.png)

Requirements: Python 3.11+, Codex desktop, and a service with /v1/models and a compatible Responses API. Supply your own URL, model IDs, and accurate context limits. Set the API key in an operating-system environment variable; never commit it or put it in the connection JSON or config.toml.

```powershell
git clone https://github.com/xdzhu/codex-model-customize-patch.git
cd codex-model-customize-patch
[Environment]::SetEnvironmentVariable('THIRD_PARTY_API_KEY', '<your-key>', 'User')
```

Reopen PowerShell after setting the variable. Typing a real key in a command can leave it in shell history; use an interactive secret prompt or a credential manager when possible.

```powershell
python patch.py connect --name example --kind openai --url https://api.example.com/v1 --wire-api responses --env-key THIRD_PARTY_API_KEY --models MODEL_SLUG --context-window 200000
python patch.py build --connections "<path-to-connections.json>" --output "<path-to-build-directory>" --verify
```

connect prints the location of the locally saved connection file. For several models with distinct specifications, edit its model_specs using [the example](examples/connections.example.json). Close Codex normally before installing:

```powershell
python patch.py install "<path-to-build-directory>"
```

Reopen Codex and use the model picker. Official models remain available. To undo, close Codex and run python patch.py restore "<path-to-build-directory>".

There is no configured automatic update feed for this fork; do not enable the background update monitor. Rebuild and verify after Codex updates. This adapter handles model traffic, so inspect the source before use. Local build output and connection files can contain sensitive operational data and must stay out of Git.

The updated example lists Opus 5.5, Sonnet 5.5, and Sonnet 4.6 in menu order. A minimal Responses request to Sonnet 5.5 returned `42`, and an incremental catalog build passed isolated verification. A later build with long-history recovery fixes was installed locally. For the tested Opus 5.5 route, short requests established an accepted `max_output_tokens` ceiling of 128000; see the [output budget findings](docs/OUTPUT_BUDGET.md) for evidence and limits. The example context limits are placeholders; use values verified with your provider.

Switching an old GPT chat to Opus may trigger costly history recovery before the final request, which can still be rejected with HTTP 400. A fresh chat with a concise readable handoff passed a small request; see the [troubleshooting record](docs/INSTALLATION.md#重启后旧会话长时间正在思考最终收到服务商-http-400).

Source base: upstream extension commit d2c4a8b8b614813846b8563c1c2e98a8bbbc829d. Original [MIT license](LICENSE) and [NOTICE](NOTICE) are retained. The added work covers environment-key authentication, Windows user environment variables, a compatible User-Agent, and an installer edge-case fix. This project is not affiliated with OpenAI, Anthropic, or API providers.
