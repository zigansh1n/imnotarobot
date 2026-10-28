(() => {

    setInterval(function () {
        let div = document.getElementById("div_for_keycaptcha");

        if (!div) return;

        if (isCaptchaWidgetRegistered("keycaptcha", 0)) return;

        let widgetInfo = getKeyCaptchaWidgetInfo(div);

        registerCaptchaWidget(widgetInfo);
    }, 2000);

    let getKeyCaptchaWidgetInfo = function(div) {
        let info = {
            captchaType: "keycaptcha",
            widgetId: 0,
            containerId: div.id,
        };

        let scripts = document.querySelectorAll("script");

        for (let i = 0; i < scripts.length; i++) {
            let code = scripts[i].textContent;

            if (code.indexOf("s_s_c_user_id") !== -1) {
                eval(code);

                info.userId = s_s_c_user_id;
                info.sessionId = s_s_c_session_id;
                info.webServerSign = s_s_c_web_server_sign;
                info.webServerSign2 = s_s_c_web_server_sign2;
                info.captchaFieldId = s_s_c_captcha_field_id;
                info.submitButtonId = s_s_c_submit_button_id;

                break;
            }
        }

        return info;
    };

})()