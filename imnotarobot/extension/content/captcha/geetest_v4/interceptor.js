(() => {
    let originalFunc;

    const geetestIsLoad = function (funcName) {
        let isLoad = false;
        const tags = {js: 'script', css: 'link'};
        const tagName = tags[funcName.split('.').pop()];
        if (tagName !== undefined) {
            let elements = document.getElementsByTagName(tagName);
            for (let i in elements) {
                if ((elements[i].href && elements[i].href.toString().indexOf(funcName) > 0)
                    || (elements[i].src && elements[i].src.toString().indexOf(funcName) > 0)) {
                    isLoad = true;
                }
            }
        }
        return isLoad;
    };

    let timer = setInterval(() => {
        originalFunc = window.initGeetest4;

        // wait for load gt script on SPA/SSR
        if (geetestIsLoad('gt4.js')) {
            Object.defineProperty(window, "initGeetest4", {
                get: function () {
                    return interceptorFunc;
                },
                set: function (e) {
                    originalFunc = e;
                }, configurable: true
            });

            clearInterval(timer);
        }
    }, 1);

    let interceptorFunc = function (params, callback) {
        const getCaptchaId = function () {
            if (params && params.captchaId) {
                return params.captchaId
            }

            const scripts = document.querySelectorAll("script");
            let src = findScript(scripts);
            const url = new URL(src);
            return url.searchParams.get("captcha_id");
        };

        const initHelper = function () {
            const captchaId = getCaptchaId();

            registerCaptchaWidget({
                captchaType: "geetest_v4",
                widgetId: captchaId,
                captchaId: captchaId
            });
        };

        const findScript = function (scripts) {
            const scriptUrl = "//gcaptcha4.geetest.com/load";

            for (let i = 0; i < scripts.length; i++) {
                const src = scripts[i].getAttribute("src");
                if (typeof src === "string" && src.indexOf(scriptUrl) > 0) {
                    return src;
                }
            }

            return null;
        }

        const getValidate = function () {
            return {
                captcha_id: document.querySelector("input[name=captcha_id]").value,
                lot_number: document.querySelector("input[name=lot_number]").value,
                pass_token: document.querySelector("input[name=pass_token]").value,
                gen_time: document.querySelector("input[name=gen_time]").value,
                captcha_output: document.querySelector("input[name=captcha_output]").value,
            };
        }

        let captchaObjEvents = {};
        originalFunc(params, (captchaObj) => {
            let captchaObjProxy = new Proxy(captchaObj, {
                get: function (target, prop) {
                    switch (prop) {
                        case 'onReady':
                        case 'appendTo':
                            initHelper();
                            return target[prop];
                        case 'getValidate':
                            const captcha_id = document.querySelector("input[name=captcha_id]").value;
                            if (captcha_id) {
                                return getValidate;
                            }
                            return target[prop];
                        case 'onSuccess':
                            return function (e) {
                                captchaObjEvents.onSuccess = e;
                            }
                        case 'onError':
                            return function (e) {
                                captchaObjEvents.onError = e;
                            }
                        case 'onClose':
                            return function (e) {
                                captchaObjEvents.onClose = e;
                            }
                        default:
                            return target[prop];
                    }
                }
            });

            window.captchaObjV4 = captchaObjProxy;
            window.captchaObjEventsV4 = captchaObjEvents;

            callback(captchaObjProxy);
        });
    }
})()
