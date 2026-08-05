(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const messagesEl = $("#messages");
  const askForm = $("#ask-form");
  const questionEl = $("#question");
  const askBtn = $("#ask-btn");
  const resetBtn = $("#reset-btn");
  const graphForm = $("#graph-form");
  const graphCanvas = $("#graph-canvas");
  const graphLegend = $("#graph-legend");
  const graphStatus = $("#graph-status");

  let needsReset = false;
  let trendsLoaded = false;
  let graphAnim = null;
  let selectedYear = null;
  let yearlyRowsCache = [];

  const PRESETS = [
    { q: "徐建明全部发文", label: "徐建明全部发文" },
    { q: "徐建明合作的作者所属机构情况", label: "徐建明合作的作者所属机构情况" },
    { q: "近十年研究趋势", label: "近十年研究趋势" },
  ];

  const COLORS = {
    author: "#1f6b57",
    collaborator: "#4d9a7f",
    paper: "#b87333",
    keyword: "#5b7c99",
    institution: "#7a6a4f",
    fund: "#8a5a7a",
    clc: "#6b6b6b",
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
    return t;
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
        body: JSON.stringify({ question: q, reset: resetFlag }),
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
      const kwRes = await api(`/trends/keywords?${qs}`);
      drawBarChart($("#keyword-chart"), kwRes.keywords || []);
    } catch (err) {
      $("#keyword-chart").textContent = `加载失败：${err.message || err}`;
    }
  }

  async function loadTrends() {
    try {
      const yearlyRes = await api("/trends/yearly");
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

  function showWelcome() {
    const items = PRESETS.map(
      (p, i) =>
        `<button type="button" class="welcome-preset" data-q="${escapeHtml(p.q)}"><span class="n">${i + 1}.</span> ${escapeHtml(p.label)}</button>`
    ).join("");
    const html = `
      <div class="md">
        <p>你好，我是<strong>青禾</strong>——《浙江大学学报（农业与生命科学版）》的知识助手。</p>
        <p>可以帮你查作者发文与合作机构，也可以看研究趋势、浏览知识图谱。直接提问，或点下面的示例开始：</p>
      </div>
      <div class="welcome-presets">${items}</div>
    `;
    const bubble = appendBubble("bot", html);
    bubble.querySelectorAll(".welcome-preset").forEach((btn) => {
      btn.addEventListener("click", () => askQuestion(btn.dataset.q || ""));
    });
  }

  function stopGraph() {
    if (graphAnim) {
      cancelAnimationFrame(graphAnim);
      graphAnim = null;
    }
  }

  function renderGraph(data) {
    stopGraph();
    const width = Math.max(graphCanvas.clientWidth || 800, 320);
    const height = Math.max(graphCanvas.clientHeight || 520, 360);
    const rawNodes = data.nodes || [];
    const rawEdges = data.edges || [];
    if (!rawNodes.length) {
      graphCanvas.innerHTML = "";
      if (graphLegend) graphLegend.innerHTML = "";
      graphStatus.textContent = "无节点";
      return;
    }

    const nodes = rawNodes.map((n, i) => {
      const angle = (i / Math.max(rawNodes.length, 1)) * Math.PI * 2;
      const baseR = 10 + Math.min(Number(n.value) || 1, 20) * 0.7;
      return {
        id: n.id,
        label: n.label || n.id,
        tip: n.title || n.label || n.id,
        group: n.group || "author",
        value: Number(n.value) || 1,
        baseR,
        x: width / 2 + Math.cos(angle) * Math.min(width, height) * 0.28,
        y: height / 2 + Math.sin(angle) * Math.min(width, height) * 0.28,
        vx: 0,
        vy: 0,
        fx: null,
        fy: null,
        visible: true,
      };
    });
    let hub = nodes[0];
    hub.x = width / 2;
    hub.y = height / 2;
    hub.fx = width / 2;
    hub.fy = height / 2;

    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const links = rawEdges
      .map((e) => ({
        source: byId[e.from],
        target: byId[e.to],
        value: Number(e.value) || 1,
      }))
      .filter((e) => e.source && e.target);

    const presentGroups = [];
    const seenG = new Set();
    for (const n of nodes) {
      if (!seenG.has(n.group)) {
        seenG.add(n.group);
        presentGroups.push(n.group);
      }
    }
    const enabledGroups = new Set(presentGroups);

    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", "100%");
    svg.style.touchAction = "none";
    svg.style.cursor = "grab";

    const bg = document.createElementNS(svgNS, "rect");
    bg.setAttribute("x", "0");
    bg.setAttribute("y", "0");
    bg.setAttribute("width", String(width));
    bg.setAttribute("height", String(height));
    bg.setAttribute("fill", "transparent");
    svg.appendChild(bg);

    const world = document.createElementNS(svgNS, "g");
    const linkG = document.createElementNS(svgNS, "g");
    const nodeG = document.createElementNS(svgNS, "g");
    world.appendChild(linkG);
    world.appendChild(nodeG);
    svg.appendChild(world);
    graphCanvas.innerHTML = "";
    graphCanvas.appendChild(svg);

    // Zoom/pan via world transform → node radius & text scale together
    const view = { x: 0, y: 0, k: 1 };
    let dragging = null;
    let dragMoved = false;
    let dragStart = null;
    let panning = null;
    let alpha = 1;

    const linkEls = links.map((l) => {
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("class", "link");
      line.setAttribute("stroke-width", String(1.2 + Math.min(l.value, 6) * 0.45));
      linkG.appendChild(line);
      return line;
    });

    function clientToWorld(clientX, clientY) {
      const rect = svg.getBoundingClientRect();
      const sx = ((clientX - rect.left) * width) / rect.width;
      const sy = ((clientY - rect.top) * height) / rect.height;
      return { x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
    }

    function syncNodeVisual(n, el) {
      const r = n.baseR;
      el.hit.setAttribute("r", String(Math.max(r + 10, 20)));
      el.dot.setAttribute("r", String(r));
      // font size tied to node radius → stays proportional under zoom
      const fs = Math.max(10, Math.min(16, r * 0.95));
      el.label.setAttribute("font-size", String(fs));
      el.label.setAttribute("dy", String(r + fs * 0.95));
      el.g.classList.toggle("is-hub", hub && hub.id === n.id);
      el.g.classList.toggle("is-dim", !n.visible);
    }

    function applyFilter() {
      nodes.forEach((n) => {
        n.visible = enabledGroups.has(n.group);
      });
      links.forEach((l, i) => {
        const on = l.source.visible && l.target.visible;
        linkEls[i].classList.toggle("is-dim", !on);
      });
      nodes.forEach((n, i) => syncNodeVisual(n, nodeEls[i]));
      paint();
    }

    function paint() {
      world.setAttribute(
        "transform",
        `translate(${view.x},${view.y}) scale(${view.k})`
      );
      links.forEach((l, i) => {
        linkEls[i].setAttribute("x1", l.source.x);
        linkEls[i].setAttribute("y1", l.source.y);
        linkEls[i].setAttribute("x2", l.target.x);
        linkEls[i].setAttribute("y2", l.target.y);
      });
      nodes.forEach((n, i) => {
        nodeEls[i].g.setAttribute("transform", `translate(${n.x},${n.y})`);
      });
    }

    function ensureTick() {
      if (!graphAnim) graphAnim = requestAnimationFrame(tick);
    }

    function centerOn(n) {
      if (!n.visible) return;
      nodes.forEach((o) => {
        o.fx = null;
        o.fy = null;
      });
      hub = n;
      n.x = width / 2;
      n.y = height / 2;
      n.fx = width / 2;
      n.fy = height / 2;
      n.vx = 0;
      n.vy = 0;
      // keep hub visually centered at current zoom
      view.x = width / 2 - n.x * view.k;
      view.y = height / 2 - n.y * view.k;
      alpha = 1;
      nodes.forEach((node, i) => syncNodeVisual(node, nodeEls[i]));
      paint();
      ensureTick();
      graphStatus.textContent = `中心：${n.label}　·　点击节点切换中心 / 图例筛选 / 滚轮缩放`;
    }

    function buildLegend() {
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
        const sw = document.createElement("span");
        sw.className = "swatch";
        sw.style.background = COLORS[g] || "#888";
        const lab = document.createElement("span");
        lab.textContent = GROUP_LABELS[g] || g;
        btn.appendChild(sw);
        btn.appendChild(lab);
        btn.addEventListener("click", () => {
          if (enabledGroups.has(g)) {
            if (enabledGroups.size <= 1) return; // keep at least one
            enabledGroups.delete(g);
            btn.classList.add("is-off");
          } else {
            enabledGroups.add(g);
            btn.classList.remove("is-off");
          }
          // if hub filtered out, pick first visible
          if (hub && !enabledGroups.has(hub.group)) {
            const next = nodes.find((n) => enabledGroups.has(n.group));
            if (next) centerOn(next);
          }
          applyFilter();
        });
        graphLegend.appendChild(btn);
      });
    }

    const nodeEls = nodes.map((n) => {
      const g = document.createElementNS(svgNS, "g");
      g.setAttribute("class", "node");
      g.style.cursor = "pointer";

      const hit = document.createElementNS(svgNS, "circle");
      hit.setAttribute("fill", "transparent");

      const dot = document.createElementNS(svgNS, "circle");
      dot.setAttribute("class", "dot");
      dot.setAttribute("fill", COLORS[n.group] || COLORS.author);
      dot.style.pointerEvents = "none";

      const label = document.createElementNS(svgNS, "text");
      label.setAttribute("text-anchor", "middle");
      label.textContent = n.label.length > 8 ? n.label.slice(0, 7) + "…" : n.label;
      label.style.pointerEvents = "none";

      const title = document.createElementNS(svgNS, "title");
      title.textContent = `${GROUP_LABELS[n.group] || n.group} · ${n.tip}`;

      g.appendChild(title);
      g.appendChild(hit);
      g.appendChild(dot);
      g.appendChild(label);
      nodeG.appendChild(g);

      const el = { g, hit, dot, label };
      syncNodeVisual(n, el);

      g.addEventListener("pointerdown", (ev) => {
        if (!n.visible) return;
        ev.stopPropagation();
        ev.preventDefault();
        dragging = n;
        dragMoved = false;
        dragStart = { x: ev.clientX, y: ev.clientY };
        g.setPointerCapture(ev.pointerId);
        g.style.cursor = "grabbing";
        svg.style.cursor = "grabbing";
        const p = clientToWorld(ev.clientX, ev.clientY);
        n.fx = p.x;
        n.fy = p.y;
        n.x = p.x;
        n.y = p.y;
        alpha = Math.max(alpha, 0.25);
        paint();
        ensureTick();
      });
      g.addEventListener("pointermove", (ev) => {
        if (dragging !== n) return;
        ev.preventDefault();
        if (
          dragStart &&
          (Math.abs(ev.clientX - dragStart.x) > 4 ||
            Math.abs(ev.clientY - dragStart.y) > 4)
        ) {
          dragMoved = true;
        }
        const p = clientToWorld(ev.clientX, ev.clientY);
        n.fx = p.x;
        n.fy = p.y;
        n.x = p.x;
        n.y = p.y;
        alpha = Math.max(alpha, 0.12);
        paint();
        ensureTick();
      });
      const endDrag = (ev) => {
        if (dragging !== n) return;
        const wasClick = !dragMoved;
        dragging = null;
        g.style.cursor = "pointer";
        svg.style.cursor = "grab";
        try {
          g.releasePointerCapture(ev.pointerId);
        } catch (_) {
          /* ignore */
        }
        if (wasClick) {
          centerOn(n);
        } else if (hub && hub.id === n.id) {
          n.fx = width / 2;
          n.fy = height / 2;
        } else {
          setTimeout(() => {
            if (dragging !== n && (!hub || hub.id !== n.id)) {
              n.fx = null;
              n.fy = null;
            }
          }, 60);
        }
      };
      g.addEventListener("pointerup", endDrag);
      g.addEventListener("pointercancel", endDrag);
      return el;
    });

    bg.addEventListener("pointerdown", (ev) => {
      if (dragging) return;
      panning = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y };
      bg.setPointerCapture(ev.pointerId);
      svg.style.cursor = "grabbing";
    });
    bg.addEventListener("pointermove", (ev) => {
      if (!panning) return;
      const rect = svg.getBoundingClientRect();
      view.x = panning.vx + ((ev.clientX - panning.x) * width) / rect.width;
      view.y = panning.vy + ((ev.clientY - panning.y) * height) / rect.height;
      paint();
    });
    const endPan = (ev) => {
      if (!panning) return;
      panning = null;
      svg.style.cursor = "grab";
      try {
        bg.releasePointerCapture(ev.pointerId);
      } catch (_) {
        /* ignore */
      }
    };
    bg.addEventListener("pointerup", endPan);
    bg.addEventListener("pointercancel", endPan);

    svg.addEventListener(
      "wheel",
      (ev) => {
        ev.preventDefault();
        const rect = svg.getBoundingClientRect();
        const mx = ((ev.clientX - rect.left) * width) / rect.width;
        const my = ((ev.clientY - rect.top) * height) / rect.height;
        const factor = ev.deltaY < 0 ? 1.1 : 0.9;
        const nextK = Math.min(4.5, Math.max(0.3, view.k * factor));
        view.x = mx - ((mx - view.x) * nextK) / view.k;
        view.y = my - ((my - view.y) * nextK) / view.k;
        view.k = nextK;
        // radii/fonts are in world units; scale(k) keeps them proportional
        paint();
      },
      { passive: false }
    );

    function tick() {
      graphAnim = null;
      const cx = hub ? hub.x : width / 2;
      const cy = hub ? hub.y : height / 2;
      if (alpha > 0.015 || dragging) {
        if (!dragging) alpha *= 0.985;
        for (let i = 0; i < nodes.length; i++) {
          if (!nodes[i].visible) continue;
          for (let j = i + 1; j < nodes.length; j++) {
            if (!nodes[j].visible) continue;
            const a = nodes[i];
            const b = nodes[j];
            let dx = b.x - a.x;
            let dy = b.y - a.y;
            let dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
            const force = (900 * alpha) / (dist * dist);
            dx = (dx / dist) * force;
            dy = (dy / dist) * force;
            if (a.fx == null) {
              a.vx -= dx;
              a.vy -= dy;
            }
            if (b.fx == null) {
              b.vx += dx;
              b.vy += dy;
            }
          }
        }
        for (const l of links) {
          if (!l.source.visible || !l.target.visible) continue;
          const dx = l.target.x - l.source.x;
          const dy = l.target.y - l.source.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
          const target = 88 + l.value * 8;
          const f = ((dist - target) * 0.035 * Math.max(alpha, 0.05)) / dist;
          if (l.source.fx == null) {
            l.source.vx += dx * f;
            l.source.vy += dy * f;
          }
          if (l.target.fx == null) {
            l.target.vx -= dx * f;
            l.target.vy -= dy * f;
          }
        }
        for (const n of nodes) {
          if (!n.visible) continue;
          if (n.fx != null) {
            n.x = n.fx;
            n.y = n.fy;
            n.vx = 0;
            n.vy = 0;
          } else {
            n.vx += (cx - n.x) * 0.012 * alpha;
            n.vy += (cy - n.y) * 0.012 * alpha;
            n.vx *= 0.86;
            n.vy *= 0.86;
            n.x += n.vx;
            n.y += n.vy;
          }
        }
        // keep hub pinned at geometric center of layout space
        if (hub && hub.fx != null) {
          hub.x = hub.fx;
          hub.y = hub.fy;
        }
        paint();
        graphAnim = requestAnimationFrame(tick);
      } else {
        paint();
      }
    }

    buildLegend();
    applyFilter();
    // center view on hub
    view.x = width / 2 - hub.x * view.k;
    view.y = height / 2 - hub.y * view.k;
    tick();

    const counts = {};
    for (const n of rawNodes) counts[n.group] = (counts[n.group] || 0) + 1;
    const parts = Object.entries(counts)
      .map(([g, c]) => `${GROUP_LABELS[g] || g}${c}`)
      .join(" · ");

    let head = parts || `${nodes.length} 节点`;
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
    graphStatus.textContent = `${head}　·　点击节点切换中心 / 右上角筛选 / 拖拽平移 / 滚轮缩放`;
  }

  const GRAPH_HINTS = {
    author: "请输入作者姓名",
    keyword: "请输入关键词，如：水稻",
    institution: "请输入机构名，如：浙江大学",
    paper: "请输入论文 DOI",
  };

  const GRAPH_DEFAULTS = {
    author: "朱军",
    keyword: "水稻",
    institution: "浙江大学",
    paper: "",
  };

  async function loadGraph() {
    const mode = $("#graph-mode").value;
    const q = $("#graph-query").value.trim();
    if (!q) {
      graphStatus.textContent = GRAPH_HINTS[mode] || "请输入查询";
      return;
    }
    graphStatus.textContent = "加载中…";
    try {
      let data;
      if (mode === "author") {
        data = await api(`/graph/network/author?name=${encodeURIComponent(q)}&limit=20`);
      } else if (mode === "keyword") {
        data = await api(`/graph/network/keyword?keyword=${encodeURIComponent(q)}&limit=20`);
      } else if (mode === "institution") {
        data = await api(`/graph/network/institution?name=${encodeURIComponent(q)}&limit=20`);
      } else {
        data = await api(`/graph/network/paper?doi=${encodeURIComponent(q)}`);
      }
      renderGraph(data);
    } catch (err) {
      stopGraph();
      graphCanvas.innerHTML = "";
      graphStatus.textContent = `失败：${err.message || err}`;
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
    input.placeholder = GRAPH_HINTS[mode] || "";
    if (!input.value.trim() || Object.values(GRAPH_DEFAULTS).includes(input.value.trim())) {
      input.value = GRAPH_DEFAULTS[mode] || "";
    }
  });

  showWelcome();
})();
