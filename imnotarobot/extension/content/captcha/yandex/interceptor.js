(() => {

    let yandexFunc;
    let yandexFuncProxy;

    Object.defineProperty(window, "smartCaptcha", {
        get: function () {
            return initYandexHandler;
        },
        set: function (f) {
            yandexFunc = f;
        }, configurable: true
    });


    const initYandexHandler = function () {
        setTimeout(function () {
            interceptorFunc();
        }, 200);
    };

    const interceptorFunc = function () {
        const initCaptcha = (arguments) => {
            registerCaptchaWidget({
                captchaType: "yandex",
                widgetId: arguments.sitekey,
                sitekey: arguments.sitekey,
                inputId: input.id,
            });
        }

        if (yandexFuncProxy) {
            yandexFuncProxy = new Proxy(yandexFunc, {
                get: function (target, prop) {
                    return new Proxy(target[prop], {
                        apply: (target, thisArg, argumentsList) => {
                            initCaptcha(argumentsList);
                            const obj = Reflect.apply(target, thisArg, argumentsList);
                            return obj;
                        }
                    });
                }
            });
        }
    }
})()
