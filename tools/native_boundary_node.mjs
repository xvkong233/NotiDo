#!/usr/local/bin/node
/* Acceptance-only CLI process boundary. Never installed as a production runner. */
import {readFileSync,writeFileSync,existsSync} from "node:fs";
import {spawnSync} from "node:child_process";

const config = JSON.parse(readFileSync(new URL("./boundary.json", import.meta.url), "utf8"));
const [script, ...args] = process.argv.slice(2);
if (!script?.startsWith("/") || config.real_node !== "/usr/local/bin/node") process.exit(97);
const create = args[0] === "task" && args[1] === "create";
if (config.mode === "reject_upload" && args[0] === "upload" &&
    script === config.attachment_script && args.includes("--project=" + config.project_id) &&
    args.includes("--sha256=" + config.sha256)) {
  // Controlled safe failure before the actual attachment CLI or any network call.
  writeFileSync(config.rejected, JSON.stringify({sha256:config.sha256,boundary:"before_attachment_cli"}), {mode:0o600});
  process.stdout.write(JSON.stringify({contract_version:1,ok:false,side_effect:"none",remote_id:null,error:{code:"ATTACHMENT_QUOTA"}}));
  process.exit(1);
}
if (config.mode === "hold_create" && create) {
  if (script !== config.real_script || !args.includes("--project=" + config.project_id) || !args.includes("--title=" + config.title) || existsSync(config.started)) process.exit(98);
  writeFileSync(config.started, JSON.stringify({title:config.title,project_id:config.project_id}), {flag:"wx",mode:0o600});
}
const result = spawnSync(config.real_node, [script, ...args], {
  stdio:["ignore","pipe","ignore"], maxBuffer:2*1024*1024,
});
if (config.mode === "hold_create" && create && result.status === 0) {
  const task = JSON.parse(result.stdout.toString("utf8"));
  if (task.title !== config.title || task.projectId !== config.project_id || !task.id) process.exit(99);
  writeFileSync(config.applied, JSON.stringify({id:task.id,title:task.title,project_id:task.projectId,boundary:"remote_success_before_stdout"}), {flag:"wx",mode:0o600});
  // Keep stdout withheld until the actual plugin/CLI/container terminates us.
  setInterval(() => {}, 1000);
} else {
  // Large project responses use an asynchronous pipe write. Wait for its
  // callback before exiting, otherwise the acceptance wrapper truncates JSON.
  process.stdout.write(result.stdout ?? "", () => process.exit(result.status ?? 96));
}
