# Architecture

The official Codex desktop app and bundled CLI remain vendor signed. This extension prepares a user scoped native model catalog and loopback adapter. It does not rewrite the app ASAR.

```text
Codex desktop model picker
  → merged native catalog
    → official GPT entry → original OpenAI authentication and destination
    → custom model entry → explicit provider route → compatible API
```

The connection file contains base_url, env_key, model IDs, optional model_specs, and protocol choice. The secret value is read from the environment when a request is made. On Windows, the user's persistent environment is a fallback when the desktop process does not inherit a terminal variable. Provider definitions and model entries are built together; a catalog entry alone cannot select a provider.

The adapter stages a build and checks it before installation. Installation requires the app to exit normally. Keep the build directory for rollback with patch.py restore. The local build, connection file, and diagnostics may contain sensitive operational data and should remain private.

The fork has no release manifest or automatic update feed. Source updates and Codex app updates require a fresh build and verification.
