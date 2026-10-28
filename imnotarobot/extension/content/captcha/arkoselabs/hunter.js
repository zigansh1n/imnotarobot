(() => {

    setInterval(function () {
        fixHeightIframe();

        let input = document.querySelector("input[name='fc-token']");

        if (!input) return;

        if (!window.arkoselabs_callback_dse7f73ek) return;

        if (isCaptchaWidgetRegistered("arkoselabs", 0)) return;

        let widgetInfo = getArkoselabsWidgetInfo(input);

        registerCaptchaWidget(widgetInfo);
    }, 2000);

    let fixHeightIframe = function () {
        const iframes = document.querySelectorAll('iframe');
        if (iframes) {
            iframes.forEach(function (iframe) {
                if (iframe.getAttribute('data-e2e') === 'enforcement-frame') {
                    if (!iframe.hasAttribute('data-height') || iframe.offsetHeight < 200) {
                        iframe.setAttribute('data-height', iframe.offsetHeight);
                    }
                    iframe.style.height = (+iframe.getAttribute('data-height') + 100) + 'px';
                }
            })
        }
    }

    let getArkoselabsWidgetInfo = function (input) {
        if (!input.id) {
            input.id = "arkoselab-input-0";
        }

        let params = {};

        input.value.split('|').forEach(pair => {
            let p = pair.split('=');
            params[p[0]] = unescape(p[1]);
        });

        return {
            captchaType: "arkoselabs",
            widgetId: 0,
            pkey: params.pk,
            surl: params.surl,
            inputId: input.id,
            callback: "arkoselabs_callback_dse7f73ek",
            data: window["arkoselabs_data_dse7f73ek"] || null,
        };
    };

})()