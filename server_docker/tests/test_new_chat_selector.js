// Regression for Yuanbao's localized New Chat button (no live cookies).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function testScript(name) {
  const source = fs.readFileSync(path.join(__dirname, "..", name), "utf8");
  const preamble = source.slice(0, source.indexOf("module.exports"));
  const scope = { setTimeout: (fn) => fn(), Promise };
  vm.runInNewContext(preamble, scope);
  assert.equal(typeof scope.openFreshChat, "function");

  async function run(buttons) {
    const clicked = [];
    const page = {
      $$: async (selector) => (buttons[selector] || []).map(({visible, label}) => ({
        boundingBox: async () => visible ? {width: 12, height: 16} : null,
        click: async () => clicked.push(label),
      })),
    };
    await scope.openFreshChat(page);
    return clicked;
  }
  assert.deepEqual(await run({'[aria-label="新建对话"]': [{visible: true,label:"zh"}]}), ["zh"]);
  assert.deepEqual(await run({
    '[aria-label="新建对话"]': [{visible: false,label:"hidden"}, {visible: true,label:"visible"}],
    '[aria-label="New Chat"]': [{visible: true,label:"english"}],
  }), ["visible"]);
  assert.deepEqual(await run({'[aria-label="New Chat"]': [{visible: true,label:"english"}]}), ["english"]);
  await assert.rejects(run({}), /YUANBAO_NEW_CHAT_UNAVAILABLE/);
  console.log(name + ": localized visible button / fallback / missing tests OK");
}
(async () => {
  await testScript("browserless_function.js");
  await testScript("browserless_text_function.js");
})().catch(err => { console.error(err); process.exitCode = 1; });
