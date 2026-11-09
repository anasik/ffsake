/*
 * OpenCode v2 plugin: serve the native `read` tool through `ffsake read`.
 *
 * The model keeps calling `read`, so tool catalogs and edit turns are
 * unchanged, but repository content comes from FFSAKE's workspace policy.
 * File-reading shell commands are rejected for the same reason.
 *
 * Install as `.opencode/plugins/<unique-id>/index.js`; the guided setup
 * (scripts/ffsake_cli_setup.py) and the benchmark harness render a
 * workspace-unique id and replace the `__FFSAKE_BIN__` / `__FFSAKE_ROOT__`
 * placeholders with absolute paths. The plugin also honours FFSAKE_BIN at
 * load time.
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

// Every generated copy gets a workspace-unique id. OpenCode caches plugin
// modules by id across workspaces, so a shared id would keep the first
// workspace's root closure and serve stale paths in later runs.
const FFSAKE_BIN = process.env.FFSAKE_BIN || "__FFSAKE_BIN__";
const FALLBACK_ROOT = "__FFSAKE_ROOT__";
const PLUGIN_ID = "__FFSAKE_PLUGIN_ID__";

// Keep this list aligned with scripts/claude_read_redirect_hook.py so both
// hosts enforce the same read path.
const READ_COMMANDS = new Set([
  "cat", "head", "tail", "sed", "awk", "grep", "rg", "find", "less", "more",
  "nl", "cut", "sort", "uniq", "wc",
]);

function workspaceRoot(ctx) {
  return (
    (ctx && ctx.location && ctx.location.directory) ||
    (ctx && ctx.app && ctx.app.directory) ||
    (FALLBACK_ROOT && !FALLBACK_ROOT.includes("__FFSAKE_") ? FALLBACK_ROOT : process.cwd())
  );
}

function configuredBinary() {
  if (!FFSAKE_BIN || FFSAKE_BIN.includes("__FFSAKE_")) {
    throw new Error("ffsake read policy: FFSAKE_BIN is not configured");
  }
  if (!fs.existsSync(FFSAKE_BIN)) {
    throw new Error(`ffsake read policy: binary not found at ${FFSAKE_BIN}`);
  }
  return FFSAKE_BIN;
}

function lineRange(input, totalLines) {
  const offset = input && input.offset != null ? Number(input.offset) : null;
  const limit = input && input.limit != null ? Number(input.limit) : null;
  const start = offset != null && Number.isFinite(offset) ? Math.max(1, offset + 1) : 1;
  const end =
    limit != null && Number.isFinite(limit)
      ? start + Math.max(1, limit) - 1
      : start + Math.max(0, totalLines - 1);
  return { start, end };
}

function redirectRead(input, root) {
  const requested = String((input && input.path) || "");
  if (!requested) {
    throw new Error("ffsake read policy: read requires a path");
  }
  const absolute = path.isAbsolute(requested)
    ? path.normalize(requested)
    : path.resolve(root, requested);
  const offset = input && input.offset != null ? Number(input.offset) : null;
  const limit = input && input.limit != null ? Number(input.limit) : null;
  let spec = absolute;
  if (offset != null || limit != null) {
    const start = offset != null && Number.isFinite(offset) ? Math.max(1, offset + 1) : 1;
    const end =
      limit != null && Number.isFinite(limit)
        ? start + Math.max(1, limit) - 1
        : start + 2000 - 1;
    spec = `${absolute}:${start}-${end}`;
  }
  const run = spawnSync(configuredBinary(), ["read", root, spec, "--raw"], {
    encoding: "utf8",
    timeout: 30000,
    maxBuffer: 64 * 1024 * 1024,
  });
  const raw = typeof run.stdout === "string" ? run.stdout : "";
  let value;
  try {
    value = JSON.parse(raw);
  } catch {
    const detail = run.error ? ` (${run.error.message})` : "";
    throw new Error(`ffsake read policy: ffsake read returned invalid output${detail}`);
  }
  if (!value || value.ok !== true) {
    throw new Error(
      `ffsake read policy: ${String((value && value.error) || "ffsake read failed")}`,
    );
  }
  const text =
    typeof value.text === "string"
      ? value.text
      : typeof value.result === "object" && value.result && typeof value.result.text === "string"
        ? value.result.text
        : "";
  const lines = text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const { start, end } = lineRange(input, lines.length);
  const numbered = lines
    .map((line, index) => `${start + index}: ${line}`)
    .join("\n");
  const name = path.basename(absolute);
  return {
    output: {
      type: "file",
      uri: pathToFileURL(absolute).href,
      name,
      content: text,
      encoding: "utf8",
      mime: "text/plain",
    },
    content: `Read file ${name}, lines ${start}-${end}${numbered ? "\n" + numbered : ""}`,
    metadata: { truncated: false, ffsake_read: true },
  };
}

function readCommandBlocked(command) {
  const words = String(command || "")
    .replace(/[;|&()]/g, " ")
    .trim()
    .split(/\s+/);
  return words.length > 0 && READ_COMMANDS.has(words[0]);
}

export default {
  id: PLUGIN_ID,
  async setup(ctx) {
    const root = workspaceRoot(ctx);
    await ctx.tool.transform((editor) => {
      const read = editor.get("read");
      if (!read) return;
      editor.update("read", (tool) => {
        tool.execute = async (input) => redirectRead(input, root);
      });
    });
    if (ctx.tool && typeof ctx.tool.hook === "function") {
      await ctx.tool.hook("execute.before", async (input) => {
        if (!input) return;
        if (input.tool === "shell" || input.tool === "bash") {
          const command = input.input && input.input.command;
          if (readCommandBlocked(command)) {
            throw new Error(
              "ffsake read policy: use the ffsake CLI read command for repository contents",
            );
          }
        }
      });
    }
  },
};
