CaptchaProcessors.register({

    captchaType: "lemin",

    canBeProcessed: function (widget, config) {
        if (!config.enabledForLemin) return false;

        return true;
    },

    attachButton: function (widget, config, button) {
        let helper = this.getHelper(widget);
        if (helper.find('.captcha-solver').length !== 0) {
            return;
        }

        button.css({
            width: helper.outerWidth() + "px"
        });

        button[0].dataset.disposable = true;

        helper.append(button);

        if (config.autoSolveLemin) button.click();
    },

    getParams: function (widget, config) {
        let params = {
            url: location.href,
            captchaId: widget.captchaId,
            divId: widget.divId
        };

        if (widget.apiServer) {
            params.apiServer = widget.apiServer;
        }

        return params;
    },

    onSolved: function (widget, { challenge_id, answer }) {
        let helper = this.getHelper(widget);
        helper.find("input[name=lemin_challenge_id]").val(challenge_id);
        helper.find("input[name=lemin_answer]").val(answer);
    },

    getForm: function (widget) {
        return this.getHelper(widget).closest("form");
    },

    getCallback: function (widget) {
        return null;
    },

    getHelper: function (widget) {
        let container = $("#" + widget.divId);
        return container.parent();
    },

});
