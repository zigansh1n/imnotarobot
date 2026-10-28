/*
 * Node tests for the extension's security-relevant logic: the page-facing config whitelist,
 * the page-initiated solve gating, the per-tab rate limit and the "find the app, then trust
 * it with the key" discovery. The extension files are loaded into this context with the
 * browser APIs stubbed out; run with `node tests/extension_js/js_tests.js`.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const EXT = path.join(__dirname, "..", "..", "imnotarobot", "extension");

let failures = 0;
function check(name, condition) {
    console.log((condition ? "ok   " : "FAIL ") + name);
    if (!condition) failures++;
}

function eq(name, actual, expected) {
    const same = JSON.stringify(actual) === JSON.stringify(expected);
    check(`${name} (${JSON.stringify(actual)})`, same);
}

// ---------- browser stubs ----------
let storedConfig = {};  // what chrome.storage.local holds under 'config'
let apiClients = [];    // every TwoCaptcha the background constructed
let apiCalls = [];      // every solve that reached a client
let onConnectHandler = null;

global.self = globalThis;
global.location = {href: "https://demo.example/form"};

global.chrome = {
    storage: {
        local: {
            get: (key, cb) => cb({config: storedConfig}),
            set: (obj, cb) => { Object.assign(storedConfig, obj.config || {}); cb && cb(obj); },
        },
    },
    runtime: {
        onInstalled: {addListener() {}},
        onConnect: {addListener: fn => { onConnectHandler = fn; }},
        onMessage: {addListener() {}},
        connect: () => ({onMessage: {addListener() {}}, onDisconnect: {addListener() {}}, postMessage() {}}),
        openOptionsPage() {}, getURL: p => "chrome-extension://test/" + p, id: "test",
    },
    i18n: {getMessage: k => k, getUILanguage: () => "en"},
    contextMenus: {removeAll: cb => cb && cb(), create() {}, onClicked: {addListener() {}}},
};

global.document = {
    createElement: tag => ({tagName: tag, style: {}, dataset: {}}),
    head: {appendChild() {}, prepend() {}},
    documentElement: {prepend() {}},
    body: {},
    addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
    evaluate: () => ({singleNodeValue: null}),
};
global.$ = () => ({each() {}, find: () => ({each() {}}), on() {}, click() {}, attr: () => "", remove() {}});
global.setInterval = () => 0;  // the extension's polling loops must not keep node alive

global.TwoCaptcha = class {
    constructor(options) { this.options = options; apiClients.push(options); }
    normal(params) { apiCalls.push(["normal", this.options.service]); return {captchaId: 1, code: "w9h5k"}; }
    recaptcha(params) { apiCalls.push(["recaptcha", this.options.service]); return {captchaId: 1, code: "tok"}; }
    async userInfo() { return {key_type: "customer", email: "ImNotARobot", balance: "0.00", valute: "USD"}; }
};

// what /app-info answers per base; null means the connection itself fails
let reachable = {};
global.fetch = async url => {
    if (url.endsWith("/app-info")) {
        const base = url.slice(0, -"/app-info".length);
        if (base in reachable) {
            if (reachable[base] === null) throw new TypeError("failed to fetch");
            return {ok: true, json: async () => reachable[base]};
        }
    }
    return {ok: false, json: async () => ({})};
};

for (const file of ["common/config.js", "background/background.js", "content/script.js"]) {
    vm.runInThisContext(fs.readFileSync(path.join(EXT, file), "utf8"), {filename: file});
}

// a processor standing in for the real ones: enabled flag + params straight from the widget
CaptchaProcessors.register({
    captchaType: "normal",
    canBeProcessed: (widget, config) => !!config.enabledForNormal,
    getParams: widget => ({body: widget.base64, from: "widget"}),
});

const settle = () => new Promise(resolve => setImmediate(resolve));

// a content-script port of one tab, as the background sees it
function contentPort(tabId) {
    const messages = [];
    const port = {name: "content", sender: {tab: {id: tabId}}, postMessage: m => messages.push(m),
                  onMessage: {addListener: fn => { port.receive = fn; }}};
    onConnectHandler(port);
    port.messages = messages;
    return port;
}

async function relaySolve(port, msg) {
    port.receive(msg);
    for (let i = 0; i < 5; i++) await settle();
    return port.messages[port.messages.length - 1];
}

// the config every test starts from: the defaults plus a page-found normal-captcha widget
async function baseConfig(overrides) {
    const config = await Config.getAll();
    return Object.assign(config, {
        apiKey: "SECRET-KEY", apiService: "http://127.0.0.1:8765", email: "me@example.com",
        proxy: "login:pass@1.2.3.4:3128", isPluginEnabled: true, enabledForNormal: true, autoSolveNormal: true,
    }, overrides);
}

async function main() {
    // ---- finding 1: getConfig must answer with the detection switches only ----
    const config = await baseConfig();
    const safe = pageConfig(config);
    check("pageConfig keeps the detection switches",
          safe.enabledForNormal === true && safe.blackListDomain === config.blackListDomain
          && safe.recaptchaV3MinScore === config.recaptchaV3MinScore);
    check("pageConfig never hands out the key, the address, the proxy or the account",
          ["apiKey", "apiService", "proxy", "proxytype", "email", "valute", "normalSources", "autoSubmitRules"]
              .every(key => !(key in safe)));
    check("pageConfig adds nothing beyond the whitelist",
          Object.keys(safe).every(key => ["enabledForNormal", "enabledForRecaptchaV2", "enabledForInvisibleRecaptchaV2",
              "enabledForRecaptchaV3", "enabledForHCaptcha", "enabledForGeetest", "enabledForGeetest_v4",
              "enabledForKeycaptcha", "enabledForArkoselabs", "enabledForLemin", "enabledForYandex",
              "enabledForCapyPuzzle", "enabledForAmazonWaf", "enabledForTurnstile", "recaptchaV3MinScore",
              "blackListDomain"].includes(key)));

    // ---- finding 2: the page may only trigger what the on-page button could ----
    const widget = {captchaType: "normal", widgetId: 0, base64: "aGk="};
    eq("page solve passes when everything is enabled",
       pageSolveRequest(widget, await baseConfig()),
       {action: "solve", captchaType: "normal", widgetId: 0, params: {body: "aGk=", from: "widget"}});
    check("page solve is refused while the plugin is off", pageSolveRequest(widget, await baseConfig({isPluginEnabled: false})) === null);
    check("page solve is refused on a blacklisted site", pageSolveRequest(widget, await baseConfig({blackListDomain: "demo.example"})) === null);
    check("page solve is refused for a disabled captcha kind", pageSolveRequest(widget, await baseConfig({enabledForNormal: false})) === null);
    check("page solve is refused without the auto-solve switch", pageSolveRequest(widget, await baseConfig({autoSolveNormal: false})) === null);
    check("page solve is refused for a kind the extension does not know",
          pageSolveRequest({captchaType: "geetest", widgetId: 1}, await baseConfig()) === null);
    check("page solve is refused for a recaptcha v3 without its own auto-solve switch",
          pageSolveRequest({captchaType: "recaptcha", version: "v3", widgetId: 2}, await baseConfig({enabledForRecaptchaV3: true})) === null);

    // ---- finding 2: the background rate-limits page-relayed solves per tab ----
    initApiClient("SECRET-KEY", "http://127.0.0.1:8765");
    const port = contentPort(7);
    const pageRequest = {action: "solve", captchaType: "normal", widgetId: 0, params: {}, messageId: "m1"};
    for (let i = 0; i < 5; i++) {
        const r = await relaySolve(port, pageRequest);
        check("page solve " + (i + 1) + " of 5 goes through", r && r.response && r.response.code === "w9h5k");
    }
    eq("the 6th page solve in a minute is refused", (await relaySolve(port, pageRequest)).error, "RATE_LIMIT_EXCEEDED");
    const otherTab = contentPort(8);
    check("another tab has its own allowance", (await relaySolve(otherTab, pageRequest)).response.code === "w9h5k");
    for (let i = 0; i < 8; i++) {
        const r = await relaySolve(port, {action: "solve", captchaType: "normal", widgetId: 0, params: {}});
        if (!r || !r.response) check("button solve is never rate-limited", false);
    }
    check("button solves (no messageId) are not rate-limited", true);

    // ---- finding 7: the app is found by /app-info, never assumed by port ----
    reachable = {"http://127.0.0.1:8765": null, "http://127.0.0.1:8767": {app: "imnotarobot"}};
    eq("discovery walks 8765-8774 and picks the port that proves itself",
       await resolveService("http://127.0.0.1:8765"), "http://127.0.0.1:8767");
    reachable = {"http://127.0.0.1:8765": {app: "something-else"}, "http://127.0.0.1:8766": {app: "imnotarobot"}};
    eq("a server that does not name imnotarobot is skipped", await resolveService(null), "http://127.0.0.1:8766");
    reachable = {"http://127.0.0.1:9000": {app: "imnotarobot"}};
    eq("a working custom address is kept", await resolveService("http://127.0.0.1:9000"), "http://127.0.0.1:9000");
    reachable = {};
    check("nothing answering means no service", (await resolveService("http://127.0.0.1:8765")) === null);

    // ---- finding 7: the key is never sent to an unverified service ----
    API = null;
    storedConfig = {apiKey: "SECRET-KEY", apiService: "http://127.0.0.1:8765"};
    const clientsBefore = apiClients.length;
    let refused = false;
    try { await ensureApiClient(); } catch (e) { refused = e.message.includes("not running"); }
    check("solving without the app says so instead of leaking the key", refused);
    check("no client was built with the key while nothing verified",
          apiClients.slice(clientsBefore).every(c => c.apiKey !== "SECRET-KEY"));
    let loginRefused = false;
    try { await popup_login({apiKey: "SECRET-KEY-2"}); } catch (e) { loginRefused = e.message.includes("not running"); }
    check("login without the app refuses instead of leaking the key", loginRefused);

    reachable = {"http://127.0.0.1:8767": {app: "imnotarobot"}};
    const info = await popup_login({apiKey: "SECRET-KEY-2"});
    check("login finds the app and connects", info.key_type === "customer");
    eq("the found port is remembered", storedConfig.apiService, "http://127.0.0.1:8767");
    eq("the client talks to the verified port only", apiClients[apiClients.length - 1].service, "http://127.0.0.1:8767");
}

main().then(() => {
    if (failures) { console.log(failures + " failure(s)"); process.exit(1); }
    console.log("all extension js tests passed");
}).catch(e => { console.error(e); process.exit(1); });
