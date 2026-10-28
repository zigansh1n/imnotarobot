/*
 * Show options page after installation
 */
chrome.runtime.onInstalled.addListener(function(details) {
    if (details.reason == 'install') {
        if (chrome.runtime.openOptionsPage) {
            chrome.runtime.openOptionsPage();
        } else {
            self.open(chrome.runtime.getURL('options/options.html'));
        }
    }
});


var API;

Config.getAll().then(async config => {
    if (config.apiKey) {
        await ensureApiClient();
    }
}).catch(error => {
    // the app is probably not running yet: the first solve retries and reports it properly
    console.warn(error.message);
});

function initApiClient(apiKey, service) {
    API = new TwoCaptcha({
        apiKey: apiKey,
        service: service,
        defaultTimeout: 300,
        pollingInterval: 5,
    });
}


/*
 * The app listens on 127.0.0.1:8765-8774, taking the first free port. A base is used only
 * after /app-info proves it is our app; the access key is never sent anywhere else.
 */
const PROBE_TIMEOUT = 1500;

async function verifiedService(base) {
    // a local port that accepts the connection but never answers must not stall the probe
    let controller = new AbortController();
    let timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT);

    try {
        let response = await fetch(base.replace(/\/+$/, "") + "/app-info", {signal: controller.signal});
        if (response.ok && (await response.json()).app === "imnotarobot") return base;
    } catch (e) {}
    finally {
        clearTimeout(timer);
    }

    return null;
}

async function resolveService(configured) {
    let candidates = [];

    if (configured) candidates.push(configured);
    for (let port = 8765; port <= 8774; port++) candidates.push("http://127.0.0.1:" + port);

    for (let base of [...new Set(candidates)]) {
        if (await verifiedService(base)) return base;
    }

    return null;
}

async function ensureApiClient() {
    if (API) return API;

    let config = await Config.getAll();

    if (!config.apiKey) throw new Error("Enter the access key in the extension options");

    let service = await resolveService(config.apiService);

    if (!service) throw new Error("The ImNotARobot app is not running (127.0.0.1:8765-8774)");

    initApiClient(config.apiKey, service);
    if (service !== config.apiService) Config.set({apiService: service});

    return API;
}


/*
 * A page can ask for solves through the DOM channel (content/script.js relays them);
 * a tab gets a few of those per minute, anything beyond that is refused.
 */
const RELAY_LIMIT = 5;
const relayHistory = new Map();  // tab id -> solve timestamps

function relayAllowed(tabId) {
    let now = Date.now();
    let recent = (relayHistory.get(tabId) || []).filter(function (t) { return now - t < 60000; });

    if (recent.length >= RELAY_LIMIT) return false;

    recent.push(now);
    relayHistory.set(tabId, recent);

    return true;
}

// tab ids get reused, and the map must not outlive the tab it counts for
chrome.tabs.onRemoved.addListener(tabId => relayHistory.delete(tabId));


/*
 * Manage message passing
 */
chrome.runtime.onConnect.addListener(function(port) {

    //console.log(port.name + ' connected');
    //console.log(port);

    port.onMessage.addListener(function (msg) {
        //console.log(port.name + ' send message: ', msg);

        let messageHandler = port.name + '_' + msg.action;

        if (self[messageHandler] === undefined) return;

        self[messageHandler](msg, port)
            .then((response) => {
                //console.log('response to [' + messageHandler + ']: ', response);
                port.postMessage({action: msg.action, request: msg, response});
            })
            .catch(error => {
                //console.log('return error to [' + messageHandler + ']: ', error.message);
                port.postMessage({action: msg.action, request: msg, error: error.message});
            });
    });

});


/*
 * Message handlers
 */

async function popup_login(msg) {
    let config = await Config.getAll();

    let service = await resolveService(msg.apiService || config.apiService);

    if (!service) throw new Error("The ImNotARobot app is not running (127.0.0.1:8765-8774)");

    initApiClient(msg.apiKey, service);

    let info = await API.userInfo();

    if (info.key_type !== "customer") {
        throw new Error("You entered worker key! Switch your account into \"customer\" mode to get right API-KEY");
    }

    info.valute = (info.valute || "USD").toUpperCase();

    Config.set({
        apiKey: msg.apiKey,
        apiService: service,
        email:  info.email,
        valute: info.valute,
    });

    return info;
}

async function popup_logout(msg) {
    Config.set({apiKey: null});
    API = null;

    return {};
}

async function popup_getAccountInfo(msg) {
    let config = await Config.getAll();

    if (!config.apiKey) throw new Error("No apiKey");

    let info = await (await ensureApiClient()).userInfo();

    info.valute = (info.valute || "USD").toUpperCase();

    return info;
}

async function content_solve(msg, port) {
    if (msg.messageId !== undefined) {
        // the request came from the page, not from our own button
        let tabId = port && port.sender && port.sender.tab ? port.sender.tab.id : 0;

        if (!relayAllowed(tabId)) throw new Error("RATE_LIMIT_EXCEEDED");
    }

    let api = await ensureApiClient();

    try {
        return await api[msg.captchaType](msg.params);
    } catch (e) {
        if (e.message !== "API_CONNECTION_ERROR") throw e;

        // the app may have moved to another port since this client was built: find it again, once
        API = null;

        return await (await ensureApiClient())[msg.captchaType](msg.params);
    }
}
