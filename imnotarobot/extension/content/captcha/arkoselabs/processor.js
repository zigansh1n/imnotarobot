CaptchaProcessors.register({

    captchaType: "arkoselabs",

    canBeProcessed: function(widget, config) {
        if (!config.enabledForArkoselabs) return false;

        if (!widget.pkey) return false;

        return true;
    },

    attachButton: function(widget, config, button) {
        let input = $("#" + widget.inputId);

        input.after(button);

        if (config.autoSolveArkoselabs) button.click();
    },

    getOriginUrl: function() {
        const href = document.location.href;
        const referrer = document.referrer;
        // we in iframe?
        if(window.parent != window) {
            return referrer;
        } else {
            return href;
        }
    },

    getParams: function(widget, config) {
        let params = {
            pageurl: this.getOriginUrl(),
            publickey: widget.pkey,
        };

        if (widget.surl) {
            params.surl = widget.surl;
        }

        if (widget.data) {
            params.data = JSON.parse(decodeURIComponent(widget.data));
        }

        return params;
    },

    onSolved: function(widget, answer) {
        $("#" + widget.inputId).val(answer);
    },

    getForm: function(widget) {
        return $("#" + widget.containerId).closest("form");
    },

    getCallback: function(widget) {
        return widget.callback;
    },

});