CaptchaProcessors.register({

    captchaType: "geetest_v4",

    canBeProcessed: function(widget, config) {
        if (!config.enabledForGeetest_v4) return false;

        return true;
    },

    attachButton: function(widget, config, button) {
        let helper = this.getHelper(widget);
        if (helper.find('.captcha-solver').length !== 0) {
            return;
        }

        button.css({
            width: helper.outerWidth() + "px"
        });

        button[0].dataset.disposable = true;

        helper.append(button);

        if (config.autoSolveGeetest_v4) button.click();
    },

    getParams: function(widget, config) {
        return {
            url: location.href,
            captchaId: widget.captchaId
        };
    },

    onSolved: function(widget, answer) {
        let helper = this.getHelper(widget);

        helper.find("input[name=captcha_id]").val(answer.captcha_id);
        helper.find("input[name=lot_number]").val(answer.lot_number);
        helper.find("input[name=pass_token]").val(answer.pass_token);
        helper.find("input[name=gen_time]").val(answer.gen_time);
        helper.find("input[name=captcha_output]").val(answer.captcha_output);

        let script = document.createElement("script");
        script.src = chrome.runtime.getURL("content/captcha/geetest_v4/validate.js");
        document.body.append(script);
    },

    getForm: function(widget) {
        return this.getHelper(widget).closest("form");
    },

    getCallback: function(widget) {
        return null;
    },

    getHelper: function(widget) {
        let container = $(".geetest_captcha")

        let helper = container.find(".twocaptcha-geetest_v4-helper");

        if (!helper.length) {
            helper = $(`
                <div class="twocaptcha-geetest_v4-helper">
                    <input type="hidden" name="captcha_id">
                    <input type="hidden" name="lot_number">
                    <input type="hidden" name="pass_token">
                    <input type="hidden" name="gen_time">
                    <input type="hidden" name="captcha_output">
                </div>
            `).appendTo(container);
        }

        return helper;
    },

});
