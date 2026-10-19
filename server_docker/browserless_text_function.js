const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Yuanbao uses localized ARIA labels; never assume an English-only UI.
async function openFreshChat(page) {
  const selectors = ['[aria-label="新建对话"]', '[aria-label="New Chat"]'];
  for (let attempt = 0; attempt < 12; attempt++) {
    for (const selector of selectors) {
      for (const candidate of await page.$$(selector)) {
        const box = await candidate.boundingBox();
        if (!box || box.width <= 0 || box.height <= 0) continue;
        await candidate.click();
        return;
      }
    }
    await sleep(350);
  }
  throw new Error("YUANBAO_NEW_CHAT_UNAVAILABLE");
}

module.exports = async ({ page, context }) => {
  const prompt = String(context?.prompt || "").trim();
  const transcript = String(context?.transcript || "").trim();
  const state = context?.session && typeof context.session === "object" ? context.session : {};
  const cookies = Array.isArray(context?.cookies) ? context.cookies : [];
  if (!prompt || !transcript) throw new Error("TEXT_INPUT_MISSING");
  if (transcript.length > 14000) throw new Error("TEXT_INPUT_TOO_LONG");
  page.setDefaultTimeout(30000);
  page.setDefaultNavigationTimeout(30000);
  if (state.ua) await page.setUserAgent(String(state.ua));
  await page.setViewport({ width: 1782, height: 1179 });
  await page.evaluateOnNewDocument((s) => {
    try {
      for (const [key, value] of Object.entries(s.localStorage || {})) {
        if (value != null) localStorage.setItem(key, String(value));
      }
      for (const [key, value] of Object.entries(s.sessionStorage || {})) {
        if (value != null) sessionStorage.setItem(key, String(value));
      }
    } catch (_) {}
  }, state);
  const extra = [];
  for (const pair of String(state.cookie || "").split(";")) {
    const raw = pair.trim(), sep = raw.indexOf("=");
    if (sep > 0) extra.push({name:raw.slice(0,sep),value:raw.slice(sep+1),url:"https://yuanbao.tencent.com/"});
  }
  if (cookies.length || extra.length) await page.setCookie(...cookies,...extra);
  await page.goto("https://yuanbao.tencent.com/",{waitUntil:"domcontentloaded",timeout:30000});
  // Never post another task's voice transcript into an automatically restored old chat.
  await openFreshChat(page);
  await sleep(900);
  const editor='[contenteditable="true"]';
  await page.waitForSelector(editor,{timeout:30000});
  const input = [
    "请严格根据下面给出的语音转写原文，完成要求，不要尝试打开或查找视频链接。",
    "不要编造转写中没有的信息。对于可能的语音识别错误，可以按上下文修正措辞，但不要改变原意。",
    "【任务要求】",prompt,"【语音转写原文】",transcript,
    "【回答要求】直接输出按任务要求整理后的中文内容，不要重复粘贴整段原文。"
  ].join("\n\n");
  await page.click(editor);
  try {
    if (typeof page.keyboard.sendCharacter === "function") await page.keyboard.sendCharacter(input);
    else await page.keyboard.type(input, {delay:0});
  } catch (_) {}
  let editorLen = await page.$eval(editor, el => (el.innerText || el.textContent || "").trim().length);
  if (editorLen < 40) {
    await page.evaluate((selector, text) => {
      const el = document.querySelector(selector);
      if (!el) return;
      el.focus();
      el.textContent = text;
      el.dispatchEvent(new InputEvent("input", {bubbles:true,inputType:"insertText",data:text}));
    }, editor, input);
    await sleep(400);
    editorLen = await page.$eval(editor, el => (el.innerText || el.textContent || "").trim().length);
  }
  if (editorLen < Math.floor(input.length * 0.85)) throw new Error("TEXT_EDITOR_INCOMPLETE");
  await page.waitForSelector("#yuanbao-send-btn",{timeout:12000});
  await page.click("#yuanbao-send-btn");
  await sleep(1400);
  let retained = await page.$eval(editor, el => (el.innerText || el.textContent || "").trim().length).catch(() => 0);
  if (retained > 50) {
    // Rich editors sometimes require a real key event before React commits
    // the draft. Retry via the same keyboard send gesture as the web UI.
    await page.click(editor);
    await page.keyboard.press("Enter");
    await sleep(1600);
    retained = await page.$eval(editor, el => (el.innerText || el.textContent || "").trim().length).catch(() => 0);
  }
  if (retained > 50) throw new Error("TEXT_NOT_SENT");
  let last="",unchanged=0,found=false;
  const deadline=Date.now()+160000;
  while (Date.now() < deadline) {
    await sleep(2500);
    const result=await page.evaluate(() => {
      const root=document.querySelector("#chat-content")||document.body;
      const targets = [...root.querySelectorAll(".hyc-markdown, .markdown-body, [class*='markdown'], [class*='answer-content']")];
      const entries=targets.map(e=>(e.innerText||"").trim()).filter(x=>x.length>30);
      const full=(root.innerText||"").trim();
      return {answer:entries.length?entries[entries.length-1]:"",full};
    });
    let value=result.answer;
    if (!value || value.length<50) {
      const pos=result.full.lastIndexOf("【回答要求】");
      if(pos>=0) {
        const rest=result.full.slice(pos);
        const end=rest.indexOf("不要重复粘贴整段原文。");
        value=end>=0?rest.slice(end+"不要重复粘贴整段原文。".length).trim():"";
      }
      if (!value || value.length<50) {
        // Last resort: extract text after the user prompt, not the prompt itself.
        const idx=result.full.indexOf(input);
        if(idx>=0) value=result.full.slice(idx+input.length).trim();
      }
    }
    if (value && value.length>70 && !value.includes("【任务要求】") && !value.includes("【语音转写原文】")) {
      found=true;
      if (value===last) unchanged++; else unchanged=0;
      last=value;
      if(unchanged>=3) break;
    }
  }
  if (!found || !last || last.length<70) throw new Error("TEXT_ANALYSIS_NOT_FOUND");
  return {data:{analysis:last,chars:last.length},type:"application/json"};
};
