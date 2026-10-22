# Validation

CI runs Python unit tests, the retained Node routing regression, and Python compilation on Windows and Ubuntu. It does not use a live API key or install Codex.

Local checks:

```sh
cd src
python -m unittest discover -s tests -q
node tests/route.test.cjs
python -m compileall -q .
```

For a desktop integration check, run patch.py build with a private connection file, a new output directory, and --verify. The isolated verification does not prove a real third-party endpoint behaves correctly. Test one short request through the installed adapter for each provider you intend to use, and record the exact app and CLI versions. A successful HTTP response alone does not establish instruction following or correct model routing.

A previous Windows test is summarized in INSTALLATION.md. It is a bounded observation, not a release certification for other Codex versions, providers, or platforms.
