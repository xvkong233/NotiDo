const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const content = document.querySelector("#content");
const feedback = document.querySelector("#feedback");
const dialog = document.querySelector("#detail");
let activeView = "overview";
let generation = 0;
let settingsSnapshot = null;
let settingsGroup = "model";
const labels = {
  collecting: "收集材料中",
  awaiting_materials: "等待材料读取",
  awaiting_clarification: "待澄清",
  task_saved_attachments_pending: "任务已存 · 附件待处理",
  partially_done: "部分完成",
  completed: "处理完成",
  cancelled: "已取消本地处理",
  validated: "已核验 · 尚未执行",
  executing: "执行中",
  succeeded: "已核验成功",
  failed_safe: "未发生副作用 · 可安全重试",
  outcome_unknown: "结果未知 · 先核查",
  created_unverified: "任务 ID 已存 · 待核查",
  uploaded_unverified: "附件 ID 已存 · 待核查",
  applied_unverified: "已应用 · 待核查",
  pending: "待处理",
  sent: "回执已送达",
  failed: "回执发送失败",
  unknown: "回执送达未知",
  ready: "原件已取得",
  unavailable: "原件不可用",
};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function message(text, error = false) {
  feedback.textContent = text;
  feedback.className = error ? "error" : "";
}
function card(title, subtitle) {
  const node = el("article", undefined, "card");
  node.append(el("h3", title));
  if (subtitle) node.append(el("p", subtitle, "subtle"));
  return node;
}
function button(text, handler, className) {
  const node = el("button", text, className);
  node.type = "button";
  node.addEventListener("click", () => busy(node, handler));
  return node;
}
async function busy(node, handler) {
  if (node.disabled) return;
  node.disabled = true;
  try {
    await handler();
  } catch (error) {
    message(error.message || "操作未完成，请刷新查看。", true);
  } finally {
    node.disabled = false;
  }
}
function badge(state) {
  return el(
    "span",
    labels[state] || state,
    `badge ${["failed_safe", "outcome_unknown", "unavailable", "failed"].includes(state) ? "error" : ["succeeded", "completed", "ready", "sent"].includes(state) ? "" : "pending"}`,
  );
}
function date(value) {
  return value
    ? new Date(value * 1000).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
      })
    : "尚未检查";
}
function field(
  parent,
  label,
  name,
  { value = "", type = "text", hint = "", options = null } = {},
) {
  const wrap = el("div", undefined, "field");
  const id = `field-${name}-${crypto.randomUUID()}`;
  const lab = el("label", label);
  lab.htmlFor = id;
  const input = el(
    options ? "select" : type === "textarea" ? "textarea" : "input",
  );
  input.id = id;
  input.name = name;
  if (input.tagName === "INPUT") input.type = type;
  if (options)
    for (const [v, t] of options) {
      const op = el("option", t);
      op.value = v;
      input.append(op);
    }
  input.value = value ?? "";
  wrap.append(lab, input);
  if (hint) wrap.append(el("span", hint, "subtle"));
  parent.append(wrap);
  return input;
}
function definition(parent, items) {
  const list = el("dl");
  for (const [label, value] of items) {
    list.append(el("dt", label), el("dd", value ?? "—"));
  }
  parent.append(list);
}
async function get(endpoint, params) {
  const result = await bridge.apiGet(endpoint, params);
  if (result?.error) throw new Error(result.error.safe_message);
  return result;
}
async function post(endpoint, body, revision) {
  const result = await bridge.apiPost(endpoint, {
    ...body,
    request_id: crypto.randomUUID(),
    expected_revision: revision,
  });
  if (result?.error) throw new Error(result.error.safe_message);
  return result;
}
function heading(title, description) {
  content.append(el("h2", title), el("p", description, "subtle"));
}
function modal(title) {
  document.querySelector("#detail-title").textContent = title;
  const body = document.querySelector("#detail-content");
  body.replaceChildren();
  if (!dialog.open) dialog.showModal();
  return body;
}
function parse(value, fallback) {
  if (value == null) return fallback;
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return fallback;
    }
  }
  return value;
}

