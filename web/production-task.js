(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const typeLabels = { formal: "正式出版", online: "优先出版", accepted: "最新录用" };
  const typeDescriptions = {
    formal: "正式出版文件 · 加工审核后上线",
    online: "终稿文件 · 优先加工审核上线",
    accepted: "完整文章信息 · 审核后即时上线"
  };
  const routeParams = new URLSearchParams(window.location.search);
  const revisionMode = routeParams.get("mode") === "revision";
  const revisionContexts = {
    "P20260824-0003": {
      source: "reject", sourceLabel: "管理员驳回", kind: "single", publication: "formal", step: 3,
      taskTitle: "水生植物重金属富集机制研究", articleTitle: "水生植物重金属富集机制",
      doi: "10.3785/j.issn.1008-9209.2026.01.003",
      reason: "文章标题与 PDF 首页不一致，请核对后重新提交。"
    },
    "P20260815-0006": {
      source: "reject", sourceLabel: "管理员驳回", kind: "single", publication: "online", step: 3,
      taskTitle: "畜禽养殖废弃物资源化利用技术", articleTitle: "畜禽养殖废弃物资源化利用技术",
      doi: "10.3785/j.issn.1008-9209.2026.01.156",
      reason: "DOI 填写有误，请核对后重新提交。"
    },
    "P20260818-0004": {
      source: "return", sourceLabel: "生产公司退回", kind: "single", publication: "accepted", step: 3,
      taskTitle: "植物源农药活性成分及作用机理研究", articleTitle: "植物源农药活性成分及作用机理研究",
      doi: "10.3785/j.issn.1008-9209.2026.01.004",
      reason: "附件中的图片分辨率不足，请重新上传清晰版本。"
    },
    "P20260817-0002": {
      source: "return", sourceLabel: "生产公司退回", kind: "multi", publication: "online", step: 3,
      taskTitle: "农业遥感影像智能识别方法研究等 4 篇",
      articleTitles: ["农业遥感影像智能识别方法研究", "农田冠层光谱特征提取方法", "多源遥感数据融合的作物分类", "无人机影像地块边界识别"],
      reason: "第 2 篇文章的 PDF 与 Word 归组错误，请调整正文文件归属后重新提交。"
    },
    "P20260813-0003": {
      source: "return", sourceLabel: "生产公司退回", kind: "issue", publication: "formal", step: 3,
      taskTitle: "2026 年第 2 期整期补录任务",
      reason: "目录页码与成品不一致，请重新上传正确的目录文件。"
    }
  };
  const revisionTaskId = routeParams.get("task") || "P20260824-0003";
  const revisionContext = revisionMode ? (revisionContexts[revisionTaskId] || revisionContexts["P20260824-0003"]) : null;
  let publicationType = "formal";
  let formalImportMode = "issue";
  let activeAttachmentArticleId = null;
  let idSeed = 20;
  let currentStep = 1;
  let maxReachedStep = 1;

  const ftpPackages = [
    { id: "pkg-1", name: "zjdxny_2026_v52_n04.zip", size: "286 MB", updated: "2026-08-26 09:30", path: "/正式出版/整期成品", status: "可导入" },
    { id: "pkg-2", name: "zjdxny_2026_v52_n03.zip", size: "264 MB", updated: "2026-07-18 16:12", path: "/正式出版/整期成品", status: "已导入", unavailable: true },
    { id: "pkg-3", name: "zjdxny_2025_v51_n06.zip", size: "241 MB", updated: "2026-01-08 11:42", path: "/正式出版/历史成品", status: "可导入" }
  ];
  const journalNames = {
    zjdxny: "浙江大学学报（农业与生命科学版）"
  };
  let issueEntrySeed = 30;
  let activeIssueFileTarget = null;
  let issueEntries = [
    {
      id: "issue-pkg-1", packageId: "pkg-1", source: "ftp", name: "zjdxny_2026_v52_n04.zip", size: "286 MB", updated: "2026-08-26 09:30",
      period: { journal: "浙江大学学报（农业与生命科学版）", year: "2026", volume: "52", number: "04" },
      files: { cover: { name: "cover_2026_v52_n04.pdf", size: "8.6 MB" }, contents: { name: "contents_2026_v52_n04.pdf", size: "1.4 MB" } }
    },
    {
      id: "issue-pkg-3", packageId: "pkg-3", source: "ftp", name: "zjdxny_2025_v51_n06.zip", size: "241 MB", updated: "2026-01-08 11:42",
      period: { journal: "浙江大学学报（农业与生命科学版）", year: "2025", volume: "51", number: "06" },
      files: { cover: { name: "cover_2025_v51_n06.pdf", size: "7.9 MB" }, contents: { name: "contents_2025_v51_n06.pdf", size: "1.2 MB" } }
    },
    {
      id: "issue-local-31", source: "local", name: "zjdxny_2026_v52_n05.zip", size: "273 MB", updated: "刚刚上传",
      period: { journal: "浙江大学学报（农业与生命科学版）", year: "2026", volume: "52", number: "05" },
      files: { cover: { name: "cover_2026_v52_n05.jpg", size: "6.8 MB" }, contents: { name: "contents_2026_v52_n05.pdf", size: "1.3 MB" } }
    }
  ];

  const demoArticles = [
    {
      id: "a1",
      title: "数字农业背景下农户技术采纳行为及影响因素研究",
      doi: "10.3785/j.issn.1008-9209.2026.01.001",
      confidence: 98,
      parseStatus: "success",
      metadata: {
        titleEn: "Farmers' Technology Adoption and Its Influencing Factors in Digital Agriculture",
        authorsZh: "周明远，陈思雨，王立新",
        authorsEn: "ZHOU Mingyuan, CHEN Siyu, WANG Lixin",
        keywordsZh: "数字农业；技术采纳；农户行为；结构方程模型",
        keywordsEn: "digital agriculture; technology adoption; farmer behavior; SEM",
        abstractZh: "基于长三角地区 862 份农户调查数据，构建结构方程模型，分析数字农业背景下农户技术采纳行为的形成机制与关键影响因素。",
        abstractEn: "Based on survey data from 862 farmers in the Yangtze River Delta, this study examines the mechanism and key factors of technology adoption in digital agriculture."
      },
      files: [
        { id: "f1", name: "数字农业背景下农户技术采纳行为及影响因素研究.pdf", ext: "pdf", size: "3.8 MB", state: "已识别" },
        { id: "f2", name: "数字农业背景下农户技术采纳行为及影响因素研究.docx", ext: "docx", size: "1.2 MB", state: "已归组" },
        { id: "f3", name: "数字农业背景下农户技术采纳行为及影响因素研究.xml", ext: "xml", size: "286 KB", state: "已归组" }
      ],
      attachments: [{ id: "x1", name: "图表高清原图.zip", size: "12.4 MB", note: "图 1—图 6 原始文件", cited: true }]
    },
    {
      id: "a2",
      title: "水稻籽粒镉积累的遗传调控机制与育种策略",
      doi: "10.3785/j.issn.1008-9209.2026.01.002",
      confidence: 84,
      parseStatus: "failed",
      metadata: {
        titleEn: "Genetic Regulation and Breeding Strategies for Cadmium Accumulation in Rice Grain",
        authorsZh: "李青，赵楠",
        authorsEn: "LI Qing, ZHAO Nan",
        keywordsZh: "水稻；镉积累；遗传调控；分子育种",
        keywordsEn: "rice; cadmium accumulation; genetic regulation; molecular breeding",
        abstractZh: "本文综述水稻籽粒镉积累相关转运蛋白与调控网络的研究进展，并讨论低镉水稻分子育种的可行路径。",
        abstractEn: ""
      },
      files: [
        { id: "f4", name: "水稻籽粒镉积累的遗传调控机制与育种策略.pdf", ext: "pdf", size: "5.1 MB", state: "已识别" },
        { id: "f5", name: "水稻籽粒镉积累_排版稿.tex", ext: "tex", size: "94 KB", state: "需确认", warning: true },
        { id: "f6", name: "水稻籽粒镉积累_参考文献.xml", ext: "xml", size: "61 KB", state: "已归组" }
      ],
      attachments: [{ id: "x2", name: "作者信息确认表.docx", size: "84 KB", note: "通讯作者已确认", cited: false }]
    }
  ];

  let articles = structuredClone(demoArticles);

  const articleList = $("#article-list");
  const bodyFileInput = $("#body-file-input");
  const attachmentInput = $("#attachment-input");
  const dropzone = $("#dropzone");
  const recognitionStatus = $("#recognition-status");
  const coverFileInput = $("#cover-file-input");
  const contentsFileInput = $("#contents-file-input");
  const localIssueInput = $("#local-issue-input");

  function applyRevisionMode() {
    if (!revisionMode || !revisionContext) return;
    document.body.classList.add("revision-mode");
    document.title = `修订生产任务 · ${revisionTaskId}`;
    publicationType = revisionContext.publication;
    formalImportMode = revisionContext.kind === "issue" ? "issue" : "articles";
    maxReachedStep = 4;

    if (revisionContext.kind === "issue") {
      issueEntries = [{
        id: "revision-issue-2", source: "ftp", name: "zjdxny_2026_v52_n02.zip", size: "258 MB", updated: "2026-08-13 08:52",
        period: { journal: "浙江大学学报（农业与生命科学版）", year: "2026", volume: "52", number: "02" },
        files: { cover: { name: "cover_2026_v52_n02.jpg", size: "7.2 MB" }, contents: null }
      }];
    } else {
      const titles = revisionContext.articleTitles || [revisionContext.articleTitle];
      articles = titles.map((title, index) => {
        const article = structuredClone(demoArticles[index % demoArticles.length]);
        const misplaced = revisionContext.kind === "multi" && index === 1;
        article.id = `revision-article-${index + 1}`;
        article.title = title;
        article.doi = revisionContext.doi || `10.3785/j.issn.1008-9209.2026.01.${String(index + 31).padStart(3, "0")}`;
        article.confidence = misplaced ? 72 : 96;
        article.files = [
          { id: `revision-pdf-${index + 1}`, name: `${title}.pdf`, ext: "pdf", size: "4.6 MB", state: "已识别" },
          { id: `revision-docx-${index + 1}`, name: `${misplaced ? titles[0] : title}.docx`, ext: "docx", size: "1.1 MB", state: misplaced ? "需调整归组" : "已归组", warning: misplaced }
        ];
        article.attachments = [];
        return article;
      });
    }

    $("#breadcrumb-current").textContent = "修订生产任务";
    $("#page-kicker").textContent = "TASK REVISION";
    $("#page-title").textContent = "修订生产任务";
    $("#page-description").textContent = `修改 ${revisionTaskId} 的问题项，未受影响的信息将继续保留。`;
    $("#step-4-title").textContent = "确认修订";
    $("#step-4-copy").textContent = "核对并重新提交";
    $(".process-track").setAttribute("aria-label", "修订生产任务流程");
    $("#save-button").textContent = "保存修订";
    if (revisionContext.kind === "issue") {
      $("#ftp-search-input").disabled = true;
      $("#refresh-ftp-button").disabled = true;
      $("#local-issue-button").disabled = true;
    }

    const banner = $("#revision-banner");
    banner.hidden = false;
    banner.classList.toggle("is-return", revisionContext.source === "return");
    $("#revision-source").textContent = revisionContext.sourceLabel;
    $("#revision-banner-title").textContent = revisionContext.taskTitle;
    $("#revision-reason").textContent = revisionContext.reason;
    $("#revision-task-id").textContent = revisionTaskId;

    $$('input[name="publication"]').forEach((radio) => {
      radio.checked = radio.value === publicationType;
      radio.disabled = true;
      radio.closest(".publication-card").classList.toggle("selected", radio.checked);
      radio.closest(".publication-card").classList.add("revision-locked");
    });
    $$('input[name="formal-mode"]').forEach((radio) => {
      radio.checked = radio.value === formalImportMode;
      radio.disabled = true;
      radio.closest(".import-mode-option").classList.toggle("selected", radio.checked);
      radio.closest(".import-mode-option").classList.add("revision-locked");
    });
  }

  function isIssueMode() {
    return publicationType === "formal" && formalImportMode === "issue";
  }

  function issueEntry(id) {
    return issueEntries.find((item) => item.id === id) || null;
  }

  function ftpEntry(packageId) {
    return issueEntries.find((item) => item.packageId === packageId) || null;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
  }

  function extension(name) {
    const ext = name.split(".").pop().toLowerCase();
    return ext === "doc" ? "doc" : ext === "docx" ? "docx" : ext;
  }

  function humanSize(bytes) {
    if (!bytes) return "—";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
  }

  function parseIssueFilename(name) {
    const match = String(name || "").match(/^(.+?)_(\d{4})_v(\d+)_n(\d+)\.zip$/i);
    if (!match) return null;
    const journalCode = match[1].toLowerCase();
    return { journal: journalNames[journalCode] || match[1], year: match[2], volume: match[3], number: match[4] };
  }

  function renderFtpFiles(filter = "") {
    const needle = filter.trim().toLowerCase();
    const matches = ftpPackages.filter((item) => item.name.toLowerCase().includes(needle));
    $("#ftp-file-list").innerHTML = matches.length ? matches.map((item) => `
      <label class="ftp-file-option ${ftpEntry(item.id) ? "selected" : ""} ${item.unavailable ? "unavailable" : ""}" data-package-id="${item.id}">
        <input type="checkbox" name="issue-package" value="${item.id}" ${ftpEntry(item.id) ? "checked" : ""} ${item.unavailable || revisionMode ? "disabled" : ""} />
        <span class="ftp-file-radio"></span>
        <span class="ftp-file-name"><span class="zip-mark">ZIP</span><span><strong>${escapeHtml(item.name)}</strong></span></span>
        <span class="ftp-file-meta">${escapeHtml(item.size)}</span>
        <span class="ftp-file-meta">${escapeHtml(item.updated)}</span>
        <span class="ftp-file-status ${item.unavailable ? "used" : ""}">${escapeHtml(item.status)}</span>
      </label>`).join("") : '<div class="ftp-empty">没有找到匹配的压缩包，请更换关键词。</div>';
  }

  function issuePeriodComplete(entry) {
    return ["journal", "year", "volume", "number"].every((key) => String(entry.period?.[key] || "").trim());
  }

  function createFtpIssueEntry(pkg) {
    const period = parseIssueFilename(pkg.name) || { journal: "", year: "", volume: "", number: "" };
    const suffix = `${period.year || "issue"}_v${period.volume || "x"}_n${period.number || "x"}`;
    return {
      id: `issue-${pkg.id}-${++issueEntrySeed}`, packageId: pkg.id, source: "ftp", name: pkg.name, size: pkg.size, updated: pkg.updated, period,
      files: { cover: { name: `cover_${suffix}.pdf`, size: "8.0 MB" }, contents: { name: `contents_${suffix}.pdf`, size: "1.2 MB" } }
    };
  }

  function renderIssueParse() {
    const panel = $("#issue-parse-panel");
    const invalidCount = issueEntries.filter((entry) => !issuePeriodComplete(entry)).length;
    const valid = issueEntries.length > 0 && invalidCount === 0;
    panel.classList.toggle("parse-error", !valid);
    $(".parse-result-heading > span", panel).textContent = valid ? "✓" : "!";
    $("#parse-result-title").textContent = !issueEntries.length ? "尚未选择整期文件" : invalidCount ? `${invalidCount} 期需要补充信息` : `已解析 ${issueEntries.length} 期`;
    $("#parse-source-name").textContent = !issueEntries.length ? "请从 FTP 选择或从本地上传" : invalidCount ? "请补充标记为空的期次信息" : "期刊与年卷期已自动填写";
    $("#issue-parse-list").innerHTML = issueEntries.length ? issueEntries.map((entry) => `
      <div class="issue-parse-row" data-entry-id="${entry.id}">
        <div class="parse-file-info"><span class="source-badge ${entry.source}">${entry.source === "ftp" ? "FTP" : "本地"}</span><span><strong title="${escapeHtml(entry.name)}">${escapeHtml(entry.name)}</strong><small>${escapeHtml(entry.size)}</small></span></div>
        <div class="issue-fields">
          <label class="journal-field"><span>期刊</span><input data-period-key="journal" class="${entry.period.journal ? "" : "invalid"}" value="${escapeHtml(entry.period.journal)}" ${revisionMode ? "disabled" : ""} /></label>
          <label><span>年份</span><input data-period-key="year" class="${entry.period.year ? "" : "invalid"}" inputmode="numeric" value="${escapeHtml(entry.period.year)}" ${revisionMode ? "disabled" : ""} /></label>
          <label><span>卷号</span><input data-period-key="volume" class="${entry.period.volume ? "" : "invalid"}" inputmode="numeric" value="${escapeHtml(entry.period.volume)}" ${revisionMode ? "disabled" : ""} /></label>
          <label><span>期号</span><input data-period-key="number" class="${entry.period.number ? "" : "invalid"}" inputmode="numeric" value="${escapeHtml(entry.period.number)}" ${revisionMode ? "disabled" : ""} /></label>
        </div>
        <button class="remove-issue-button" data-remove-issue="${entry.id}" type="button" ${revisionMode ? "hidden" : ""}>移除</button>
      </div>`).join("") : '<div class="issue-parse-empty">尚未选择文件</div>';
  }

  function renderIssueAssets() {
    const list = $("#issue-assets-list");
    if (!list) return;
    list.innerHTML = issueEntries.length ? issueEntries.map((entry) => `
      <section class="issue-batch-card" data-entry-id="${entry.id}">
        <div class="selected-package-card">
          <span class="zip-mark">ZIP</span>
          <span><strong>${escapeHtml(entry.name)}</strong><small>${entry.source === "ftp" ? "FTP 文件" : "本地文件"} · ${escapeHtml(entry.size)}</small></span>
          <span class="package-period">${escapeHtml(entry.period.year || "—")} 年 · 第 ${escapeHtml(entry.period.volume || "—")} 卷 · 第 ${escapeHtml(entry.period.number || "—")} 期</span>
        </div>
        <div class="issue-upload-grid">
          ${["cover", "contents"].map((kind) => {
            const file = entry.files[kind];
            const isCover = kind === "cover";
            return `<article class="issue-upload-card" data-issue-file="${kind}">
              <div class="issue-file-mark ${isCover ? "cover" : "contents"}">${isCover ? "封" : "目"}</div>
              <div class="issue-upload-copy"><span>必传</span><h3>${isCover ? "封面文件" : "目录文件"}</h3><p>${isCover ? "PDF / JPG / PNG" : "PDF / Word / XML"} · 不超过 50 MB</p></div>
              <div class="issue-file-state">${file ? `<strong>✓ 上传成功</strong><small title="${escapeHtml(file.name)}">${escapeHtml(file.name)} · ${escapeHtml(file.size)}</small>` : '<strong class="waiting">等待上传</strong><small>尚未选择文件</small>'}</div>
              <button class="secondary-button issue-file-button" data-choose-issue-file="${kind}" data-entry-id="${entry.id}" type="button">${file ? "重新上传" : "上传文件"}</button>
            </article>`;
          }).join("")}
        </div>
      </section>`).join("") : '<div class="issue-assets-empty">请先选择整期文件</div>';
  }

  function syncIssueUi() {
    renderFtpFiles($("#ftp-search-input")?.value || "");
    renderIssueParse();
    renderIssueAssets();
    updateFooter();
  }

  function updateFlowMode() {
    const issue = isIssueMode();
    $("#formal-import-mode").hidden = publicationType !== "formal";
    $("#standard-upload-panel").hidden = issue;
    $("#standard-upload-tip").hidden = issue;
    $("#issue-source-panel").hidden = !issue;
    $("#standard-article-panel").hidden = issue;
    $("#issue-assets-panel").hidden = !issue;
    $("#step-2-title").textContent = issue ? "选择整期文件" : "上传与识别";
    $("#step-2-copy").textContent = issue ? "FTP / 本地导入" : "整理文章文件";
    $("#step-3-title").textContent = issue ? "上传期刊文件" : "校对信息";
    $("#step-3-copy").textContent = issue ? "补充封面与目录" : "确认文章信息";
    $("#page-description").textContent = revisionMode
      ? `修改 ${revisionTaskId} 的问题项，未受影响的信息将继续保留。`
      : issue
        ? "批量导入多期成品包，并补充封面与目录。"
        : "上传稿件后，系统将自动识别文章、整理正文文件并提取元数据。";
    renderIssueParse();
    renderIssueAssets();
    updateFooter();
  }

  function normalizedTitle(name) {
    return name
      .replace(/\.(pdf|docx?|tex|xml)$/i, "")
      .replace(/[_\-\s]*(排版稿|终稿|最终版|修订版|参考文献|正文|article|manuscript|final|v\d+)$/i, "")
      .replace(/[_\-]+/g, " ")
      .trim();
  }

  function articleOptions(selectedId) {
    return articles.map((article) => `<option value="${article.id}" ${article.id === selectedId ? "selected" : ""}>${escapeHtml(article.title || "未命名文章")}</option>`).join("");
  }

  function fileRow(file, articleId) {
    const ext = escapeHtml(file.ext);
    return `<div class="file-row" data-file-id="${file.id}">
      <span class="file-type ${ext === "pdf" || ext === "xml" || ext === "tex" ? ext : ""}">${ext}</span>
      <span class="file-main"><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${escapeHtml(file.size)}</small></span>
      <span class="file-state ${file.warning ? "warning" : ""}">${escapeHtml(file.state)}</span>
      <label class="file-group-field"><span>归属文章</span><select class="file-group-select" data-action="move-file" aria-label="调整 ${escapeHtml(file.name)} 的所属文章">${articleOptions(articleId)}</select></label>
      <button class="remove-file" data-action="remove-file" type="button" title="移除文件" aria-label="移除 ${escapeHtml(file.name)}"><svg viewBox="0 0 24 24"><path d="M6 7h12M9 7V5h6v2M8 7l1 12h6l1-12M11 10v6M13 10v6"/></svg></button>
    </div>`;
  }

  function attachmentRow(file) {
    const ext = escapeHtml(extension(file.name));
    return `<div class="attachment-row" data-attachment-id="${file.id}">
      <span class="file-type attachment-type">${ext}</span>
      <span class="file-main"><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><small>${escapeHtml(file.size)}</small></span>
      <label class="attachment-note-field"><span>附件备注</span><input class="attachment-note" value="${escapeHtml(file.note)}" placeholder="填写备注" aria-label="${escapeHtml(file.name)} 的备注" /></label>
      <label class="attachment-citation"><input type="checkbox" data-action="attachment-citation" ${file.cited ? "checked" : ""} /><span class="citation-check"><svg viewBox="0 0 16 16"><path d="m4 8 2.5 2.5L12 5"/></svg></span><span class="citation-copy"><b>正文中引用</b><small>勾选表示正文内已引用此附件</small></span></label>
      <button class="remove-file" data-action="remove-attachment" type="button" title="移除附件" aria-label="移除 ${escapeHtml(file.name)}"><svg viewBox="0 0 24 24"><path d="M6 7h12M9 7V5h6v2M8 7l1 12h6l1-12M11 10v6M13 10v6"/></svg></button>
    </div>`;
  }

  function metadataMarkup(article) {
    if (publicationType !== "accepted") return "";
    const metadata = article.metadata || {};
    const status = article.parseStatus === "failed"
      ? `<span class="parse-failed">部分字段解析失败，请手动补充</span>`
      : `<span>PDF 元数据解析完成，可直接修改</span>`;
    const fields = [
      ["titleEn", "英文标题", "input", true, false],
      ["authorsZh", "中文作者", "input", true, false],
      ["authorsEn", "英文作者", "input", true, false],
      ["keywordsZh", "中文关键词", "input", true, false],
      ["keywordsEn", "英文关键词", "input", true, false],
      ["abstractZh", "中文摘要", "textarea", true, true],
      ["abstractEn", "英文摘要", "textarea", true, true]
    ];
    return `<div class="metadata-header"><b>录用元数据</b>${status}</div>
      <div class="metadata-grid">${fields.map(([key, label, tag, required, wide]) => {
        const value = metadata[key] || "";
        const empty = required && !value.trim();
        return `<div class="form-field ${wide ? "wide" : ""}">
          <label for="${article.id}-${key}">${label}${required ? '<span class="required">*</span>' : ""}</label>
          ${tag === "textarea"
            ? `<textarea id="${article.id}-${key}" data-meta="${key}" class="${empty ? "invalid" : ""}" placeholder="识别失败时可手动填写">${escapeHtml(value)}</textarea>`
            : `<input id="${article.id}-${key}" data-meta="${key}" class="${empty ? "invalid" : ""}" value="${escapeHtml(value)}" placeholder="识别失败时可手动填写" />`}
          ${empty ? `<span class="field-error">该字段未识别，请手动补充</span>` : ""}
        </div>`;
      }).join("")}</div>`;
  }

  function articleMarkup(article, index) {
    const warning = article.confidence < 90;
    const hasError = !article.title.trim() || (publicationType === "accepted" && requiredMetadataMissing(article).length > 0);
    return `<article class="article-card ${hasError ? "has-error" : ""}" data-article-id="${article.id}" style="animation-delay:${index * 45}ms">
      <header class="article-card-head">
        <div><h3 title="${escapeHtml(article.title)}">${escapeHtml(article.title || "未命名文章")}</h3><p>DOI：${escapeHtml(article.doi || "未填写")} · ${article.files.length} 个正文文件 · ${article.attachments.length} 个附件</p></div>
        <span class="confidence-badge ${warning ? "warning" : ""}">${warning ? "归组需确认" : `标题匹配 ${article.confidence}%`}</span>
        <button class="card-menu" data-action="remove-article" type="button" title="删除这篇文章" aria-label="删除 ${escapeHtml(article.title || "未命名文章")}">···</button>
      </header>
      <div class="article-card-body">
        <div class="article-core-fields">
          <div class="form-field">
            <label for="${article.id}-title">文章标题<span class="required">*</span></label>
            <input id="${article.id}-title" data-field="title" class="${article.title.trim() ? "" : "invalid"}" value="${escapeHtml(article.title)}" placeholder="请输入文章标题" />
            ${article.title.trim() ? "" : '<span class="field-error">文章标题不能为空</span>'}
          </div>
          <div class="form-field">
            <label for="${article.id}-doi">DOI<span class="field-optional">选填</span></label>
            <input id="${article.id}-doi" data-field="doi" value="${escapeHtml(article.doi)}" placeholder="选填，如 10.3785/j.issn.xxxx" />
          </div>
        </div>
        ${metadataMarkup(article)}
        <div class="file-and-attachment">
          <section class="asset-section body-assets">
            <div class="subsection-head">
              <div class="asset-heading"><span class="asset-section-icon"><svg viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7zM14 3v5h4M10 12h5M10 16h5"/></svg></span><span><strong>正文文件</strong><small>PDF、Word、LaTeX、XML</small></span><em>${article.files.length}</em></div>
              <button class="mini-button" data-action="add-body" type="button"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>添加正文</button>
            </div>
            <div class="file-list">${article.files.length ? article.files.map((file) => fileRow(file, article.id)).join("") : '<div class="empty-files">暂无正文文件，请添加</div>'}</div>
          </section>
          <section class="asset-section attachment-assets">
            <div class="subsection-head">
              <div class="asset-heading"><span class="asset-section-icon attachment"><svg viewBox="0 0 24 24"><path d="m9 12 5-5a3 3 0 1 1 4 4l-7 7a5 5 0 0 1-7-7l7-7"/></svg></span><span><strong>文章附件</strong><small>支持多个附件，可填写备注与引用状态</small></span><em>${article.attachments.length}</em></div>
              <button class="mini-button" data-action="add-attachment" type="button"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>添加附件</button>
            </div>
            <div class="attachment-list">${article.attachments.length ? article.attachments.map(attachmentRow).join("") : '<div class="empty-files">暂无附件</div>'}</div>
          </section>
        </div>
      </div>
    </article>`;
  }

  function requiredMetadataMissing(article) {
    const required = ["titleEn", "authorsZh", "authorsEn", "keywordsZh", "keywordsEn", "abstractZh", "abstractEn"];
    return required.filter((key) => !String(article.metadata?.[key] || "").trim());
  }

  function getValidation() {
    if (isIssueMode()) {
      const missingPackage = issueEntries.length === 0;
      const missingPeriod = issueEntries.filter((entry) => !issuePeriodComplete(entry)).length;
      const missingCover = issueEntries.filter((entry) => !entry.files.cover).length;
      const missingContents = issueEntries.filter((entry) => !entry.files.contents).length;
      return { issue: true, missingPackage, missingPeriod, missingCover, missingContents, valid: !missingPackage && !missingPeriod && !missingCover && !missingContents };
    }
    const missingTitles = articles.filter((article) => !article.title.trim()).length;
    const missingFiles = articles.filter((article) => article.files.length === 0).length;
    const missingMetadata = publicationType === "accepted"
      ? articles.reduce((sum, article) => sum + requiredMetadataMissing(article).length, 0)
      : 0;
    return { missingTitles, missingFiles, missingMetadata, valid: articles.length > 0 && !missingTitles && !missingFiles && !missingMetadata };
  }

  function getCounts() {
    return {
      files: articles.reduce((sum, article) => sum + article.files.length, 0),
      attachments: articles.reduce((sum, article) => sum + article.attachments.length, 0)
    };
  }

  function validationIssues(validation) {
    const issues = [];
    if (validation.issue) {
      if (validation.missingPackage) issues.push("整期压缩包");
      if (validation.missingPeriod) issues.push(`${validation.missingPeriod} 期的期刊和年卷期信息`);
      if (validation.missingCover) issues.push(`${validation.missingCover} 期封面`);
      if (validation.missingContents) issues.push(`${validation.missingContents} 期目录`);
      return issues;
    }
    if (!articles.length) issues.push("至少添加一篇文章");
    if (validation.missingTitles) issues.push(`${validation.missingTitles} 个标题`);
    if (validation.missingFiles) issues.push(`${validation.missingFiles} 篇缺少正文`);
    if (validation.missingMetadata) issues.push(`${validation.missingMetadata} 个元数据字段`);
    return issues;
  }

  function updateFooter() {
    const validation = getValidation();
    const counts = getCounts();
    const note = $(".validation-note");
    const icon = $("#validation-icon");
    const title = $("#validation-title");
    const copy = $("#validation-copy");
    const primary = $("#create-button");
    const back = $("#back-button");
    note.classList.remove("invalid");
    back.hidden = currentStep === 1;

    if (isIssueMode()) {
      const ftpCount = issueEntries.filter((entry) => entry.source === "ftp").length;
      const localCount = issueEntries.length - ftpCount;
      if (currentStep === 1) {
        icon.textContent = "1";
        title.textContent = "正式出版 · 整期压缩包";
        copy.textContent = "下一步可从 FTP 多选或从本地批量上传。";
        primary.innerHTML = "下一步：选择整期文件 <span>→</span>";
      } else if (currentStep === 2) {
        const stepValid = issueEntries.length > 0 && !validation.missingPeriod;
        icon.textContent = stepValid ? "✓" : "!";
        title.textContent = !issueEntries.length ? "请选择整期文件" : validation.missingPeriod ? `${validation.missingPeriod} 期信息不完整` : `已选择 ${issueEntries.length} 期`;
        copy.textContent = !issueEntries.length ? "可从 FTP 多选或从本地上传 ZIP。" : validation.missingPeriod ? "请检查解析结果并补充缺失信息。" : `FTP ${ftpCount} 期 · 本地 ${localCount} 期`;
        note.classList.toggle("invalid", !stepValid);
        primary.innerHTML = "下一步：上传期刊文件 <span>→</span>";
      } else if (currentStep === 3) {
        icon.textContent = validation.valid ? "✓" : "!";
        title.textContent = validation.valid ? `${issueEntries.length} 期封面与目录已上传` : "还需补充期刊文件";
        copy.textContent = validation.valid ? "可进入最终确认。" : `待补充：${validationIssues(validation).join("、")}。`;
        note.classList.toggle("invalid", !validation.valid);
        primary.innerHTML = "下一步：确认任务 <span>→</span>";
      } else {
        icon.textContent = validation.valid ? "✓" : "!";
        title.textContent = validation.valid ? (revisionMode ? "修订内容已准备就绪" : `${issueEntries.length} 个整期任务已准备就绪`) : "任务信息尚未完整";
        copy.textContent = validation.valid ? (revisionMode ? "重新提交后，任务将回到待初审。" : "创建后进入待初审，由管理员完成分发。") : `待补充：${validationIssues(validation).join("、")}。`;
        note.classList.toggle("invalid", !validation.valid);
        primary.innerHTML = revisionMode ? "重新提交初审 <span>→</span>" : `创建 ${issueEntries.length} 个整期任务 <span>→</span>`;
      }
      return;
    }

    if (currentStep === 1) {
      icon.textContent = "1";
      title.textContent = `已选择${typeLabels[publicationType]}`;
      copy.textContent = "下一步上传文章源文件。";
      primary.innerHTML = "下一步：上传文件 <span>→</span>";
    } else if (currentStep === 2) {
      icon.textContent = counts.files ? "✓" : "!";
      title.textContent = counts.files ? `已识别 ${articles.length} 篇文章` : "请先上传文章文件";
      copy.textContent = counts.files ? `共 ${counts.files} 个正文文件，下一步可调整归组并校对信息。` : "支持 PDF、Word、LaTeX 和 XML 格式。";
      note.classList.toggle("invalid", !counts.files);
      primary.innerHTML = "下一步：校对信息 <span>→</span>";
    } else if (currentStep === 3) {
      icon.textContent = validation.valid ? "✓" : "!";
      title.textContent = validation.valid ? "所有必填信息已完整" : "还有必填信息需要补充";
      copy.textContent = validation.valid
        ? `共 ${articles.length} 篇文章，可进入最终确认。`
        : `待补充：${validationIssues(validation).join("、")}。`;
      note.classList.toggle("invalid", !validation.valid);
      primary.innerHTML = "下一步：确认任务 <span>→</span>";
    } else {
      icon.textContent = validation.valid ? "✓" : "!";
      title.textContent = validation.valid ? (revisionMode ? "修订内容已准备就绪" : "任务已准备就绪") : "任务信息尚未完整";
      copy.textContent = validation.valid
        ? (revisionMode ? "重新提交后，任务将回到待初审。" : "创建后进入待初审，由管理员选择加工方式和生产公司。")
        : `待补充：${validationIssues(validation).join("、")}。`;
      note.classList.toggle("invalid", !validation.valid);
      primary.innerHTML = revisionMode ? "重新提交初审 <span>→</span>" : "创建生产任务 <span>→</span>";
    }
  }

  function renderConfirmation() {
    const counts = getCounts();
    const validation = getValidation();
    if (isIssueMode()) {
      const ftpCount = issueEntries.filter((entry) => entry.source === "ftp").length;
      const localCount = issueEntries.length - ftpCount;
      $("#confirmation-kicker").textContent = revisionMode ? "READY TO RESUBMIT" : "READY TO CREATE";
      $("#confirmation-title").textContent = revisionMode ? "确认整期修订" : "确认整期任务";
      $(".confirmation-heading p").textContent = revisionMode ? "请核对修订文件，重新提交后任务将回到待初审。" : `${issueEntries.length} 期将分别创建任务并提交管理员初审。`;
      $("#confirmation-summary").innerHTML = `
        <section class="summary-block">
          <h3>批量任务</h3>
          <div class="summary-type">
            <span class="summary-type-icon"><span style="font:700 11px/1 var(--utility)">ZIP</span></span>
            <span><strong>正式出版 · ${issueEntries.length} 期</strong><small>FTP ${ftpCount} 期 · 本地 ${localCount} 期</small></span>
          </div>
          <div class="summary-stats">
            <span class="summary-stat"><b>${issueEntries.length}</b><span>期次</span></span>
            <span class="summary-stat"><b>${issueEntries.length}</b><span>封面</span></span>
            <span class="summary-stat"><b>${issueEntries.length}</b><span>目录</span></span>
          </div>
        </section>
        <section class="summary-block">
          <h3>期次清单</h3>
          <div class="summary-issue-list">${issueEntries.map((entry) => `
            <div class="summary-issue-group">
              <div class="summary-issue-head"><span><strong>${escapeHtml(entry.period.journal || "期刊待填写")}</strong><small>${escapeHtml(entry.period.year || "—")} 年 · 第 ${escapeHtml(entry.period.volume || "—")} 卷 · 第 ${escapeHtml(entry.period.number || "—")} 期</small></span><em>${entry.source === "ftp" ? "FTP" : "本地"}</em></div>
              <div class="summary-articles">
                <div class="summary-article"><span><strong>${escapeHtml(entry.name)}</strong><small>整期压缩包 · ${escapeHtml(entry.size)}</small></span><em>已选择</em></div>
                <div class="summary-article"><span><strong>${escapeHtml(entry.files.cover?.name || "未上传封面")}</strong><small>封面文件 · ${escapeHtml(entry.files.cover?.size || "—")}</small></span><em>${entry.files.cover ? "已上传" : "需补充"}</em></div>
                <div class="summary-article"><span><strong>${escapeHtml(entry.files.contents?.name || "未上传目录")}</strong><small>目录文件 · ${escapeHtml(entry.files.contents?.size || "—")}</small></span><em>${entry.files.contents ? "已上传" : "需补充"}</em></div>
              </div>
            </div>`).join("")}</div>
          ${validation.valid ? "" : `<div class="summary-warning"><b>!</b><span>仍需补充：${validationIssues(validation).join("、")}。</span></div>`}
        </section>`;
      return;
    }
    $("#confirmation-kicker").textContent = revisionMode ? "READY TO RESUBMIT" : "READY TO CREATE";
    $("#confirmation-title").textContent = revisionMode ? "确认任务修订" : "确认生产任务";
    $(".confirmation-heading p").textContent = revisionMode ? "请核对修改内容，重新提交后任务将回到待初审。" : "请最后核对发布类型与文章清单，创建后由管理员初审并分发。";
    $("#confirmation-summary").innerHTML = `
      <section class="summary-block">
        <h3>任务概览</h3>
        <div class="summary-type">
          <span class="summary-type-icon"><svg viewBox="0 0 24 24"><path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5"/></svg></span>
          <span><strong>${typeLabels[publicationType]}</strong><small>${typeDescriptions[publicationType]}</small></span>
        </div>
        <div class="summary-stats">
          <span class="summary-stat"><b>${articles.length}</b><span>篇文章</span></span>
          <span class="summary-stat"><b>${counts.files}</b><span>个正文</span></span>
          <span class="summary-stat"><b>${counts.attachments}</b><span>个附件</span></span>
        </div>
      </section>
      <section class="summary-block">
        <h3>文章清单</h3>
        <div class="summary-articles">
          ${articles.map((article) => `<div class="summary-article">
            <span><strong>${escapeHtml(article.title || "未填写标题")}</strong><small>DOI：${escapeHtml(article.doi || "未填写（选填）")} · ${article.files.length} 个正文文件 · ${article.attachments.length} 个附件</small></span>
            <em>${article.title && article.files.length ? "已完整" : "需补充"}</em>
          </div>`).join("")}
        </div>
        ${validation.valid ? "" : `<div class="summary-warning"><b>!</b><span>仍需补充：${validationIssues(validation).join("、")}。</span></div>`}
      </section>`;
  }

  function goToStep(targetStep, options = {}) {
    const target = Math.max(1, Math.min(4, Number(targetStep)));
    const counts = getCounts();
    if (target > maxReachedStep + 1) {
      toast("请先完成当前步骤", true);
      return false;
    }
    if (isIssueMode() && target >= 3) {
      const issueValidation = getValidation();
      if (issueValidation.missingPackage || issueValidation.missingPeriod) {
        toast(issueValidation.missingPackage ? "请先选择整期文件" : `请补充 ${issueValidation.missingPeriod} 期的期刊和年卷期信息`, true);
        return false;
      }
    }
    if (!isIssueMode() && target >= 3 && !counts.files) {
      toast("请先上传至少一个正文文件", true);
      return false;
    }
    if (target === 4 && !getValidation().valid) {
      currentStep = 3;
      maxReachedStep = Math.max(maxReachedStep, 3);
      if (isIssueMode()) {
        renderIssueAssets();
        toast(`请先补充：${validationIssues(getValidation()).join("、")}`, true);
      } else {
        renderArticles();
        toast("请先补充标红的必填信息", true);
        requestAnimationFrame(() => {
          const firstInvalid = $(".invalid", articleList);
          firstInvalid?.scrollIntoView({ behavior: "smooth", block: "center" });
          firstInvalid?.focus();
        });
      }
    } else {
      currentStep = target;
      maxReachedStep = Math.max(maxReachedStep, target);
    }

    $$('[data-step-panel]').forEach((panel) => {
      const active = Number(panel.dataset.stepPanel) === currentStep;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    $$(".process-track li").forEach((item) => {
      const step = Number(item.dataset.step);
      item.classList.toggle("active", step === currentStep);
      item.classList.toggle("done", step < currentStep || (step === 4 && $(".process-track").classList.contains("completed")));
      item.classList.toggle("locked", step > maxReachedStep + 1);
      const button = $(".step-nav", item);
      button.disabled = step > maxReachedStep + 1;
      if (step === currentStep) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
    });
    $(".process-track").style.setProperty("--progress", `${((currentStep - 0.5) / 4) * 100}%`);
    if (currentStep === 4) renderConfirmation();
    updateFooter();
    if (!options.silent) $(".process-track").scrollIntoView({ behavior: "smooth", block: "start" });
    return currentStep === target;
  }

  function updateSummary() {
    const { files: fileCount, attachments: attachmentCount } = getCounts();
    $("#article-summary").innerHTML = `<b>${articles.length}</b> 篇文章 · <b>${fileCount}</b> 个正文 · <b>${attachmentCount}</b> 个附件`;
    const recognitionArticleCount = $("#article-count");
    const recognitionFileCount = $("#file-count");
    if (recognitionArticleCount) recognitionArticleCount.textContent = articles.length;
    if (recognitionFileCount) recognitionFileCount.textContent = fileCount;

    if (currentStep === 4) renderConfirmation();
    updateFooter();
  }

  function renderArticles() {
    articleList.innerHTML = articles.length
      ? articles.map(articleMarkup).join("")
      : `<div class="panel" style="text-align:center;color:var(--muted);padding:44px">尚未识别到文章，请上传正文文件或新建空白文章。</div>`;
    $("#article-subtitle").textContent = publicationType === "accepted"
      ? "逐篇校对 PDF 解析结果；识别失败字段可直接手动补充。"
      : "检查文章标题和正文归组，可为每篇文章添加附件。";
    updateSummary();
  }

  function setRecognitionState(state, title, copy) {
    recognitionStatus.className = `recognition-status ${state}`;
    $(".recognition-status strong").textContent = title;
    $(".recognition-status p").innerHTML = copy;
  }

  function toast(message, error = false) {
    const element = document.createElement("div");
    element.className = `toast ${error ? "error" : ""}`;
    element.innerHTML = `<span>${error ? "!" : "✓"}</span>${escapeHtml(message)}`;
    $("#toast-region").append(element);
    setTimeout(() => element.remove(), 3200);
  }

  function createBlankArticle(title = "") {
    return {
      id: `a${++idSeed}`,
      title,
      doi: "",
      confidence: title ? 78 : 0,
      parseStatus: "failed",
      metadata: { titleEn: "", authorsZh: "", authorsEn: "", keywordsZh: "", keywordsEn: "", abstractZh: "", abstractEn: "" },
      files: [], attachments: []
    };
  }

  function addUploadedFiles(fileList, forcedArticleId = null) {
    const files = [...fileList].filter((file) => /\.(pdf|docx?|tex|xml)$/i.test(file.name));
    if (!files.length) {
      toast("未发现支持的正文文件，请选择 PDF、Word、LaTeX 或 XML。", true);
      return;
    }
    setRecognitionState("loading", "正在识别文章并自动归组…", `正在处理 <b>${files.length}</b> 个文件，正在匹配文章标题与文件版本。`);
    bodyFileInput.disabled = true;

    setTimeout(() => {
      files.forEach((file) => {
        let target = forcedArticleId ? articles.find((article) => article.id === forcedArticleId) : null;
        const title = normalizedTitle(file.name);
        if (!target) {
          target = articles.find((article) => article.title && (article.title.includes(title) || title.includes(article.title.slice(0, Math.max(5, title.length - 4)))));
        }
        if (!target) {
          target = createBlankArticle(title);
          target.confidence = 92;
          articles.push(target);
        }
        const ext = extension(file.name);
        target.files.push({ id: `f${++idSeed}`, name: file.name, ext, size: humanSize(file.size), state: ext === "pdf" ? "已识别" : "已归组" });
      });
      bodyFileInput.disabled = false;
      bodyFileInput.value = "";
      renderArticles();
      setRecognitionState("success", "自动归组完成", `已识别 <b>${articles.reduce((n, a) => n + a.files.length, 0)}</b> 个文件并归为 <b>${articles.length}</b> 篇文章，请检查归组结果。`);
      toast(`已完成 ${files.length} 个文件的识别与归组`);
    }, 1250);
  }

  $$('input[name="publication"]').forEach((radio) => {
    radio.addEventListener("change", (event) => {
      publicationType = event.target.value;
      $$(".publication-card").forEach((card) => card.classList.toggle("selected", $("input", card).checked));
      renderArticles();
      if (publicationType === "accepted") {
        setRecognitionState("success", "PDF 元数据解析完成", `已解析 <b>${articles.length}</b> 篇文章，其中 <b>${articles.filter((a) => a.parseStatus === "failed").length}</b> 篇有字段需要手动补充。`);
        toast("已切换为最新录用，请校对解析出的文章元数据");
      } else {
        setRecognitionState("success", "自动归组完成", `已识别 <b>${articles.reduce((n, a) => n + a.files.length, 0)}</b> 个文件并归为 <b>${articles.length}</b> 篇文章，请检查归组结果。`);
      }
      updateFlowMode();
    });
  });

  $$('input[name="formal-mode"]').forEach((radio) => {
    radio.addEventListener("change", (event) => {
      formalImportMode = event.target.value;
      $$(".import-mode-option").forEach((option) => option.classList.toggle("selected", $("input", option).checked));
      maxReachedStep = 1;
      updateFlowMode();
      toast(formalImportMode === "issue" ? "已切换为整期压缩包导入" : "已切换为单篇 / 多篇文章上传");
    });
  });

  $("#ftp-file-list").addEventListener("change", (event) => {
    if (!event.target.matches('input[name="issue-package"]')) return;
    const pkg = ftpPackages.find((item) => item.id === event.target.value);
    if (!pkg) return;
    if (event.target.checked && !ftpEntry(pkg.id)) issueEntries.push(createFtpIssueEntry(pkg));
    if (!event.target.checked) issueEntries = issueEntries.filter((item) => item.packageId !== pkg.id);
    syncIssueUi();
  });

  $("#ftp-search-input").addEventListener("input", (event) => renderFtpFiles(event.target.value));

  $("#refresh-ftp-button").addEventListener("click", (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "正在刷新…";
    setTimeout(() => {
      button.disabled = false;
      button.textContent = "刷新列表";
      renderFtpFiles($("#ftp-search-input").value);
      toast("FTP 文件列表已刷新");
    }, 700);
  });

  $("#local-issue-button").addEventListener("click", () => localIssueInput.click());

  localIssueInput.addEventListener("change", () => {
    const files = [...localIssueInput.files].filter((file) => /\.zip$/i.test(file.name));
    let added = 0;
    files.forEach((file) => {
      if (issueEntries.some((entry) => entry.name === file.name)) return;
      const period = parseIssueFilename(file.name) || { journal: "", year: "", volume: "", number: "" };
      issueEntries.push({ id: `issue-local-${++issueEntrySeed}`, source: "local", name: file.name, size: humanSize(file.size), updated: "刚刚上传", period, files: { cover: null, contents: null } });
      added += 1;
    });
    localIssueInput.value = "";
    syncIssueUi();
    if (added) toast(`已添加 ${added} 个本地整期文件`);
    else toast("没有新增 ZIP 文件", true);
  });

  $("#issue-parse-list").addEventListener("input", (event) => {
    if (!event.target.matches("[data-period-key]")) return;
    const row = event.target.closest("[data-entry-id]");
    const entry = issueEntry(row?.dataset.entryId);
    if (!entry) return;
    entry.period[event.target.dataset.periodKey] = event.target.value.trim();
    event.target.classList.toggle("invalid", !event.target.value.trim());
    renderIssueAssets();
    updateFooter();
  });

  $("#issue-parse-list").addEventListener("change", (event) => {
    if (!event.target.matches("[data-period-key]")) return;
    renderIssueParse();
    renderIssueAssets();
    updateFooter();
  });

  $("#issue-parse-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-issue]");
    if (!button) return;
    issueEntries = issueEntries.filter((entry) => entry.id !== button.dataset.removeIssue);
    syncIssueUi();
  });

  $("#issue-assets-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-choose-issue-file]");
    if (!button) return;
    activeIssueFileTarget = { entryId: button.dataset.entryId, kind: button.dataset.chooseIssueFile };
    (activeIssueFileTarget.kind === "cover" ? coverFileInput : contentsFileInput).click();
  });

  function handleIssueFile(input) {
    const file = input.files[0];
    const target = activeIssueFileTarget;
    const entry = issueEntry(target?.entryId);
    if (!file || !entry) return;
    entry.files[target.kind] = { name: file.name, size: humanSize(file.size) };
    input.value = "";
    renderIssueAssets();
    updateFooter();
    toast(`${target.kind === "cover" ? "封面" : "目录"}文件上传成功`);
    activeIssueFileTarget = null;
  }

  coverFileInput.addEventListener("change", () => handleIssueFile(coverFileInput));
  contentsFileInput.addEventListener("change", () => handleIssueFile(contentsFileInput));

  $(".process-track").addEventListener("click", (event) => {
    const item = event.target.closest("li[data-step]");
    if (!item) return;
    goToStep(Number(item.dataset.step));
  });

  bodyFileInput.addEventListener("change", () => {
    const targetArticleId = bodyFileInput.dataset.targetArticle || null;
    delete bodyFileInput.dataset.targetArticle;
    addUploadedFiles(bodyFileInput.files, targetArticleId);
  });
  dropzone.addEventListener("dragover", (event) => { event.preventDefault(); dropzone.classList.add("dragover"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragover");
    addUploadedFiles(event.dataTransfer.files);
  });

  $("#status-detail-button").addEventListener("click", () => {
    const detail = $("#recognition-detail");
    detail.hidden = !detail.hidden;
    $("#status-detail-button").textContent = detail.hidden ? "查看识别说明" : "收起识别说明";
  });

  $("#demo-button").addEventListener("click", () => {
    setRecognitionState("loading", "正在重新识别示例文件…", "正在匹配文件标题与版本，请稍候。");
    setTimeout(() => {
      articles = structuredClone(demoArticles);
      renderArticles();
      setRecognitionState("success", publicationType === "accepted" ? "PDF 元数据解析完成" : "自动归组完成", `已识别 <b>6</b> 个文件并归为 <b>2</b> 篇文章，请检查归组结果。`);
      toast("示例稿件已重新加载");
    }, 850);
  });

  $("#add-article-button").addEventListener("click", () => {
    articles.push(createBlankArticle());
    renderArticles();
    requestAnimationFrame(() => {
      const lastCard = articleList.lastElementChild;
      lastCard?.scrollIntoView({ behavior: "smooth", block: "center" });
      $("[data-field='title']", lastCard)?.focus();
    });
  });

  articleList.addEventListener("input", (event) => {
    const card = event.target.closest(".article-card");
    const article = articles.find((item) => item.id === card?.dataset.articleId);
    if (!article) return;
    if (event.target.matches("[data-field]")) {
      const field = event.target.dataset.field;
      article[field] = event.target.value;
      event.target.classList.toggle("invalid", field === "title" && !article[field].trim());
      if (field === "title") {
        $(".article-card-head h3", card).textContent = article.title || "未命名文章";
        $$(`option[value="${article.id}"]`, articleList).forEach((option) => { option.textContent = article.title || "未命名文章"; });
      }
      if (field === "doi") {
        $(".article-card-head p", card).textContent = `DOI：${article.doi || "未填写"} · ${article.files.length} 个正文文件 · ${article.attachments.length} 个附件`;
      }
    }
    if (event.target.matches("[data-meta]")) {
      article.metadata[event.target.dataset.meta] = event.target.value;
      event.target.classList.toggle("invalid", !event.target.value.trim());
    }
    if (event.target.matches(".attachment-note")) {
      const row = event.target.closest("[data-attachment-id]");
      const attachment = article.attachments.find((item) => item.id === row.dataset.attachmentId);
      if (attachment) attachment.note = event.target.value;
    }
    updateSummary();
  });

  articleList.addEventListener("change", (event) => {
    if (event.target.matches("[data-action='attachment-citation']")) {
      const card = event.target.closest(".article-card");
      const article = articles.find((item) => item.id === card?.dataset.articleId);
      const row = event.target.closest("[data-attachment-id]");
      const attachment = article?.attachments.find((item) => item.id === row?.dataset.attachmentId);
      if (attachment) attachment.cited = event.target.checked;
      updateSummary();
      return;
    }
    if (!event.target.matches("[data-action='move-file']")) return;
    const card = event.target.closest(".article-card");
    const source = articles.find((article) => article.id === card.dataset.articleId);
    const target = articles.find((article) => article.id === event.target.value);
    const row = event.target.closest("[data-file-id]");
    const fileIndex = source.files.findIndex((file) => file.id === row.dataset.fileId);
    if (!target || target === source || fileIndex < 0) return;
    const [file] = source.files.splice(fileIndex, 1);
    file.state = "手动归组";
    file.warning = false;
    target.files.push(file);
    renderArticles();
    toast(`已将“${file.name}”移动到目标文章`);
  });

  articleList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const card = button.closest(".article-card");
    const article = articles.find((item) => item.id === card.dataset.articleId);
    if (!article) return;
    const action = button.dataset.action;

    if (action === "remove-file") {
      const id = button.closest("[data-file-id]").dataset.fileId;
      article.files = article.files.filter((file) => file.id !== id);
      renderArticles();
      toast("正文文件已移除");
    }
    if (action === "remove-attachment") {
      const id = button.closest("[data-attachment-id]").dataset.attachmentId;
      article.attachments = article.attachments.filter((file) => file.id !== id);
      renderArticles();
      toast("附件已移除");
    }
    if (action === "add-attachment") {
      activeAttachmentArticleId = article.id;
      attachmentInput.click();
    }
    if (action === "add-body") {
      bodyFileInput.dataset.targetArticle = article.id;
      bodyFileInput.click();
    }
    if (action === "remove-article") {
      const index = articles.indexOf(article);
      if (articles.length === 1 && article.files.length) {
        toast("至少保留一篇文章，可先移除正文文件。", true);
        return;
      }
      articles.splice(index, 1);
      renderArticles();
      toast("文章已移除");
    }
  });

  attachmentInput.addEventListener("change", () => {
    const article = articles.find((item) => item.id === activeAttachmentArticleId);
    if (!article) return;
    [...attachmentInput.files].forEach((file) => article.attachments.push({ id: `x${++idSeed}`, name: file.name, size: humanSize(file.size), note: "", cited: false }));
    const count = attachmentInput.files.length;
    attachmentInput.value = "";
    renderArticles();
    toast(`已添加 ${count} 个附件，可继续填写备注`);
  });

  $("#save-button").addEventListener("click", () => {
    $("#saved-time").textContent = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    toast("草稿已保存");
  });

  $("#back-button").addEventListener("click", () => goToStep(currentStep - 1));

  $("#create-button").addEventListener("click", () => {
    if (currentStep < 4) {
      goToStep(currentStep + 1);
      return;
    }
    const validation = getValidation();
    if (!validation.valid) {
      if (isIssueMode()) {
        toast(`请先补充：${validationIssues(validation).join("、")}`, true);
      } else {
        renderArticles();
        const firstInvalid = $(".invalid", articleList);
        firstInvalid?.scrollIntoView({ behavior: "smooth", block: "center" });
        setTimeout(() => firstInvalid?.focus(), 400);
        toast("请先补充标红的必填信息", true);
      }
      return;
    }
    const button = $("#create-button");
    button.classList.add("loading");
    button.disabled = true;
    setTimeout(() => {
      button.classList.remove("loading");
      button.disabled = revisionMode;
      if (revisionMode) toast("修订已提交，任务已回到待初审");
      else toast(`生产任务创建成功，已提交管理员初审${isIssueMode() ? "" : ` · ${articles.length} 篇文章`}`);
      $(".process-track").classList.add("completed");
      $$(".process-track li").forEach((item) => item.classList.add("done"));
      button.innerHTML = revisionMode ? "已重新提交初审 <span>✓</span>" : "已提交管理员初审 <span>✓</span>";
    }, 1200);
  });

  applyRevisionMode();
  renderFtpFiles();
  syncIssueUi();
  renderArticles();
  updateFlowMode();
  goToStep(revisionMode ? revisionContext.step : 1, { silent: true });
})();
