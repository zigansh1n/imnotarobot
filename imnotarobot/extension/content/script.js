/*
 * Load necessary actions in main scope
 */
Config.getAll().then(function (config) {
    const isBlocked = isBlockDomain(config.blackListDomain);
    let scripts = [
        ["content/core_helpers.js"],
        ["content/communication_helpers.js"],
        ["content/captcha/geetest/interceptor.js", config.isPluginEnabled && config.enabledForGeetest && !isBlocked],
        ["content/captcha/geetest_v4/interceptor.js", config.isPluginEnabled && config.enabledForGeetest_v4 && !isBlocked],
        ["content/captcha/hcaptcha/interceptor.js", config.isPluginEnabled && config.enabledForHCaptcha && !isBlocked],
        ["content/captcha/hcaptcha/hunter.js"],
        ["content/captcha/keycaptcha/hunter.js"],
        ["content/captcha/recaptcha/interceptor.js", config.isPluginEnabled && (config.enabledForRecaptchaV2 || config.enabledForInvisibleRecaptchaV2 || config.enabledForRecaptchaV3) && !isBlocked],
        ["content/captcha/recaptcha/hunter.js"],
        ["content/captcha/arkoselabs/interceptor.js", config.isPluginEnabled && config.enabledForArkoselabs && !isBlocked],
        ["content/captcha/arkoselabs/hunter.js"],
        ["content/captcha/lemin/interceptor.js", config.isPluginEnabled && config.enabledForLemin && !isBlocked],
        ["content/captcha/yandex/interceptor.js", config.isPluginEnabled && config.enabledForYandex && !isBlocked],
        ["content/captcha/amazon_waf/interceptor.js", config.isPluginEnabled && config.enabledForAmazonWaf && !isBlocked],
        ["content/captcha/turnstile/hunter.js"]
    ];

    scripts.forEach(s => {
        if (s.length > 1 && !s[1]) return;

        let script = document.createElement('script');
        script.src = chrome.runtime.getURL(s[0]);
        (document.head || document.documentElement).prepend(script);
    });

});


/*
 * Captcha Processors Repository
 */
var CaptchaProcessors = {

    list: {},

    register: function (processor) {
        this.list[processor.captchaType] = processor;
    },

    get: function (captchaType) {
        return this.list[captchaType];
    },

};

/*
 * Main loop.
 * It iterates over found captcha widgets and processes them.
 */
let CAPTCHA_WIDGETS_LOOP = setInterval(function () {
    Config.getAll().then(config => {
        if (!config.isPluginEnabled) return;
        if (config.apiKey === null) return;
        if (isBlockDomain(config.blackListDomain)) return;

        $("head").find("captcha-widget").each(function () {
            let widget = $(this);
            let widgetInfo = prepareWidgetInfo(widget[0].dataset);

            if (widgetInfo.reset) {
                getSolverButton(widgetInfo.captchaType, widgetInfo.widgetId).remove();
                widget.removeAttr("data-loaded");
                widget.removeAttr("data-reset");
            }

            if (widgetInfo.loaded) return;

            let processor = CaptchaProcessors.get(widgetInfo.captchaType);

            if (processor.canBeProcessed(widgetInfo, config)) {
                let button = createSolverButton(widgetInfo.captchaType, widgetInfo.widgetId, config);
                processor.attachButton(widgetInfo, config, button);
                widget[0].dataset.loaded = true;
            }
        });

    });
}, 2000);


/*
 * Background communication
 */
var background = chrome.runtime.connect({ name: "content" });

background.onMessage.addListener(function (msg) {

    if (msg.action == "solve") {
        if (msg.request.messageId) {
            return respondToWebPageMessage(msg);
        }

        let button = getSolverButton(msg.request.captchaType, msg.request.widgetId);

        if (msg.error === undefined) {
            changeSolverButtonState(button, "solved", chrome.i18n.getMessage("solved"));
            doActionsOnSuccess(msg);
        } else {
            changeSolverButtonState(button, "error", msg.error);
            tryAgain(button);
        }
    }

});

background.onDisconnect.addListener(function (port) {
    clearInterval(CAPTCHA_WIDGETS_LOOP);
});


