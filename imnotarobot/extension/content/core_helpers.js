let initCoreHelpers = function () {

    /*
     * Create widgets container
     */
    let widgetsList = document.querySelector('head > captcha-widgets');
    if ( ! widgetsList ) {
        widgetsList = document.createElement("captcha-widgets");
        document.head.appendChild(widgetsList);
    }

    /*
     * Widgets container helper
     */
    window.registerCaptchaWidget = function(widgetInfo) {
        let widget = document.createElement("captcha-widget");

        for (let k in widgetInfo) {
            widget.dataset[k] = widgetInfo[k];
        }

        widgetsList.appendChild(widget);
    };

    /*
     * Widgets container helper
     */
    window.isCaptchaWidgetRegistered = function(captchaType, widgetId) {
        let widgets = widgetsList.children;

        for (let i = 0; i < widgets.length; i++) {
            if (widgets[i].dataset.captchaType !== captchaType) continue;
            if (widgets[i].dataset.widgetId !== widgetId + '') continue;
            return true;
        }

        return false;
    };

    /*
     * Widgets container helper
     */
    window.resetCaptchaWidget = function(captchaType, widgetId) {
        let widgets = widgetsList.children;

        for (let i = 0; i < widgets.length; i++) {
            let wd = widgets[i].dataset;

            if (wd.captchaType != captchaType) continue;
            if (wd.widgetId != widgetId) continue;

            wd.reset = true; break;
        }
    };

    /*
     * Widgets container helper
     */
    window.getCaptchaWidgetButton = function(captchaType, widgetId) {
        return document.querySelector(".captcha-solver[data-captcha-type='" + captchaType + "'][data-widget-id='" + widgetId + "']");
    };

};

setTimeout(initCoreHelpers, 200);