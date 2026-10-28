CaptchaProcessors.register({

    captchaType: "keycaptcha",

    div: null,
    params: null,

    canBeProcessed: function(widget, config) {
        if (!config.enabledForKeycaptcha) return false;

        if (!widget.captchaFieldId) return false;

        return true;
    },

    attachButton: function(widget, config, button) {
        $("#" + widget.containerId).after(button);
        if (config.autoSolveKeycaptcha) button.click();
    },

    getParams: function(widget, config) {
        return {
            url: location.href,
            s_s_c_user_id: widget.userId,
            s_s_c_session_id: widget.sessionId,
            s_s_c_web_server_sign: widget.webServerSign,
            s_s_c_web_server_sign2: widget.webServerSign2,
        };
    },

    onSolved: function(widget, answer) {
        $("#" + widget.containerId).remove();
        this.removeScript();
        $("#" + widget.captchaFieldId).val(answer);
    },

    getForm: function(widget) {
          return $("#" + widget.captchaFieldId).closest("form");
    },

    getCallback: function(widget) {
        return null;
    },

    removeScript: function() {
        let scriptUrl = "//backs.keycaptcha.com/swfs/cap.js";

        let scripts = $("body").find("script");

        for (let i = 0; i < scripts.length; i++) {
            let src = scripts.eq(i).attr("src");

            if (typeof src == "string" && src.indexOf(scriptUrl)) {
                scripts.eq(i).remove();
                break;
            }
        }
    }

});