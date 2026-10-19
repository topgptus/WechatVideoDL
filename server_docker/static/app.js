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
    const method = String(options.method || "GET").toUpperCase();
    const headers = new Headers(options.headers || {});
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
      if (csrf) headers.set("X-CSRF-Token", csrf);
    }
    const response = await fetch(url, {...options, headers});
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

  function fillTaskSteps(list, progress) {
    list.replaceChildren();
    for (const [index, step] of (progress?.steps || []).entries()) {
      const li = document.createElement("li");
      li.className = `task-step task-step-${step.state || "waiting"}`;
      const num = document.createElement("span");
      num.className = "task-step-indicator";
      num.textContent = step.state === "complete" ? "✓" : step.state === "failed" ? "!" : `${index + 1}`;
      const body = document.createElement("span");body.className="task-step-body";
      const title = document.createElement("span");title.className="task-step-name";
      title.textContent = step.label;body.appendChild(title);
      for (const error of (step.issues || [])) {
        const note = document.createElement("small");note.className="task-step-issue";
        note.textContent = error;body.appendChild(note);
      }
      const status = document.createElement("span");status.className="task-step-state";
      status.textContent = step.state === "complete" ? (step.recovered ? "已恢复" : "已完成") :
        step.state === "active" ? "进行中" : step.state === "failed" ? "失败" : "待执行";
      li.append(num,body,status);list.appendChild(li);
    }
  }

  // 单任务与批量任务共用模式、输出开关和逐字稿联动规则。
  function bindTaskOutputOptions({modeInputs, flags, timed, analysisOptions, formatOptions, timedWrap, hint}) {
    const defaults = {
      video_only:[true,false,false,false],
      narration_only:[false,false,false,true],
      analysis_only:[false,true,false,false],
      full:[true,true,true,true],
    };
    const modeValue = () => modeInputs.find(x => x.checked)?.value || "custom";
    function updateMode() {
      const selected = modeValue();
      if (defaults[selected]) defaults[selected].forEach((value,i) => { flags[i].checked=value; });
      if (!flags[1].checked) flags[2].checked = false;
      flags[2].disabled = !flags[1].checked;
      timed.disabled = !flags[3].checked;
      if (!flags[3].checked) timed.checked = false;
      timedWrap.classList.toggle("hidden", !flags[3].checked);
      analysisOptions.classList.toggle("hidden", !flags[1].checked);
      formatOptions.classList.toggle("hidden", !(flags[1].checked || flags[2].checked || flags[3].checked));
      hint.textContent = flags[3].checked
        ? timed.checked
          ? "只输出一份「时间轴逐字稿」，包含实际识别时间窗；不再另存普通逐字稿。"
          : "只输出一份「原始逐字稿」，按语义标点整理段落，不带时间轴。"
        : "启用原始语音逐字稿后，可选择是否在输出中附带时间轴。";
    }
    modeInputs.forEach(input => input.addEventListener("change",updateMode));
    [...flags,timed].forEach(input=>input.addEventListener("change",()=>{
      if (modeValue() !== "custom") modeInputs.find(x=>x.value==="custom").checked=true;
      updateMode();
    }));
    updateMode();
    return {updateMode, modeValue};
  }

  async function initWorkspace() {
    const videoUrl = $("video-url"), presetSelect = $("preset-select"), promptInput = $("prompt-input");
    const outputFormat = $("output-format"), downloadMp4 = $("download-mp4"), parseButton = $("parse-button");
    const modeInputs = [...document.querySelectorAll('[name="task-mode"]')];
    const analysisToggle = $("generate-analysis"), narrationToggle = $("generate-narration");
    const rawToggle = $("generate-raw"), timedToggle = $("generate-timed");
    const analysisOptions = $("analysis-options"), formatOptions = $("task-output-options");
    const taskHint = $("task-output-hint");
    const {updateMode,modeValue} = bindTaskOutputOptions({
      modeInputs,flags:[downloadMp4,analysisToggle,narrationToggle,rawToggle],
      timed:timedToggle,analysisOptions,formatOptions,timedWrap:$("timed-option"),hint:taskHint,
    });
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
      if (preset?.id === "script_copy") narrationToggle.checked = true;
      updateMode();
      if (analysisToggle.checked) promptInput.focus();
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

    let lastProgressSignature = "";
    function renderJobProgress(data) {
      const progress = data?.progress;
      if (!progress || !Array.isArray(progress.steps)) return;
      const signature = JSON.stringify({progress, stage:data.stage, status:data.status});
      if (signature === lastProgressSignature) return;
      lastProgressSignature = signature;
      $("progress-summary").textContent = progress.queued
        ? `排队等待 · 共 ${progress.total_steps} 个步骤`
        : `已完成 ${progress.completed_steps} / ${progress.total_steps} 个步骤`;
      const active = (progress.steps || []).find(step => step.state === "active");
      $("progress-current").textContent = progress.queued ? "等待处理"
        : active ? `当前：${active.label}` : "等待最终确认";
      const list = $("progress-steps");
      fillTaskSteps(list,progress);
      const measured = $("progress-measured"), detail = [];
      const stats = progress.activity || {};
      if (typeof stats.recognized_windows === "number")
        detail.push(`SenseVoice 已识别 ${stats.recognized_windows} 个窗口`);
      if (typeof stats.finished_groups === "number")
        detail.push(`已完成 ${stats.finished_groups} 个约 30 分钟处理组`);
      if (typeof stats.ai_total === "number")
        detail.push(`AI 内容已整理 ${stats.ai_done} / ${stats.ai_total} 部分`);
      // Keep an unobtrusive final audit of the same real step states.
      const completedFlow = $("result-workflow");
      const workflowSteps = $("result-workflow-steps");
      workflowSteps.replaceChildren(...[...list.children].map(li => li.cloneNode(true)));
      $("result-workflow-summary").textContent =
        `处理流程 · ${progress.completed_steps}/${progress.total_steps} 步完成`;
      completedFlow.classList.remove("hidden");
      completedFlow.open = true;
      measured.textContent = detail.join(" · ");
      measured.classList.toggle("hidden", !detail.length);
      const files = $("progress-outputs");files.replaceChildren();
      const outputs = progress.outputs || [];
      if (outputs.length) {
        const label = document.createElement("strong");
        label.textContent = "计划输出";files.appendChild(label);
        const items = document.createElement("div");items.className="task-output-tags";
        outputs.forEach(output => {
          const item = document.createElement("span");
          item.className = `task-output-tag ${output.ready ? "task-output-ready" : ""}`;
          item.textContent = `${output.ready ? "✓ " : "○ "}${output.label}`;
          items.appendChild(item);
        });files.appendChild(items);
      }
    }

    function setBusy(busy) {
      parseButton.disabled = busy;
      parseButton.querySelector("span:first-child").textContent = busy ? "任务进行中 · 可刷新页面" : "开始任务";
    }

    function showError(message, details = "") {
      progressBox.classList.toggle("hidden", !$("progress-steps").children.length);
      lastProgressSignature = "";
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
      lastProgressSignature = "";
      resultContent.classList.remove("hidden");
      resultError.classList.add("hidden");
      setStatus(resultStatus, data.status === "partial" ? "部分完成" : "已完成",
                data.status === "partial" ? "partial" : "completed");
      $("result-title").textContent = data.title || data.id || "微信视频号";
      const thumb = $("result-thumbnail");
      if (data.thumbnail?.download_path) { thumb.src = data.thumbnail.download_path; thumb.classList.remove("hidden"); }
      else { thumb.removeAttribute("src"); thumb.classList.add("hidden"); }
      renderSourceActions($("result-source-actions"), data.source_url || videoUrl.value.trim(), data.video, data.yuanbao?.preview_url);
      $("result-id").textContent = data.id || "—";
      $("result-attempts").textContent = `${data.attempts_used || 1} / ${data.max_attempts || 3}`;
      $("result-format").textContent = (data.output_format || "txt").toUpperCase();
      const transcriptionTime = fmtTranscriptionDuration(data.transcription_elapsed_ms);
      const timingElement = $("result-transcription-time");
      timingElement.classList.toggle("hidden", !transcriptionTime);
      timingElement.textContent = transcriptionTime ? `本地转写耗时：${transcriptionTime}` : "";
      const coverage = $("result-transcription-coverage");
      const seconds = Number(data.transcription_duration_seconds || 0);
      const windows = Number(data.transcription_segment_count || 0);
      const groups = Number(data.transcription_chunk_count || 0);
      coverage.classList.toggle("hidden", !seconds);
      coverage.textContent = seconds ? `已转写：${windows} 个识别窗口 · ${groups} 个处理组 · 音频总时长 ${Math.floor(seconds / 3600)}小时${Math.floor(seconds % 3600 / 60)}分${Math.round(seconds % 60)}秒${data.analysis_chunk_count ? ` · AI整理 ${data.analysis_chunk_count} 段` : ""}` : "";
      const notice = $("result-quality-notice");
      const qualityError = data.analysis_status === "unavailable" || data.status === "partial";
      const asrGrounded = data.analysis_source === "local_transcript";
      $("analysis-source-label").textContent = asrGrounded ? "基于 SenseVoice 逐字稿的 AI 解析" : "元宝内容";
      notice.classList.toggle("hidden", !qualityError && !asrGrounded);
      notice.textContent = qualityError ? (data.error_message || "部分产物暂不可用，请核对历史任务详情")
        : asrGrounded ? "AI 解析来自完整的本地语音识别结果，可能存在转写或概括误差，建议核对关键原句。" : "";
      $("result-analysis-preview").classList.toggle("hidden", data.analysis_status === "skipped");
      analysisContent.textContent = data.analysis_status === "unavailable"
        ? (data.error_message || "元宝无法完成视频内容分析，请查看已保存的视频或口播文件。")
        : (data.content || "");
      technicalJson.textContent = JSON.stringify(data, null, 2);
      retryNotice.classList.toggle("hidden", !(data.retry_errors || []).length);
      retryNotice.textContent = (data.retry_errors || []).length
        ? `任务曾遇到 ${data.retry_errors.length} 次临时错误，后台已自动恢复。` : "";
      downloadLinks.replaceChildren();
      if (data.bundle?.download_path) downloadLinks.appendChild(downloadLink(data.bundle.download_path, "ALL", "all"));
      if (data.text?.download_path) downloadLinks.appendChild(downloadLink(data.text.download_path, "AI解析", "analysis"));
      if (data.transcript?.download_path) downloadLinks.appendChild(downloadLink(data.transcript.download_path, data.transcript_kind === "raw_asr" ? "原始逐字稿" : "AI整理旁白", "voice"));
      if (data.raw_transcript?.download_path && data.raw_transcript.download_path !== data.transcript?.download_path) downloadLinks.appendChild(downloadLink(data.raw_transcript.download_path, "原始逐字稿", "voice"));
      if (data.timed_transcript?.download_path) downloadLinks.appendChild(downloadLink(data.timed_transcript.download_path, "时间轴逐字稿", "voice"));
      if (data.video?.download_path) downloadLinks.appendChild(downloadLink(data.video.download_path, "MP4", "video"));
    }

    let pollIntervalMs = 3000;
    async function watchJob(jobId, initialData = null) {
      if (watchingJob) return;
      watchingJob = true;
      setBusy(true);
      resultEmpty.classList.add("hidden");
      resultContent.classList.add("hidden");
      resultError.classList.add("hidden");
      progressBox.classList.remove("hidden");
      lastProgressSignature = "";
      $("progress-steps").replaceChildren();
      $("progress-summary").textContent = "正在获取任务步骤…";
      $("progress-current").textContent = "";
      $("progress-measured").classList.add("hidden");
      $("progress-outputs").replaceChildren();
      progressTitle.textContent = `任务 ${jobId} 已提交`;
      progressDetail.textContent = "后台解析中，关闭或刷新页面不会取消任务。";
      if (initialData?.progress) renderJobProgress(initialData);
      if (Number.isFinite(initialData?.poll_interval_seconds))
        pollIntervalMs = Math.min(30000, Math.max(1000, initialData.poll_interval_seconds * 1000));
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
          if (Number.isFinite(data.poll_interval_seconds))
            pollIntervalMs = Math.min(30000, Math.max(1000, data.poll_interval_seconds * 1000));
          if (data.source_url) videoUrl.value = data.source_url;
          technicalJson.textContent = JSON.stringify(data, null, 2);
          renderJobProgress(data);
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
            yuanbao_video: "读取视频信息中",
            downloading_video: "下载 MP4 视频中",
            preparing_media: "准备音频素材中",
            local_transcription: "本地语音转写中",
            yuanbao_transcript: "元宝分析口播文本中",
            saving_results: "保存成果中"
          };
          progressTitle.textContent = queued ? `任务 ${jobId} 排队中`
            : `任务 ${jobId} · ${stages[data.stage] || "正在解析"}`;
          progressDetail.textContent = queued ? "等待浏览器空闲，任务会自动开始。"
            : (data.stage === "local_transcription" ? "长视频由本地 SenseVoice 转写，可能需要数分钟；可以关闭页面。"
              : `第 ${data.attempts_used || 1} / ${data.max_attempts || 3} 次尝试 · 即使关闭页面也会继续。`);
          await sleep(pollIntervalMs);
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
            output_format: outputFormat.value, task_mode: modeValue(),
            download: downloadMp4.checked, analyze: analysisToggle.checked,
            narration: narrationToggle.checked, raw_transcript: rawToggle.checked,
            timed_transcript: timedToggle.checked
          })
        });
        if (!data.id) throw new Error("服务器未返回任务 ID，请在历史记录中确认是否已提交。");
        localStorage.setItem(activeJobKey, data.id);
        await watchJob(data.id,data);
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

  function downloadLink(path, label, kind = "") {
    const a = document.createElement("a");
    a.className = `download-pill${kind ? ` action-${kind}` : ""}`;
    a.href = path;
    a.textContent = label;
    if (kind === "all") a.title = "下载当前任务的全部文件";
    if (kind === "analysis") a.title = "下载 AI 解析结果";
    if (kind === "voice") a.title = "下载原始旁白逐字稿（未经 AI 改写）";
    if (kind === "video") a.title = "下载 MP4 视频";
    return a;
  }

  function openVideoPlayer(path) {
    const dialog = $("video-play-dialog"), video = $("video-play-element");
    if (!dialog || !video || !path || !String(path).startsWith(basePath + "/api/history/")) return;
    $("video-play-error").classList.add("hidden");
    video.src = path;
    dialog.showModal();
    video.play().catch(() => { /* Browser autoplay requires interaction; controls remain available. */ });
  }
  function closeVideoPlayer() {
    const video = $("video-play-element"), dialog = $("video-play-dialog");
    if (video) {video.pause();video.removeAttribute("src");video.load();}
    if (dialog?.open) dialog.close();
  }
  $("video-play-close")?.addEventListener("click",closeVideoPlayer);
  $("video-play-dialog")?.addEventListener("close",closeVideoPlayer);
  $("video-play-element")?.addEventListener("error",() => $("video-play-error")?.classList.remove("hidden"));

  function videoPlayButton(path) {
    const btn = document.createElement("button");btn.type="button";
    btn.className="source-link play-source-button";btn.textContent="▶ 播放视频";
    btn.addEventListener("click",()=>openVideoPlayer(path));
    return btn;
  }

  function sourceLink(url, label = "打开原链接") {
    const a = document.createElement("a");
    a.className = "source-link"; a.href = url; a.textContent = label;
    a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }

  async function copySourceLink(url, button) {
    // A generic weixin:// URL has no video target and can silently fail on Mac.
    // Copy the actual source URL instead; this also works with desktop WeChat.
    try {
      await navigator.clipboard.writeText(url);
      if (button) {
        const prior = button.textContent;
        button.textContent = "已复制";
        button.setAttribute("aria-label", "视频号链接已复制");
        window.setTimeout(() => {
          if (button.isConnected) {
            button.textContent = prior;
            button.setAttribute("aria-label", prior);
          }
        }, 2200);
      }
    } catch (_) {
      // Browsers without Clipboard permission still give the user a URL
      // they can manually copy; do not claim it was copied.
      window.prompt("复制以下视频号链接，在微信中粘贴打开：", url);
    }
  }

  function copySourceButton(url) {
    const button = document.createElement("button");
    button.className = "text-button copy-source-button";
    button.type = "button";
    button.textContent = "复制链接";
    button.setAttribute("aria-label", "复制视频号原链接");
    button.addEventListener("click", () => copySourceLink(url, button));
    return button;
  }

  function renderSourceActions(container, url, video = null, preview = null) {
    if (!container) return;
    container.replaceChildren();
    if (!url) return;
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) return;
    } catch (_) { return; }
    if (video?.play_path) container.appendChild(videoPlayButton(video.play_path));
    // Only a real WeChat client may reuse a WeChat login. The server cannot
    // transfer the user's WeChat session into a desktop browser.
    const wechat = document.createElement("button");
    wechat.type="button";wechat.className="text-button";wechat.textContent="在微信中查看";
    wechat.title="在已登录微信中查看原视频；其他浏览器可能仍要求微信授权";
    wechat.addEventListener("click",async()=>{
      if (/MicroMessenger/i.test(navigator.userAgent)) {
        window.location.href = (preview && /^https:\/\/channels\.weixin\.qq\.com\//.test(preview)) ? preview : url;
      } else if (typeof navigator.share === "function") {
        try {await navigator.share({url,title:"在微信中查看视频号视频"});}
        catch (err) {if (err?.name !== "AbortError") await copySourceLink(url,wechat);}
      } else {
        await copySourceLink(url,wechat);
        let note = container.querySelector(".video-source-inline-notice");
        if (!note) {note=document.createElement("p");note.className="video-source-inline-notice";container.appendChild(note);}
        note.textContent="链接已复制，请粘贴到已登录的微信中打开；普通网页无法自动获得微信登录态。";
      }
    });
    container.appendChild(wechat);
  }

  function conciseHistoryTitle(fullTitle) {
    const normalized = String(fullTitle || "").replace(/\s+/gu, " ").trim();
    const chars = Array.from(normalized);
    const maxLength = 36;
    if (chars.length <= maxLength) return normalized;
    const excerpt = chars.slice(0, maxLength).join("");
    // First complete meaningful clause, without fabricating a new summary.
    // Short introductory commas should not hide the actual subject.
    const sentence = [...excerpt.matchAll(/[。！？!?；;]/gu)]
      .find((match) => match.index >= 13 && match.index <= 31);
    const clause = [...excerpt.matchAll(/[，,：:]/gu)]
      .find((match) => match.index >= 14 && match.index <= 28);
    const boundary = sentence || clause;
    const prefix = boundary
      ? excerpt.slice(0, boundary.index).trim()
      : excerpt.trimEnd();
    return prefix.replace(/[，,：:；;。！？!?]+$/gu, "") + "…";
  }

  function compactHistoryDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return fmtDate(value);
    // Historical dates are presented consistently in Beijing time.
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false
    }).format(date);
  }

  async function initHistory() {
    const root = $("history-list"), filter = $("history-filter"), refresh = $("refresh-history");
    const pageSize = $("history-page-size"), summary = $("history-page-summary");
    const pagePosition = $("history-page-position"), first = $("history-first");
    const prev = $("history-prev"), next = $("history-next"), last = $("history-last");
    const saved = Number(localStorage.getItem("wvd-history-page-size"));
    const allowed = [10, 20, 50, 100];
    pageSize.value = String(allowed.includes(saved) ? saved : 20);
    let page = 1, pages = 1, total = 0, jobs = [], requestSerial = 0;
    const selectionToggle = $("history-select-toggle"), bulk = $("history-bulk-toolbar");
    const selectPage = $("history-select-page"), selectAll = $("history-select-all");
    const clearSelected = $("history-clear-selected"), deleteSelected = $("history-delete-selected");
    const selectedCount = $("history-selected-count");
    const selected = new Set();
    let selecting = false;
    function updateSelection() {
      bulk.classList.toggle("hidden", !selecting);
      selectionToggle.textContent = selecting ? "完成" : "选择";
      selectionToggle.setAttribute("aria-pressed", String(selecting));
      selectedCount.textContent = `已选 ${selected.size} 条`;
      deleteSelected.disabled = selected.size === 0;
    }
    selectionToggle.addEventListener("click", () => {
      selecting = !selecting;
      if (!selecting) selected.clear();
      updateSelection();render();
    });
    selectPage.addEventListener("click", () => {
      jobs.filter(job => !["queued", "running"].includes(job.status))
        .forEach(job => selected.add(job.id));
      updateSelection();render();
    });
    selectAll.addEventListener("click", async () => {
      try {
        const query = filter.value.trim();
        const data = await safeFetch(routePath("api/history/selection") +
                                    (query ? "?" + new URLSearchParams({q:query}) : ""));
        const ids = data.ids || [];
        if (!ids.length) return;
        ids.forEach(id => selected.add(id));
        updateSelection();render();
      } catch (error) { window.alert(error.message); }
    });
    clearSelected.addEventListener("click", () => {selected.clear();updateSelection();render();});
    deleteSelected.addEventListener("click", async () => {
      if (!selected.size) return;
      const ids = [...selected];
      if (!window.confirm(`将永久删除 ${ids.length} 条历史任务及其专属文件。此操作不可撤销，是否继续？`)) return;
      deleteSelected.disabled = true;
      let removed = 0;
      try {
        for (let i=0;i<ids.length;i+=100) {
          const data = await safeFetch(routePath("api/history/bulk-delete"), {
            method: "POST", headers: {"Content-Type":"application/json"},
            body:JSON.stringify({ids:ids.slice(i,i+100)})
          });
          for (const id of data.deleted_ids || []) {selected.delete(id);removed++;}
        }
        window.alert(`已删除 ${removed} 条任务。`);
        await load();
      } catch (error) {
        window.alert(`已删除 ${removed} 条；后续操作未完成：${error.message}`);
        await load();
      } finally {updateSelection();}
    });
    updateSelection();

    function render() {
      root.replaceChildren();
      if (!jobs.length) {
        const empty = document.createElement("div");
        empty.className = "history-empty panel";
        empty.textContent = filter.value.trim() ? "没有匹配的历史记录。" : "还没有历史记录，完成一次解析后会出现在这里。";
        root.appendChild(empty);
      } else jobs.forEach(job => root.appendChild(historyCard(job, load, {
        selecting, selected, changed: updateSelection,
      })));
      summary.textContent = `共 ${total} 条任务，每页 ${pageSize.value} 条`;
      pagePosition.textContent = `${page} / ${pages}`;
      first.disabled = prev.disabled = page <= 1;
      next.disabled = last.disabled = page >= pages;
    }

    async function load() {
      const serial = ++requestSerial;
      const params = new URLSearchParams({page: String(page), per_page: pageSize.value});
      const query = filter.value.trim();
      if (query) params.set("q", query);
      try {
        const data = await safeFetch(routePath("api/history") + "?" + params.toString());
        if (serial !== requestSerial) return;
        jobs = data.jobs || [];
        const pagination = data.pagination || {};
        page = Number(pagination.page) || 1;
        pages = Math.max(1, Number(pagination.pages) || 1);
        total = Number(pagination.total) || 0;
        render();
      } catch (error) {
        if (serial !== requestSerial) return;
        root.replaceChildren();
        const box = document.createElement("div");box.className = "history-empty panel";
        box.textContent = error.message;root.appendChild(box);
        summary.textContent = "加载失败，请重试";
      }
    }

    function goTo(target) {
      page = Math.min(pages, Math.max(1, target));
      load();
    }
    first.addEventListener("click", () => goTo(1));
    prev.addEventListener("click", () => goTo(page - 1));
    next.addEventListener("click", () => goTo(page + 1));
    last.addEventListener("click", () => goTo(pages));
    pageSize.addEventListener("change", () => {
      localStorage.setItem("wvd-history-page-size", pageSize.value);
      page = 1;load();
    });
    let searchTimer;
    filter.addEventListener("input", () => {
      clearTimeout(searchTimer);
      page = 1;
      searchTimer = setTimeout(load, 230);
    });
    refresh.addEventListener("click", load);
    await load();
    setInterval(() => { if (jobs.some(job => ["queued", "running"].includes(job.status))) load(); }, 4000);
  }

  function historyCard(job, reload, selection = null) {
    const card = document.createElement("article"); card.className = "history-card panel";
    if (selection?.selecting) {
      const label = document.createElement("label");
      label.className = "history-select-item";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = selection.selected.has(job.id);
      box.disabled = ["queued", "running"].includes(job.status);
      box.setAttribute("aria-label", `选中任务 ${job.id}`);
      box.addEventListener("change", () => {
        if (box.checked) selection.selected.add(job.id);
        else selection.selected.delete(job.id);
        selection.changed();
      });
      const labelText = document.createElement("span");labelText.textContent="选择";
      label.append(box,labelText);card.appendChild(label);
      card.classList.add("history-card-selectable");
    }
    const titleText = job.title || job.id;
    const visual = document.createElement("div"); visual.className = "history-visual";
    if (job.thumbnail?.download_path) {
      const img = document.createElement("img"); img.className = "history-thumbnail"; img.src = job.thumbnail.download_path; img.alt = `${titleText} 缩略图`; visual.appendChild(img);
    } else {
      const placeholder = document.createElement("div"); placeholder.className = "history-thumbnail-placeholder";
      placeholder.textContent = job.download_requested === false ? "No VIDEO" : "No Cover";
      visual.appendChild(placeholder);
    }
    const main = document.createElement("div"); main.className = "history-main";
    const heading = document.createElement("h3"); heading.className = "history-title";
    const shorterTitle = conciseHistoryTitle(titleText);
    heading.textContent = shorterTitle;
    heading.title = titleText;
    heading.setAttribute("aria-label", titleText);

    const meta = document.createElement("div"); meta.className = "history-meta";
    const badge = document.createElement("span");
    setStatus(badge, job.status === "completed" ? "已完成" : job.status === "failed" ? "失败" : job.status === "partial" ? "部分完成" : job.status === "queued" ? "排队中" : "进行中", job.status === "queued" ? "running" : job.status);

    const date = document.createElement("time");
    date.className = "history-created";
    date.textContent = compactHistoryDate(job.created_at);
    date.dateTime = job.created_at || "";
    date.title = "创建时间（北京时间）：" + (Number.isNaN(new Date(job.created_at).getTime())
      ? String(job.created_at || "—")
      : new Intl.DateTimeFormat("zh-CN", {
          timeZone: "Asia/Shanghai", year: "numeric", month: "long",
          day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false
        }).format(new Date(job.created_at)));
    meta.append(badge, date);
    const transcriptionTime = fmtTranscriptionDuration(job.transcription_elapsed_ms);
    if (transcriptionTime) {
      const timing = document.createElement("span"); timing.className = "history-asr-time";
      timing.textContent = "转写 " + transcriptionTime;
      timing.title = "本地音轨提取、语音识别与文本保存耗时；不包含元宝分析或视频下载";
      meta.appendChild(timing);
    }
    if (job.transcription_duration_seconds && job.transcription_segment_count) {
      const info = document.createElement("span");info.className="history-asr-time";
      const duration = Math.round(Number(job.transcription_duration_seconds));
      info.textContent = `识别 ${job.transcription_segment_count} 个窗口 · ${job.transcription_chunk_count || "—"} 个处理组 · ${Math.floor(duration/3600)}小时${Math.floor(duration%3600/60)}分`;
      meta.appendChild(info);
    }
    const id = document.createElement("code");
    id.className = "history-job-id";
    id.textContent = job.id;
    id.title = "任务 ID：" + job.id;
    id.setAttribute("aria-label", "任务 ID：" + job.id);
    meta.appendChild(id);
    if (job.owner_username) {
      const owner = document.createElement("span");
      owner.className = "history-owner";
      owner.textContent = "用户：" + job.owner_username;
      meta.appendChild(owner);
    }
    main.append(heading, meta);
    const actions = document.createElement("div"); actions.className = "history-actions"; actions.setAttribute("role", "group"); actions.setAttribute("aria-label", "任务操作");
    // Preserve the user's exact visual and keyboard order. Unavailable
    // files are skipped rather than turning into broken download links.
    if (job.bundle?.download_path) actions.appendChild(downloadLink(job.bundle.download_path, "ALL", "all"));
    if (job.text?.download_path) actions.appendChild(downloadLink(job.text.download_path, "AI解析", "analysis"));
    if (job.transcript?.download_path) actions.appendChild(downloadLink(job.transcript.download_path, job.transcript_kind === "raw_asr" ? "原始逐字稿" : "AI整理旁白", "voice"));
    if (job.raw_transcript?.download_path && job.raw_transcript.download_path !== job.transcript?.download_path) actions.appendChild(downloadLink(job.raw_transcript.download_path, "原始逐字稿", "voice"));
    if (job.timed_transcript?.download_path) actions.appendChild(downloadLink(job.timed_transcript.download_path, "时间轴逐字稿", "voice"));
    if (job.video?.download_path) actions.appendChild(downloadLink(job.video.download_path, "MP4", "video"));
    const detailBtn = document.createElement("button"); detailBtn.className = "text-button history-detail-button"; detailBtn.type = "button"; detailBtn.textContent = "详情";
    const del = document.createElement("button"); del.className = "danger-button"; del.type = "button"; del.textContent = "删除";
    if (job.status === "queued" || job.status === "running") { del.disabled = true; del.title = "任务完成后才可删除"; }
    actions.append(detailBtn, del); card.append(visual, main, actions);
    const detail = document.createElement("div"); detail.id = "history-detail-" + job.id; detail.className = "history-detail hidden";
    detailBtn.setAttribute("aria-controls", detail.id); detailBtn.setAttribute("aria-expanded", "false");

    const info = document.createElement("dl"); info.className = "history-detail-info";
    const detailRow = (label, build) => {
      const row = document.createElement("div"); row.className = "history-info-row";
      const dt = document.createElement("dt"); dt.textContent = label;
      const dd = document.createElement("dd");
      build(dd);
      row.append(dt, dd); info.appendChild(row);
    };
    detailRow("完整标题", (dd) => { dd.textContent = titleText; });
    detailRow("原视频链接", (dd) => {
      const url = job.source_url || job.input_url || "";
      if (url) {
        const address = document.createElement("span");
        address.className = "history-source-url"; address.textContent = url;
        dd.appendChild(address);
        const links = document.createElement("div");
        links.className = "source-actions history-detail-source";
        renderSourceActions(links, url, job.video, job.yuanbao?.preview_url);
        dd.appendChild(links);
      } else { dd.textContent = "无来源链接"; }
    });
    detailRow("本次 Prompt", (dd) => {
      dd.className = "history-prompt-full";
      dd.textContent = job.prompt || "未设置 Prompt";
    });

    const workflow = document.createElement("section");workflow.className="history-step-review";
    const workflowHeading=document.createElement("strong");workflowHeading.textContent="处理步骤";
    const workflowList=document.createElement("ol");workflowList.className="task-step-list";
    fillTaskSteps(workflowList,job.progress);
    workflow.append(workflowHeading,workflowList);
    const contentTitle = document.createElement("h4"); contentTitle.textContent = "AI 解析内容";
    const pre = document.createElement("pre");
    detail.append(info, workflow, contentTitle, pre);
    card.appendChild(detail);

    detailBtn.addEventListener("click", async () => {
      if (!detail.classList.contains("hidden")) {
        detail.classList.add("hidden"); detailBtn.setAttribute("aria-expanded", "false"); return;
      }
      detail.classList.remove("hidden"); detailBtn.setAttribute("aria-expanded", "true");
      pre.textContent = "正在加载内容…";
      try {
        const data = await safeFetch(routePath("api/history/" + encodeURIComponent(job.id)));
        pre.textContent = data.job.content || data.job.error_message || "没有文本内容";
        fillTaskSteps(workflowList,data.job.progress);
      } catch (e) { pre.textContent = e.message; }
    });
    del.addEventListener("click", async () => { if (!window.confirm(`确定删除《${titleText}》及其关联文件吗？此操作不可恢复。`)) return; del.disabled = true; try { await safeFetch(routePath(`api/history/${encodeURIComponent(job.id)}`), { method: "DELETE" }); await reload(); } catch (e) { window.alert(e.message); del.disabled = false; } });
    return card;
  }


  function initSettings() {
    const shell = document.querySelector(".settings-shell");
    if (!shell) return;
    const links = [...shell.querySelectorAll("[data-settings-target]")];
    const panels = [...shell.querySelectorAll("[data-settings-panel]")];
    const available = new Set(panels.map(panel => panel.dataset.settingsPanel));
    function selectPane(name) {
      const target = available.has(name) ? name : "account";
      links.forEach(link => {
        const active = link.dataset.settingsTarget === target;
        link.classList.toggle("is-active", active);
        if (active) link.setAttribute("aria-current", "location");
        else link.removeAttribute("aria-current");
      });
      panels.forEach(panel => panel.classList.toggle("is-active", panel.dataset.settingsPanel === target));
    }
    selectPane(location.hash.slice(1) || shell.dataset.initialSection || "account");
    window.addEventListener("hashchange", () => selectPane(location.hash.slice(1)));
    const voiceCheck = $("sensevoice-check-button");
    voiceCheck?.addEventListener("click", async () => {
      const state = $("sensevoice-state"), message = $("sensevoice-message");
      voiceCheck.disabled = true;
      message.textContent = "正在检查本地转写服务…";
      try {
        const result = await safeFetch(routePath("api/admin/sensevoice-status"));
        const voice = result.sensevoice || {};
        state.textContent = voice.ready ? "就绪" : (voice.enabled ? "未就绪" : "尚未启用");
        state.classList.toggle("completed", !!voice.ready);
        state.classList.toggle("failed", !voice.ready);
        message.textContent = voice.message || "无法读取服务状态";
      } catch (error) { message.textContent = "检查失败：" + error.message; }
      finally { voiceCheck.disabled = false; }
    });
    const button = $("yuanbao-diagnostic-button");
    if (!button) return;
    button.addEventListener("click", async () => {
      const output = $("yuanbao-diagnostic-result");
      button.disabled = true;
      output.textContent = "正在检查元宝连接…";
      try {
        const { diagnostic: d = {} } = await safeFetch(routePath("api/admin/browserless-diagnostic"));
        const entries = [
          ["元宝页面", d.yuanbaoPage], ["新建对话", d.newChatAvailable], ["输入框", d.editorVisible],
          ["发送按钮", d.sendButtonEnabled], ["聊天区域", d.chatPresent],
          ["登录提示", !d.loginPromptDetected], ["安全验证", !d.verificationDetected],
        ];
        const list = document.createElement("dl");
        list.className = "settings-check-list";
        for (const [label, success] of entries) {
          const row = document.createElement("div");
          const dt = document.createElement("dt");dt.textContent = label;
          const dd = document.createElement("dd");
          dd.textContent = success ? "正常" : "需检查";
          dd.className = success ? "check-ok" : "check-warning";
          row.append(dt, dd);list.appendChild(row);
        }
        output.replaceChildren(list);
      } catch (error) { output.textContent = `检查未完成：${error.message}`; }
      finally { button.disabled = false; }
    });
  }

  function initBatches() {
    const urls = $("batch-urls"), counter = $("batch-url-count"), create = $("batch-submit");
    const modeInputs = [...document.querySelectorAll('[name="batch-task-mode"]')];
    const flags = [$("batch-download"),$("batch-analyze"),$("batch-narration"),$("batch-raw")];
    const timed = $("batch-timed"), preset = $("batch-preset"), prompt = $("batch-prompt");
    const batches = $("batch-choose"), list = $("batch-job-list"), summary = $("batch-summary");
    const createMsg = $("batch-create-feedback"), actionMsg = $("batch-action-feedback");
    const monitorPanel = document.querySelector(".batch-monitor-panel");
    let newest = "", busy = false, timer = null, templates = [];
    const labels = {queued:"待执行",running:"运行中",paused:"已暂停",completed:"已完成",failed:"失败",cancelled:"已取消"};
    const {updateMode,modeValue} = bindTaskOutputOptions({
      modeInputs,flags,timed,analysisOptions:$("batch-analysis-options"),
      formatOptions:$("batch-format-options"),timedWrap:$("batch-timed-wrap"),
      hint:$("batch-task-output-hint"),
    });
    function countPreview() {
      const matches=(urls.value.match(/https?:\/\/[^\s<>"'“”‘’]+/g)||[]);
      const seen=new Set(matches.map(x=>x.replace(/[。；;，,、！!？?）)】]>]+$/g,"")));
      counter.textContent = `${seen.size} 条链接${matches.length>seen.size ? ` · 重复 ${matches.length-seen.size}` : ""}`;
      counter.title = "最多 30 条，最终校验以提交接口为准";
    }
    urls.addEventListener("input",countPreview);countPreview();
    safeFetch(routePath("api/presets")).then(data=>{
      templates=data.presets||[];
      for (const tpl of templates) {
        const opt=document.createElement("option");opt.value=tpl.id;opt.textContent=tpl.name;
        preset.appendChild(opt);
      }
    }).catch(()=>{});
    preset.addEventListener("change",()=>{
      const found=templates.find(x=>x.id===preset.value);
      prompt.value=found ? found.prompt||"" : "";
      if (found?.id==="script_copy" && flags[1].checked) flags[2].checked=true;
      updateMode();
    });
    function publicState(job) {
      if (job.queue_state==="cancelled") return "cancelled";
      if (job.queue_state==="paused") return "paused";
      if (job.queue_state==="waiting" || job.queue_state==="scheduled") return "queued";
      return job.status;
    }
    function renderBatch(batch) {
      monitorPanel.dataset.empty = "false";
      const counts=batch.counts||{};
      $("batch-list-total").textContent=`${(batch.jobs||[]).length} 条任务`;
      summary.replaceChildren();
      const headline=document.createElement("strong");headline.textContent=`${batch.total} 条视频`;
      const details=document.createElement("div");details.className="batch-status-tags";
      for (const key of ["completed","running","queued","paused","failed","cancelled"]) {
        const el=document.createElement("span");el.textContent=`${labels[key]} ${counts[key]||0}`;details.appendChild(el);
      }
      summary.append(headline,details);
      $("batch-pause").disabled=!counts.queued;
      $("batch-resume").disabled=!counts.paused;
      $("batch-retry").disabled=!counts.failed;
      $("batch-cancel").disabled=!counts.queued && !counts.paused;
      const exported=(batch.jobs||[]).some(j=>j.video?.filename || j.text?.filename ||
          j.transcript?.filename || j.raw_transcript?.filename ||
          j.timed_transcript?.filename || j.thumbnail?.filename);
      const bundle=$("batch-export");
      const running=(counts.queued||0)+(counts.running||0)+(counts.paused||0);
      bundle.classList.toggle("hidden",!exported);
      $("batch-export-help").classList.toggle("hidden",!exported);
      bundle.firstChild.textContent = running
        ? "下载本批次已生成成果 ZIP " : "下载本批次全部成果 ZIP ";
      bundle.href=routePath(`api/batches/${encodeURIComponent(batch.id)}/all.zip`);
      list.replaceChildren();
      for (const job of batch.jobs || []) {
        const item=document.createElement("li");item.className="batch-job";
        const header=document.createElement("div");header.className="batch-job-head";
        const title=document.createElement("span");title.className="batch-job-title";
        title.textContent=job.video_title || job.source_url || job.id;
        const state=publicState(job);
        const stateNode=document.createElement("span");stateNode.className="batch-job-state";
        stateNode.dataset.state=state;stateNode.textContent=labels[state]||"处理中";
        header.append(title,stateNode);
        const progress=job.progress||{},meta=document.createElement("div");meta.className="batch-job-meta";
        const current=(progress.steps||[]).find(x=>x.state==="active");
        meta.textContent=`${job.id} · ${progress.completed_steps||0}/${progress.total_steps||0} 步${current?` · ${current.label}`:""}`;
        const meter=document.createElement("div");meter.className="batch-job-meter";
        const fill=document.createElement("span");
        fill.style.width=progress.total_steps>0 ? `${Math.round(100*(progress.completed_steps||0)/progress.total_steps)}%` : "0%";
        meter.setAttribute("aria-label",`已确认 ${progress.completed_steps||0}/${progress.total_steps||0} 个步骤`);
        meter.appendChild(fill);
        const footer=document.createElement("div");footer.className="batch-job-links";
        const date=document.createElement("span");date.className="batch-job-meta";
        date.textContent=job.error_code ? `错误：${job.error_code}` : `创建于 ${fmtDate(job.created_at)}`;
        const detail=document.createElement("a");detail.href=routePath(`history?q=${encodeURIComponent(job.id)}`);
        detail.textContent="查看任务 →";
        footer.append(date,detail);item.append(header,meta,meter,footer);list.appendChild(item);
      }
      if (!batch.jobs?.length) {
        const empty=document.createElement("li");empty.className="batch-empty";empty.textContent="尚无任务";list.appendChild(empty);
      }
    }
    async function loadBatches(preserveSelection=true) {
      if (busy) return;
      busy=true;
      try {
        const data=await safeFetch(routePath("api/batches"),{cache:"no-store"});
        const rows=data.batches||[],selected=preserveSelection && batches.value ? batches.value : newest || rows[0]?.id || "";
        batches.replaceChildren();
        if (!rows.length) {
          const option=document.createElement("option");option.value="";option.textContent="暂无批次";batches.appendChild(option);
          monitorPanel.dataset.empty="true";
          summary.textContent="尚未创建批量任务";list.replaceChildren();return;
        }
        for (const row of rows) {
          const opt=document.createElement("option");opt.value=row.id;
          opt.textContent=`${fmtDate(row.created_at)} · ${row.total} 条`;
          batches.appendChild(opt);
        }
        batches.value=rows.some(x=>x.id===selected) ? selected : rows[0].id;
        renderBatch(rows.find(x=>x.id===batches.value)||rows[0]);
      } catch(err) {
        monitorPanel.dataset.empty="false";
        summary.textContent="暂时无法读取任务队列";
        actionMsg.textContent=`队列更新失败：${err.message}`;
      }
      finally {busy=false;}
    }
    create.addEventListener("click",async()=>{
      if (create.disabled)return;
      create.disabled=true;createMsg.textContent="正在验证链接并创建批次…";
      try {
        const data=await safeFetch(routePath("api/batches"),{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({text:urls.value,task_mode:modeValue(),download:flags[0].checked,
            analyze:flags[1].checked,narration:flags[2].checked,raw_transcript:flags[3].checked,
            timed_transcript:timed.checked,output_format:$("batch-format").value,
            preset_id:preset.value,prompt:prompt.value})});
        newest=data.batch.id;
        createMsg.textContent=`已创建 ${data.batch.total} 条任务${data.duplicates_skipped ? `，忽略 ${data.duplicates_skipped} 条重复链接` : ""}。`;
        await loadBatches(false);
      } catch(err){createMsg.textContent=`创建失败：${err.message}`;}
      finally {create.disabled=false;}
    });
    batches.addEventListener("change",()=>loadBatches(true));
    $("batch-refresh").addEventListener("click",()=>loadBatches(true));
    for (const action of ["pause","resume","retry","cancel"]) {
      const button=$("batch-"+action);
      button.addEventListener("click",async()=>{
        const id=batches.value;if(!id)return;
        if (action==="cancel" && !window.confirm("仅取消尚未开始的任务；当前正在执行的任务将继续。确认取消？"))return;
        button.disabled=true;actionMsg.textContent="正在更新任务队列…";
        try {
          const data=await safeFetch(routePath(`api/batches/${encodeURIComponent(id)}/action`),{
            method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});
          actionMsg.textContent=`已更新 ${data.changed} 条任务${data.active_uninterrupted ? `；${data.active_uninterrupted} 条正在执行的任务未中断` : ""}。`;
          renderBatch(data.batch);
        } catch(err){actionMsg.textContent=`操作失败：${err.message}`;}
        finally {await loadBatches(true);}
      });
    }
    loadBatches(false);
    timer=window.setInterval(()=>{if(!document.hidden)loadBatches(true);},3000);
    window.addEventListener("pagehide",()=>window.clearInterval(timer),{once:true});
  }

  if (page === "workspace") initWorkspace();
  if (page === "settings") initSettings();
  if (page === "history") initHistory();
  if (page === "batches") initBatches();
  window.WechatVideoDL = { safeFetch, applyTheme, openInWeChat };
})();
