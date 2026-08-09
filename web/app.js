(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const messagesEl = $("#messages");
  const askForm = $("#ask-form");
  const questionEl = $("#question");
  const askBtn = $("#ask-btn");
  const resetBtn = $("#reset-btn");
  const journalSelect = $("#journal-select");
  const graphForm = $("#graph-form");
  const graphCanvas = $("#graph-canvas");
  const graphLegend = $("#graph-legend");
  const graphStatus = $("#graph-status");

  let needsReset = false;
  let trendsLoaded = false;
  let graphAnim = null;
  let graphNetwork = null;
  let graphNodesDS = null;
  let graphEdgesDS = null;
  let graphHubId = null;
  let graphLoadSeq = 0;
  let graphLoading = false;
  const graphCache = new Map();
  const GRAPH_CACHE_MAX = 24;
  let selectedYear = null;
  let yearlyRowsCache = [];

  function currentJournalId() {
    return (journalSelect && journalSelect.value) || "ZDXBNXB";
  }

  function withJournal(path) {
    const sep = path.includes("?") ? "&" : "?";
    return `${path}${sep}journal_id=${encodeURIComponent(currentJournalId())}`;
  }

  const PRESETS_BY_JOURNAL = {
    ZDXBNXB: [
      { q: "徐建明全部发文", label: "作者发文" },
      { q: "徐建明合作的作者所属机构情况", label: "合作机构" },
      { q: "徐建明和施加春合作的发文有哪些", label: "合著论文" },
    ],
    ZDXBRWB: [
      { q: "近十年发文趋势和热门关键词？", label: "发文趋势" },
      { q: "本刊主要研究方向有哪些？", label: "研究方向" },
      { q: "浙江大学相关作者有哪些代表性成果？", label: "代表成果" },
    ],
  };

  const GRAPH_DEFAULTS_BY_JOURNAL = {
    ZDXBNXB: {
      author: "朱军",
      keyword: "水稻",
      institution: "浙江大学",
      paper: "",
    },
    ZDXBRWB: {
      author: "",
      keyword: "法治",
      institution: "浙江大学",
      paper: "",
    },
  };

  function getPresets() {
    return PRESETS_BY_JOURNAL[currentJournalId()] || PRESETS_BY_JOURNAL.ZDXBNXB;
  }

  function getGraphDefaults() {
    return (
      GRAPH_DEFAULTS_BY_JOURNAL[currentJournalId()] ||
      GRAPH_DEFAULTS_BY_JOURNAL.ZDXBNXB
    );
  }
  const COLORS = {
    author: "#1a73e8",
    collaborator: "#5b9cf5",
    paper: "#e37400",
    keyword: "#188038",
    institution: "#7c3aed",
    fund: "#d93025",
    clc: "#5f6368",
  };

  const GRAPH_SHAPES = {
    author: "dot",
    collaborator: "dot",
    paper: "box",
    keyword: "diamond",
    institution: "triangle",
    fund: "star",
    clc: "square",
  };

  const GROUP_LABELS = {
    author: "作者",
    collaborator: "合作者",
    paper: "论文",
    keyword: "关键词",
    institution: "机构",
    fund: "基金",
    clc: "分类",
  };

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail = data?.detail || res.statusText || "请求失败";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  /** Strip internal routing notes if model/UI ever leaks them. */
  function stripRouteNoise(text) {
    return String(text || "")
      .replace(/\n{0,2}#{0,3}\s*路由[：:][\s\S]*$/g, "")
      .replace(/(?:^|\n)\s*路由[：:][^\n]*(?:\n(?![^\n])[^\n]*)*$/g, "")
      .replace(/(?:^|\n)\s*路由[：:].*$/gm, "")
      .trim();
  }

  function cleanUrl(url) {
    return String(url || "")
      .trim()
      .replace(/^["'<]+/, "")
      .replace(/["'>\s]+$/g, "")
      .replace(/[.,;:!?，。；！？]+$/g, "");
  }

  /** Ensure every ** on a line is paired; auto-close or drop orphans. */
  function balanceBoldLine(line) {
    if (!line.includes("**")) return line;
    const parks = [];
    let work = line.replace(/`[^`\n]+`/g, (m) => {
      parks.push(m);
      return `\0${parks.length - 1}\0`;
    });
    const markers = [];
    for (let i = 0; i < work.length - 1; i++) {
      if (work[i] === "*" && work[i + 1] === "*") {
        markers.push(i);
        i += 1;
      }
    }
    const n = markers.length;
    if (n === 0 || n % 2 === 0) {
      // ok
    } else if (n === 1) {
      const at = markers[0];
      const before = work.slice(0, at);
      const after = work.slice(at + 2);
      if (/^#{1,6}\s+/.test(before) || !after.trim()) {
        work = before + after;
      } else {
        const core = after.replace(/\s+$/, "");
        const trail = after.slice(core.length);
        work = `${before}**${core}**${trail}`;
      }
    } else {
      const last = markers[markers.length - 1];
      work = work.slice(0, last) + work.slice(last + 2);
      while (((work.match(/\*\*/g) || []).length % 2) === 1) {
        const i = work.lastIndexOf("**");
        if (i < 0) break;
        work = work.slice(0, i) + work.slice(i + 2);
      }
    }
    return work.replace(/\0(\d+)\0/g, (_, i) => parks[Number(i)] || "");
  }

  function ensureBoldClosed(text) {
    if (!text || !text.includes("**")) return text;
    const lines = String(text).split("\n");
    let inFence = false;
    return lines
      .map((line) => {
        if (line.trim().startsWith("```")) {
          inFence = !inFence;
          return line;
        }
        return inFence ? line : balanceBoldLine(line);
      })
      .join("\n");
  }

  /** Repair model HTML / mangled anchors before Markdown render. */
  function normalizeAnswerText(text) {
    let t = String(text || "").replace(/\r\n/g, "\n");

    // **title**（2025）** → **title**（2025）
    t = t.replace(
      /(\*\*[^*]+?\*\*)\s*([（(]\s*\d{4}\s*年?\s*[)）])\s*\*\*/g,
      "$1$2"
    );
    // title（2025）** DOI → title（2025） DOI
    t = t.replace(
      /([）)])\s*\*\*(?=\s*(?:DOI|\[查看全文]|查看全文|$))/gim,
      "$1"
    );
    // ATX headings: ### **title / ### **title** → ### title
    t = t.replace(/^(#{1,6}\s+)(.+)$/gm, (_, hashes, body) => {
      let b = String(body || "").trim();
      if (b.startsWith("**") && b.endsWith("**") && b.length > 4) {
        const inner = b.slice(2, -2).trim();
        if (!inner.includes("**")) b = inner;
      } else if (b.startsWith("**")) {
        b = b.slice(2).trimStart();
      } else if (b.endsWith("**") && (b.match(/\*\*/g) || []).length === 1) {
        b = b.slice(0, -2).trimEnd();
      }
      if (((b.match(/\*\*/g) || []).length % 2) === 1) {
        b = b.replace(/\*\*/g, "");
      }
      return hashes + b;
    });
    // opening ** without close on the same line
    t = t.replace(/^\*\*(?=[^*].*$)(?!.*\*\*)/gm, "");
    t = t.replace(/\*\*(?=\s*(?:查看全文|DOI|$))/gm, "");
    t = t.replace(/\*\*(?=\s*$)/gm, "");

    // <a href="url"...>label</a>
    t = t.replace(
      /<a\s+[^>]*href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi,
      (_, url, label) => {
        const u = cleanUrl(url);
        const lab = String(label)
          .replace(/<[^>]+>/g, "")
          .replace(/\s+/g, " ")
          .trim() || "查看全文";
        return `[${lab}](${u})`;
      }
    );

    // Broken leftovers:
    // https://...doi..." target="blank" rel="noopener">链接
    // https://...doi..." target="_blank" rel="noopener">链接
    t = t.replace(
      /(https?:\/\/[^\s"'<>]+)\s*["']?\s*target\s*=\s*["']?_?blank["']?[^>\n]*>\s*([^\n<]*)/gi,
      (_, url, label) => {
        const lab = String(label || "").trim() || "查看全文";
        return `[${lab}](${cleanUrl(url)})`;
      }
    );

    // Drop any remaining HTML tags
    t = t.replace(/<\/?[^>]+>/g, "");

    // 链接: https://...  → markdown
    t = t.replace(
      /((?:^|\n)\s*(?:[-*]\s*)?)(?:链接|原文|全文)\s*[:：]\s*(https?:\/\/[^\s"'<>]+)/g,
      (_, prefix, url) => `${prefix}[查看全文](${cleanUrl(url)})`
    );

    // Force ordered list for paper / collaborator entry blocks
    t = numberEntryBlocks(t);
    // Hard guarantee before HTML render: every ** is paired
    return ensureBoldClosed(t);
  }

  function isEntryMetaLine(s) {
    const t = String(s || "").trim();
    if (!t) return false;
    return (
      /^[-*]?\s*(年份|DOI|链接|原文|全文|题名|所属机构|机构|单位|共同论文|合作论文)\s*[:：]/i.test(
        t
      ) ||
      /^[-*]?\s*\[查看全文\]\(https?:\/\//i.test(t) ||
      /^[-*]?\s*查看全文\s*$/i.test(t) ||
      /^\[.+\]\(https?:\/\/www\.academax\.com\/doi\//i.test(t) ||
      /^https?:\/\/www\.academax\.com\/doi\//i.test(t) ||
      /^DOI\s*[:：]/i.test(t)
    );
  }

  function nextNonBlank(lines, from) {
    let j = from;
    while (j < lines.length && /^\s*$/.test(lines[j])) j += 1;
    return j;
  }

  function isSectionHeading(s) {
    const t = String(s || "").trim();
    return (
      /^#{1,4}\s+/.test(t) ||
      /^(主要合作者|合作者机构|机构分布|热门关键词|主要基金|发文量趋势|全部发文|相关论文)/.test(
        t
      )
    );
  }

  /**
   * Convert "标题\\n- 元数据" blocks into one-line ordered items so <ol> works.
   * Covers papers (年份/DOI) and collaborators (所属机构).
   */
  function numberEntryBlocks(text) {
    const lines = text.split("\n");
    const out = [];
    let i = 0;
    let idx = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (isSectionHeading(line)) {
        // restart numbering for each section; keep heading
        idx = 0;
        out.push(line);
        i += 1;
        continue;
      }

      const numbered = line.match(/^\s*(\d+)\.\s+(.+?)\s*$/);
      const plain = line.match(/^\s*(?:[-*]\s*)?(?:题名\s*[:：]\s*)?(.+?)\s*$/);
      const titleText = numbered
        ? numbered[2]
        : plain
          ? plain[1].replace(/^题名\s*[:：]\s*/, "")
          : "";

      const peek = nextNonBlank(lines, i + 1);
      const peekLine = peek < lines.length ? lines[peek] : "";
      const looksEntry =
        titleText &&
        !/^#{1,4}\s/.test(line) &&
        !isEntryMetaLine(line) &&
        !isSectionHeading(titleText) &&
        isEntryMetaLine(peekLine) &&
        !/^(按年|热门|主要|发文量|本刊发文|该作者|年份|统计|结论)/.test(titleText);

      if (looksEntry) {
        idx = numbered ? Number(numbered[1]) : idx + 1;
        let year = "";
        let doi = "";
        let url = "";
        let institution = "";
        let coPapers = "";
        i += 1;
        while (i < lines.length) {
          if (/^\s*$/.test(lines[i])) {
            const n = nextNonBlank(lines, i + 1);
            if (n < lines.length && isEntryMetaLine(lines[n])) {
              i += 1;
              continue;
            }
            break;
          }
          if (!isEntryMetaLine(lines[i])) break;
          const raw = lines[i].trim().replace(/^[-*]\s*/, "");
          const y = raw.match(/^年份\s*[:：]\s*(.+)$/i);
          const d = raw.match(/^DOI\s*[:：]\s*(.+)$/i);
          const inst = raw.match(/^(?:所属机构|机构|单位)\s*[:：]\s*(.+)$/i);
          const co = raw.match(/^(?:共同论文|合作论文)\s*[:：]\s*(.+)$/i);
          const md = raw.match(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/);
          const bare = raw.match(/^(https?:\/\/\S+)/);
          if (y) year = y[1].trim();
          else if (d)
            doi = d[1].trim().replace(/^https?:\/\/www\.academax\.com\/doi\//i, "");
          else if (inst) institution = inst[1].trim();
          else if (co) coPapers = co[1].trim();
          else if (md) url = cleanUrl(md[2]);
          else if (bare) url = cleanUrl(bare[1]);
          else if (/查看全文|链接|原文/i.test(raw) && !url && doi) {
            url = `https://www.academax.com/doi/${doi}`;
          }
          i += 1;
        }
        if (!url && doi) url = `https://www.academax.com/doi/${doi}`;

        // Person line: 施加春（6篇） / paper title
        let title = stripOuterBold(titleText.trim());
        const person = title.match(/^(.+?)\s*[（(]\s*(\d+)\s*篇\s*[)）]\s*$/);
        if (person && institution) {
          out.push(
            `${idx}. **${stripOuterBold(person[1])}**（${person[2]}篇）：${institution}`
          );
        } else if (institution) {
          out.push(`${idx}. **${title}**：${institution}`);
        } else {
          title = title.replace(/\s*DOI\s*[:：].*$/i, "").trim();
          const yearInTitle = /[（(]\s*\d{4}\s*年?\s*[)）]\s*$/.test(title);
          const bits = [`${idx}. **${title}**`];
          if (year && !yearInTitle) bits.push(`（${year}年）`);
          if (coPapers) bits.push(` · 合作 ${coPapers}`);
          if (doi) bits.push(` DOI: ${doi}`);
          if (url) bits.push(` [查看全文](${url})`);
          out.push(bits.join(""));
        }
        continue;
      }

      out.push(line);
      i += 1;
    }
    return out.join("\n");
  }

  /** Remove surrounding ** / __ so we never wrap twice. */
  function stripOuterBold(s) {
    let t = String(s || "").trim();
    for (let i = 0; i < 3; i++) {
      const next = t
        .replace(/^\*{2,}\s*([\s\S]*?)\s*\*{2,}$/, "$1")
        .replace(/^_{2,}\s*([\s\S]*?)\s*_{2,}$/, "$1")
        .trim();
      if (next === t) break;
      t = next;
    }
    return t;
  }

  function renderInline(s) {
    let t = escapeHtml(s);
    const slots = [];
    const park = (html) => {
      const key = `\uE000${slots.length}\uE001`;
      slots.push(html);
      return key;
    };

    // Markdown links first, then bare URLs — park so auto-link
    // cannot re-match inside href="..."
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, text, url) => {
      const u = cleanUrl(url);
      return park(`<a href="${u}" target="_blank" rel="noopener">${text}</a>`);
    });
    // Only match clean URL chars — never eat " target=..."
    t = t.replace(/https?:\/\/[^\s<>"']+/g, (raw) => {
      const url = cleanUrl(raw);
      return park(`<a href="${url}" target="_blank" rel="noopener">${url}</a>`);
    });

    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Allow ****title**** / **title** (model + normalizer may double-wrap)
    t = t.replace(/\*\*+([^*]+)\*\*+/g, "<strong>$1</strong>");
    t = t.replace(/__+([^_]+)__+/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    t = t.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
    t = t.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    // Never leave raw ** in HTML (unpaired leftovers)
    t = t.replace(/\*\*/g, "");

    t = t.replace(/\uE000(\d+)\uE001/g, (_, i) => slots[Number(i)] || "");
    return t;
  }

  function renderTable(rows) {
    if (!rows.length) return "";
    const cells = rows.map((r) =>
      r
        .replace(/^\||\|$/g, "")
        .split("|")
        .map((c) => c.trim())
    );
    if (cells.length < 2) return "";
    const isSep = (row) => row.every((c) => /^:?-+:?$/.test(c));
    let head = cells[0];
    let body = cells.slice(1);
    if (body[0] && isSep(body[0])) body = body.slice(1);
    const th = head.map((c) => `<th>${renderInline(c)}</th>`).join("");
    const tr = body
      .map((r) => `<tr>${r.map((c) => `<td>${renderInline(c)}</td>`).join("")}</tr>`)
      .join("");
    return `<table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`;
  }

  /** Lightweight Markdown → HTML. */
  function renderMarkdown(src) {
    const text = normalizeAnswerText(stripRouteNoise(src));
    const chunks = text.split(/(```[\s\S]*?```)/g);
    const html = chunks
      .map((chunk) => {
        if (chunk.startsWith("```")) {
          const m = chunk.match(/^```([^\n]*)\n?([\s\S]*?)```$/);
          const body = (m ? m[2] : chunk.slice(3, -3)).replace(/\n$/, "");
          return `<pre><code>${escapeHtml(body)}</code></pre>`;
        }
        return renderMdBlocks(chunk);
      })
      .join("");
    return `<div class="md">${html}</div>`;
  }

  function renderMdBlocks(src) {
    const lines = src.split("\n");
    const out = [];
    let listType = null; // top-level ol|ul
    let openLi = false;
    let nestedUl = false;
    let tableBuf = [];

    const closeNestedUl = () => {
      if (nestedUl) {
        out.push("</ul>");
        nestedUl = false;
      }
    };
    const closeLi = () => {
      closeNestedUl();
      if (openLi) {
        out.push("</li>");
        openLi = false;
      }
    };
    const closeList = () => {
      closeLi();
      if (listType) {
        out.push(listType === "ol" ? "</ol>" : "</ul>");
        listType = null;
      }
    };
    const flushTable = () => {
      if (tableBuf.length) {
        out.push(renderTable(tableBuf));
        tableBuf = [];
      }
    };
    const peekListKind = (from) => {
      for (let j = from; j < lines.length; j++) {
        const raw = lines[j].trimEnd();
        if (!raw.trim()) continue;
        if (/^\d+\.\s+/.test(raw)) return "ol";
        if (/^\s{0,3}[-*+]\s+/.test(raw)) return "ul";
        if (/^\s{2,}[-*+]\s+/.test(raw)) return "nested";
        return null;
      }
      return null;
    };

    for (let idx = 0; idx < lines.length; idx++) {
      const line = lines[idx].trimEnd();
      if (/^\|?.+\|.+\|?$/.test(line.trim()) && line.includes("|")) {
        closeList();
        tableBuf.push(line.trim());
        continue;
      }
      flushTable();

      if (!line.trim()) {
        const kind = peekListKind(idx + 1);
        // Keep a top-level list open across blank lines when next item continues it
        // (including nested bullets under an ordered list).
        if (listType === "ol" && (kind === "ol" || kind === "nested")) continue;
        if (listType === "ul" && kind === "ul") continue;
        closeList();
        continue;
      }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
        closeList();
        out.push("<hr>");
        continue;
      }
      const h = line.match(/^(#{1,4})\s+(.+)$/);
      if (h) {
        closeList();
        const n = h[1].length;
        out.push(`<h${n}>${renderInline(h[2])}</h${n}>`);
        continue;
      }
      if (/^>\s?/.test(line)) {
        closeList();
        out.push(`<blockquote><p>${renderInline(line.replace(/^>\s?/, ""))}</p></blockquote>`);
        continue;
      }

      // Nested bullets under the current ordered-list item
      const nested = line.match(/^\s{2,}[-*+]\s+(.+)$/);
      if (nested && listType === "ol" && openLi) {
        if (!nestedUl) {
          out.push("<ul>");
          nestedUl = true;
        }
        out.push(`<li>${renderInline(nested[1])}</li>`);
        continue;
      }

      const ul = line.match(/^[-*+]\s+(.+)$/);
      if (ul) {
        if (listType !== "ul") {
          closeList();
          out.push("<ul>");
          listType = "ul";
        } else {
          closeLi();
        }
        out.push(`<li>${renderInline(ul[1])}`);
        openLi = true;
        continue;
      }
      const ol = line.match(/^\d+\.\s+(.+)$/);
      if (ol) {
        if (listType !== "ol") {
          closeList();
          out.push("<ol>");
          listType = "ol";
        } else {
          closeLi();
        }
        out.push(`<li>${renderInline(ol[1])}`);
        openLi = true;
        continue;
      }
      closeList();
      out.push(`<p>${renderInline(line)}</p>`);
    }
    flushTable();
    closeList();
    return out.join("");
  }

  function formatCitations(citations) {
    if (!Array.isArray(citations) || !citations.length) return "";
    const items = citations
      .slice(0, 6)
      .map((c) => {
        const title = escapeHtml(c.title || c.doi || "文献");
        const url = c.url || (c.doi ? `https://www.academax.com/doi/${c.doi}` : "");
        return url
          ? `<li><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${title}</a></li>`
          : `<li>${title}</li>`;
      })
      .join("");
    return `<div class="refs"><ul>${items}</ul></div>`;
  }

  function appendBubble(role, html) {
    const div = document.createElement("div");
    div.className = `bubble ${role}`;
    div.innerHTML = html;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function switchTab(name) {
    $$(".tab").forEach((btn) => btn.classList.toggle("is-active", btn.dataset.tab === name));
    $$(".panel").forEach((panel) => {
      const active = panel.id === `panel-${name}`;
      panel.classList.toggle("is-active", active);
      panel.hidden = !active;
    });
    if (name === "trends" && !trendsLoaded) loadTrends();
    if (name === "graph" && !graphCanvas.querySelector("svg")) {
      loadGraph().catch(() => {});
    }
  }

  async function askQuestion(question) {
    const q = question.trim();
    if (!q) return;
    setPresetsVisible(false);
    appendBubble("user", escapeHtml(q));
    questionEl.value = "";
    askBtn.disabled = true;
    const pending = appendBubble("bot", '<div class="md"><p class="stream-status">检索与分析中…</p></div>');
    const resetFlag = needsReset;
    needsReset = false;
    let acc = "";
    let citations = [];

    const paint = (finalAnswer) => {
      const text = finalAnswer != null ? finalAnswer : acc;
      pending.innerHTML =
        renderMarkdown(text || "（无回答）") + formatCitations(citations);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    };

    try {
      const res = await fetch("/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: q,
          reset: resetFlag,
          journal_id: currentJournalId(),
        }),
      });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const errBody = await res.json();
          detail = errBody?.detail || detail;
        } catch (_) {}
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      if (!res.body) throw new Error("浏览器不支持流式响应");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const part of parts) {
          const line = part
            .split("\n")
            .map((l) => l.trim())
            .find((l) => l.startsWith("data:"));
          if (!line) continue;
          let ev;
          try {
            ev = JSON.parse(line.slice(5).trim());
          } catch (_) {
            continue;
          }
          if (ev.type === "status") {
            if (!acc) {
              pending.innerHTML = `<div class="md"><p class="stream-status">${escapeHtml(
                ev.message || "处理中…"
              )}</p></div>`;
            }
          } else if (ev.type === "delta") {
            acc += ev.text || "";
            paint();
          } else if (ev.type === "done") {
            citations = ev.citations || [];
            paint(ev.answer || acc || "（无回答）");
          } else if (ev.type === "error") {
            throw new Error(ev.message || "流式请求失败");
          }
        }
      }
      if (!acc && !pending.querySelector("strong, p, li, ol, ul")) {
        pending.textContent = "（无回答）";
      }
    } catch (err) {
      if (acc) {
        paint();
        const note = document.createElement("p");
        note.className = "stream-error";
        note.textContent = `（流式中断：${err.message || err}）`;
        pending.appendChild(note);
      } else {
        pending.textContent = `请求失败：${err.message || err}`;
      }
    } finally {
      askBtn.disabled = false;
      questionEl.focus();
    }
  }

  function tipEl() {
    return $("#chart-tooltip");
  }

  function showTip(clientX, clientY, html) {
    const tip = tipEl();
    if (!tip) return;
    tip.hidden = false;
    tip.innerHTML = html;
    tip.style.left = `${clientX}px`;
    tip.style.top = `${clientY}px`;
  }

  function hideTip() {
    const tip = tipEl();
    if (tip) tip.hidden = true;
  }

  function svgEl(name, attrs = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
    return node;
  }

  function drawLineChart(el, rows) {
    const w = 720;
    const h = 280;
    const pad = { t: 20, r: 18, b: 36, l: 44 };
    const years = rows.map((r) => Number(r.year));
    const vals = rows.map((r) => Number(r.paper_count) || 0);
    el.innerHTML = "";
    if (!years.length) {
      el.textContent = "暂无数据";
      return;
    }
    const minY = Math.min(...years);
    const maxY = Math.max(...years);
    const maxV = Math.max(...vals, 1);
    const iw = w - pad.l - pad.r;
    const ih = h - pad.t - pad.b;
    const x = (year) => pad.l + ((year - minY) / Math.max(maxY - minY, 1)) * iw;
    const y = (v) => pad.t + ih - (v / maxV) * ih;

    const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = Math.round((maxV * i) / ticks);
      const yy = y(v);
      svg.appendChild(
        svgEl("line", {
          class: "grid",
          x1: pad.l,
          y1: yy,
          x2: pad.l + iw,
          y2: yy,
        })
      );
      const t = svgEl("text", { class: "axis", x: pad.l - 8, y: yy + 4, "text-anchor": "end" });
      t.textContent = String(v);
      svg.appendChild(t);
    }
    [minY, Math.round((minY + maxY) / 2), maxY].forEach((yr) => {
      const t = svgEl("text", { class: "axis", x: x(yr), y: h - 10, "text-anchor": "middle" });
      t.textContent = String(yr);
      svg.appendChild(t);
    });

    const pts = years.map((yr, i) => `${x(yr)},${y(vals[i])}`).join(" ");
    svg.appendChild(
      svgEl("polygon", {
        class: "area",
        points: `${pad.l},${pad.t + ih} ${pts} ${pad.l + iw},${pad.t + ih}`,
      })
    );
    svg.appendChild(svgEl("polyline", { class: "line", points: pts }));

    years.forEach((yr, i) => {
      const cx = x(yr);
      const cy = y(vals[i]);
      const hit = svgEl("circle", { class: "hit", cx, cy, r: 14 });
      const point = svgEl("circle", {
        class: `point${selectedYear === yr ? " is-active" : ""}`,
        cx,
        cy,
        r: selectedYear === yr ? 6 : 4.5,
      });
      const onEnter = (ev) => {
        point.classList.add("is-active");
        showTip(ev.clientX, ev.clientY, `<strong>${yr}</strong> 年<br>${vals[i]} 篇`);
      };
      const onMove = (ev) => showTip(ev.clientX, ev.clientY, `<strong>${yr}</strong> 年<br>${vals[i]} 篇`);
      const onLeave = () => {
        if (selectedYear !== yr) point.classList.remove("is-active");
        hideTip();
      };
      const onClick = () => {
        selectedYear = selectedYear === yr ? null : yr;
        hideTip();
        refreshKeywordChart();
        drawLineChart(el, rows);
        const clearBtn = $("#clear-year-filter");
        if (clearBtn) clearBtn.hidden = selectedYear == null;
      };
      [hit, point].forEach((n) => {
        n.addEventListener("pointerenter", onEnter);
        n.addEventListener("pointermove", onMove);
        n.addEventListener("pointerleave", onLeave);
        n.addEventListener("click", onClick);
      });
      svg.appendChild(hit);
      svg.appendChild(point);
    });
    el.appendChild(svg);
  }

  function drawBarChart(el, rows) {
    const data = rows.slice(0, 12);
    el.innerHTML = "";
    if (!data.length) {
      el.textContent = "暂无数据";
      return;
    }
    const w = 720;
    const rowH = 26;
    const pad = { t: 8, r: 52, b: 8, l: 120 };
    const h = pad.t + pad.b + data.length * rowH;
    const maxV = Math.max(...data.map((r) => Number(r.paper_count) || 0), 1);
    const iw = w - pad.l - pad.r;
    const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });

    data.forEach((r, i) => {
      const v = Number(r.paper_count) || 0;
      const bw = Math.max((v / maxV) * iw, 2);
      const yy = pad.t + i * rowH + 4;
      const label = String(r.keyword || "");
      const short = label.length > 10 ? label.slice(0, 9) + "…" : label;
      const text = svgEl("text", {
        class: "label",
        x: pad.l - 8,
        y: yy + 13,
        "text-anchor": "end",
      });
      text.textContent = short;
      const bar = svgEl("rect", {
        class: "bar",
        x: pad.l,
        y: yy,
        width: bw,
        height: 16,
        rx: 3,
      });
      const num = svgEl("text", { class: "axis", x: pad.l + bw + 6, y: yy + 13 });
      num.textContent = String(v);
      bar.addEventListener("pointerenter", (ev) => {
        bar.classList.add("is-hot");
        showTip(ev.clientX, ev.clientY, `<strong>${escapeHtml(label)}</strong><br>${v} 篇`);
      });
      bar.addEventListener("pointermove", (ev) => {
        showTip(ev.clientX, ev.clientY, `<strong>${escapeHtml(label)}</strong><br>${v} 篇`);
      });
      bar.addEventListener("pointerleave", () => {
        bar.classList.remove("is-hot");
        hideTip();
      });
      bar.addEventListener("click", () => {
        hideTip();
        // jump to graph filtered by keyword
        $("#graph-mode").value = "keyword";
        $("#graph-query").value = label;
        switchTab("graph");
        loadGraph().catch(() => {});
      });
      svg.appendChild(text);
      svg.appendChild(bar);
      svg.appendChild(num);
    });
    el.appendChild(svg);
  }

  async function refreshKeywordChart() {
    const cap = $("#keyword-caption");
    const qs = new URLSearchParams({ limit: "12" });
    if (selectedYear != null) {
      qs.set("start_year", String(selectedYear));
      qs.set("end_year", String(selectedYear));
      if (cap) cap.textContent = `${selectedYear} 年热门关键词`;
    } else if (cap) {
      cap.textContent = "热门关键词";
    }
    try {
      const kwRes = await api(withJournal(`/trends/keywords?${qs}`));
      drawBarChart($("#keyword-chart"), kwRes.keywords || []);
    } catch (err) {
      $("#keyword-chart").textContent = `加载失败：${err.message || err}`;
    }
  }

  async function loadTrends() {
    try {
      const yearlyRes = await api(withJournal("/trends/yearly"));
      yearlyRowsCache = yearlyRes.yearly || [];
      drawLineChart($("#yearly-chart"), yearlyRowsCache);
      await refreshKeywordChart();
      trendsLoaded = true;
      const clearBtn = $("#clear-year-filter");
      if (clearBtn) {
        clearBtn.hidden = selectedYear == null;
        clearBtn.onclick = () => {
          selectedYear = null;
          clearBtn.hidden = true;
          drawLineChart($("#yearly-chart"), yearlyRowsCache);
          refreshKeywordChart();
        };
      }
    } catch (err) {
      $("#yearly-chart").textContent = `加载失败：${err.message || err}`;
    }
  }

  function setPresetsVisible(visible) {
    const el = $("#presets");
    if (!el) return;
    el.hidden = !visible;
  }

  function renderPresets() {
    const el = $("#presets");
    if (!el) return;
    el.innerHTML =
      `<span class="presets-label">示例</span>` +
      getPresets().map(
        (p) =>
          `<button type="button" data-q="${escapeHtml(p.q)}">${escapeHtml(p.label)}</button>`
      ).join("");
    el.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => askQuestion(btn.dataset.q || ""));
    });
  }

  function showWelcome() {
    messagesEl.innerHTML = "";
    setPresetsVisible(true);
  }

  function stopGraph() {
    if (graphAnim) {
      cancelAnimationFrame(graphAnim);
      graphAnim = null;
    }
    if (graphNetwork) {
      try {
        graphNetwork.destroy();
      } catch (_) {
        /* ignore */
      }
      graphNetwork = null;
    }
    graphNodesDS = null;
    graphEdgesDS = null;
    graphHubId = null;
  }

  function shortLabel(text, n = 10) {
    const t = String(text || "").trim();
    return t.length <= n ? t : t.slice(0, n - 1) + "…";
  }

  function graphCacheKey(mode, q) {
    return `${mode}:${String(q || "").trim()}`;
  }

  function putGraphCache(key, data) {
    if (graphCache.has(key)) graphCache.delete(key);
    graphCache.set(key, data);
    while (graphCache.size > GRAPH_CACHE_MAX) {
      const oldest = graphCache.keys().next().value;
      graphCache.delete(oldest);
    }
  }

  function buildVisNodes(rawNodes, hubId) {
    return rawNodes.map((n) => {
      const group = n.group || "author";
      const isHub = n.id === hubId;
      const color = COLORS[group] || COLORS.author;
      const size = isHub ? 26 : 10 + Math.min(Number(n.value) || 1, 14);
      return {
        id: n.id,
        label: shortLabel(n.label || n.id, isHub ? 12 : 9),
        title: `${GROUP_LABELS[group] || group} · ${n.title || n.label || n.id}`,
        group,
        value: Number(n.value) || 1,
        query: n.query || n.label || n.id,
        fullLabel: n.label || n.id,
        shape: GRAPH_SHAPES[group] || "dot",
        size,
        borderWidth: isHub ? 3 : 1.5,
        borderWidthSelected: 3,
        color: {
          background: color,
          border: isHub ? "#e37400" : color,
          highlight: { background: color, border: "#202124" },
          hover: { background: color, border: "#202124" },
        },
        font: {
          color: "#202124",
          size: isHub ? 14 : 12,
          face: "PingFang SC, Hiragino Sans GB, Microsoft YaHei, sans-serif",
          strokeWidth: 3,
          strokeColor: "#ffffff",
        },
      };
    });
  }

  function buildVisEdges(rawEdges) {
    return rawEdges.map((e, i) => ({
      id: e.id || `e${i}`,
      from: e.from,
      to: e.to,
      value: Math.max(1, Number(e.value) || 1),
      title: e.title || e.label || "",
      color: {
        color: "#c5cad3",
        highlight: "#5f6368",
        hover: "#80868b",
        opacity: 0.8,
      },
      smooth: false,
    }));
  }

  function physicsOptions(stabilize) {
    return {
      enabled: !!stabilize,
      solver: "barnesHut",
      barnesHut: {
        gravitationalConstant: -2800,
        centralGravity: 0.25,
        springLength: 95,
        springConstant: 0.04,
        damping: 0.55,
        avoidOverlap: 0.2,
      },
      stabilization: stabilize
        ? { enabled: true, iterations: 36, fit: true }
        : { enabled: false },
    };
  }

  const GRAPH_INTERACTION = {
    hover: true,
    tooltipDelay: 100,
    hideEdgesOnDrag: true,
    navigationButtons: true,
    keyboard: { enabled: false },
    // Disable default wheel-zoom: trackpad inertia over the canvas feels like "mouse enter = zoom"
    zoomView: false,
    dragView: true,
    dragNodes: true,
    multiselect: false,
  };

  let graphPointerDirty = false;

  /** Ctrl/⌘ + wheel zooms; plain wheel leaves page/trackpad scroll alone. */
  function bindGraphCtrlZoom() {
    if (!graphCanvas || graphCanvas.dataset.ctrlZoomBound === "1") return;
    graphCanvas.dataset.ctrlZoomBound = "1";
    graphCanvas.addEventListener(
      "wheel",
      (ev) => {
        if (!graphNetwork) return;
        if (!(ev.ctrlKey || ev.metaKey)) return;
        ev.preventDefault();
        ev.stopPropagation();
        const scale = graphNetwork.getScale() || 1;
        const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
        const next = Math.min(3.5, Math.max(0.25, scale * factor));
        graphNetwork.moveTo({ scale: next, animation: false });
      },
      { passive: false }
    );
  }

  /** Break stuck drag/pan state (common after mid-drag DataSet updates). */
  function releaseGraphPointer(force = false) {
    if (!graphNetwork) return;
    if (!force && !graphPointerDirty) return;
    graphPointerDirty = false;
    try {
      graphNetwork.setOptions({
        physics: { enabled: false },
        interaction: {
          ...GRAPH_INTERACTION,
          dragView: false,
          dragNodes: false,
        },
      });
      graphNetwork.setOptions({ interaction: { ...GRAPH_INTERACTION } });
    } catch (_) {
      /* ignore */
    }
  }

  function expandNodeAsCenter(meta, hubId) {
    if (!meta) return;
    const modeMap = {
      author: "author",
      collaborator: "author",
      keyword: "keyword",
      institution: "institution",
      paper: "paper",
    };
    const mode = modeMap[meta.group];
    if (!mode) return;
    const q = String(meta.query || meta.label || "").trim();
    if (!q) return;
    if (hubId && meta.id === hubId) {
      if (graphNetwork) {
        releaseGraphPointer(true);
        graphNetwork.selectNodes([hubId]);
        graphNetwork.fit({
          animation: { duration: 180, easingFunction: "easeInOutQuad" },
        });
      }
      return;
    }
    const modeEl = $("#graph-mode");
    const queryEl = $("#graph-query");
    if (modeEl) modeEl.value = mode;
    if (queryEl) {
      queryEl.value = q;
      queryEl.placeholder = GRAPH_HINTS[mode] || "";
    }
    graphStatus.textContent = `展开中心：${meta.label}…`;
    releaseGraphPointer(true);
    loadGraph().catch(() => {});
  }

  function updateGraphStatus(data, rawNodes) {
    const counts = {};
    for (const n of rawNodes) counts[n.group] = (counts[n.group] || 0) + 1;
    const parts = Object.entries(counts)
      .map(([g, c]) => `${GROUP_LABELS[g] || g}${c}`)
      .join(" · ");

    let head = parts || `${rawNodes.length} 节点`;
    if (data.author) {
      const a = data.author;
      head = `${a.name_zh || a.name_en || ""} · 发文 ${a.paper_count ?? "-"} · ${parts}`;
    } else if (data.keyword) {
      const k = data.keyword;
      head = `${k.label || ""} · 相关 ${k.paper_count ?? "-"} 篇 · ${parts}`;
    } else if (data.institution) {
      const i = data.institution;
      head = `${i.name || ""} · ${i.paper_count ?? "-"} 篇 · ${parts}`;
    } else if (data.paper) {
      head = `${data.paper.title || data.paper.doi || ""} · ${parts}`;
    }
    graphStatus.textContent = `${head}　·　点击节点展开两跳子图 / 拖拽平移 / Ctrl+滚轮或右下角按钮缩放`;
  }

  function buildLegend(presentGroups, enabledGroups, nodeItems, hubId) {
    if (!graphLegend) return;
    graphLegend.innerHTML = "";
    const title = document.createElement("div");
    title.className = "legend-title";
    title.textContent = "类型筛选";
    graphLegend.appendChild(title);
    presentGroups.forEach((g) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.group = g;
      if (!enabledGroups.has(g)) btn.classList.add("is-off");
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = COLORS[g] || "#888";
      const lab = document.createElement("span");
      lab.textContent = GROUP_LABELS[g] || g;
      btn.appendChild(sw);
      btn.appendChild(lab);
      btn.addEventListener("click", () => {
        if (enabledGroups.has(g)) {
          if (enabledGroups.size <= 1) return;
          enabledGroups.delete(g);
          btn.classList.add("is-off");
        } else {
          enabledGroups.add(g);
          btn.classList.remove("is-off");
        }
        if (!graphNodesDS) return;
        graphNodesDS.update(
          nodeItems.map((n) => ({
            id: n.id,
            hidden: !enabledGroups.has(n.group),
          }))
        );
        if (hubId && !enabledGroups.has(nodeItems.find((n) => n.id === hubId)?.group)) {
          const next = nodeItems.find((n) => enabledGroups.has(n.group));
          if (next && graphNetwork) {
            graphNetwork.selectNodes([next.id]);
            graphNetwork.focus(next.id, {
              scale: graphNetwork.getScale(),
              animation: { duration: 180 },
            });
          }
        }
      });
      graphLegend.appendChild(btn);
    });
  }

  function renderGraph(data) {
    const rawNodes = data.nodes || [];
    const rawEdges = data.edges || [];
    if (!rawNodes.length) {
      stopGraph();
      graphCanvas.innerHTML = "";
      if (graphLegend) graphLegend.innerHTML = "";
      graphStatus.textContent = "无节点";
      return;
    }
    if (typeof vis === "undefined" || !vis.Network) {
      stopGraph();
      graphCanvas.innerHTML = "";
      graphStatus.textContent = "图谱组件加载失败，请检查网络后刷新";
      return;
    }

    const hubId = rawNodes[0].id;
    graphHubId = hubId;
    const presentGroups = [];
    const seenG = new Set();
    for (const n of rawNodes) {
      const g = n.group || "author";
      if (!seenG.has(g)) {
        seenG.add(g);
        presentGroups.push(g);
      }
    }
    const enabledGroups = new Set(presentGroups);
    const nodeItems = buildVisNodes(rawNodes, hubId);
    const edgeItems = buildVisEdges(rawEdges);

    const finishLayout = (() => {
      let done = false;
      return () => {
        if (done || !graphNetwork) return;
        done = true;
        graphNetwork.setOptions({ physics: physicsOptions(false) });
        // fit only — avoid focus({scale}) which looks like unexpected zoom
        graphNetwork.fit({
          animation: { duration: 180, easingFunction: "easeInOutQuad" },
        });
        graphNetwork.selectNodes([hubId]);
      };
    })();

    if (graphNetwork && graphNodesDS && graphEdgesDS) {
      releaseGraphPointer(true);
      graphNetwork.setOptions({ physics: physicsOptions(true) });
      graphNodesDS.clear();
      graphEdgesDS.clear();
      graphNodesDS.add(nodeItems);
      graphEdgesDS.add(edgeItems);
      graphNetwork.once("stabilizationIterationsDone", finishLayout);
      setTimeout(finishLayout, 400);
    } else {
      graphNodesDS = new vis.DataSet(nodeItems);
      graphEdgesDS = new vis.DataSet(edgeItems);
      graphCanvas.innerHTML = "";
      graphNetwork = new vis.Network(
        graphCanvas,
        { nodes: graphNodesDS, edges: graphEdgesDS },
        {
          autoResize: true,
          interaction: { ...GRAPH_INTERACTION },
          physics: physicsOptions(true),
          nodes: { scaling: { min: 10, max: 32 }, margin: 6 },
          edges: {
            width: 1.1,
            selectionWidth: 2,
            hoverWidth: 1.6,
            scaling: { min: 1, max: 3 },
          },
        }
      );
      bindGraphCtrlZoom();
      graphNetwork.once("stabilizationIterationsDone", finishLayout);
      graphNetwork.on("dragStart", () => {
        graphPointerDirty = true;
      });
      graphNetwork.on("dragEnd", () => releaseGraphPointer(true));
      graphNetwork.on("click", (params) => {
        if (!params.nodes.length) return;
        const id = params.nodes[0];
        const meta = graphNodesDS.get(id);
        if (!meta || meta.hidden) return;
        const payload = {
          id: meta.id,
          group: meta.group,
          label: meta.fullLabel || meta.label,
          query: meta.query,
        };
        // Defer so vis finishes mouseup / internal drag cleanup before we swap data
        setTimeout(() => expandNodeAsCenter(payload, graphHubId), 40);
      });
      if (!window.__graphPointerReleaseBound) {
        window.__graphPointerReleaseBound = true;
        const onUp = () => releaseGraphPointer(false);
        window.addEventListener("pointerup", onUp);
        window.addEventListener("pointercancel", onUp);
      }
    }

    buildLegend(presentGroups, enabledGroups, nodeItems, hubId);
    updateGraphStatus(data, rawNodes);

    // Prefetch 1-hop neighbors for snappier next clicks
    const modeMap = {
      author: "author",
      collaborator: "author",
      keyword: "keyword",
      institution: "institution",
      paper: "paper",
    };
    const prefetch = [];
    for (const n of rawNodes.slice(0, 18)) {
      if (n.id === hubId) continue;
      const mode = modeMap[n.group];
      const q = String(n.query || n.label || "").trim();
      if (!mode || !q) continue;
      const key = graphCacheKey(mode, q);
      if (!graphCache.has(key)) prefetch.push({ mode, q, key });
    }
    if (prefetch.length) {
      setTimeout(() => {
        prefetch.slice(0, 6).forEach(({ mode, q, key }) => {
          fetchGraphData(mode, q)
            .then((d) => putGraphCache(key, d))
            .catch(() => {});
        });
      }, 80);
    }
  }

  const GRAPH_HINTS = {
    author: "请输入作者姓名",
    keyword: "请输入关键词",
    institution: "请输入机构名，如：浙江大学",
    paper: "请输入论文 DOI",
  };

  async function fetchGraphData(mode, q) {
    if (mode === "author") {
      return api(
        withJournal(`/graph/network/author?name=${encodeURIComponent(q)}&limit=20`)
      );
    }
    if (mode === "keyword") {
      return api(
        withJournal(
          `/graph/network/keyword?keyword=${encodeURIComponent(q)}&limit=20`
        )
      );
    }
    if (mode === "institution") {
      return api(
        withJournal(
          `/graph/network/institution?name=${encodeURIComponent(q)}&limit=20`
        )
      );
    }
    return api(withJournal(`/graph/network/paper?doi=${encodeURIComponent(q)}`));
  }

  async function loadGraph() {
    const mode = $("#graph-mode").value;
    const q = $("#graph-query").value.trim();
    if (!q) {
      graphStatus.textContent = GRAPH_HINTS[mode] || "请输入查询";
      return;
    }
    const key = graphCacheKey(mode, q);
    const seq = ++graphLoadSeq;
    const cached = graphCache.get(key);
    graphLoading = !cached;

    if (cached) {
      renderGraph(cached);
    } else {
      graphStatus.textContent = "加载中…";
    }

    try {
      const data = await fetchGraphData(mode, q);
      if (seq !== graphLoadSeq) return;
      putGraphCache(key, data);
      // Skip identical re-render when cache already shown and payload unchanged
      if (
        !cached ||
        cached.nodes?.length !== data.nodes?.length ||
        cached.edges?.length !== data.edges?.length ||
        cached.nodes?.[0]?.id !== data.nodes?.[0]?.id
      ) {
        renderGraph(data);
      } else {
        updateGraphStatus(data, data.nodes || []);
      }
    } catch (err) {
      if (seq !== graphLoadSeq) return;
      if (!cached) {
        stopGraph();
        graphCanvas.innerHTML = "";
        graphStatus.textContent = `失败：${err.message || err}`;
      }
    } finally {
      if (seq === graphLoadSeq) graphLoading = false;
    }
  }

  $$(".tab").forEach((btn) => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));
  askForm.addEventListener("submit", (e) => {
    e.preventDefault();
    askQuestion(questionEl.value);
  });
  resetBtn.addEventListener("click", () => {
    messagesEl.innerHTML = "";
    needsReset = true;
    showWelcome();
  });
  graphForm.addEventListener("submit", (e) => {
    e.preventDefault();
    loadGraph();
  });
  $("#graph-mode").addEventListener("change", () => {
    const mode = $("#graph-mode").value;
    const input = $("#graph-query");
    const defaults = getGraphDefaults();
    const allDefaults = [
      ...Object.values(GRAPH_DEFAULTS_BY_JOURNAL.ZDXBNXB),
      ...Object.values(GRAPH_DEFAULTS_BY_JOURNAL.ZDXBRWB),
    ];
    input.placeholder = GRAPH_HINTS[mode] || "";
    if (!input.value.trim() || allDefaults.includes(input.value.trim())) {
      input.value = defaults[mode] || "";
    }
  });

  if (journalSelect) {
    journalSelect.addEventListener("change", () => {
      needsReset = true;
      messagesEl.innerHTML = "";
      showWelcome();
      renderPresets();
      trendsLoaded = false;
      selectedYear = null;
      yearlyRowsCache = [];
      graphCache.clear();
      stopGraph();
      graphCanvas.innerHTML = "";
      graphStatus.textContent = "";
      const mode = $("#graph-mode").value;
      $("#graph-query").value = getGraphDefaults()[mode] || "";
      $("#graph-query").placeholder = GRAPH_HINTS[mode] || "";
      const active = document.querySelector(".tab.is-active")?.dataset?.tab;
      if (active === "trends") loadTrends();
    });
  }

  renderPresets();
  showWelcome();
  {
    const mode = $("#graph-mode").value;
    $("#graph-query").value = getGraphDefaults()[mode] || "";
    $("#graph-query").placeholder = GRAPH_HINTS[mode] || "";
  }
})();
