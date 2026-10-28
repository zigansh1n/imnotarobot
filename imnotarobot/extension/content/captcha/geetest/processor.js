CaptchaProcessors.register({

    captchaType: "geetest",

    canBeProcessed: function(widget, config) {
        if (!config.enabledForGeetest) return false;

        if (!$("#" + widget.containerId).length) return false;

        return true;
    },

    attachButton: function(widget, config, button) {
        let helper = this.getHelper(widget);

        // already init?
        if (helper.find('.captcha-solver').length !== 0) {
            return;
        }

        button.css({
            width: helper.outerWidth() + "px"
        });

        button[0].dataset.disposable = true;

        helper.append(button);

        if (config.autoSolveGeetest) button.click();
    },

    getParams: function(widget, config) {
        let params = {
            url: location.href,
            gt: widget.gt,
            challenge: widget.challenge,
        };

        if (widget.apiServer) {
            params.apiServer = widget.apiServer;
        }

        return params;
    },

    onSolved: function(widget, answer) {
        let helper = this.getHelper(widget);

        helper.find("input[name=geetest_challenge]").val(answer.geetest_challenge);
        helper.find("input[name=geetest_validate]").val(answer.geetest_validate);
        helper.find("input[name=geetest_seccode]").val(answer.geetest_seccode);

        let script = document.createElement("script");
        script.src = chrome.runtime.getURL("content/captcha/geetest/validate.js");
        document.body.append(script);
    },

    getForm: function(widget) {
        return this.getHelper(widget).closest("form");
    },

    getCallback: function(widget) {
        return null;
    },

    getHelper: function(widget) {
        let container = $("#" + widget.containerId);

        let helper = container.find(".twocaptcha-geetest-helper");

        if (!helper.length) {
            helper = $(`
                <div class="twocaptcha-geetest-helper">
                    <div class="geetest_form">
                        <input type="hidden" name="geetest_challenge">
                        <input type="hidden" name="geetest_validate">
                        <input type="hidden" name="geetest_seccode">
                    </div>
                </div>
            `).appendTo(container);
        }

        return helper;
    },

});