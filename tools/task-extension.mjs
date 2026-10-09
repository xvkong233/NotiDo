#!/usr/bin/env node
/* Fixed machine-output wrapper around official CLI library. GPL-3.0-only. */
import { getApi } from "../node_modules/@suibiji/dida-cli/dist/lib/api.js";

const [command, project, task, raw] = process.argv.slice(2);
try {
  if (
    !["update", "complete", "delete", "probe"].includes(command) ||
    !/^[A-Za-z0-9_-]+$/.test(project) ||
    !/^[A-Za-z0-9_-]+$/.test(task)
  )
    throw new Error();
  const api = await getApi();
  if (command === "delete") {
    await api.deleteTask(project, task);
    console.log(JSON.stringify({contract_version:1,ok:true,side_effect:"applied",data:{id:task,projectId:project,accepted:true}}));
  } else if (command === "probe") {
    let exists = true;
    try {
      const current = await api.getTask(project,task);
      if (current?.id !== task || current?.projectId !== project) throw new Error();
    } catch (error) {
      if (!/^DIDA API 错误 404:/.test(String(error?.message))) throw error;
      const projects = await api.getProjects();
      if (!Array.isArray(projects) || !projects.some(item => item.id === project)) throw new Error();
      exists = false;
    }
    console.log(JSON.stringify({id:task,projectId:project,exists}));
  } else if (command === "complete") {
    await api.completeTask(project, task);
    console.log(
      JSON.stringify({ id: task, projectId: project, accepted: true }),
    );
  } else {
    const fields = JSON.parse(raw);
    const allowed = new Set([
      "title",
      "content",
      "dueDate",
      "startDate",
      "isAllDay",
      "timeZone",
      "priority",
      "repeatFlag",
      "repeatFrom",
    ]);
    if (
      !fields ||
      typeof fields !== "object" ||
      Array.isArray(fields) ||
      Object.keys(fields).some((k) => !allowed.has(k))
    )
      throw new Error();
    console.log(
      JSON.stringify(
        await api.updateTask(task, { id: task, projectId: project, ...fields }),
      ),
    );
  }
} catch (error) {
  if (command === "delete") {
    const rejected = /^DIDA API 错误 (?:400|401|403):/.test(String(error?.message));
    console.log(JSON.stringify({contract_version:1,ok:false,side_effect:rejected?"none":"unknown",remote_id:task,error:{code:rejected?"CLI_DELETE_REJECTED":"CLI_DELETE_UNKNOWN"}}));
  } else console.error("TASK_EXTENSION_FAILED");
  process.exitCode = 1;
}