function reviewList(parent, items, render) {
  const list = el("div", undefined, "review-list");
  const controls = el("div", undefined, "actions");
  let offset = 0;
  const draw = () => {
    list.replaceChildren(...items.slice(offset, offset + 20).map(render));
    const previous = button("上一批", () => { offset -= 20; draw(); });
    const next = button("下一批", () => { offset += 20; draw(); });
    previous.disabled = offset === 0;
    next.disabled = offset + 20 >= items.length;
    controls.replaceChildren(previous, el("span", `${offset + 1}–${Math.min(offset + 20, items.length)} / ${items.length}`), next);
  };
  if (items.length) {
    parent.append(list, controls);
    draw();
  } else parent.append(el("p", "暂无记录。", "subtle"));
}

async function overview() {
  const status = await get("status");
  heading("处理状态", "依赖就绪与处理结果分开显示。实际结果以操作核验为准。");
  const grid = el("div", undefined, "grid section");
  for (const [title, state, detail] of [
    ["AstrBot 桥接", true, `固定版本 ${status.astrbot_bridge.version}`],
    ["AI 处理", true, "由 AstrBot 当前会话管理"],
    [
      "滴答任务",
      status.task_cli.account_confirmed,
      `CLI ${status.task_cli.version}`,
    ],
    [
      "原生附件",
      status.attachment_cli.readiness,
      status.attachment_cli.reason || "上传与下载核验",
    ],
    ["数据库 / worker", !status.maintenance, `${status.db} / ${status.worker}`],
  ]) {
    const panel = card(title, detail);
    panel.append(badge(state ? "ready" : "pending"));
    grid.append(panel);
  }
  content.append(grid);
  if (status.maintenance) {
    const panel = card("维护中", status.maintenance);
    panel.classList.add("section");
    content.append(panel);
  }
  const counts = card("持久作业", "关闭或刷新页面不会重新提交任务。");
  counts.classList.add("section");
  for (const row of status.jobs)
    counts.append(el("p", `${labels[row.state] || row.state}：${row.count}`));
  counts.append(el("p", `检查时间：${date(status.checked_at)}`, "subtle"));
  content.append(counts);
}

