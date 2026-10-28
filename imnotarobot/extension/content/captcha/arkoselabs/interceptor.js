(() => {

    let originalFunCaptcha;

    Object.defineProperty(window, "FunCaptcha", {

        get: function () {
            return function(e) {
                handleArkoselabsInit(e);
                return originalFunCaptcha(e);
            };
        },

        set: function (e) {
            window.ArkoseEnforcement = new Proxy(window.ArkoseEnforcement, {
                construct: function(target, args) {
                    handleArkoselabsInit(args[0]);
                    return new target(...args);
                },
            });

            originalFunCaptcha = e;
        },

    });

    let handleArkoselabsInit = function(params) {
        let dataKey = "arkoselabs_data_dse7f73ek";
        let callbackKey = "arkoselabs_callback_dse7f73ek";

        window[callbackKey] = params.callback;

        if (params.data) {
            window[dataKey] = encodeURIComponent(JSON.stringify(params.data));
        }
    };

})()