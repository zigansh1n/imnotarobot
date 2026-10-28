/*
 * Translate text elements
 */
$("[data-lang]").each(function() {
    let el = $(this);

    let key = el.attr("data-lang");

    let strings = [];

    for (let i = 0; i < this.attributes.length; i++){
        let attr = this.attributes[i];

        if (attr.nodeName.match(/^data-lang-string/)) {
            strings.push(attr.nodeValue);
        }
    }

    let message = chrome.i18n.getMessage(key, strings);

    if (message.length === 0) {
        message = key;
    }

    el.html(message);
});
