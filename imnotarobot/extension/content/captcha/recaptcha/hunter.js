(() => {

    setInterval(function () {
        if (window.___grecaptcha_cfg === undefined) return;
        if (___grecaptcha_cfg.clients === undefined) return;

        for (let widgetId in ___grecaptcha_cfg.clients) {
            let widget = ___grecaptcha_cfg.clients[widgetId];

            if (isCaptchaWidgetRegistered("recaptcha", widget.id)) continue;

            let widgetInfo = getRecaptchaWidgetInfo(widget);

            registerCaptchaWidget(widgetInfo);
        }
    }, 2000);

    let getRecaptchaWidgetInfo = function (widget) {
        let info = {
            captchaType: "recaptcha",
            widgetId: widget.id,
            version: "v2",
            sitekey: null,
            action: null,
            s: null,
            callback: null,
            enterprise: grecaptcha.enterprise ? true : false,
            containerId: null,
            bindedButtonId: null,
        };

        /*
         * Check if is badge
         */
        let isBadge = false;

        mainLoop: for (let k1 in widget) {
            if (typeof widget[k1] !== "object") continue;

            for (let k2 in widget[k1]) {
                if (widget[k1][k2] && widget[k1][k2].classList && widget[k1][k2].classList.contains("grecaptcha-badge")) {
                    isBadge = true;
                    break mainLoop;
                }
            }
        }


        /*
         * 1. Look for version
         */
        if (isBadge) {
            info.version = "v3";

            for (let k1 in widget) {
                let obj = widget[k1];

                if (typeof obj !== "object") continue;

                for (let k2 in obj) {
                    if (typeof obj[k2] !== "string") continue;
                    if (obj[k2] == "fullscreen") info.version = "v2_invisible";
                }
            }
        }

        /*
         * 2. Look for containerId
         */
        let n1;
        for (let k in widget) {
            if (widget[k] && widget[k].nodeType) {
                if (widget[k].id) {
                    info.containerId = widget[k].id;
                } else if (widget[k].dataset.sitekey) {
                    widget[k].id = "recaptcha-container-" + Date.now();
                    info.containerId = widget[k].id;
                } else if (info.version == 'v2') {
                    if (!n1) {
                        n1 = widget[k];
                        continue;
                    }

                    if (widget[k].isSameNode(n1)) {
                        widget[k].id = "recaptcha-container-" + Date.now();
                        info.containerId = widget[k].id;
                        break;
                    }
                }
            }
        }

        /*
         * 3. Look for sitekey, action, s and callback
         */
        for (let k1 in widget) {
            let obj = widget[k1];

            if (typeof obj !== "object") continue;

            for (let k2 in obj) {
                if (obj[k2] === null) continue;
                if (typeof obj[k2] !== "object") continue;
                if (obj[k2].sitekey === undefined) continue;
                if (obj[k2].action === undefined) continue;

                for (let k3 in obj[k2]) {
                    if (k3 === "sitekey") info.sitekey = obj[k2][k3];
                    if (k3 === "action") info.action = obj[k2][k3];
                    if (k3 === "s") info.s = obj[k2][k3];
                    if (k3 === "callback") info.callback = obj[k2][k3];
                    if (k3 === "bind" && obj[k2][k3]) {
                        if (typeof obj[k2][k3] === "string") {
                            info.bindedButtonId = obj[k2][k3];
                        } else {
                            let button = obj[k2][k3];
                            if (button.id === undefined) {
                                button.id = "recaptchaBindedElement" + widget.id;
                            }
                            info.bindedButtonId = button.id;
                        }
                    }
                }
            }
        }

        /*
         * 4. Prepare callback
         */
        if (typeof info.callback === "function") {
            let callbackKey = "reCaptchaWidgetCallback" + widget.id;
            window[callbackKey] = info.callback;
            info.callback = callbackKey;
        }

        return info;
    };

})()