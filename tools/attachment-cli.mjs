#!/usr/bin/env node
/* GPL-3.0-only. Fixed China-region attachment extension. No generic HTTP command. */
import { readFile, writeFile, stat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

const [command, ...tokens] = process.argv.slice(2);
const allowedCommands = new Set(["quota", "task-get", "task-probe", "upload", "inspect"]);
const options = {};
let sideEffect = "none";
let reliableId = null;
function fail(code) {
  const error = new Error(code);
  error.safeCode = code;
  throw error;
}
for (const token of tokens) {
  const match = /^--(project|task|attachment|file|name|sha256)=(.*)$/s.exec(
    token,
  );
  if (!match || options[match[1]] !== undefined) fail("INVALID_ARGUMENT");
  options[match[1]] = match[2];
}
function id(name) {
  const value = options[name];
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(value))
    fail("INVALID_ID");
  return encodeURIComponent(value);
}

try {
  if (!allowedCommands.has(command)) fail("UNSUPPORTED_OPERATION");
  const auth = JSON.parse(
    await readFile(
      join(homedir(), ".config", "notido-attachments", "config.json"),
      "utf8",
    ),
  );
  if (!auth.token || auth.region !== "cn") fail("ATTACHMENT_AUTH_REQUIRED");
  const headers = {
    Cookie: `t=${auth.token}`,
    "X-Device": JSON.stringify({
      platform: "web",
      version: 6430,
      id: "notido-controlled-cli",
    }),
    "User-Agent": "Mozilla/5.0 NotiDo/0.1",
    Origin: "https://dida365.com",
    Referer: "https://dida365.com/webapp/",
  };
  async function api(
    version,
    path,
    { method = "GET", body, binary = false } = {},
  ) {
    const response = await fetch(
      `https://api.dida365.com/api/${version}${path}`,
      {
        method,
        headers:
          body && !(body instanceof FormData)
            ? { ...headers, "Content-Type": "application/json" }
            : headers,
        body:
          body instanceof FormData
            ? body
            : body
              ? JSON.stringify(body)
              : undefined,
        signal: AbortSignal.timeout(method === "POST" ? 110000 : 50000),
      },
    );
    if (!response.ok) fail(`REMOTE_HTTP_${response.status}`);
    const reader = response.body.getReader();
    const chunks = [];
    let length = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > (binary ? 10000000 : 1048576)) {
        await reader.cancel();
        fail("RESPONSE_LIMIT");
      }
      chunks.push(value);
    }
    const bytes = Buffer.concat(chunks);
    if (binary) {
      if ((response.headers.get("content-type") || "").includes("text/html"))
        fail("DOWNLOAD_NOT_BINARY");
      return bytes;
    }
    try {
      return JSON.parse(bytes.toString("utf8"));
    } catch {
      fail("RESPONSE_NOT_JSON");
    }
  }
  async function task() {
    const items = await api("v2", `/project/${id("project")}/tasks`);
    if (!Array.isArray(items)) fail("TASK_SCHEMA_INVALID");
    const matches = items.filter(
      (item) => item.id === options.task && item.projectId === options.project,
    );
    if (matches.length !== 1) fail("TASK_NOT_UNIQUE");
    return matches[0];
  }
  let data;
  if (command === "quota") {
    data = {
      underQuota: await api("v1", "/attachment/isUnderQuota"),
      dailyLimit: await api("v1", "/attachment/dailyLimit"),
    };
  } else if (command === "task-probe") {
    const current = await api("v2", `/task/${id("task")}`);
    if (current?.id !== options.task || current?.projectId !== options.project ||
        ![0, 1].includes(current.deleted)) fail("TASK_PROBE_SCHEMA_INVALID");
    data = {id: options.task, projectId: options.project,
      exists: current.deleted === 0, proof: current.deleted === 1 ? "native_deleted_flag" : "native_active_flag"};
  } else if (command === "task-get") {
    data = await task();
  } else if (command === "inspect") {
    const current = await task();
    const matches = (current.attachments || []).filter(
      (a) => a.id === options.attachment || a.refId === options.attachment,
    );
    if (matches.length !== 1) fail("ATTACHMENT_NOT_REGISTERED");
    const bytes = await api(
      "v1",
      `/attachment/${id("project")}/${id("task")}/${id("attachment")}?action=download`,
      { binary: true },
    );
    data = {
      id: options.attachment,
      task_id: options.task,
      project_id: options.project,
      size: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex"),
      metadata: matches[0],
    };
  } else {
    if ((await stat(options.file)).size > 10000000) fail("FILE_SIZE_LIMIT");
    const current = await task();
    id("attachment");
    if (
      (current.attachments || []).some(
        (a) => a.id === options.attachment || a.refId === options.attachment,
      )
    )
      fail("ATTACHMENT_ALREADY_REGISTERED_RECONCILE");
    if ((await api("v1", "/attachment/isUnderQuota")) !== true)
      fail("ATTACHMENT_QUOTA");
    const bytes = await readFile(options.file);
    if (createHash("sha256").update(bytes).digest("hex") !== options.sha256)
      fail("SOURCE_HASH_MISMATCH");
    const name = options.name || "original";
    if (!name || name.length > 200 || /[\r\n\0/\\]/.test(name))
      fail("FILENAME_INVALID");
    const form = new FormData();
    form.append("file", new Blob([bytes]), name);
    sideEffect = "unknown";
    const uploaded = await api(
      "v1",
      `/attachment/upload/${id("project")}/${id("task")}/${id("attachment")}`,
      { method: "POST", body: form },
    );
    if (
      !uploaded ||
      typeof uploaded !== "object" ||
      uploaded.refId !== options.attachment ||
      !uploaded.path
    )
      fail("UPLOAD_SCHEMA_INVALID");
    reliableId = options.attachment;
    // Fetch again before appending; preserve all existing task fields and attachments.
    const latest = await task();
    if (
      latest.title !== current.title ||
      latest.dueDate !== current.dueDate ||
      latest.content !== current.content
    )
      fail("TASK_CHANGED_AFTER_UPLOAD");
    const attachment = {
      ...uploaded,
      id: options.attachment,
      refId: options.attachment,
      projectId: options.project,
      fileName: name,
      size: bytes.length,
    };
    delete attachment.taskId;
    await api("v2", "/batch/task", {
      method: "POST",
      body: {
        update: [
          {
            ...latest,
            attachments: [...(latest.attachments || []), attachment],
          },
        ],
      },
    });
    sideEffect = "applied";
    data = {
      id: reliableId,
      task_id: options.task,
      project_id: options.project,
    };
  }
  console.log(
    JSON.stringify({
      contract_version: 1,
      ok: true,
      side_effect: sideEffect,
      data,
    }),
  );
} catch (error) {
  console.log(
    JSON.stringify({
      contract_version: 1,
      ok: false,
      side_effect: sideEffect,
      remote_id: reliableId,
      error: { code: error.safeCode || "ATTACHMENT_CLI_FAILED" },
    }),
  );
  process.exitCode = 1;
}
