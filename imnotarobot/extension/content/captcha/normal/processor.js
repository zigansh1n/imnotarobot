CaptchaProcessors.register({

    captchaType: "normal",

    canBeProcessed: function(widget, config) {
        if (!config.enabledForNormal) return false;

        return true;
    },

    attachButton: function(widget, config, button) {
        $("#" + widget.inputId).after(button);
        if (config.autoSolveNormal) button.click();
    },

    getParams: function(widget, config) {
        return {
            body: widget.base64,
        };
    },

    onSolved: function(widget, answer) {
        let input = document.getElementById(widget.inputId);

        input.value = answer;

        input.dispatchEvent(new Event('input', {
            bubbles: true,
            data: answer,
        }));
    },

    getForm: function(widget) {
        return $("#" + widget.inputId).closest("form");
    },

    getCallback: function(widget) {
        return null;
    },

});