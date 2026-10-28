(() => {

    setInterval(function () {
        let input = document.getElementById("cf-turnstile");
        if (!input) return;

        if (isCaptchaWidgetRegistered("turnstile", 0)) return;

        getTurnstileWidgetInfo(input);
    }, 2000);

    const getTurnstileData = function () {
        const cf = document.getElementById('cf-turnstile');
        if (cf) {
            return cf.getAttribute('data-sitekey');
        }

        return null;
    };

    const getTurnstileWidgetInfo = function (input) {
        const sitekey = getTurnstileData();

        if (sitekey) {
            if (!input.id) {
                input.id = "turnstile-input-" + sitekey;
            }

            registerCaptchaWidget({
                captchaType: "turnstile",
                widgetId: sitekey,
                sitekey: sitekey,
                inputId: input.id,
            });
        }
    };
})()