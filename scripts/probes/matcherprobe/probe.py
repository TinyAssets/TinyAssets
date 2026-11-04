"""The sync-ignore matcher's memory and step, measured in a real sandbox (`LIST_BYTES` and
`STEP_NS`, `limits-audit.md`): `heap.ts` bundled, run in one throwaway sandbox of the pinned
template with its own Node (its heap limit, the sync agent's peak over a sync-out of
500,000 entries, each class's heap a list byte, the automaton's step), the sandbox killed
and proven gone. Under 15 sandbox-minutes: the sandbox's own timeout is 14 minutes.

    E2B_API_KEY=... python3 scripts/probes/matcherprobe/probe.py [--out results.json]
        [--list-bytes N]   (the ceiling proven to fit beside the agent's measured peak)
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from e2b import Sandbox

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
PLATFORM = ROOT / "platform"


def bundle(out: Path) -> Path:
    script = (
        "const { createRequire } = require('node:module');"
        f"const esbuild = createRequire({json.dumps(str(PLATFORM / 'package.json'))})('esbuild');"
        f"esbuild.buildSync({{ entryPoints: [{json.dumps(str(HERE / 'heap.ts'))}],"
        f" outfile: {json.dumps(str(out))}, bundle: true, platform: 'node', target: 'node22',"
        " format: 'esm', banner: { js: \"import { createRequire as __r } from 'node:module';"
        " const require = __r(import.meta.url);\" }, logLevel: 'warning' });")
    subprocess.run(["node", "-e", script], check=True)
    return out


def main():
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    # The ceiling to prove (`LIST_BYTES`), in bytes.
    list_bytes = int(sys.argv[sys.argv.index("--list-bytes") + 1]) \
        if "--list-bytes" in sys.argv else 12 * 1024 * 1024

    template = re.search(r'"E2B_TEMPLATE":\s*"([^"]+)"',
                         (PLATFORM / "sandbox" / "wrangler.jsonc").read_text()).group(1)
    with tempfile.TemporaryDirectory() as tmp:
        js = bundle(Path(tmp) / "heap.mjs")
        results = {"template": template, "started": time.time()}
        sandbox = Sandbox.create(template=template, timeout=840)
        results["sandbox"] = sandbox.sandbox_id
        try:
            sandbox.files.write("/tmp/heap.mjs", js.read_text())
            node = "/usr/local/bin/node"
            classes = ["literalName", "literalPath", "suffix", "prefix", "pathPrefix",
                       "anywhere", "automaton", "negated", "literalNameDiverging",
                       "literalPathDiverging", "suffixDiverging", "prefixDiverging",
                       "pathPrefixDiverging", "anywhereDiverging"]

            def run(name, command, timeout):
                try:
                    done = sandbox.commands.run(command, user="root", timeout=timeout)
                    results[name] = json.loads(done.stdout.strip().splitlines()[-1])
                except Exception as error:  # a heap out of memory is a finding, not a stop
                    results[name] = {"failed": str(error)[-400:]}
                print(name, json.dumps(results[name]), flush=True)

            run("limit", f"{node} /tmp/heap.mjs limit", 60)
            run("step", f"{node} /tmp/heap.mjs step", 120)
            run("peak", f"{node} /tmp/heap.mjs peak 500000", 600)
            for c in classes:
                run(f"class:{c}", f"{node} --expose-gc /tmp/heap.mjs classes {c} 4194304", 120)
            # The ceiling's proof: each class at `list_bytes`, beside the agent's own peak.
            peak = results["peak"]["heap_peak"]
            for c in classes:
                run(f"fit:{c}", f"{node} /tmp/heap.mjs fit {c} {list_bytes} {peak}", 180)
            memory = sandbox.commands.run("cat /sys/fs/cgroup/memory.max 2>/dev/null; free -b",
                                          user="root", timeout=30)
            results["memory"] = memory.stdout
        finally:
            Sandbox.kill(sandbox.sandbox_id)
        # Proven gone: no running sandbox of ours by its id.
        listed = Sandbox.list()
        items = listed.next_items() if hasattr(listed, "next_items") else listed
        results["gone"] = all(getattr(info, "sandbox_id", None) != results["sandbox"]
                              for info in items)
        results["minutes"] = round((time.time() - results["started"]) / 60, 2)
    print(json.dumps(results, indent=2))
    if out:
        Path(out).write_text(json.dumps(results, indent=2))
    if not results["gone"]:
        raise SystemExit("the probe's sandbox is still listed")


if __name__ == "__main__":
    main()
