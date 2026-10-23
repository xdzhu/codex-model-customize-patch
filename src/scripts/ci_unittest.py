"""Run the standard suite and surface failures as GitHub annotations."""
import os
import re
import subprocess
import sys


result = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
)
sys.stdout.write(result.stdout)
if result.returncode and os.environ.get("GITHUB_ACTIONS") == "true":
    names = re.findall(r"(?m)^(?:FAIL|ERROR): (.+)$", result.stdout)
    summaries = re.findall(r"(?m)^(?:AssertionError|AttributeError|ConnectionError|FileNotFoundError|OSError|RuntimeError|TimeoutError|TypeError|ValueError): .+$", result.stdout)
    if not names:
        names = ["unittest suite exited without a named failure"]
    for index, name in enumerate(names[:20]):
        detail = summaries[index] if index < len(summaries) else "See the full job log"
        message = (name + ": " + detail)[:1000]
        message = re.sub(r"sk-[A-Za-z0-9_-]{12,}|(?i:Bearer\s+)[A-Za-z0-9._-]+", "[redacted]", message)
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::error title=Python unittest failure::" + message)
sys.exit(result.returncode)
