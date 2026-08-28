(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const tasks = [
    { id: "P20260826-0012", title: "水稻抗旱相关基因的功能分析", subtitle: "张三，李四，王五 等", kind: "single", kindLabel: "单篇文章", publication: "正式出版", status: "待确认", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-27 09:15", step: 3, note: "科睿已上传成品，等待编辑确认。", doi: "10.3785/j.issn.1008-9209.2026.01.012", processingType: "全文XML", company: "科睿", files: ["水稻抗旱相关基因的功能分析.pdf", "图表高清原图.zip", "水稻抗旱相关基因的功能分析_成品.pdf"] },
    { id: "P20260826-0011", title: "玉米耐盐碱品种筛选研究", subtitle: "赵六，孙七 等", kind: "single", kindLabel: "单篇文章", publication: "优先出版", status: "加工中", handler: "生产公司", handlerDetail: "科睿", updated: "2026-08-27 08:42", step: 2, note: "源文件已锁定，科睿正在加工。", doi: "10.3785/j.issn.1008-9209.2026.01.011", processingType: "全文XML", company: "科睿", locked: true },
    { id: "P20260825-0008", title: "2026 年第 4 期（总第 128 期）整期生产任务", subtitle: "包含 12 篇文章", kind: "issue", kindLabel: "按期", publication: "正式出版", status: "待复审", handler: "管理员", handlerDetail: "初审与复审", updated: "2026-08-26 17:30", step: 4, note: "林编辑确认通过，等待管理员复审。", processingType: "全文XML", company: "科睿", period: "2026 年 · 第 52 卷 · 第 04 期" },
    { id: "P20260825-0007", title: "小麦条锈病抗性基因定位与验证", subtitle: "周八，吴九 等", kind: "single", kindLabel: "单篇文章", publication: "最新录用", status: "待初审", handler: "管理员", handlerDetail: "初审与复审", updated: "2026-08-26 16:05", step: 1, note: "等待管理员初审。" },
    { id: "P20260824-0005", title: "大豆根际微生物多样性分析", subtitle: "郑十，陈十一 等", kind: "multi", kindLabel: "多篇 · 3", publication: "最新录用", status: "已发布", handler: "管理员", handlerDetail: "发布管理", updated: "2026-08-24 11:20", step: 5, note: "已发布至期刊官网与优先出版频道。", processingType: "无需加工", company: "科睿" },
    { id: "P20260824-0003", title: "水生植物重金属富集机制研究", subtitle: "何十二，朱十三 等", kind: "single", kindLabel: "单篇文章", publication: "正式出版", status: "创建", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-24 10:08", step: 0, note: "管理员初审驳回：文章标题与 PDF 首页不一致，请核对后重新提交。", returned: true },
    { id: "P20260823-0001", title: "微生物发酵产物的分离纯化工艺优化", subtitle: "冯十四，韩十五 等", kind: "single", kindLabel: "单篇文章", publication: "优先出版", status: "待确认", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-23 18:22", step: 3, note: "生产公司已上传第二版成品。" },
    { id: "P20260820-0002", title: "2026 年第 3 期（总第 127 期）整期生产任务", subtitle: "包含 10 篇文章", kind: "issue", kindLabel: "按期", publication: "正式出版", status: "已发布", handler: "管理员", handlerDetail: "发布管理", updated: "2026-08-20 15:40", step: 5, note: "整期内容已发布。", period: "2026 年 · 第 52 卷 · 第 03 期" },
    { id: "P20260819-0009", title: "青稞抗逆性转录组学分析", subtitle: "金十六，许十七 等", kind: "single", kindLabel: "单篇文章", publication: "优先出版", status: "加工中", handler: "生产公司", handlerDetail: "科睿", updated: "2026-08-19 14:33", step: 2, note: "源文件已锁定，科睿正在加工。", processingType: "元数据", company: "科睿", locked: true },
    { id: "P20260818-0004", title: "植物源农药活性成分及作用机理研究", subtitle: "罗十八，方十九 等", kind: "single", kindLabel: "单篇文章", publication: "最新录用", status: "待初审", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-18 09:50", step: 1, note: "生产公司退回：附件中的图片分辨率不足。", companyReturned: true },
    { id: "P20260817-0002", title: "农业遥感影像智能识别方法研究等 4 篇", subtitle: "包含 4 篇文章", kind: "multi", kindLabel: "多篇 · 4", publication: "优先出版", status: "待初审", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-17 16:18", step: 1, note: "生产公司退回：第 2 篇文章的 PDF 与 Word 归组错误。", companyReturned: true },
    { id: "P20260816-0008", title: "稻田土壤碳循环关键过程研究", subtitle: "陆二十二，蒋二十三 等", kind: "single", kindLabel: "单篇文章", publication: "正式出版", status: "待确认", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-16 15:22", step: 3, note: "生产公司已上传成品，等待编辑确认。" },
    { id: "P20260815-0006", title: "畜禽养殖废弃物资源化利用技术", subtitle: "韩二十四，杨二十五 等", kind: "single", kindLabel: "单篇文章", publication: "优先出版", status: "创建", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-15 13:40", step: 0, note: "管理员初审驳回：DOI 填写有误，请核对后重新提交。", returned: true },
    { id: "P20260814-0005", title: "茶树病虫害绿色防控技术集成", subtitle: "徐二十六，何二十七 等", kind: "multi", kindLabel: "多篇 · 2", publication: "最新录用", status: "创建", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-14 10:06", step: 0, note: "元数据解析完成，等待编辑确认。" },
    { id: "P20260813-0003", title: "2026 年第 2 期整期补录任务", subtitle: "包含 11 篇文章", kind: "issue", kindLabel: "按期", publication: "正式出版", status: "待初审", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-13 09:18", step: 1, note: "生产公司退回：目录页码与成品不一致。", companyReturned: true },
    { id: "P20260812-0001", title: "设施蔬菜水肥一体化调控研究", subtitle: "许二十八，郑二十九 等", kind: "single", kindLabel: "单篇文章", publication: "优先出版", status: "待确认", handler: "林编辑", handlerDetail: "编辑", updated: "2026-08-12 17:45", step: 3, note: "生产公司已上传修订成品，等待编辑确认。" }
  ];

  const steps = ["创建", "待初审", "加工中", "待确认", "待复审", "已发布"];
  const stepIcons = ["ri-file-add-line", "ri-shield-check-line", "ri-tools-line", "ri-thumb-up-line", "ri-time-line", "ri-send-plane-line"];
  const roleProfiles = {
    editor: {
      name: "林编辑", role: "责任编辑", avatar: "林", title: "我的生产任务", breadcrumb: "我的生产任务",
      subtitle: "查看并处理编辑角色负责的生产任务", column: "当前处理人",
      scopes: { todo: "待我处理", all: "全部任务", progress: "进行中", closed: "已结束" }
    },
    admin: {
      name: "管理员", role: "系统管理员", avatar: "管", title: "生产任务审核", breadcrumb: "审核管理工作台",
      subtitle: "完成初审分发、过程监管与发布复审", column: "生产公司",
      scopes: { todo: "待我审核", all: "全部任务", progress: "生产中", closed: "已发布" }
    },
    company: {
      name: "科睿", role: "生产公司", avatar: "科", title: "加工任务", breadcrumb: "生产公司工作台",
      subtitle: "处理管理员分发的任务并提交生产产物", column: "加工类型",
      scopes: { todo: "待加工", all: "全部任务", progress: "已提交", closed: "已完成" }
    }
  };
  let selectedId = tasks[0].id;
  let currentScope = "todo";
  let currentDetailTab = "records";
  let currentRole = "editor";
  let companyOutputs = [];

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
  }

  function statusClass(task) {
    if (task.returned || task.companyReturned) return "status-returned";
    if (task.status === "待确认" || task.status === "创建") return task.status === "待确认" ? "status-confirm" : "status-draft";
    if (task.status === "加工中") return "status-process";
    if (task.status === "已发布") return "status-published";
    if (task.status === "已下架") return "status-unpublished";
    return "";
  }

  function roleVisible(task) {
    if (currentRole === "editor") return true;
    if (currentRole === "admin") return task.status !== "创建" || task.returned;
    return Boolean(assignmentFor(task)) && task.step >= 2;
  }

  function scopeMatch(task) {
    if (currentScope === "all") return true;
    if (currentRole === "editor") {
      if (currentScope === "todo") return ["创建", "待确认"].includes(task.status) || task.companyReturned;
      if (currentScope === "progress") return ["待初审", "加工中", "待确认", "待复审"].includes(task.status);
      return ["已发布", "已下架"].includes(task.status);
    }
    if (currentRole === "admin") {
      if (currentScope === "todo") return (task.status === "待初审" && task.handler === "管理员" && !task.companyReturned) || task.status === "待复审";
      if (currentScope === "progress") return ["加工中", "待确认"].includes(task.status);
      return ["已发布", "已下架"].includes(task.status);
    }
    if (currentScope === "todo") return task.status === "加工中";
    if (currentScope === "progress") return ["待确认", "待复审"].includes(task.status);
    return ["已发布", "已下架"].includes(task.status);
  }

  function assignmentFor(task) {
    if (task.step < 2 && !task.companyReturned) return null;
    return {
      processingType: task.processingType || (task.publication === "最新录用" ? "元数据" : "全文XML"),
      company: task.company || "科睿"
    };
  }

  function filteredTasks() {
    const query = $("#task-search").value.trim().toLowerCase();
    const kind = $("#kind-filter").value;
    const publication = $("#publication-filter").value;
    const status = $("#status-filter").value;
    return tasks.filter((task) => {
      const haystack = `${task.title} ${task.subtitle} ${task.id} ${task.doi || ""} ${task.period || ""}`.toLowerCase();
      return roleVisible(task) && scopeMatch(task) && (!query || haystack.includes(query)) && (kind === "all" || task.kind === kind) && (publication === "all" || task.publication === publication) && (status === "all" || task.status === status);
    });
  }

  function roleContextMarkup(task) {
    const assignment = assignmentFor(task);
    if (currentRole === "admin") return `<strong>${escapeHtml(assignment?.company || "待指派")}</strong><small>${escapeHtml(assignment?.processingType || "初审后分发")}</small>`;
    if (currentRole === "company") return `<strong>${escapeHtml(assignment?.processingType || "-")}</strong><small>${escapeHtml(task.deadline || "2026-09-03 前")}</small>`;
    return `<strong>${escapeHtml(task.handler)}</strong><small>${escapeHtml(task.handlerDetail)}</small>`;
  }

  function renderTasks() {
    const visibleTasks = filteredTasks();
    $("#task-list").innerHTML = visibleTasks.map((task) => `
      <button class="task-row ${task.id === selectedId ? "selected" : ""}" type="button" data-task-id="${task.id}" aria-label="查看 ${escapeHtml(task.title)}">
        <span class="task-title"><strong>${escapeHtml(task.title)}</strong><small class="task-meta"><span class="task-id-inline">${task.id}</span><em class="task-kind">${escapeHtml(task.kindLabel)}</em>${task.kind === "issue" ? `<span class="task-issue-count">${escapeHtml(task.period || task.subtitle)}</span>` : task.doi ? `<span class="task-doi">DOI ${escapeHtml(task.doi)}</span>` : ""}</small></span>
        <span class="status-cell"><b class="status-badge ${statusClass(task)}">${escapeHtml(task.status)}</b>${task.returned ? '<small class="status-subline">管理员驳回</small>' : task.companyReturned ? '<small class="status-subline">生产公司退回</small>' : task.locked ? '<small class="status-subline locked"><i class="ri-lock-line"></i> 源文件已锁定</small>' : ""}</span>
        <span class="publication-cell"><b class="publication-tag" data-publication="${escapeHtml(task.publication)}">${escapeHtml(task.publication)}</b></span>
        <span class="handler role-context-cell">${roleContextMarkup(task)}</span>
        <span class="updated"><strong>${escapeHtml(task.updated.split(" ")[0])}</strong><small>${escapeHtml(task.updated.split(" ")[1] || "")}</small></span>
      </button>`).join("");
    $("#empty-state").hidden = visibleTasks.length > 0;
    $("#result-count").textContent = `共 ${visibleTasks.length} 条`;
    updateScopeCounts();
  }

  function updateScopeCounts() {
    const savedScope = currentScope;
    $$(".scope-tabs button").forEach((button) => {
      currentScope = button.dataset.scope;
      $("span", button).textContent = tasks.filter((task) => roleVisible(task) && scopeMatch(task)).length;
    });
    currentScope = savedScope;
  }

  function timelineFor(task) {
    const assignment = assignmentFor(task) || { processingType: "全文XML", company: "科睿" };
    const base = [
      { icon: "ri-file-add-line", color: "", time: "2026-08-25 14:05", who: "林编辑", role: "编辑", label: "提交初审", labelColor: "", text: "创建生产任务并提交管理员初审", detail: "初始文件版本：V0.1" },
      { icon: "ri-send-plane-line", color: "green", time: "2026-08-25 16:20", who: "管理员", role: "初审", label: "初审通过并分发", labelColor: "green", text: "管理员完成初审，选择加工类型和生产公司后分发任务", detail: `加工类型：${assignment.processingType} · 生产公司：${assignment.company}` },
      { icon: "ri-building-4-line", color: "blue", time: "2026-08-25 16:22", who: "生产公司", role: assignment.company, label: "接收任务", labelColor: "blue", text: "生产公司收到管理员分发的任务", detail: `加工类型：${assignment.processingType}` },
      { icon: "ri-lock-line", color: "blue", time: "2026-08-26 10:30", who: "生产公司", role: assignment.company, label: "源文件锁定", labelColor: "blue", text: "生产公司接收任务后锁定源文件", detail: "文件版本：V1.0" },
      { icon: "ri-upload-2-line", color: "blue", time: "2026-08-27 08:42", who: "生产公司", role: assignment.company, label: "上传成品", labelColor: "blue", text: "生产公司完成加工并上传产物文件", detail: "文件版本：V1.0" },
      { icon: "ri-thumb-up-line", color: "orange", time: "2026-08-27 09:15", who: "林编辑", role: "编辑", label: "待确认", labelColor: "orange", text: "生产公司已上传成品，等待林编辑确认", detail: "文件版本：V1.0" }
    ];
    if (task.id === "P20260826-0012") return [...base].reverse();
    const wasReturned = task.returned || task.companyReturned;
    const current = {
      icon: task.status === "已发布" ? "ri-check-line" : wasReturned ? "ri-arrow-go-back-line" : "ri-time-line",
      color: task.status === "已发布" ? "green" : wasReturned ? "red" : "blue",
      time: task.updated,
      who: task.companyReturned ? "生产公司" : task.returned ? "管理员" : task.handler,
      role: task.companyReturned ? assignment.company : task.returned ? "初审" : task.handlerDetail,
      label: task.companyReturned ? "退回编辑" : task.returned ? "初审驳回" : task.status,
      labelColor: task.status === "已发布" ? "green" : wasReturned ? "red" : "blue",
      text: task.note,
      detail: task.companyReturned ? `原加工类型：${assignment.processingType} · 重新提交后由管理员复核并分发` : task.locked ? "源文件版本：V1.0 · 已锁定" : "任务记录已更新"
    };
    const historyDepth = task.companyReturned ? 4 : task.step >= 3 ? Math.min(6, task.step + 2) : task.step === 2 ? 3 : Math.max(1, task.step);
    return [current, ...base.slice(0, historyDepth).reverse()];
  }

  function renderFlow(task) {
    $("#flow-track").innerHTML = steps.map((step, index) => `<div class="flow-step ${index < task.step ? "done" : index === task.step ? "current" : ""}"><span class="flow-icon"><i class="${index < task.step ? "ri-check-line" : stepIcons[index]}"></i></span><span>${step}</span></div>`).join("");
  }

  function recordsMarkup(task) {
    return `<div class="timeline">${timelineFor(task).map((item) => `<article class="timeline-item"><span class="timeline-icon ${item.color}"><i class="${item.icon}"></i></span><div class="timeline-who"><time>${item.time}</time><strong>${escapeHtml(item.who)}</strong>${item.role ? `<small>${escapeHtml(item.role)}</small>` : ""}</div><div class="timeline-event"><div><span class="event-label ${item.labelColor}">${escapeHtml(item.label)}</span><p>${escapeHtml(item.text)}</p></div><small>${escapeHtml(item.detail)}</small></div></article>`).join("")}</div>`;
  }

  function overviewMarkup(task) {
    const assignment = assignmentFor(task);
    return `<div class="overview-grid"><div class="overview-item"><span>创建类型</span><strong>${escapeHtml(task.kindLabel)}</strong></div><div class="overview-item"><span>发布类型</span><strong>${escapeHtml(task.publication)}</strong></div><div class="overview-item"><span>当前处理方</span><strong>${escapeHtml(task.handlerDetail)}</strong></div><div class="overview-item"><span>最近更新</span><strong>${escapeHtml(task.updated)}</strong></div><div class="overview-item"><span>加工类型</span><strong>${escapeHtml(assignment?.processingType || "待管理员初审后选择")}</strong></div><div class="overview-item"><span>生产公司</span><strong>${escapeHtml(assignment?.company || "待管理员初审后指派")}</strong></div><div class="overview-item"><span>DOI</span><strong>${escapeHtml(task.doi || "未填写")}</strong></div><div class="overview-item"><span>期次</span><strong>${escapeHtml(task.period || "非整期任务")}</strong></div></div><div class="detail-note"><strong>当前说明：</strong>${escapeHtml(task.note)}</div>`;
  }

  function filesMarkup(task) {
    const files = task.files || [task.kind === "issue" ? "zjdxny_2026_v52_n04.zip" : `${task.title}.pdf`, task.kind === "issue" ? "cover_2026_v52_n04.jpg" : "作者信息确认表.docx"];
    return `<section class="file-section"><h3>源文件与产物</h3>${files.map((name, index) => { const ext = name.split(".").pop().toLowerCase(); return `<div class="file-card"><span class="file-icon ${ext === "xml" ? "xml" : ext === "zip" ? "zip" : "pdf"}">${escapeHtml(ext)}</span><span><strong>${escapeHtml(name)}</strong><small>${index === files.length - 1 && task.status === "待确认" ? "生产成品 · V1.0" : "源文件 · V1.0"}</small></span><button type="button">${index === files.length - 1 ? "预览" : "下载"}</button></div>`; }).join("")}</section>`;
  }

  function editorActionFor(task) {
    if (task.status === "创建") return task.returned ? { type: "revision", label: "处理驳回" } : { type: "edit", label: "继续编辑" };
    if (task.status === "待初审") {
      if (task.companyReturned) return { type: "revision", label: "处理退回" };
      return { type: "withdraw", label: "撤回任务" };
    }
    if (task.status === "待确认") return { type: "none", label: "" };
    if (task.status === "待复审") return { type: "preview", label: "网页预览" };
    return { type: "none", label: "" };
  }

  function roleActionFor(task) {
    if (currentRole === "editor") return editorActionFor(task);
    if (currentRole === "admin") {
      if (task.status === "待初审" && task.handler === "管理员" && !task.companyReturned) return { type: "initial-review", label: "初审并分发", emphasis: "primary" };
      if (task.status === "待复审") return { type: "final-review", label: "复审发布", emphasis: "primary" };
      if (task.status === "已发布") return { type: "unpublish", label: "下架", emphasis: "danger" };
      return { type: "none", label: "", emphasis: "secondary" };
    }
    if (task.status === "加工中") return { type: "company-process", label: "进入加工", emphasis: "primary" };
    return { type: "none", label: "", emphasis: "secondary" };
  }

  function renderDetail() {
    const task = tasks.find((item) => item.id === selectedId) || tasks[0];
    $("#detail-title").textContent = task.title;
    $("#detail-id").textContent = task.id;
    $("#detail-status").className = `status-badge ${statusClass(task)}`;
    $("#detail-status").textContent = task.status;
    $("#detail-handler").textContent = `${task.handler}（${task.handlerDetail}）`;
    renderFlow(task);
    $("#detail-content").innerHTML = currentDetailTab === "records" ? recordsMarkup(task) : currentDetailTab === "overview" ? overviewMarkup(task) : filesMarkup(task);
    $$(".detail-tabs button").forEach((button) => button.classList.toggle("active", button.dataset.detailTab === currentDetailTab));
    const canReview = currentRole === "editor" && task.status === "待确认";
    const action = roleActionFor(task);
    const hasAction = action.type !== "none";
    $("#review-product-button").hidden = !canReview;
    $("#full-detail-button").hidden = !hasAction;
    $("#full-detail-button").textContent = action.label;
    $("#full-detail-button").dataset.roleAction = action.type;
    $("#full-detail-button").className = action.emphasis === "primary" ? "primary-button" : action.emphasis === "danger" ? "danger-button" : "secondary-button";
    $(".detail-actions").hidden = !canReview && !hasAction;
    $("#detail-panel").classList.toggle("no-actions", !canReview && !hasAction);
  }

  function selectTask(id) {
    selectedId = id;
    $("#task-workspace").classList.remove("detail-closed");
    renderTasks();
    renderDetail();
  }

  function applyFilters() {
    const visible = filteredTasks();
    if (visible.length && !visible.some((task) => task.id === selectedId)) selectedId = visible[0].id;
    renderTasks();
    if (visible.length) renderDetail();
  }

  function resetFilters() {
    $("#task-search").value = "";
    $("#kind-filter").value = "all";
    $("#publication-filter").value = "all";
    $("#status-filter").value = "all";
    applyFilters();
  }

  function applyRole(role) {
    currentRole = role;
    currentScope = "todo";
    currentDetailTab = "records";
    const profile = roleProfiles[role];
    document.body.dataset.role = role;
    document.title = `${profile.title} · 刊行`;
    $("#current-avatar").textContent = profile.avatar;
    $("#current-user-name").textContent = profile.name;
    $("#current-user-role").textContent = profile.role;
    $("#breadcrumb-role").textContent = profile.breadcrumb;
    $("#page-title").textContent = profile.title;
    $("#page-subtitle").textContent = profile.subtitle;
    $("#role-column-title").textContent = profile.column;
    $("#create-task-link").hidden = role !== "editor";
    $$(".scope-tabs button").forEach((button) => {
      $("b", button).textContent = profile.scopes[button.dataset.scope];
      button.classList.toggle("active", button.dataset.scope === "todo");
    });
    $$("#role-switcher [data-role]").forEach((button) => button.classList.toggle("active", button.dataset.role === role));
    $("#role-switcher").hidden = true;
    $("#user-menu").setAttribute("aria-expanded", "false");
    $("#task-workspace").classList.add("detail-closed");
    resetFilters();
    const first = filteredTasks()[0];
    if (first) selectedId = first.id;
    renderTasks();
    if (first) renderDetail();
  }

  function toast(message, error = false) {
    const item = document.createElement("div");
    item.className = "toast";
    item.innerHTML = `<i class="${error ? "ri-error-warning-line" : "ri-checkbox-circle-line"}"></i><span>${escapeHtml(message)}</span>`;
    $("#toast-region").append(item);
    setTimeout(() => item.remove(), 2600);
  }

  function selectedTask() {
    return tasks.find((item) => item.id === selectedId);
  }

  function openModal(id) {
    $(`#${id}`).hidden = false;
  }

  function closeModal(id) {
    $(`#${id}`).hidden = true;
  }

  function refreshAfterChange() {
    const visible = filteredTasks();
    if (visible.length && !visible.some((task) => task.id === selectedId)) selectedId = visible[0].id;
    renderTasks();
    if (visible.length) renderDetail();
    else $("#task-workspace").classList.add("detail-closed");
  }

  function openInitialReview(task) {
    $("#admin-review-task").textContent = `${task.title} · ${task.id}`;
    $("#processing-type").value = task.publication === "最新录用" ? "元数据" : "全文XML";
    $("#production-company").value = "科睿";
    $("#admin-review-note").value = "";
    openModal("admin-review-modal");
  }

  function openFinalReview(task) {
    $("#final-review-task").textContent = `${task.title} · ${task.id}`;
    $("#final-review-note").value = "";
    openModal("final-review-modal");
  }

  function renderCompanyOutputs() {
    $("#company-output-list").innerHTML = companyOutputs.map((file, index) => {
      const ext = file.name.split(".").pop().toLowerCase();
      return `<div class="output-file"><span class="file-icon ${ext === "xml" ? "xml" : ext === "zip" ? "zip" : "pdf"}">${escapeHtml(ext)}</span><span><strong>${escapeHtml(file.name)}</strong><small>${escapeHtml(file.size || "示例文件")} · 待提交</small></span><button type="button" data-remove-output="${index}" aria-label="移除 ${escapeHtml(file.name)}"><i class="ri-close-line"></i></button></div>`;
    }).join("");
    $("#company-submit-button").disabled = companyOutputs.length === 0;
  }

  function openCompanyWorkbench(task) {
    const assignment = assignmentFor(task);
    companyOutputs = [...(task.outputs || [])];
    $("#company-processing-type").textContent = assignment.processingType;
    $("#company-deadline").textContent = task.deadline || "2026-09-03";
    $("#company-task-id").textContent = task.id;
    $("#company-work-note").value = "";
    const sources = task.files?.filter((file) => !file.includes("成品")) || [task.kind === "issue" ? "zjdxny_2026_v52_n04.zip" : `${task.title}.pdf`, "附件材料.zip"];
    $("#company-source-files").innerHTML = sources.map((name) => `<div class="source-file"><i class="ri-file-download-line"></i><span><strong>${escapeHtml(name)}</strong><small>源文件 · 已锁定</small></span><button type="button" data-download-source>下载</button></div>`).join("");
    renderCompanyOutputs();
    openModal("company-work-modal");
  }

  $$(".scope-tabs button").forEach((button) => button.addEventListener("click", () => {
    currentScope = button.dataset.scope;
    $$(".scope-tabs button").forEach((item) => item.classList.toggle("active", item === button));
    applyFilters();
  }));
  ["#task-search", "#kind-filter", "#publication-filter", "#status-filter"].forEach((selector) => $(selector).addEventListener(selector === "#task-search" ? "input" : "change", applyFilters));
  $("#more-filter-button").addEventListener("click", () => { $("#more-filter-popover").hidden = !$("#more-filter-popover").hidden; });
  $("#clear-filter-button").addEventListener("click", () => { resetFilters(); $("#more-filter-popover").hidden = true; });
  $("#empty-state button").addEventListener("click", resetFilters);
  $("#task-list").addEventListener("click", (event) => { const row = event.target.closest("[data-task-id]"); if (row) selectTask(row.dataset.taskId); });
  $("#close-detail-button").addEventListener("click", () => $("#task-workspace").classList.add("detail-closed"));
  $("#copy-task-id").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("#detail-id").textContent); toast("任务编号已复制"); } catch { toast("未能复制任务编号", true); } });
  $$(".detail-tabs button").forEach((button) => button.addEventListener("click", () => { currentDetailTab = button.dataset.detailTab; renderDetail(); }));

  $("#user-menu").addEventListener("click", (event) => {
    event.stopPropagation();
    const switcher = $("#role-switcher");
    switcher.hidden = !switcher.hidden;
    $("#user-menu").setAttribute("aria-expanded", String(!switcher.hidden));
  });
  $$("#role-switcher [data-role]").forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    applyRole(button.dataset.role);
    toast(`已切换至${roleProfiles[button.dataset.role].title}`);
  }));
  document.addEventListener("click", () => { $("#role-switcher").hidden = true; $("#user-menu").setAttribute("aria-expanded", "false"); });

  $$('[data-close-modal]').forEach((button) => button.addEventListener("click", () => closeModal(button.dataset.closeModal)));
  $$(".modal-backdrop").forEach((backdrop) => backdrop.addEventListener("click", (event) => { if (event.target === backdrop) backdrop.hidden = true; }));
  $("#review-product-button").addEventListener("click", () => { $("#review-note").value = ""; openModal("review-modal"); });
  $("#close-review-button").addEventListener("click", () => closeModal("review-modal"));

  $("#approve-product-button").addEventListener("click", () => {
    const task = selectedTask();
    task.status = "待复审"; task.step = 4; task.handler = "管理员"; task.handlerDetail = "初审与复审"; task.updated = "2026-08-28 10:18"; task.note = "林编辑确认成品通过，等待管理员复审。";
    closeModal("review-modal"); refreshAfterChange(); toast("成品已确认，任务进入待复审");
  });
  $("#return-product-button").addEventListener("click", () => {
    const note = $("#review-note").value.trim();
    if (!note) { $("#review-note").focus(); toast("退回加工前请填写具体原因", true); return; }
    const task = selectedTask();
    task.status = "加工中"; task.step = 2; task.handler = "生产公司"; task.handlerDetail = task.company || "科睿"; task.updated = "2026-08-28 10:18"; task.note = `林编辑退回加工：${note}`; task.locked = true;
    closeModal("review-modal"); refreshAfterChange(); toast("已退回生产公司重新加工");
  });

  $("#admin-dispatch-button").addEventListener("click", () => {
    const task = selectedTask();
    task.processingType = $("#processing-type").value; task.company = $("#production-company").value; task.deadline = $("#processing-deadline").value;
    task.status = "加工中"; task.step = 2; task.handler = "生产公司"; task.handlerDetail = task.company; task.updated = "2026-08-28 10:30"; task.locked = true; task.returned = false; task.companyReturned = false;
    task.note = `管理员初审通过，已按“${task.processingType}”分发至${task.company}。`;
    closeModal("admin-review-modal"); refreshAfterChange(); toast("初审通过，任务已分发给生产公司");
  });
  $("#admin-reject-button").addEventListener("click", () => {
    const note = $("#admin-review-note").value.trim();
    if (!note) { $("#admin-review-note").focus(); toast("驳回前请填写具体原因", true); return; }
    const task = selectedTask();
    task.status = "创建"; task.step = 0; task.handler = "林编辑"; task.handlerDetail = "编辑"; task.updated = "2026-08-28 10:30"; task.returned = true; task.companyReturned = false; task.note = `管理员初审驳回：${note}`;
    closeModal("admin-review-modal"); refreshAfterChange(); toast("任务已驳回编辑修改");
  });

  $("#publish-button").addEventListener("click", () => {
    const channels = $$('input[name="publish-channel"]:checked').map((input) => input.value);
    if (!channels.length) { toast("请至少选择一个发布渠道", true); return; }
    const task = selectedTask();
    task.status = "已发布"; task.step = 5; task.handler = "管理员"; task.handlerDetail = "发布管理"; task.updated = "2026-08-28 10:40"; task.publishChannels = channels; task.note = `管理员复审通过，已发布至${channels.join("、")}。`;
    closeModal("final-review-modal"); refreshAfterChange(); toast("复审通过，任务已发布");
  });
  $("#final-reject-button").addEventListener("click", () => {
    const note = $("#final-review-note").value.trim();
    if (!note) { $("#final-review-note").focus(); toast("驳回前请填写具体原因", true); return; }
    const task = selectedTask();
    const target = $("#final-reject-target").value;
    if (target === "加工中") { task.step = 2; task.handler = "生产公司"; task.handlerDetail = task.company || "科睿"; task.locked = true; }
    else if (target === "待确认") { task.step = 3; task.handler = "林编辑"; task.handlerDetail = "编辑"; }
    else { task.step = 0; task.handler = "林编辑"; task.handlerDetail = "编辑"; task.returned = true; task.locked = false; }
    task.status = target; task.updated = "2026-08-28 10:40"; task.note = `管理员复审驳回：${note}`;
    closeModal("final-review-modal"); refreshAfterChange(); toast(`任务已驳回至${target}`);
  });

  $("#load-output-demo").addEventListener("click", () => {
    const task = selectedTask();
    const stem = task.kind === "issue" ? "2026年第4期" : task.title;
    companyOutputs = [{ name: `${stem}_成品.pdf`, size: "12.8 MB" }, { name: `${stem}.xml`, size: "184 KB" }];
    renderCompanyOutputs();
  });
  $("#company-output-input").addEventListener("change", (event) => {
    companyOutputs = [...event.target.files].map((file) => ({ name: file.name, size: `${Math.max(.1, file.size / 1024 / 1024).toFixed(1)} MB` }));
    renderCompanyOutputs();
  });
  $("#company-output-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-output]");
    if (!button) return;
    companyOutputs.splice(Number(button.dataset.removeOutput), 1); renderCompanyOutputs();
  });
  $("#company-source-files").addEventListener("click", (event) => { if (event.target.closest("[data-download-source]")) toast("源文件开始下载（Mock）"); });
  $("#company-submit-button").addEventListener("click", () => {
    const task = selectedTask();
    task.outputs = [...companyOutputs]; task.files = [...(task.files || [`${task.title}.pdf`]), ...companyOutputs.map((file) => file.name)];
    task.status = "待确认"; task.step = 3; task.handler = "林编辑"; task.handlerDetail = "编辑"; task.updated = "2026-08-28 11:05"; task.note = $("#company-work-note").value.trim() || "生产公司已完成加工并提交产物，等待林编辑确认。"; task.companyReturned = false;
    closeModal("company-work-modal"); refreshAfterChange(); toast("产物已提交编辑确认");
  });
  $("#company-return-button").addEventListener("click", () => {
    const note = $("#company-work-note").value.trim();
    if (!note) { $("#company-work-note").focus(); toast("退回编辑前请填写具体原因", true); return; }
    const task = selectedTask();
    task.status = "待初审"; task.step = 1; task.handler = "林编辑"; task.handlerDetail = "编辑"; task.updated = "2026-08-28 11:05"; task.note = `生产公司退回：${note}`; task.companyReturned = true; task.locked = false;
    closeModal("company-work-modal"); refreshAfterChange(); toast("任务已退回编辑处理");
  });

  $("#confirm-unpublish-button").addEventListener("click", () => {
    const note = $("#unpublish-note").value.trim();
    if (!note) { $("#unpublish-note").focus(); toast("下架前请填写原因", true); return; }
    const task = selectedTask();
    task.status = "已下架"; task.handler = "管理员"; task.handlerDetail = "发布管理"; task.updated = "2026-08-28 11:20"; task.note = `管理员下架：${note}`;
    closeModal("unpublish-modal"); refreshAfterChange(); toast("任务已下架并终止");
  });

  $("#full-detail-button").addEventListener("click", () => {
    const task = selectedTask();
    const action = roleActionFor(task);
    if (action.type === "revision") { window.location.href = `/static/production-task.html?mode=revision&task=${encodeURIComponent(task.id)}`; return; }
    if (action.type === "withdraw") {
      task.status = "创建"; task.step = 0; task.handler = "林编辑"; task.handlerDetail = "编辑"; task.updated = "2026-08-28 11:30"; task.note = "林编辑撤回任务，待修改后重新发起。"; task.returned = false; task.companyReturned = false;
      refreshAfterChange(); toast("任务已撤回，状态变为创建"); return;
    }
    if (action.type === "preview") { toast("已打开网页预览（Mock）"); return; }
    if (action.type === "initial-review") { openInitialReview(task); return; }
    if (action.type === "final-review") { openFinalReview(task); return; }
    if (action.type === "company-process") { openCompanyWorkbench(task); return; }
    if (action.type === "unpublish") { $("#unpublish-note").value = ""; openModal("unpublish-modal"); return; }
    if (action.type === "edit") toast("已进入任务编辑模式（Mock）");
  });
  $$(".pagination button:not([aria-label])").forEach((button) => button.addEventListener("click", () => { $$(".pagination button:not([aria-label])").forEach((item) => item.classList.toggle("active", item === button)); toast(`已切换到第 ${button.textContent} 页（Mock）`); }));

  applyRole("editor");
})();
