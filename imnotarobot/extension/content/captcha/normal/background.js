function imageContextMenuAttachSourceHandler(info, tab) {
    chrome.storage.local.get('imageManualShown', function (result) {
        //chrome.storage.local.set({imageManualShown: 1});
        chrome.tabs.sendMessage(tab.id, {
            command: "getContextMenuEl",
            showManual: result.imageManualShown ? false : true,
            element: info.editable ? 'input' : 'image',
        }, {frameId: info.frameId}, data => {
            saveElementPath(
                new URL(info.pageUrl).host,
                info.editable ? 'input' : 'image',
                data.xpath
            );
        });
    });
}

chrome.contextMenus.removeAll(function () {

    // chrome.i18n.getMessage("...") is not working with manifest v3 (probably bug), lets wait for the fix
    // https://bugs.chromium.org/p/chromium/issues/detail?id=1159438

    chrome.contextMenus.create({
        id: "markAsCaptchaSource",
        title: "Mark As Captcha Source", // chrome.i18n.getMessage("markAsCaptchaSource"),
        contexts: ["image"],
    });

    chrome.contextMenus.create({
        id: "putCaptchaAnswerHere",
        title: "Put Captcha Answer Here", // chrome.i18n.getMessage("putCaptchaAnswerHere"),
        contexts: ["editable"],
    });

});

chrome.contextMenus.onClicked.addListener(imageContextMenuAttachSourceHandler);


function saveElementPath(host, type, path) {
    Config.get('normalSources').then(normalSources => {
        let source = null;
        normalSources.forEach(function (s) {
            if (s.host == host) source = s;
        });
        if (!source) {
            source = {host, image: '', input: ''};
            normalSources.push(source);
        }
        source[type] = path;
        Config.set({normalSources});
    });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
        if (message.command === 'loadTaintedImageInBackgroundFrame') {
            if (typeof document !== 'undefined') {
                let iframe = document.createElement("iframe");
                iframe.src = message.img_src;
                iframe.width = "1px";
                iframe.height = "1px";
                iframe.name = message.img_src;
                document.body.appendChild(iframe);
                sendResponse({});
            }
        } else {
            sendResponse(undefined);
        }
    }
);