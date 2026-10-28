let normalHunterInterval = setInterval(function () {
    Config.getAll().then(config => {
        let host = new URL(location.href).host;
        let source = null;
        config.normalSources.forEach(s => {
            if (
                s.host == host
                && s.image.length
                && s.input.length
                && getElementByXpath(s.image)
                && getElementByXpath(s.input)
            ) {
                source = s;
            }
        });

        if (!source) return;

        if (window.isCaptchaWidgetRegistered("normal", 0)) return;

        getNormalCaptchaWidgetInfo(source, function(widgetInfo) {
            registerCaptchaWidget(widgetInfo);
        });
    }).catch(e => {
        /* context probably invalidated, the page must be restarted */
        clearInterval(normalHunterInterval);
    });
}, 2000);

let getElementByXpath = function(path) {
    return document.evaluate(path, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
};

let getNormalCaptchaWidgetInfo = function(source, callback) {
    let img = getElementByXpath(source.image);
    let input = getElementByXpath(source.input);
    let form = input.closest('form');

    if (img.getAttribute('image-reader') == 'in-progress') return;
    img.setAttribute('image-reader', 'in-progress');

    ImageReader.getBase64(img, function(imgBase64) {
        if (!img.id) img.id = "normal-captcha-" + Date.now();
        if (!input.id) input.id = "normal-captcha-answer-" + Date.now();
        if (form && !form.id) form.id = "normal-captcha-container-" + Date.now();

        callback({
            captchaType: "normal",
            widgetId: 0,
            imageId: img.id,
            inputId: input.id,
            containerId: form ? form.id : undefined,
            base64: imgBase64,
        });
    });
};

const ImageReader = {

    getBase64: function(img, callback) {
        if (img.src.indexOf("data:image/") == -1) {
            try {
                this.getBase64ViaCanvas(img, callback);
            } catch (e) {
                this.getBase64FromTaintedImage(img, callback);
            }
        } else {
            this.getBase64FromSrc(img, callback);
        }
    },

    getBase64FromSrc: function(img, callback) {
        let imgBase64 = decodeURI(img.src).replace(/\s+/g, "");
        callback(this.removeDataPrefix(imgBase64));
    },

    getBase64ViaCanvas: function(img, callback) {
        let canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        let context = canvas.getContext('2d');
        context.drawImage(img, 0, 0, img.naturalWidth, img.naturalHeight);
        let imgBase64 = canvas.toDataURL("image/png")
        callback(this.removeDataPrefix(imgBase64));
    },

    getBase64FromTaintedImage: function(img, callback) {
        chrome.runtime.sendMessage({
            command: "loadTaintedImageInBackgroundFrame",
            img_src: img.src
        }, function(response) {
            if (response === undefined) {
                let iframe = document.createElement("iframe");
                iframe.src = img.src;
                iframe.width = "1px";
                iframe.height = "1px";
                iframe.name = img.src;
                document.body.appendChild(iframe);
            }
        });

        let interval = setInterval(function() {
            chrome.storage.local.get('taintedImageBase64', function(storage) {
                if (storage.taintedImageBase64) {
                    callback(storage.taintedImageBase64);
                    clearInterval(interval);
                    chrome.storage.local.remove('taintedImageBase64');
                }
            });
        }, 200);
    },

    removeDataPrefix: function(base64image) {
        return base64image.replace(/^data:image\/(png|jpg|jpeg|pjpeg|gif|bmp|pict|tiff);base64,/i, "");
    },

};

$(document).ready(function () {
    if (window.top != window.self && document.body && document.body.children.length == 1 && document.getElementsByTagName("img").length == 1) {
        let img = document.getElementsByTagName("img")[0];
        if (img.src != window.location.href) return;
        let onloadCallback = function () {
            ImageReader.getBase64(this, imgBase64 => {
                chrome.storage.local.set({taintedImageBase64: imgBase64});
            });
        }
        if (img.complete) {
            onloadCallback.call(img);
        } else {
            img.onload = onloadCallback;
        }
    }
});
