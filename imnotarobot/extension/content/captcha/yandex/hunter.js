(() => {

    setInterval(function () {
        let input = document.querySelector("input[name='smart-token']");
        if (!input) return;

        if (isCaptchaWidgetRegistered("yandex", 0)) return;

        getYandexWidgetInfo(input);
    }, 2000);

    const getYandexData = function () {
        let src;
        const iframes = document.querySelectorAll('iframe');
        if (iframes) {
             iframes.forEach(function (iframe) {
                const s = iframe.getAttribute('src');
                if (s.indexOf('captcha-api.yandex.ru') !== -1) {
                    src = s;
                }
            });
        }

        if (src) {
            const url = new URL(src);
            return url.searchParams.get("sitekey");
        }

        return null;
    };

    const getYandexWidgetInfo = function (input) {
        const sitekey = getYandexData();

        if (sitekey) {
            if (!input.id) {
                input.id = "yandex-input-" + sitekey;
            }

            registerCaptchaWidget({
                captchaType: "yandex",
                widgetId: sitekey,
                sitekey: sitekey,
                inputId: input.id,
            });
        }
    };
})()