// Read-only Yuanbao inspection. Never returns page text, cookies or account data.
module.exports = async ({ page, context }) => {
  const session = context?.session || {};
  if (session.ua) await page.setUserAgent(String(session.ua));
  await page.evaluateOnNewDocument((state) => {
    try {
      for (const [key, val] of Object.entries(state.localStorage || {})) localStorage.setItem(key, String(val));
      for (const [key, val] of Object.entries(state.sessionStorage || {})) sessionStorage.setItem(key, String(val));
    } catch (_) {}
  }, session);
  const cookies = Array.isArray(context?.cookies) ? context.cookies : [];
  const extra = [];
  for (const part of String(session.cookie || '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0) extra.push({ name: part.slice(0, i).trim(), value: part.slice(i + 1).trim(), url: 'https://yuanbao.tencent.com/' });
  }
  if (cookies.length || extra.length) await page.setCookie(...cookies, ...extra);
  await page.goto('https://yuanbao.tencent.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await new Promise(resolve => setTimeout(resolve, 2200));
  const result = await page.evaluate(() => {
    const body = document.body?.innerText || '';
    const editor = document.querySelector('[contenteditable="true"]');
    const send = document.querySelector('#yuanbao-send-btn');
    const newChatAvailable = [...document.querySelectorAll(
      '[aria-label="新建对话"], [aria-label="New Chat"]'
    )].some(el => Boolean(el.getClientRects().length));
    return {
      yuanbaoPage: location.hostname === 'yuanbao.tencent.com',
      editorPresent: Boolean(editor),
      editorVisible: Boolean(editor?.getClientRects().length),
      newChatAvailable,
      sendButtonPresent: Boolean(send),
      sendButtonEnabled: Boolean(send && !send.disabled),
      chatPresent: Boolean(document.querySelector('#chat-content')),
      loginPromptDetected: /扫码登录|请使用微信扫描二维码登录|未登录/.test(body),
      verificationDetected: /滑块验证|安全验证|验证码/.test(body),
      cardSelectorPresent: Boolean(document.querySelector('.hyc-link-card')),
    };
  });
  return { data: result, type: 'application/json' };
};