function doActionsOnSuccess(msg) {
    let widget = getWidgetInfo(msg.request.captchaType, msg.request.widgetId);
    let processor = CaptchaProcessors.get(msg.request.captchaType);

    processor.onSolved(widget, msg.response.code);

    Config.getAll().then(config => {
        let callback = processor.getCallback(widget);

        if (callback) {
            let textarea = document.createElement('textarea');
            textarea.id = 'twocaptcha-callback-trigger';
            textarea.setAttribute('data-function', callback);
            textarea.value = msg.response.code;
            document.body.appendChild(textarea);
        }

        if (config.autoSubmitForms === true) {
            let timeout = parseInt(config.submitFormsDelay) * 1000;

            setTimeout(function () {
                if (!executeAutoSubmitRule(config.autoSubmitRules)) {
                    processor.getForm(widget).submit();
                }
            }, timeout);
        }
    });
}

function isBlockDomain(blackListDomain) {
    const domains = blackListDomain.split("\n") || [];
    for (let i = 0; i < domains.length; i++) {
        if (domains[i]) {
            const regExp = new RegExp(domains[i], 'i');
            if (regExp.test(location.href)) {
                return true;
            }
        }
    }

    return false;
}

function executeAutoSubmitRule(rules) {
    for (let i = 0; i < rules.length; i++) {
        let regExp = new RegExp(rules[i].url_pattern);
        if (regExp.test(location.href)) {
            let textarea = document.createElement('textarea');
            textarea.id = 'twocaptcha-autosubmit-code';
            textarea.value = rules[i].code;
            document.body.appendChild(textarea);
            return true;
        }
    }
    return false;
}

function executeAutoSubmitRuleCode(code) {

}

function tryAgain(button) {
    Config.getAll().then(config => {
        let countErrors = parseInt(button[0].dataset.countErrors || 0);

        if (config.repeatOnErrorTimes >= countErrors) {
            setTimeout(function () {
                button.click();
            }, config.repeatOnErrorDelay * 1000);
        }
    });
}

function attachProxyParams(params, config) {
    if (!config.useProxy) return;

    let proxy = config.proxy.trim();

    if (!proxy.length) return;

    params.proxy = {
        type: config.proxytype,
        uri: proxy,
    };
}


/*
 * Solver button
 */
function createSolverButton(captchaType, widgetId, config) {
    let button = $(`
        <div class="captcha-solver captcha-solver_${config.buttonPosition}" data-state="ready" data-captcha-type="${captchaType}" data-widget-id="${widgetId}">
            <div class="captcha-solver-image">
                <img src="${chrome.runtime.getURL("assets/images/icon_32.png")}">
            </div>
            <div class="captcha-solver-info">${chrome.i18n.getMessage("solveWith2Captcha")}</div>
        </div>
    `);

    button.click(function () {
        if (!["ready", "error"].includes(button.attr("data-state"))) return;

        if (button[0].dataset.countErrors && button[0].dataset.disposable) {
            return changeSolverButtonState(button, "error", "EXPIRED");
        }

        changeSolverButtonState(button, "solving", chrome.i18n.getMessage("solving"));

        let widget = getWidgetInfo(captchaType, widgetId);

        Config.getAll().then(function (config) {
            let params = CaptchaProcessors.get(captchaType).getParams(widget, config);
            attachProxyParams(params, config);

            background.postMessage({
                action: "solve",
                captchaType: captchaType,
                widgetId: widgetId,
                params: params,
            });
        });
    });

    return button;
}

function changeSolverButtonState(button, state, message) {
    button.attr("data-state", state)
    button.find(".captcha-solver-info").text(message);

    if (state === "error") {
        button[0].dataset.countErrors = parseInt(button[0].dataset.countErrors || 0) + 1
    }
}

function getSolverButton(captchaType, widgetId) {
    return $(".captcha-solver[data-captcha-type=" + captchaType + "][data-widget-id=" + widgetId + "]");
}

function getWidgetInfo(captchaType, widgetId) {
    let widget = $("head").find("captcha-widget[data-captcha-type=" + captchaType + "][data-widget-id=" + widgetId + "]");

    if (!widget.length) return null;

    return prepareWidgetInfo(widget[0].dataset);
}

function prepareWidgetInfo(dataset) {
    let w = {};

    for (let k in dataset) {
        w[k] = dataset[k];

        if (w[k] === "null") w[k] = null;
        if (w[k] === "false") w[k] = false;
        if (w[k] === "true") w[k] = true;
    }

    return w;
}


/*
 * Communication with web page
 */
