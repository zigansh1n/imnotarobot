(() => {
    let originalFunc;

    Object.defineProperty(window, "initGeetest", {
        get: function () {
            return initGeetestHandler;
        },
        set: function (f) {
            originalFunc = f;
        }, configurable: true
    });

    let initGeetestHandler = function (params, callback) {
        setTimeout(function () {
            interceptorFunc(params, callback);
        }, 200);
    };

    let interceptorFunc = function (params, callback) {
        let getSelectorId = function (selector) {
            let id;

            /* selector is jQuery object */
            if (typeof selector === "object" && selector[0] !== undefined) {
                selector = selector[0];
            }

            if (typeof selector === "object" && typeof selector.appendChild !== "undefined") {
                if (selector.id) {
                    id = "#" + selector.id
                } else {
                    let n = document.createElement(selector.tagName);
                    n.id = "antcpt" + Math.round(Math.random() * 1e3);
                    selector.appendChild(n);
                    id = "#" + n.id
                }
            } else if (typeof selector === "string") {
                id = selector
            }

            if (id && id[0] === "#") {
                id = id.substr(1);
            }

            return id;
        };

        let initHelper = function () {
            registerCaptchaWidget({
                captchaType: "geetest",
                widgetId: 0,
                containerId: params.appendToSelector,
                gt: params.gt,
                challenge: params.challenge,
                apiServer: params.api_server || null,
            });
        };

        let captchaObjEvents = {};

        function findSelector(e) {
            try {
                return document.querySelector(e)
            } catch (t) {
                if (typeof CSS.escape === "function") {
                    return document.querySelector(CSS.escape(e))
                }
            }
        }

        function appendToSelector(e) {
            if (e && typeof document.querySelector === "function") {
                const selector = findSelector(e);
                if (selector) {
                    const obj = selector.getElementsByClassName("geetest_holder");
                    if (obj && obj.length) {
                        Array.from(obj).forEach((e => e.parentElement.removeChild(e)))
                    }
                }
            }
        }

        let captchaObj = {
            appendTo: function (e) {
                if (params.product !== "bind") {
                    const selectorId = getSelectorId(e);
                    params.appendToSelector = selectorId;
                    initHelper();
                    setTimeout((function () {
                        if (typeof captchaObjEvents.onReady === "function") {
                            captchaObjEvents.onReady(e)
                        }
                    }), 100)
                }
                return this
            },
            bindForm: function (e) {
                const selectorId = getSelectorId(e);
                params.appendToSelector = selectorId;
                initHelper();
            },
            onReady: function (e) {
                captchaObjEvents.onReady = e;
                if (params.product === "bind") {
                    if (typeof captchaObjEvents.onReady === "function") {
                        captchaObjEvents.onReady(e);
                    }
                }
                return this
            },
            onSuccess: function (e) {
                captchaObjEvents.onSuccessCallback = e;
                return this
            },
            onError: function (e) {
                captchaObjEvents.onError = e;
                return this
            },
            onClose: function (e) {
                captchaObjEvents.onClose = e;
                return this
            },
            getValidate: function () {
                return {
                    geetest_challenge: null,
                    geetest_validate: null,
                    geetest_seccode: null,
                };
            },
            destroy: function () {
                appendToSelector(params.appendToSelector)
            },
            verify: function () {
                const selectorId = getSelectorId(document.querySelector('#captchaBox') || document.forms[0]);
                params.appendToSelector = selectorId;
                initHelper();
            }
        };

        let captchaObjProxy = new Proxy(captchaObj, {
            get: function (target, prop) {
                if (prop in target) {
                    return target[prop];
                } else {
                    return function () {
                    };
                }
            },
        });

        if (typeof callback === "function") {
            callback(captchaObjProxy)
        }

        window.captchaObj = captchaObj;
        window.captchaObjEvents = captchaObjEvents;
    };

})()