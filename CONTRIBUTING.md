# Contributing

Issues and pull requests are welcome. Include your operating system, Codex desktop and bundled CLI versions, the command that failed, and a redacted error. Do not upload app bundles, generated catalogs, user configuration, API keys, authentication files, or raw logs.

This fork currently maintains the extension edition only. Automated release publishing and updates are disabled. Test Windows and macOS code paths when relevant; describe the platform actually tested. Run unit tests, the Node routing regression and isolated build verification before claiming an install is ready.

Keep model routing explicit, preserve the official GPT catalog and login, and keep credentials out of configuration files. New compatible providers should use env_key where possible. Never hardcode a model name as a proxy for capability; obtain metadata from the provider or document operator supplied values.

The upstream project and its MIT license are acknowledged in the README, LICENSE, and NOTICE. Preserve those files.