async function listing(endpoint, render, cursor = null, append = false) {
  const current = generation;
  const result = await get(endpoint, cursor ? { cursor } : {});
  if (current !== generation) return;
  let list = append ? content.querySelector(".list") : null;
  if (!list) {
    list = el("div", undefined, "list section");
    content.append(list);
  }
  if (!result.items.length && !append)
    list.append(el("p", "暂无记录。", "empty"));
  for (const item of result.items) list.append(await render(item));
  content.querySelector(".pagination")?.remove();
  if (result.next_cursor) {
    const pagination = el("div", undefined, "pagination");
    pagination.append(
      button("载入更多", () =>
        listing(endpoint, render, result.next_cursor, true),
      ),
    );
    content.append(pagination);
  }
}
async function notices() {
  heading("通知 / 待处理", "查看已读范围、行动依据、澄清问题和关联任务。");
  await listing("notices", async (item) => {
    const panel = card(
      `材料组 ${item.id.slice(0, 8)}`,
      `接收于 ${date(item.first_at)}`,
    );
    panel.append(badge(item.state));
    if (item.state_origin === "historical") panel.append(el("p", "历史状态，未经过原生结论登记", "subtle"));
    const actions = el("div", undefined, "actions");
    actions.append(button("查看依据与问题", () => noticeDetail(item.id)));
    panel.append(actions);
    return panel;
  });
}
async function noticeDetail(id) {
  const result = await get(`notices/${id}`);
  const body = modal("通知与读取依据");
  const group = result.group;
  definition(body, [
    ["材料组", group.id],
    ["状态", labels[group.state] || group.state],
    ["版本", group.revision],
  ]);
  const conclusion = parse(result.native_outcome?.[0]?.declaration, null);
  if (conclusion?.pending_reason) body.append(card("尚待处理", conclusion.pending_reason));
  if (result.ai_owner === "astrbot" && !result.native_outcome?.length)
    body.append(card("历史处理状态", "这条记录产生于原生结论登记上线前，旧完成标记不作为本版处理闭环的证据。"));
  const question = parse(result.session.question, null);
  body.append(card(
    result.restore_hold ? "恢复前的材料" : "在 AstrBot 会话继续",
    result.restore_hold
      ? "先在操作 / 恢复页完成历史核查。通知理解与追问请回到 AstrBot 原会话继续；旧计划保持暂停。"
      : "通知理解、身份判断与追问使用 AstrBot 当前会话和记忆。此页只展示已保存证据与操作记录。",
  ));
  if (question && question.group_id === group.id) {
    const panel = card("历史问题", "保留供核对，请在 AstrBot 原会话继续说明。");
    for (const text of question.questions || []) panel.append(el("p", text));
    for (const range of question.context?.unread_ranges || [])
      panel.append(el("p", `未处理范围：${range.source_id.slice(0, 8)} · ${range.location} · 字符 ${range.start}–${range.end}`, "subtle"));
    body.append(panel);
  }
  body.append(el("h3", "已读 / 未读位置"));
  for (const segment of result.segments) {
    const panel = card(
      `${segment.source_id.slice(0, 8)} · ${segment.location}`,
      segment.state === "read" ? "已读取" : `未读取：${segment.reason}`,
    );
    panel.append(el("p", segment.text || "无可读正文", "evidence"));
    body.append(panel);
  }
  for (const version of result.versions) {
    const plan = parse(version.payload, {});
    const panel = card(`行动计划版本 ${version.revision}`, plan.notice_summary);
    for (const item of plan.tasks || [])
      panel.append(
        el(
          "p",
          `${item.title || "修改已有任务"} · ${item.relevance} / ${item.obligation}`,
        ),
      );
    body.append(panel);
  }
  const targets = result.saved_targets || [];
  if (!targets.length) {
    body.append(card("新增材料", "请在 AstrBot 原会话发送材料。任务保存后，可在这里选择该任务补充原生附件。"));
    return;
  }
  const upload = card(
    "补充原件",
    "明确补充到当前材料组，不会无条件挂载到最近任务。",
  );
  const file = field(upload, "选择原件", "file", { type: "file" });
  const target = field(upload, "原件关联", "target_operation_id", {
    options: [
      ["", "请选择已保存任务"],
      ...targets.map((item) => {
        const plan = typeof item.plan === "string" ? JSON.parse(item.plan) : item.plan;
        return [item.id, `${plan.fields?.title || "已存任务"} · ${item.remote_id}`];
      }),
    ],
    hint: "已处理的通知必须选择一个已核验任务；此操作只补原件。",
  });
  upload.append(
    button("上传到此组", async () => {
      if (!file.files[0]) throw new Error("请选择原件。");
      if (!target.value) throw new Error("请选择原件关联任务。");
      const original = file.files[0];
      if (original.size > (result.material_budget?.file_bytes || 20 * 1024 * 1024))
        throw new Error("原件超过配置字节预算。");
      const bytes = await original.arrayBuffer();
      const hash = Array.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
        (x) => x.toString(16).padStart(2, "0"),
      ).join("");
      const query = new URLSearchParams({
        request_id: crypto.randomUUID(),
        expected_revision: String(group.revision),
        sha256: hash,
        name: original.name,
        target_operation_id: target.value,
      });
      const response = await bridge.upload(
        `groups/${group.id}/files?${query}`,
        original,
      );
      if (response?.error) throw new Error(response.error.safe_message);
      message(response.attachment_scheduled ? "原件已保存，补上传任务已入队。" : "原件已保存，等待读取与关联核验。");
      dialog.close();
      await load();
    }),
  );
  body.append(upload);
}