let webPageMsgInterval = setInterval(function () {
    $("body > solver-ext-messages").children().each(function () {
        let msg = $(this)[0];

        if (!msg.dataset.received) {
            msg.dataset.received = true;

            if (msg.dataset.action === "getConfig") {
                Config.getAll().then(config => {
                    setWebPageMessageResponse(msg, config);
                }).catch(e => {
                    setWebPageMessageResponse(msg, { error: e.message });
                });
            } else if (msg.dataset.action === "solve") {
                let data = JSON.parse(decodeURIComponent(msg.dataset.data));

                background.postMessage({
                    action: "solve",
                    captchaType: data.captchaType,
                    widgetId: data.widgetId,
                    params: data.params,
                    messageId: msg.dataset.messageId,
                });
            } else if (msg.dataset.action === "getRecaptchaV3InterceptorInfo") {
                setWebPageMessageResponse(msg, {
                    extId: chrome.runtime.id,
                    i18n: {
                        solving: chrome.i18n.getMessage("solving"),
                        solved: chrome.i18n.getMessage("solved"),
                    }
                });
            } else {
                setWebPageMessageResponse(msg, { error: "unknown_action" });
            }
        }
    });
}, 200);

function respondToWebPageMessage(msg) {
    let message = $("body > solver-ext-messages > solver-ext-message[data-message-id=" + msg.request.messageId + "]");

    if (!message.length) return;

    if (msg.error) {
        setWebPageMessageResponse(message[0], { error: msg.error });
    } else {
        setWebPageMessageResponse(message[0], { response: msg.response.code });
    }
}

function setWebPageMessageResponse(message, response) {
    message.dataset.response = encodeURIComponent(JSON.stringify(response));
}


/*
 * ContextMenu helper
 */
let contextMenuEl = null;

document.addEventListener("contextmenu", function (event) {
    contextMenuEl = event.target;
}, true);

chrome.runtime.onMessage.addListener(function (request, sender, sendResponse) {
    if (request.command == "getContextMenuEl") {
        sendResponse({ xpath: getXPath(contextMenuEl) });
        if (request.element == 'input') {
            $('.twocaptcha-toast .close').click();
        } else if (request.showManual) {
            showToast(chrome.i18n.getMessage("normalManual"));
        }
    }
});

function showToast(message) {
    if (!$('body > .twocaptcha-toast-container').length) {
        $('body').append(`<div class="twocaptcha-toast-container"></div>`);
    }

    let toastEl = $(`
        <div class="twocaptcha-toast">
            <img src="${chrome.runtime.getURL("assets/images/logo.svg")}" class="twocaptcha-toast-logo">
            <button type="button" class="close">&times;</button>
            <div class="twocaptcha-toast-message">${message}</div>
        </div>
    `).appendTo('.twocaptcha-toast-container');

    setTimeout(function () {
        toastEl.addClass('visible');
    }, 50);

    toastEl.find('button.close').click(function (e) {
        toastEl.removeClass('visible');
        setTimeout(function () {
            toastEl.remove();
        }, 300);
    });
}

function getXPath(node) {
    var comp, comps = [];
    var parent = null;
    var xpath = '';
    var getPos = function (node) {
        var position = 1, curNode;
        if (node.nodeType == Node.ATTRIBUTE_NODE) {
            return null;
        }
        for (curNode = node.previousSibling; curNode; curNode = curNode.previousSibling) {
            if (curNode.nodeName == node.nodeName) {
                ++position;
            }
        }
        return position;
    }

    if (node instanceof Document) {
        return '/';
    }

    for (; node && !(node instanceof Document); node = node.nodeType == Node.ATTRIBUTE_NODE ? node.ownerElement : node.parentNode) {
        comp = comps[comps.length] = {};
        switch (node.nodeType) {
            case Node.TEXT_NODE:
                comp.name = 'text()';
                break;
            case Node.ATTRIBUTE_NODE:
                comp.name = '@' + node.nodeName;
                break;
            case Node.PROCESSING_INSTRUCTION_NODE:
                comp.name = 'processing-instruction()';
                break;
            case Node.COMMENT_NODE:
                comp.name = 'comment()';
                break;
            case Node.ELEMENT_NODE:
                comp.name = node.nodeName;
                break;
        }
        comp.position = getPos(node);
    }

    for (var i = comps.length - 1; i >= 0; i--) {
        comp = comps[i];
        xpath += '/' + comp.name;
        if (comp.position != null) {
            xpath += '[' + comp.position + ']';
        }
    }

    return xpath;

}