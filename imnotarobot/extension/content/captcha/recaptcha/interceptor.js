(() => {

    let recaptchaInstance;

    Object.defineProperty(window, "grecaptcha", {
        configurable: true,
        get: function () {
            return recaptchaInstance;
        },
        set: function (e) {
            recaptchaInstance = e;
            manageRecaptchaObj(e);
            manageEnterpriseObj(e);
        },
    });

    let manageRecaptchaObj = function (obj) {
        if (window.___grecaptcha_cfg === undefined) return;
        let originalExecuteFunc;
        let originalResetFunc;

        if (obj.execute) originalExecuteFunc = obj.execute;
        if (obj.reset) originalResetFunc = obj.reset;

        Object.defineProperty(obj, "execute", {
            configurable: true,
            get: function () {
                return async function (sitekey, options) {
                    if (!options) {
                        if (!isInvisible()) {
                            return await originalExecuteFunc(sitekey, options);
                        }
                    }

                    let config = await sendMsgToSolverCS("getConfig");

                    if (!config.enabledForRecaptchaV3) {
                        return await originalExecuteFunc(sitekey, options);
                    }

                    if (isBlacklisted(window.location.href, config)) {
                        return await originalExecuteFunc(sitekey, options);
                    }

                    let widgetId = addWidgetInfo(sitekey, options);

                    return await waitForResult(widgetId);
                };
            },
            set: function (e) {
                originalExecuteFunc = e;
            },
        });

        Object.defineProperty(obj, "reset", {
            configurable: true,
            get: function () {
                return function (widgetId) {
                    if (widgetId === undefined) {
                        let ids = Object.keys(___grecaptcha_cfg.clients)[0];
                        widgetId = ids.length ? ids[0] : 0;
                    }

                    resetCaptchaWidget("recaptcha", widgetId);

                    return originalResetFunc(widgetId);
                };
            },
            set: function (e) {
                originalResetFunc = e;
            },
        });
    };

    let manageEnterpriseObj = function (obj) {
        if (window.___grecaptcha_cfg === undefined) return;
        let originalEnterpriseObj;

        Object.defineProperty(obj, "enterprise", {
            configurable: true,
            get: function () {
                return originalEnterpriseObj;
            },
            set: function (ent) {
                originalEnterpriseObj = ent;

                let originalExecuteFunc;
                let originalResetFunc;

                Object.defineProperty(ent, "execute", {
                    configurable: true,
                    get: function () {
                        return async function (sitekey, options) {
                            if (!options) {
                                if (!isInvisible()) {
                                    return await originalExecuteFunc(sitekey, options);
                                }
                            }

                            let config = await sendMsgToSolverCS("getConfig");

                            if (!config.enabledForRecaptchaV3) {
                                return await originalExecuteFunc(sitekey, options);
                            }

                            if (isBlacklisted(window.location.href, config)) {
                                return await originalExecuteFunc(sitekey, options);
                            }

                            let widgetId = addWidgetInfo(sitekey, options, "1");

                            return await waitForResult(widgetId);
                        };
                    },
                    set: function (e) {
                        originalExecuteFunc = e;
                    },
                });

                Object.defineProperty(ent, "reset", {
                    configurable: true,
                    get: function () {
                        return function (widgetId) {
                            if (widgetId === undefined) {
                                let ids = Object.keys(___grecaptcha_cfg.clients)[0];
                                widgetId = ids.length ? ids[0] : 0;
                            }

                            resetCaptchaWidget("recaptcha", widgetId);

                            return originalResetFunc(widgetId);
                        };
                    },
                    set: function (e) {
                        originalResetFunc = e;
                    },
                });
            },
        });
    };

    let addWidgetInfo = function (sitekey, options, enterprise) {
        let widgetId = parseInt(Date.now() / 1000);

        let badge = document.querySelector(".grecaptcha-badge");
        if (!badge.id) badge.id = "recaptcha-badge-" + widgetId;

        let callback = "rv3ExecCallback" + widgetId;
        window[callback] = function (response) {
            getCaptchaWidgetButton("recaptcha", widgetId).dataset.response = response;
        };

        let widgetInfo = {
            captchaType: "recaptcha",
            widgetId: widgetId,
            version: "v3",
            sitekey: sitekey,
            action: options ? options.action : '',
            s: null,
            enterprise: enterprise ? true : false,
            callback: callback,
            containerId: badge.id,
        };

        registerCaptchaWidget(widgetInfo);

        return widgetId;
    };

    let waitForResult = function (widgetId) {
        return new Promise(function (resolve, reject) {
            let interval = setInterval(function () {
                let button = getCaptchaWidgetButton("recaptcha", widgetId);

                if (button && button.dataset.response) {
                    resolve(button.dataset.response);
                    clearInterval(interval);
                }
            }, 500);
        });
    };

    let isInvisible = function () {
        let widgets = document.querySelectorAll('head captcha-widget');

        for (let i = 0; i < widgets.length; i++) {
            if (widgets[i].dataset.version == 'v2_invisible') {
                let badge = document.querySelector('.grecaptcha-badge');
                badge.id = "recaptcha-badge-" + widgets[i].dataset.widgetId;
                widgets[i].dataset.containerId = badge.id;
                return true;
            }
        }

        return false;
    };

    let isBlacklisted = function (url, config) {
        let m = config.blackListDomain.split('\n').filter(function (entry) {
            return url.includes(entry);
        });
        return m.length > 0;
    };

})()