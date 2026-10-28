CaptchaProcessors.register({

    captchaType: "hcaptcha",

    canBeProcessed: function(widget, config) {
        if (!config.enabledForHCaptcha) return false;

        if (!$("#" + widget.containerId).length) return false;

        if (!widget.sitekey) return false;

        return true;
    },

    attachButton: function(widget, config, button) {
        let container = $("#" + widget.containerId);

        button.css({
            width: container.find('iframe').outerWidth() + "px"
        });

        container.append(button);

        if (config.autoSolveHCaptcha) button.click();
    },

    getParams: function(widget, config) {
        return {
            url: location.href,
            sitekey: widget.sitekey,
        };
    },

    onSolved: function(widget, answer) {
        let container = $("#" + widget.containerId);

        container.find("textarea").val(answer);
        container.find("iframe").attr("data-hcaptcha-response", answer);
    },

    getForm: function(widget) {
        return $("#" + widget.containerId).closest("form");
    },

    getCallback: function(widget) {
        return widget.callback;
    },

});