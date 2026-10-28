(() => {

    let leminInstance;
    let leminInstanceProxy;

    Object.defineProperty(window, "leminCroppedCaptcha", {
        get: function () {
            return interceptorFunc();
        },
        set: function (f) {
            leminInstance = f;
        },
    });

    let interceptorFunc = function () {
        const getLeminData = function () {
            const scripts = document.querySelectorAll("script");
            let src = findScript(scripts);
            const url = new URL(src);
            const captchaId = src.match('(CROPPED_.*)/')[1];
            const domain = src.match('(http.*)captcha/v1/cropped/')[1];
            const divId = document.querySelector('.lemin-captcha-input-box').parentElement.getAttribute('id');

            return [captchaId, domain, divId];
        };

        const initHelper = function () {
            const [captchaId, apiServer, divId] = getLeminData();

            registerCaptchaWidget({
                captchaType: "lemin",
                widgetId: captchaId,
                captchaId: captchaId,
                apiServer: apiServer,
                divId: divId
            });
        };

        const findScript = function (scripts) {
            const scriptUrl = "/captcha/v1/cropped/CROPPED_";

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
                answer: document.querySelector("input[name=lemin_answer]").value,
                challenge_id: document.querySelector("input[name=lemin_challenge_id]").value,
            }
        }

        if (leminInstance) {
            leminInstanceProxy = new Proxy(leminInstance, {
                get: function (target, prop) {
                    return new Proxy(target[prop], {
                        apply: (target, thisArg, argumentsList) => {
                            const obj = Reflect.apply(target, thisArg, argumentsList);
                            if (obj && obj.getCaptchaValue) {
                                obj.getCaptchaValue = getValidate;
                            }
                            return obj;
                        }
                    });
                }
            });

            initHelper();
        }

        return leminInstanceProxy;
    }
})()
