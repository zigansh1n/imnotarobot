CaptchaProcessors.register({

    captchaType: "recaptcha",

    canBeProcessed: function(widget, config) {
        if (widget.version === "v2" && !config.enabledForRecaptchaV2) return false;
        if (widget.version === "v2_invisible" && !config.enabledForInvisibleRecaptchaV2) return false;
        if (widget.version === "v3" && !config.enabledForRecaptchaV3) return false;

        let binded = this.getBindedElements(widget);

        return !(!binded.button && !binded.textarea);
    },

    attachButton: function(widget, config, button) {
        let binded = this.getBindedElements(widget);

        if (binded.textarea) {
            binded.textarea.parent().css({height: "auto"})
            if (widget.version == "v2" || widget.version == "v2_invisible") {
                binded.textarea.parent().after(button);
            } else {
                const formBinded = $('form');
                if (formBinded.length) {
                    $('form').after(button);
                } else {
                    binded.textarea.after(button);
                }
            }
        } else {
            binded.button.after(button);
        }

        if (
            (widget.version == "v2" && config.autoSolveRecaptchaV2) ||
            (widget.version == "v2_invisible" && config.autoSolveInvisibleRecaptchaV2) ||
            (widget.version == "v3" && config.autoSolveRecaptchaV3)
        ) {
            button.click();
        }
    },

    onSolved: function(widget, answer) {
        let textarea = this.getBindedElements(widget).textarea;

        if (!textarea) {
            textarea = this.getForm(widget).find("textarea[name=g-recaptcha-response]");
        }

        textarea.val(answer);
    },

    getForm: function(widget) {
        let binded = this.getBindedElements(widget);

        if (binded.textarea) {
            return binded.textarea.closest("form");
        }

        return binded.button.closest("form");
    },

    getCallback: function(widget) {
        return widget.callback;
    },

    getParams: function(widget, config) {
        let params = {
            sitekey: widget.sitekey,
            url: location.href,
        };

        if (widget.version === "v2_invisible") {
            params.invisible = 1;
        }

        if (widget.version === "v3") {
            params.version = "v3";
            params.score = config.recaptchaV3MinScore;
        }

        if (widget.action) {
            params.action = widget.action;
        }

        if (widget.s) {
            params["data-s"] = widget.s;
        }

        if (widget.enterprise) {
            params.enterprise = 1;
        }

        return params;
    },

    getBindedElements: function(widget) {
        let elements = {
            button: null,
            textarea: null,
        };

        if (widget.bindedButtonId) {
            let button = $("#" + widget.bindedButtonId);
            if (button.length) elements.button = button;
        } else {
            let textarea = $("#" + widget.containerId + " textarea[name=g-recaptcha-response]");
            if (textarea.length) elements.textarea = textarea;
        }

        return elements;
    },

});