(() => {
  const $ = (id) => document.getElementById(id);
  const page = document.body?.dataset.page || "";
  const basePath = (document.body?.dataset.basePath || "").replace(/\/+$/, "");
  const themeSelect = $("theme-select");

  function routePath(path) {
    const suffix = String(path || "").replace(/^\/+/, "");
    return `${basePath}/${suffix}`;
  }

  function applyTheme(value) {
    const theme = ["light", "dark", "system"].includes(value) ? value : "system";
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("wvd-theme", theme);
    if (themeSelect) themeSelect.value = theme;
  }
  if (themeSelect) {
    applyTheme(localStorage.getItem("wvd-theme") || "system");
    themeSelect.addEventListener("change", () => applyTheme(themeSelect.value));
  }

  class ApiError extends Error {
    constructor(message, { status = 0, data = null, excerpt = "" } = {}) {
      super(message); this.name = "ApiError"; this.status = status; this.data = data; this.excerpt = excerpt;
    }
  }

  async function safeFetch(url, options = {}) {
    const response = await fetch(url, options);
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    const raw = await response.text();
    if (!contentType.includes("application/json")) {
      const excerpt = raw.replace(/\s+/g, " ").trim().slice(0, 500);
      throw new ApiError(`服务器返回了非 JSON 错误（HTTP ${response.status}）。可能是反向代理或上游服务超时。`, { status: response.status, excerpt });
    }
    let data;
    try { data = raw ? JSON.parse(raw) : {}; }
    catch (_) { throw new ApiError(`服务器返回了无法解析的 JSON（HTTP ${response.status}）。`, { status: response.status, excerpt: raw.slice(0, 500) }); }
    if (!response.ok || data.ok === false) {
      const message = data?.error?.message || `请求失败（HTTP ${response.status}）`;
      throw new ApiError(message, { status: response.status, data });
    }
    return data;
  }

  function escapeText(value) { return value == null ? "" : String(value); }
  function fmtDate(value) {
    if (!value) return "—";
    try { return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
    catch (_) { return value; }
  }
  function fmtTranscriptionDuration(value) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "";
    const seconds = value / 1000;
    if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
    const whole = Math.round(seconds);
    return `${Math.floor(whole / 60)} 分 ${String(whole % 60).padStart(2, "0")} 秒`;
  }
  function setStatus(el, text, kind = "idle") {
    if (!el) return; el.textContent = text; el.className = `status-badge ${kind}`;
  }

  async function initWorkspace() {
    const videoUrl = $("video-url"), presetSelect = $("preset-select"), promptInput = $("prompt-input");
    const outputFormat = $("output-format"), downloadMp4 = $("download-mp4"), parseButton = $("parse-button");
    const resultEmpty = $("result-empty"), resultContent = $("result-content"), resultError = $("result-error");
    const resultStatus = $("result-status"), technicalJson = $("technical-json"), analysisContent = $("analysis-content");
    const retryNotice = $("retry-notice"), downloadLinks = $("download-links"), healthPill = $("health-pill");
    let presets = [];

    try {
      const data = await safeFetch(routePath("api/presets")); presets = data.presets || [];
      presets.forEach((preset) => { const option = document.createElement("option"); option.value = preset.id; option.textContent = preset.name; presetSelect.appendChild(option); });
    } catch (_) {}
    presetSelect.addEventListener("change", () => {
      const preset = presets.find((item) => item.id === presetSelect.value);
      promptInput.value = preset ? preset.prompt : "";
      promptInput.focus();
    });

    try {
      const health = await safeFetch(routePath("health"));
      if (health.ok) { healthPill.classList.add("ok"); healthPill.querySelector("span:last-child").textContent = "服务在线"; }
    } catch (_) { healthPill.querySelector("span:last-child").textContent = "服务状态未知"; }

    const progressBox = $("result-progress");
    const progressTitle = $("progress-title");
    const progressDetail = $("progress-detail");
    const activeJobKey = "wvd-active-job";
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    let watchingJob = false;

    function setBusy(busy) {
      parseButton.disabled = busy;
      parseButton.querySelector("span:first-child").textContent = busy ? "任务进行中 · 可刷新页面" : "开始解析";
    }

    function showError(message, details = "") {
      progressBox.classList.add("hidden");
      setStatus(resultStatus, "失败", "failed");
      resultContent.classList.add("hidden");
      resultError.classList.remove("hidden");
      $("error-message").textContent = message;
      const box = $("error-details");
      box.classList.toggle("hidden", !details);
      $("error-excerpt").textContent = details;
    }

    function showResult(data) {
      progressBox.classList.add("hidden");
      resultContent.classList.remove("hidden");
      resultError.classList.add("hidden");
      setStatus(resultStatus, "已完成", "completed");
      $("result-title").textContent = data.title || data.id || "微信视频号";
      const thumb = $("result-thumbnail");
      if (data.thumbnail?.download_path) { thumb.src = data.thumbnail.download_path; thumb.classList.remove("hidden"); }
      else { thumb.removeAttribute("src"); thumb.classList.add("hidden"); }
      renderSourceActions($("result-source-actions"), data.source_url || videoUrl.value.trim());
      $("result-id").textContent = data.id || "—";
      $("result-attempts").textContent = `${data.attempts_used || 1} / ${data.max_attempts || 3}`;
      $("result-format").textContent = (data.output_format || "txt").toUpperCase();
      const transcriptionTime = fmtTranscriptionDuration(data.transcription_elapsed_ms);
      const timingElement = $("result-transcription-time");
      timingElement.classList.toggle("hidden", !transcriptionTime);
      timingElement.textContent = transcriptionTime ? `本地转写耗时：${transcriptionTime}` : "";
      analysisContent.textContent = data.analysis_status === "unavailable"
        ? (data.error_message || "元宝无法完成视频内容分析，请查看已保存的视频或口播文件。")
        : (data.content || "");
      technicalJson.textContent = JSON.stringify(data, null, 2);
      retryNotice.classList.toggle("hidden", !(data.retry_errors || []).length);
      retryNotice.textContent = (data.retry_errors || []).length
        ? `任务曾遇到 ${data.retry_errors.length} 次临时错误，后台已自动恢复。` : "";
      downloadLinks.replaceChildren();
      if (data.bundle?.download_path) downloadLinks.appendChild(downloadLink(data.bundle.download_path, "下载全部"));
      if (data.text?.download_path) downloadLinks.appendChild(downloadLink(data.text.download_path, `下载 ${(data.output_format || "txt").toUpperCase()}`));
      if (data.video?.download_path) downloadLinks.appendChild(downloadLink(data.video.download_path, "下载 MP4"));
      if (data.transcript?.download_path) downloadLinks.appendChild(downloadLink(data.transcript.download_path, "下载完整口播"));
    }

    async function watchJob(jobId) {
      if (watchingJob) return;
      watchingJob = true;
      setBusy(true);
      resultEmpty.classList.add("hidden");
      resultContent.classList.add("hidden");
      resultError.classList.add("hidden");
      progressBox.classList.remove("hidden");
      progressTitle.textContent = `任务 ${jobId} 已提交`;
      progressDetail.textContent = "后台解析中，关闭或刷新页面不会取消任务。";
      let failures = 0;
      try {
        while (true) {
          let data;
          try {
            const response = await safeFetch(routePath(`api/history/${encodeURIComponent(jobId)}`), { cache: "no-store" });
            data = response.job;
            failures = 0;
          } catch (error) {
            failures += 1;
            if (failures >= 6) {
              // Preserve the ID: the worker may still finish after a network outage.
              setStatus(resultStatus, "等待连接恢复", "running");
              progressTitle.textContent = "暂时无法获取任务进度";
              progressDetail.textContent = "后台任务可能仍在运行；刷新页面或进入历史记录可继续查看。";
              return;
            }
            setStatus(resultStatus, "等待连接恢复", "running");
            progressDetail.textContent = "网络暂时中断，正在尝试恢复进度查询。";
            await sleep(5000);
            continue;
          }
          if (!data) { showError("无法读取任务状态，请到历史记录查看。"); return; }
          if (data.source_url) videoUrl.value = data.source_url;
          technicalJson.textContent = JSON.stringify(data, null, 2);
          if (data.status === "completed" || data.status === "partial") {
            localStorage.removeItem(activeJobKey);
            showResult(data);
            if (data.status === "partial") setStatus(resultStatus, "部分完成", "partial");
            return;
          }
          if (data.status === "failed") {
            localStorage.removeItem(activeJobKey);
            showError(data.error_message || "任务失败，请查看历史记录。", data.error_code || "");
            return;
          }
          const queued = data.status === "queued";
          setStatus(resultStatus, queued ? "排队中" : "处理中", "running");
          const stages = {
            yuanbao_video: "元宝解析视频中",
            local_transcription: "本地语音转写中",
            yuanbao_transcript: "元宝分析口播文本中",
            saving_results: "保存成果中"
          };
          progressTitle.textContent = queued ? `任务 ${jobId} 排队中`
            : `任务 ${jobId} · ${stages[data.stage] || "正在解析"}`;
          progressDetail.textContent = queued ? "等待浏览器空闲，任务会自动开始。"
            : (data.stage === "local_transcription" ? "长视频正在本地转写，可能需要数分钟；可以关闭页面。"
              : `第 ${data.attempts_used || 1} / ${data.max_attempts || 3} 次尝试 · 即使关闭页面也会继续。`);
          const pollSeconds = Math.max(1, Number(data.poll_interval_seconds) || 3);
          await sleep(pollSeconds * 1000);
        }
      } finally {
        watchingJob = false;
        setBusy(false);
      }
    }

    parseButton.addEventListener("click", async () => {
      if (watchingJob) return;
      const url = videoUrl.value.trim();
      if (!url) { videoUrl.focus(); return; }
      setBusy(true);
      resultEmpty.classList.add("hidden");
      resultContent.classList.add("hidden");
      resultError.classList.add("hidden");
      setStatus(resultStatus, "提交中", "running");
      try {
        const data = await safeFetch(routePath("api/parse"), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url, prompt: promptInput.value.trim() || null, preset_id: presetSelect.value || null,
            output_format: outputFormat.value, download: downloadMp4.checked
          })
        });
        if (!data.id) throw new Error("服务器未返回任务 ID，请在历史记录中确认是否已提交。");
        localStorage.setItem(activeJobKey, data.id);
        await watchJob(data.id);
      } catch (error) {
        showError(error.message || "请求失败", error.excerpt || "");
      } finally {
        if (!watchingJob) setBusy(false);
      }
    });
    const activeJob = localStorage.getItem(activeJobKey);
    if (activeJob && /^wxv_\d{6}$/.test(activeJob)) {
      watchJob(activeJob); // Recover ongoing work after reload/navigation.
    } else if (activeJob) localStorage.removeItem(activeJobKey);
    $("copy-content")?.addEventListener("click", async () => { if (analysisContent.textContent) await navigator.clipboard.writeText(analysisContent.textContent); });
  }

  function downloadLink(path, label) {
    const a = document.createElement("a"); a.className = "download-pill"; a.href = path; a.textContent = label; return a;
  }

  function sourceLink(url, label = "打开原链接") {
    const a = document.createElement("a");
    a.className = "source-link"; a.href = url; a.textContent = label;
    a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }

  async function openInWeChat(url) {
    let copied = false;
    try { await navigator.clipboard.writeText(url); copied = true; }
    catch (_) { copied = false; }
    if (!copied) window.prompt("请复制视频号链接，然后在微信中打开：", url);
    window.location.href = "weixin://";
  }

  function wechatButton(url) {
    const button = document.createElement("button");
    button.className = "text-button wechat-button"; button.type = "button"; button.textContent = "微信打开";
    button.addEventListener("click", () => openInWeChat(url));
    return button;
  }

  function renderSourceActions(container, url) {
    if (!container) return;
    container.replaceChildren();
    if (!url) return;
    container.append(sourceLink(url), wechatButton(url));
  }

  async function initHistory() {
    const root = $("history-list"), filter = $("history-filter"), refresh = $("refresh-history");
    let jobs = [];
    const render = () => {
      const q = (filter.value || "").trim().toLowerCase();
      const visible = jobs.filter((job) => !q || [job.title, job.id, job.source_url, job.input_url, job.prompt, job.preset_name].some((v) => String(v || "").toLowerCase().includes(q)));
      root.replaceChildren();
      if (!visible.length) { const empty = document.createElement("div"); empty.className = "history-empty panel"; empty.textContent = q ? "没有匹配的历史记录。" : "还没有历史记录，完成一次解析后会出现在这里。"; root.appendChild(empty); return; }
      visible.forEach((job) => root.appendChild(historyCard(job, load)));
    };
    const load = async () => {
      if (!jobs.length) root.innerHTML = '<div class="history-empty panel">正在加载历史记录…</div>';
      try { const data = await safeFetch(routePath("api/history")); jobs = data.jobs || []; render(); }
      catch (error) { root.replaceChildren(); const box = document.createElement("div"); box.className = "history-empty panel"; box.textContent = error.message; root.appendChild(box); }
    };
    filter.addEventListener("input", render); refresh.addEventListener("click", load); await load();
    setInterval(() => { if (jobs.some((job) => ["queued", "running"].includes(job.status))) load(); }, 4000);
  }

  function historyCard(job, reload) {
    const card = document.createElement("article"); card.className = "history-card panel";
    const titleText = job.title || job.id;
    const visual = document.createElement("div"); visual.className = "history-visual";
    if (job.thumbnail?.download_path) {
      const img = document.createElement("img"); img.className = "history-thumbnail"; img.src = job.thumbnail.download_path; img.alt = `${titleText} 缩略图`; visual.appendChild(img);
    } else {
      const placeholder = document.createElement("div"); placeholder.className = "history-thumbnail-placeholder";
      placeholder.textContent = job.download_requested === false ? "MD Only" : "No Cover";
      visual.appendChild(placeholder);
    }
    const main = document.createElement("div"); main.className = "history-main";
    const heading = document.createElement("h3"); heading.className = "history-title"; heading.textContent = titleText;
    const top = document.createElement("div"); top.className = "history-topline";
    const id = document.createElement("span"); id.className = "history-id"; id.textContent = job.id;
    const badge = document.createElement("span"); setStatus(badge, job.status === "completed" ? "已完成" : job.status === "failed" ? "失败" : job.status === "partial" ? "部分完成" : job.status === "queued" ? "排队中" : "进行中", job.status === "queued" ? "running" : job.status);
    const date = document.createElement("span"); date.className = "history-date"; date.textContent = fmtDate(job.created_at);
    top.append(id, badge, date);
    const transcriptionTime = fmtTranscriptionDuration(job.transcription_elapsed_ms);
    if (transcriptionTime) {
      const timing = document.createElement("span"); timing.className = "history-date";
      timing.textContent = `本地转写耗时：${transcriptionTime}`;
      timing.title = "含本地音轨提取、语音识别和逐字稿保存；不包含元宝分析或视频下载";
      top.appendChild(timing);
    }
    const sourceActions = document.createElement("div"); sourceActions.className = "source-actions history-source-actions";
    renderSourceActions(sourceActions, job.source_url || job.input_url || "");
    const prompt = document.createElement("p"); prompt.className = "history-prompt"; prompt.textContent = job.prompt || "未设置 Prompt";
    main.append(heading, top, sourceActions, prompt);
    const actions = document.createElement("div"); actions.className = "history-actions";
    if (job.bundle?.download_path) actions.appendChild(downloadLink(job.bundle.download_path, "下载全部"));
    if (job.text?.download_path) actions.appendChild(downloadLink(job.text.download_path, `下载 ${(job.output_format || "txt").toUpperCase()}`));
    if (job.video?.download_path) actions.appendChild(downloadLink(job.video.download_path, "MP4"));
    if (job.transcript?.download_path) actions.appendChild(downloadLink(job.transcript.download_path, "口播原文"));
    const detailBtn = document.createElement("button"); detailBtn.className = "text-button"; detailBtn.type = "button"; detailBtn.textContent = "详情";
    const del = document.createElement("button"); del.className = "danger-button"; del.type = "button"; del.textContent = "删除";
    if (job.status === "queued" || job.status === "running") { del.disabled = true; del.title = "任务完成后才可删除"; }
    actions.append(detailBtn, del); card.append(visual, main, actions);
    const detail = document.createElement("div"); detail.className = "history-detail hidden"; const pre = document.createElement("pre"); detail.appendChild(pre); card.appendChild(detail);
    detailBtn.addEventListener("click", async () => { if (!detail.classList.contains("hidden")) { detail.classList.add("hidden"); return; } try { const data = await safeFetch(routePath(`api/history/${encodeURIComponent(job.id)}`)); pre.textContent = data.job.content || data.job.error_message || "没有文本内容"; detail.classList.remove("hidden"); } catch (e) { pre.textContent = e.message; detail.classList.remove("hidden"); } });
    del.addEventListener("click", async () => { if (!window.confirm(`确定删除《${titleText}》及其关联文件吗？此操作不可恢复。`)) return; del.disabled = true; try { await safeFetch(routePath(`api/history/${encodeURIComponent(job.id)}`), { method: "DELETE" }); await reload(); } catch (e) { window.alert(e.message); del.disabled = false; } });
    return card;
  }


  if (page === "workspace") initWorkspace();
  if (page === "history") initHistory();
  window.WechatVideoDL = { safeFetch, applyTheme, openInWeChat };
})();