async function assets() {
  heading("原件", "原字节保存在受保护的数据目录。阅读副本不会替代上传原件。");
  await listing("assets", async (item) => {
    const panel = card(
      item.name,
      `接收于 ${date(item.created_at)} · 材料组 ${item.group_id.slice(0, 8)}`,
    );
    panel.append(badge(item.state));
    definition(panel, [
      ["SHA-256", item.hash],
      ["错误", item.error],
    ]);
    if (item.state === "ready")
      panel.append(
        button("下载原件", () =>
          bridge.download(`assets/${item.id}/download`, {}, item.name),
        ),
      );
    return panel;
  });
}

async function operations() {
  heading(
    "操作 / 恢复",
    "未知或未核验结果先核查。安全重试只适用于已证明没有副作用的操作。",
  );
  const recovery = await get("recovery/status");
  if (recovery?.required) {
    const settings = await get("settings");
    const panel = card("备份恢复复核", "本地回滚不撤销远端变化。请核对备份后的任务历史与未匹配任务；解除维护后旧操作仍保持暂停。");
    const review = recovery.review;
    const payload = parse(review?.payload, {});
    panel.append(button("开始新的只读复核", async () => {
      await post("recovery/check", {}, settings.revision);
      message("已保存当前批次复核结果。");
      await load();
    }));
    if (review?.state === "checking") panel.append(button("读取下一批历史目标", async () => {
      await post("recovery/check", { review_id: review.id }, settings.revision);
      message("已保存下一批复核结果。");
      await load();
    }));
    if (review) {
      panel.append(el("p", `复核时间：${date(review.checked_at)} · 其他账号记录：${payload.other_account_operations || 0} · 旧草稿：${payload.held_groups || 0}`));
      reviewList(panel, payload.history || [], (item) => {
        const descriptions = {
          matches: "与已存字段一致", external_change: "远端已有变化，请核对差异",
          no_replay: "尚未开始或未发生副作用，继续保留",
          outside_allowed_scope: "不在当前允许清单内，需人工核对",
          unknown_without_reliable_id: "缺少可靠远端 ID，继续保持未知",
        };
        const line = card(item.operation_id, descriptions[item.verification] || item.verification);
        line.append(el("p", JSON.stringify(item.current || item.differences || { state: item.state, remote_id: item.remote_id }), "evidence"));
        if (item.differences) line.append(el("p", JSON.stringify(item.differences), "evidence"));
        return line;
      });
      const tasks = payload.scope?.tasks || [];
      panel.append(el("h3", "当前清单中的未完成任务"));
      reviewList(panel, tasks, (task) => el("p", `${task.title} · ${task.id} · ${task.dueDate || "无日期"}`, "evidence"));
    }
    if (review?.state === "ready") {
      const acknowledged = field(panel, "我已人工核对备份后的新增/修改及未匹配任务（含旧账号记录）", "reviewed_remote_history", { type: "checkbox" });
      const paused = field(panel, "保留旧操作暂停，未知结果不重做，未开始计划另行核验", "keep_old_operations_paused", { type: "checkbox" });
      panel.append(button("确认复核并解除恢复维护", async () => {
        await post("recovery/confirm", { review_id: review.id, reviewed_remote_history: acknowledged.checked, keep_old_operations_paused: paused.checked }, settings.revision);
        message("复核已确认，旧操作保持暂停。请逐项查看计划再重新核验。");
        await load();
      }, "primary"));
    }
    content.append(panel);
  }
  await listing("operations", async (item) => {
    const result = parse(item.result, {});
    const panel = card(
      `${{ create: "新增任务", update: "修改任务", complete: "完成任务", delete: "删除任务", upload: "上传原件" }[item.kind] || item.kind} · ${item.id.slice(0, 8)}`,
      `创建于 ${date(item.created_at)}`,
    );
    panel.append(badge(item.state));
    definition(panel, [
      ["远端 ID", item.remote_id],
      ["尝试次数", item.attempt],
      ["最近核查", date(item.checked_at)],
      ["错误", result.error?.code],
      ["后续处理", item.paused ? "已暂停" : "正常"],
    ]);
    const actions = el("div", undefined, "actions");
    actions.append(
      button("查看计划与实际结果", () => operationDetail(item.id)),
    );
    for (const [state, action, label] of [
      ["validated", "cancel-local", "取消尚未开始操作"],
      ["failed_safe", "retry", "安全重试"],
    ])
      if (item.state === state && !(item.kind === "delete" && action === "retry"))
        actions.append(
          button(label, async () => {
            await post(`operations/${item.id}/${action}`, {}, item.revision);
            message("请求已记账。");
            await load();
          }),
        );
    if (
      [
        "outcome_unknown",
        "created_unverified",
        "uploaded_unverified",
        "applied_unverified",
      ].includes(item.state)
    )
      actions.append(
        button("只读核查", async () => {
          await post(`operations/${item.id}/reconcile`, {}, item.revision);
          message("已排队核查。");
          await load();
        }),
      );
    panel.append(actions);
    return panel;
  });
  const receipts = card("回执补发", "只重新发送固化回执，不重做任务或附件。");
  receipts.classList.add("section");
  const records = await get("receipts");
  for (const item of records.items.filter((x) => x.state !== "sent")) {
    const row = el("div", undefined, "section");
    row.append(badge(item.state), el("p", item.body, "evidence"));
    row.append(
      button("补发此回执", async () => {
        await post(`receipts/${item.id}/retry`, {}, item.revision);
        message("已排队补发回执。");
        await load();
      }),
    );
    receipts.append(row);
  }
  content.append(receipts);
}
async function operationDetail(id) {
  const item = await get(`operations/${id}`);
  const body = modal("操作核验详情");
  const plan = item.plan;
  definition(body, [
    ["操作", item.id],
    ["账号作用域", item.account_ref],
    ["清单", plan.project_id],
    ["任务", item.remote_id || plan.task_id],
    ["状态", labels[item.state] || item.state],
    ["版本", item.revision],
  ]);
  body.append(
    el("h3", "计划字段"),
    el("p", JSON.stringify(plan.fields, null, 2), "evidence"),
    el("h3", "实际核验结果"),
    el("p", JSON.stringify(parse(item.result, {}), null, 2), "evidence"),
  );
  for (const version of item.plan_versions || []) {
    const snapshot = card(`固化计划 ${version.plan_id}`, `保存于 ${date(version.created_at)}`);
    snapshot.append(el("p", JSON.stringify(version.payload.fields, null, 2), "evidence"));
    body.append(snapshot);
  }
  if (item.state === "validated" && item.paused && item.attempt === 0) {
    if (item.kind === "delete") {
      body.append(card("删除已暂停", "请返回 AstrBot 查询当前任务，展示具体目标并重新二次确认。"));
    } else if (plan.delivery_mode !== "framework_tool") {
      body.append(card("历史计划已暂停", "此计划由旧的独立 AI 流程生成。请在 AstrBot 原会话核对后继续，身份与记忆由 AstrBot 管理。"));
    } else {
    const panel = card("重新核验未开始计划", "确认后按所示字段执行，保留原期限与原解析时区，并保存新的计划版本。");
    const overdue = field(panel, "若原期限已过，明确补记逾期", "allow_overdue", { type: "checkbox" });
    panel.append(button("确认所示计划并重新核验", async () => {
      await post(`operations/${id}/revalidate`, { confirm: true, allow_overdue: overdue.checked }, item.revision);
      message("已保存新计划版本，等待执行前核验。");
      dialog.close();
      await load();
    }, "primary"));
    body.append(panel);
    }
  }
  if (
    item.kind === "create" &&
    ["outcome_unknown", "created_unverified"].includes(item.state)
  ) {
    const panel = card(
      "关联既有任务",
      "先核验账号、允许清单与真实内容，匹配成功后再关联。",
    );
    const project = field(panel, "清单 ID", "project_id", {
      value: plan.project_id,
    });
    const task = field(panel, "真实任务 ID", "task_id");
    panel.append(
      button("核验并关联", async () => {
        await post(
          `operations/${id}/link-existing`,
          { project_id: project.value, task_id: task.value },
          item.revision,
        );
        message("已保存目标并排队核查。");
        dialog.close();
        await load();
      }),
    );
    body.append(panel);
  }
}

