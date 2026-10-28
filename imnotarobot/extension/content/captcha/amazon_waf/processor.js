CaptchaProcessors.register({

    captchaType: "amazon_waf",

    canBeProcessed: function(widget, config) {
        if (!config.enabledForAmazonWaf) return false;

        if (!widget.sitekey) return false;

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

        if (config.autoSolveAmazonWaf) button.click();
    },

    getParams: function(widget, config) {
        let params = {
            url: location.href,
            sitekey : widget.sitekey,
            context : widget.context,
            iv : widget.iv,
        };

        return params;
    },

    onSolved: function(widget, answer) {
        let helper = this.getHelper(widget);
        const challenge = helper.find("challenge.input")
        if (challenge.length) {
            challenge.val(answer.captcha_voucher);
        }

        if (!helper.find('.twocaptcha-amazon_waf-helper').length) {
            $(`
                <div class="twocaptcha-amazon_waf-helper">                  
                    <input type="hidden" name="amazon_waf_captcha_voucher">                   
                    <input type="hidden" name="amazon_waf_existing_token">                   
                </div>
            `).appendTo(helper);
        }

        helper.find("input[name=amazon_waf_captcha_voucher]").val(answer.captcha_voucher);
        helper.find("input[name=amazon_waf_existing_token]").val(answer.existing_token);

        let script = document.createElement("script");
        script.src = chrome.runtime.getURL("content/captcha/amazon_waf/validate.js");
        document.body.append(script);
    },

    getForm: function(widget) {
        return this.getHelper(widget).closest("form");
    },

    getCallback: function(widget) {
        return null;
    },

    getHelper: function(widget) {
        let container = $("#" + widget.inputId);
        return container.parent();
    },

});
