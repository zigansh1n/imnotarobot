(() => {

    setInterval(function () {
        if (window.registerCaptchaWidget) {
            if (isCaptchaWidgetRegistered("hcaptcha", 0)) return;

            let textarea = document.querySelector("textarea[name=h-captcha-response]");

            if (!textarea) return;

            let container = textarea.parentNode;

            if (!container.id) {
                container.id = "hcaptcha-container-" + Date.now();
            }

            registerCaptchaWidget({
                captchaType: "hcaptcha",
                widgetId: 0,
                containerId: container.id,
                sitekey: container.dataset.sitekey || null,
                callback: container.dataset.callback || null,
            });
        }
    }, 2000);

})()