async function settings() {
  const snapshot = await get("settings");
  settingsSnapshot = snapshot;
  heading("设置", "按需调整配置。模型凭据和身份记忆由 AstrBot 管理。");
  const current = snapshot.settings;
  const shell = el("div", undefined, "settings-shell");
  const navigation = el("aside", undefined, "settings-navigation");
  navigation.setAttribute("aria-label", "设置分组");
  const workspace = el("div", undefined, "settings-workspace");
  const form = el("form");
  form.id = `settings-form-${crypto.randomUUID()}`;
  const groups = {};
  const groupButtons = {};
  for (const [id, title, subtitle] of [
    ["model", "常规", "沿用 AstrBot 的 AI 与记忆，设置时间显示偏好。"],
    ["account", "滴答与清单", "管理任务与附件授权，确定允许操作的清单。"],
    ["sessions", "会话授权", "选择哪些 AstrBot 会话可以交付通知和任务。"],
    ["runtime", "材料与运行", "调整材料收集、读取范围和处理时限。"],
    ["data", "数据与维护", "设置本地记录的保留时间，管理授权。"],
  ]) {
    const group = el("section", undefined, "settings-group");
    group.id = `settings-${id}`;
    group.append(el("h2", title), el("p", subtitle, "subtle settings-description"));
    groups[id] = group;
    const select = button(title, () => selectGroup(id), "settings-group-button");
    select.setAttribute("aria-controls", group.id);
    navigation.append(select);
    groupButtons[id] = select;
    workspace.append(group);
  }
  const panel = card("时间偏好");
  const grid = el("div", undefined, "form-grid");
  const inputs = {};
  inputs.timezone = field(grid, "时区", "timezone", {
    value: current.timezone,
  });
  panel.append(grid);
  groups.model.append(panel, card("身份认知", "沿用当前会话与 AstrBot 记忆，无需单独维护身份档案。只在影响当前事项的条件未知时询问。"));
  const budgetInputs = {};
  const budgetLabels = {
    materials: {
      file_bytes: "单原件字节", group_files: "单组原件数", group_bytes: "单组原件字节",
      message_characters: "正文字符数", message_nodes: "消息节点数", document_characters: "单文档读取字符数",
      pdf_pages: "单 PDF 页数", screenshots: "独立截图数", pdf_visuals: "PDF 视觉页数",
      docx_visuals: "DOCX 嵌图数", total_visuals: "全组视觉单元数", image_pixels: "单图像像素数",
      block_characters: "文本块字符数", block_overlap: "相邻文本块重叠字符数", blocks: "单组文本块数",
    },
    time_budgets: {
      read_seconds: "单文档读取秒数",
      group_read_seconds: "同组文件解码／渲染累计秒数",
    },
    retention: {
      body_days: "正文与识别副本保留天数", summary_days: "完成摘要保留天数",
      completed_task_days: "观察到任务完成后的证据保留天数", dedup_minimum_days: "轻量幂等键最短保留天数（当前长期保留）",
    },
  };
  const budgetTitles = { materials: "材料读取预算", time_budgets: "处理时限", retention: "记录保留" };
  for (const [section, labels] of Object.entries(budgetLabels)) {
    if (!current[section]) continue;
    const budget = card(budgetTitles[section]);
    const budgetGrid = el("div", undefined, "form-grid");
    for (const [name, label] of Object.entries(labels)) {
      budgetInputs[`${section}.${name}`] = field(budgetGrid, label, `${section}.${name}`, {
        value: current[section][name], type: "number",
      });
    }
    budget.append(budgetGrid);
    groups[section === "retention" ? "data" : "runtime"].append(budget);
  }
  const projectPanel = card("操作范围", "任务写入与查询只在允许清单内进行。未指定清单时使用默认清单。");
  let projects = [];
  try {
    projects = (await get("projects")).projects;
  } catch {
    projectPanel.append(el("p", "尚未读取真实清单，请先完成任务授权。", "subtle"));
  }
  const defaultProject = field(projectPanel, "默认清单", "default_project", {
    value: current.default_project,
    options: [["", "未选择"], ...projects.map((x) => [x.id, x.name])],
  });
  const allowed = el("fieldset", undefined, "project-choices");
  allowed.append(el("legend", "允许清单"));
  const checkboxes = [];
  for (const project of projects) {
    const label = el("label", undefined, "checkbox");
    const box = el("input");
    box.type = "checkbox";
    box.value = project.id;
    box.checked = current.allowed_projects.includes(project.id);
    label.append(box, document.createTextNode(project.name));
    allowed.append(label);
    checkboxes.push(box);
  }
  projectPanel.append(allowed);
  groups.account.append(projectPanel);
  for (const input of workspace.querySelectorAll("input, select, textarea")) input.setAttribute("form", form.id);
  const save = el("button", "保存配置", "primary");
  save.type = "submit";
  const saveStatus = el("span", "配置已同步", "subtle");
  form.className = "settings-save";
  form.append(saveStatus, save);
  workspace.append(form);
  workspace.addEventListener("input", (event) => {
    if (event.target.getAttribute("form") === form.id) saveStatus.textContent = "有尚未保存的修改";
  });
  function selectGroup(id) {
    settingsGroup = id;
    for (const [key, group] of Object.entries(groups)) {
      group.hidden = key !== id;
      if (key === id) groupButtons[key].setAttribute("aria-current", "page");
      else groupButtons[key].removeAttribute("aria-current");
    }
    form.hidden = id === "sessions";
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(save, async () => {
      const proposed = {
        ...current,
        timezone: inputs.timezone.value.trim(),
        default_project: defaultProject.value || null,
        allowed_projects: checkboxes
          .filter((x) => x.checked)
          .map((x) => x.value),
      };
      for (const [path, input] of Object.entries(budgetInputs)) {
        const [section, name] = path.split(".");
        proposed[section] = { ...proposed[section], [name]: Number(input.value) };
      }
      const response = await post(
        "settings/save",
        { settings: proposed },
        snapshot.revision,
      );
      message(
        `配置已保存；${response.affected_pending_items} 个待执行事项暂停等待重新核验。`,
      );
      await load();
    });
  });
  shell.append(navigation, workspace);
  content.append(shell);
  await identitySettings(snapshot, groups.sessions);
  await authSettings(snapshot, groups.account, groups.data);
  selectGroup(settingsGroup);
}
async function identitySettings(snapshot, target) {
  const panel = card(
    "授权会话",
    "填写框架真实 platform ID、sender ID 和 unified message origin。昵称和转发作者不能授权。",
  );
  panel.classList.add("section");
  const grid = el("div", undefined, "form-grid");
  const platform = field(grid, "框架 platform ID", "platform_id");
  const actor = field(grid, "发起者 sender ID", "actor_id");
  const origin = field(grid, "会话 unified message origin", "origin");
  const enabled = field(grid, "会话状态", "enabled", {
    value: "true",
    options: [
      ["true", "启用"],
      ["false", "撤销授权"],
    ],
  });
  panel.append(
    grid,
    button("绑定 / 更新授权", async () => {
      const result = await post(
        "identity/bind",
        {
          platform_id: platform.value.trim(),
          actor_id: actor.value.trim(),
          origin: origin.value.trim(),
          enabled: enabled.value === "true",
        },
        snapshot.revision,
      );
      message(
        `会话授权已更新；${result.affected_pending_items} 个待执行事项暂停。`,
      );
      await load();
    }),
  );
  for (const binding of snapshot.bindings)
    panel.append(
      el(
        "p",
        `${binding.enabled ? "已启用" : "已撤权"} · actor ${binding.actor_key.slice(0, 12)} · session ${binding.session_key.slice(0, 12)}`,
        "subtle",
      ),
    );
  target.append(panel);
}
async function authSettings(snapshot, target, maintenanceTarget) {
  const grid = el("div", undefined, "grid section");
  for (const kind of ["task", "attachment"]) {
    const panel = card(
      kind === "task" ? "任务授权" : "附件授权",
      kind === "task"
        ? "官方 CLI OAuth 凭据，授权值只写不读。"
        : "同账号网页会话凭据，须以允许清单中的真实任务核验。",
    );
    const secret = field(
      panel,
      kind === "task" ? "Access token" : "网页会话 t",
      "secret",
      { type: "password" },
    );
    secret.autocomplete = "new-password";
    const mode =
      kind === "task"
        ? field(panel, "账号选择", "account_mode", {
            value: snapshot.settings.account_ref ? "same" : "new",
            options: [
              ["same", "同账号重新授权"],
              ["new", "确认更换 / 新账号"],
            ],
          })
        : null;
    const project =
      kind === "attachment"
        ? field(panel, "核验清单 ID", "verification_project")
        : null;
    const task =
      kind === "attachment"
        ? field(panel, "核验任务 ID", "verification_task")
        : null;
    panel.append(
      button(
        "检查并保存授权",
        async () => {
          const value = secret.value;
          secret.value = "";
          const result = await post(
            `auth/${kind}/set`,
            {
              secret: value,
              account_mode: mode?.value || "same",
              ...(project
                ? {
                    verification_project: project.value,
                    verification_task: task.value,
                  }
                : {}),
            },
            snapshot.revision,
          );
          message(`授权已核验并保存。账号作用域 ${result.account_ref}`);
          await load();
        },
        "primary",
      ),
    );
    grid.append(panel);
  }
  target.append(grid);
  const clear = card(
    "清除当前授权",
    "会暂停旧计划、失效任务选择编号；不会删除远端任务。",
  );
  clear.classList.add("section");
  const confirm = field(clear, "输入“清除当前授权”确认", "confirm");
  clear.append(
    button(
      "清除任务与附件授权",
      async () => {
        await post("auth/clear", { confirm: confirm.value }, snapshot.revision);
        message("授权已清除。");
        await load();
      },
      "danger",
    ),
  );
  maintenanceTarget.append(clear);
}

async function load() {
  const ticket = ++generation;
  content.setAttribute("aria-busy", "true");
  content.replaceChildren(el("p", "正在读取…", "loading"));
  try {
    if (!bridge) throw new Error("请从 AstrBot 插件 Pages 打开此页面。");
    await bridge.ready();
    if (ticket !== generation) return;
    content.replaceChildren();
    await { overview, notices, assets, operations, settings }[activeView]();
  } catch (error) {
    if (ticket === generation) {
      content.replaceChildren(el("p", "读取失败，请刷新后重试。", "empty"));
      message(error.message, true);
    }
  } finally {
    if (ticket === generation) content.setAttribute("aria-busy", "false");
  }
}
document.querySelectorAll("[data-view]").forEach((node) =>
  node.addEventListener("click", () => {
    activeView = node.dataset.view;
    document
      .querySelectorAll("[data-view]")
      .forEach((item) => item.toggleAttribute("aria-current", false));
    node.setAttribute("aria-current", "page");
    message("");
    load();
  }),
);
document.querySelector("#refresh").addEventListener("click", () => load());
document
  .querySelector("#close-detail")
  .addEventListener("click", () => dialog.close());
await load();
