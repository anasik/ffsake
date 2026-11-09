#!/usr/bin/env python3
"""Model-free checks for the OpenCode read-redirect plugin.

Renders the plugin for a disposable workspace, loads it in Node with a stub
OpenCode host, and verifies the wrapped `read` tool and shell block. No model
call and no paid provider request is made.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).resolve().with_name("opencode_read_redirect_plugin.js")
BINARY = ROOT / "target/debug/ffsake"

HARNESS = r"""
import plugin from "./plugin.mjs";

const root = process.argv[2];
const outside = process.argv[3];
const state = { transforms: 0, hooks: [] };
let execute = null;

const ctx = {
  location: { directory: root },
  tool: {
    async transform(callback) {
      state.transforms += 1;
      callback({
        get: (name) => (name === "read" ? { name: "read" } : undefined),
        update: (name, change) => {
          const tool = { name };
          change(tool);
          execute = tool.execute;
        },
      });
    },
    async hook(name, callback) {
      state.hooks.push(name);
      state.callback = callback;
    },
  },
};

await plugin.setup(ctx);
const results = {};
results.id = plugin.id;
results.transforms = state.transforms;
results.hooks = state.hooks;

const read = await execute({ path: "a.txt" });
results.read = {
  type: read.output.type,
  uri: read.output.uri,
  name: read.output.name,
  content: read.output.content,
  encoding: read.output.encoding,
  mime: read.output.mime,
  displayed: read.content,
  metadata: read.metadata,
};

const ranged = await execute({ path: "a.txt", offset: 1, limit: 1 });
results.ranged = {
  content: ranged.output.content,
  displayed: ranged.content,
};

let blocked = null;
try {
  await state.callback({ tool: "shell", input: { command: "cat a.txt" } });
} catch (error) {
  blocked = String(error && error.message ? error.message : error);
}
results.blocked = blocked;

let allowed = true;
try {
  await state.callback({ tool: "shell", input: { command: "git ls-files && python3 app.py" } });
  await state.callback({ tool: "read", input: { path: "a.txt" } });
} catch {
  allowed = false;
}
results.allowed = allowed;

let outsideError = null;
try {
  await execute({ path: outside });
} catch (error) {
  outsideError = String(error && error.message ? error.message : error);
}
results.outside = outsideError;

console.log(JSON.stringify(results));
"""


def main():
    assert BINARY.is_file(), f"build the CLI first: {BINARY}"
    node = shutil.which("node")
    assert node, "node is required for the model-free plugin smoke test"
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agent_benchmark_suite as suite

    suite.CLI_BINARY = BINARY
    with tempfile.TemporaryDirectory(prefix="ffsake-opencode-redirect-") as td:
        root = Path(td).resolve()
        (root / "a.txt").write_text("one\ntwo\nthree\n")
        outside = root.parent / f"{root.name}-outside.txt"
        outside.write_text("secret\n")
        try:
            plugin_id, source = suite.opencode_read_redirect_source(root)
            assert '"__FFSAKE_BIN__"' not in source
            assert '"__FFSAKE_ROOT__"' not in source
            assert '"__FFSAKE_PLUGIN_ID__"' not in source
            assert plugin_id in source
            (root / "plugin.mjs").write_text(source)
            (root / "harness.mjs").write_text(HARNESS)
            run = subprocess.run(
                [node, str(root / "harness.mjs"), str(root), str(outside)],
                capture_output=True, text=True, timeout=60, cwd=root,
            )
            assert run.returncode == 0, run.stderr
            value = json.loads(run.stdout)
        finally:
            outside.unlink(missing_ok=True)

    assert value["transforms"] == 1, value
    assert "execute.before" in value["hooks"], value
    read = value["read"]
    assert read["type"] == "file", read
    assert read["name"] == "a.txt", read
    assert read["content"] == "one\ntwo\nthree\n", read
    assert "1: one" in read["displayed"] and "3: three" in read["displayed"], read
    assert read["metadata"]["ffsake_read"] is True, read
    assert value["ranged"]["content"] == "two\n", value["ranged"]
    assert value["blocked"] and "ffsake read policy" in value["blocked"], value
    assert value["allowed"] is True, value
    assert value["outside"], "outside-workspace read was not denied"
    assert "ffsake read policy" in value["outside"], value["outside"]

    print(json.dumps({
        "plugin_id_workspace_unique": True,
        "read_served_by_ffsake": True,
        "read_range_preserved": True,
        "shell_file_reads_blocked": True,
        "ordinary_shell_allowed": True,
        "outside_read_denied": True,
        "model_calls": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
