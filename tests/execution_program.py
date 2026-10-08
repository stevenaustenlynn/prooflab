"""New public toy subprocess for runner tests; no external data or services."""

import json
import os
from pathlib import Path
import signal
import sys
import time

mode = sys.argv[1]
if mode == "bytes":
    os.write(1, b"stdout\x00\xff\n")
    os.write(2, b"stderr\xfe\x00\n")
elif mode == "args":
    print(json.dumps(sys.argv[2:]))
elif mode == "fail":
    print("before failure", flush=True)
    print("failure detail", file=sys.stderr, flush=True)
    sys.exit(7)
elif mode in ("timeout", "ignore-term"):
    if mode == "ignore-term" and os.name == "posix":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(1, b"before timeout\xff\n")
    os.write(2, b"partial stderr\xfe\n")
    time.sleep(60)
elif mode == "clean":
    assert not Path("created.txt").exists()
    assert Path("input.txt").read_bytes() == b"captured input\n"
    Path("created.txt").write_text("trial state")
    Path("input.txt").write_text("mutated")
    print("clean")
elif mode == "seed":
    print(os.environ.get("PROOFLAB_SEED", "absent"))
elif mode == "environment":
    print("present" if os.environ.get("PROOFLAB_TEST_SECRET") else "absent")
else:
    print("ok")
