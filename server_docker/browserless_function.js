const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function findVideoUrl(value, depth = 0) {
  if (depth > 12 || value == null) return "";
  if (typeof value === "string") {
    if (/^https:\/\/finder\.video\.qq\.com\//i.test(value)) return value;
    return "";
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findVideoUrl(item, depth + 1);
      if (found) return found;
    }
    return "";
  }
  if (typeof value === "object") {
    const preferred = [
      value?.h264VideoInfo?.videoUrl,
      value?.h265VideoInfo?.videoUrl,
      value?.videoUrl,
      value?.url,
    ];
    for (const item of preferred) {
      if (typeof item === "string" && /^https:\/\/finder\.video\.qq\.com\//i.test(item)) return item;
    }
    for (const item of Object.values(value)) {
      const found = findVideoUrl(item, depth + 1);
      if (found) return found;
    }
  }
  return "";
}

module.exports = async ({ page, context }) => {
  const requestText = String(context?.requestText || "").trim();
  const cookies = Array.isArray(context?.cookies) ? context.cookies : [];
  const sessionState =
    context?.session && typeof context.session === "object" ? context.session : {};
  if (!requestText) throw new Error("requestText is empty");

  page.setDefaultTimeout(30000);
  page.setDefaultNavigationTimeout(30000);

  if (sessionState.ua) {
    await page.setUserAgent(String(sessionState.ua));
  }
  await page.setViewport({ width: 1782, height: 1179 });

  await page.evaluateOnNewDocument((state) => {
    try {
      if (state.platform) {
        Object.defineProperty(navigator, "platform", {
          get: () => state.platform,
          configurable: true,
        });
      }
      if (state.language) {
        Object.defineProperty(navigator, "language", {
          get: () => state.language,
          configurable: true,
        });
      }
      for (const [key, value] of Object.entries(state.localStorage || {})) {
        if (value !== null && value !== undefined) localStorage.setItem(key, String(value));
      }
      for (const [key, value] of Object.entries(state.sessionStorage || {})) {
        if (value !== null && value !== undefined) sessionStorage.setItem(key, String(value));
      }
    } catch (_) {}
    try {
      const originalPause = HTMLMediaElement.prototype.pause;
      HTMLMediaElement.prototype.play = function () {
        try {
          this.muted = true;
          this.volume = 0;
          originalPause.call(this);
        } catch (_) {}
        return Promise.resolve();
      };
    } catch (_) {}
  }, sessionState);

  const extraCookies = [];
  for (const pair of String(sessionState.cookie || "").split(";")) {
    const item = pair.trim();
    if (!item) continue;
    const i = item.indexOf("=");
    if (i <= 0) continue;
    extraCookies.push({
      name: item.slice(0, i),
      value: item.slice(i + 1),
      url: "https://yuanbao.tencent.com/",
    });
  }
  if (cookies.length || extraCookies.length) {
    await page.setCookie(...cookies, ...extraCookies);
  }

  await page.goto("https://yuanbao.tencent.com/", { waitUntil: "domcontentloaded", timeout: 30000 });
  await sleep(1200);

  const initialBody = await page.evaluate(() => document.body?.innerText || "");
  if (initialBody.includes("微信扫码登录") || initialBody.includes("请使用微信扫描二维码登录") || initialBody.includes("未登录")) {
    throw new Error("YUANBAO_LOGIN_REQUIRED:" + initialBody.slice(0, 500));
  }

  // Yuanbao restores a previously opened chat on the root URL. Explicitly
  // open a fresh conversation to avoid silently posting into old history.
  const newChat = await page.$('[aria-label="New Chat"]');
  if (!newChat) throw new Error("YUANBAO_NEW_CHAT_UNAVAILABLE");
  await page.click('[aria-label="New Chat"]');
  await sleep(900);

  const editor = '[contenteditable="true"]';
  await page.waitForSelector(editor, { timeout: 30000 });
  await page.click(editor);
  if (typeof page.keyboard.sendCharacter === "function") {
    await page.keyboard.sendCharacter(requestText);
  } else {
    await page.keyboard.type(requestText, { delay: 1 });
  }
  await sleep(300);
  let editorText = await page.$eval(editor, (el) => (el.innerText || el.textContent || "").trim());
  if (editorText !== requestText) {
    await page.evaluate(
      (selector, text) => {
        const el = document.querySelector(selector);
        if (!el) return;
        el.focus();
        el.textContent = text;
        el.dispatchEvent(
          new InputEvent("input", {
            bubbles: true,
            inputType: "insertText",
            data: text,
          })
        );
      },
      editor,
      requestText
    );
    await sleep(200);
    editorText = await page.$eval(editor, (el) => (el.innerText || el.textContent || "").trim());
  }
  if (editorText !== requestText) throw new Error("YUANBAO_EDITOR_MISMATCH");
  await page.waitForSelector("#yuanbao-send-btn", { timeout: 10000 });
  // Observe only in-browser booleans; never log the submitted prompt or chat.
  const before = await page.evaluate((needle) => {
    const t = document.querySelector("#chat-content")?.innerText || "";
    return t.split(needle).length - 1;
  }, requestText);
  await page.click("#yuanbao-send-btn");
  let messageAcknowledged = false;
  const sendDeadline = Date.now() + 15000;
  while (Date.now() < sendDeadline) {
    messageAcknowledged = await page.evaluate(({ needle, previous }) => {
      const content = document.querySelector("#chat-content")?.innerText || "";
      const occurrences = content.split(needle).length - 1;
      return occurrences > previous;
    }, { needle: requestText, previous: before });
    if (messageAcknowledged) break;
    await sleep(500);
  }
  if (!messageAcknowledged) throw new Error("YUANBAO_SEND_UNCONFIRMED");
  // Video-card generation is often slower on the first Yuanbao request.
  // Previously the fixed 65s cutoff made a second identical chat request
  // necessary. Allow more time without blocking the HTTP client (async API).
  try {
    await page.waitForSelector(".hyc-link-card", { timeout: 110000 });
  } catch (err) {
    // Safe diagnostics only: never include the chat body, session or cookies
    // in Browserless' error string or application retry history.
    const state = await page.evaluate(() => ({
      editorReady: Boolean(document.querySelector('[contenteditable="true"]')),
      chatPresent: Boolean(document.querySelector("#chat-content")),
      chatLength: Math.min(9999, (document.querySelector("#chat-content")?.innerText || "").length),
    })).catch(() => ({}));
    throw new Error("YUANBAO_CARD_TIMEOUT:" + JSON.stringify(state));
  }

  let rawContent = "";
  let previous = "";
  let stable = 0;
  const answerDeadline = Date.now() + 125000;
  while (!context?.extractOnly && Date.now() < answerDeadline) {
    await sleep(2000);
    rawContent = await page.evaluate(() => document.querySelector("#chat-content")?.innerText || "");
    if (rawContent.includes("微信扫码登录") || rawContent.includes("请使用微信扫描二维码登录")) {
      throw new Error("YUANBAO_LOGIN_REQUIRED");
    }
    const marker = rawContent.indexOf("微信视频号");
    const answer = marker >= 0 ? rawContent.slice(marker + 5).trim() : "";
    if (answer.length >= 80) {
      if (rawContent === previous) stable += 1;
      else stable = 0;
      if (stable >= 2) break;
    }
    previous = rawContent;
  }

  const card = await page.evaluate(async () => {
    const e = document.querySelector(".hyc-link-card");
    if (!e) return null;
    const key = Object.keys(e).find((x) => x.startsWith("__reactFiber"));
    const props = e?.[key]?.return?.memoizedProps || {};
    let resolved = "";
    try {
      resolved = props.resolveUrl ? await props.resolveUrl(props.url) : props.url || "";
    } catch (_) {
      resolved = props.url || "";
    }
    return {
      source: props.source || "",
      title: props.content || "",
      coverUrl: props.coverUrl || "",
      originalUrl: props.url || "",
      previewUrl: String(resolved || ""),
    };
  });

  if (!card?.previewUrl) throw new Error("YUANBAO_PREVIEW_URL_MISSING");

  const marker = rawContent.indexOf("微信视频号");
  let answer = marker >= 0 ? rawContent.slice(marker + "微信视频号".length).trim() : rawContent.trim();
  answer = answer.replace(/\n+源\s*$/u, "").trim();

  let interceptedVideoUrl = "";
  page.on("response", async (response) => {
    if (interceptedVideoUrl) return;
    try {
      const headers = response.headers();
      const type = String(headers["content-type"] || "");
      if (!type.includes("json")) return;
      const body = await response.json();
      const found = findVideoUrl(body);
      if (found) interceptedVideoUrl = found;
    } catch (_) {}
  });

  // 获取真实 URL 时阻止媒体资源真正下载。页面仍会给 video.src 赋值，
  // 但音视频字节不会进入 Chromium，因此不会发生后台播放。
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    if (request.resourceType() === "media") request.abort();
    else request.continue();
  });

  const feedUrl = card.previewUrl.includes("no_autoplay=")
    ? card.previewUrl
    : card.previewUrl + (card.previewUrl.includes("?") ? "&" : "?") + "no_autoplay=1";
  await page.goto(feedUrl, { waitUntil: "domcontentloaded", timeout: 30000 });

  let directUrl = "";
  const videoDeadline = Date.now() + 30000;
  while (Date.now() < videoDeadline) {
    if (interceptedVideoUrl) {
      directUrl = interceptedVideoUrl;
      break;
    }
    directUrl = await page.evaluate(() => {
      const video = document.querySelector("video");
      if (!video) return "";
      try {
        video.muted = true;
        video.volume = 0;
        video.pause();
      } catch (_) {}
      return video.src || video.currentSrc || "";
    });
    if (/^https:\/\/finder\.video\.qq\.com\//i.test(directUrl)) break;
    directUrl = "";
    await sleep(500);
  }

  if (!directUrl) throw new Error("WECHAT_VIDEO_URL_MISSING");

  return {
    data: {
      yuanbaoContent: answer,
      rawContent,
      previewUrl: card.previewUrl,
      title: String(card.title || "").trim(),
      source: card.source,
      coverUrl: card.coverUrl,
      directUrl,
    },
    type: "application/json",
  };
};
