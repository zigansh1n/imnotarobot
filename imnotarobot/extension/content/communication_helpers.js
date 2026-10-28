window.sendMsgToSolverCS = function(action, data) {
    return new Promise(function(resolve, reject) {
        let messages = document.querySelector("body > solver-ext-messages");

        if (!messages) {
            messages = document.createElement("solver-ext-messages");
            messages.style.display = "none";
            document.body.appendChild(messages);
        }

        let message = document.createElement("solver-ext-message");
        message.dataset.action = action;
        message.dataset.messageId = Date.now();
        if (data) message.dataset.data = encodeURIComponent(JSON.stringify(data));
        messages.appendChild(message);

        let waitResponseInterval = setInterval(function() {
            if (message.dataset.response) {
                try {
                    let data = JSON.parse(decodeURIComponent(message.dataset.response));

                    if (data.error) {
                        reject(new Error(data.error));
                    } else {
                        resolve(data);
                    }
                } catch (e) {
                    reject(new Error("Cannot parse message response"));
                }

                clearTimeout(waitResponseInterval);
                message.remove();
                if (!messages.childNodes.length) messages.remove();
            }
        }, 200);
    });
}

/*
 * Execute callbacks
 */
setInterval(function() {
    let textarea = document.querySelector('textarea[id=twocaptcha-callback-trigger]');
    if (textarea) {
        let func = textarea.getAttribute('data-function');
        let data = textarea.value;
        textarea.remove();
        window[func](data);
    }
}, 1000);

/*
 * Execute autosubmit rules
 */
setInterval(function() {
    let textarea = document.querySelector('textarea[id=twocaptcha-autosubmit-code]');
    if (textarea) {
        let steps = textarea.value.trim().split("\n");
        textarea.remove();
        let el = null;
        for (let i = 0; i < steps.length; i++) {
            let step = JSON.parse(steps[i]);

            if (step.type === 'source') {
                if (step.value === 'window') el = window;
                if (step.value === 'document') el = document;
            } else if (step.type === 'property') {
                el = el[step.value]
            } else if (step.type === 'method') {
                if (step.args && step.args.length) {
                    el = el[step.value](...step.args);
                } else {
                    el = el[step.value]();
                }
            } else if (step.type === 'index') {
                el = el[step.value];
            }
        }
    }
}, 1000);