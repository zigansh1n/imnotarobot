/*
 * Show current configurations
 */
Config.getAll().then(config => {

    var form = $("#config-form");

    for (let key in config) {
        let el = $("[name=" + key + "]");

        let val = config[key];

        if (!el.length) continue;

        if (el.is("[type=text]") || el.is("select") || el.is("textarea")) {
            el.val(val);
        }

        if (el.is("[type=checkbox]")) {
            el.prop("checked", val);
        }

        el.closest(".custom-select").find(".custom-select-value").text(val);
    }

    $(".container").attr("style", "");

});


/*
 * Save configurations
 */
$("#config-form").on("keyup change", "input,select,textarea", function() {
    let key = $(this).attr("name");
    let value = $(this).val();

    if ($(this).is("[type=checkbox]")) {
        value = $(this).is(":checked");
    }

    let dataType = $(this).attr("data-type");

    if (dataType == 'int') value = parseInt(value) || 0;
    if (dataType == 'float') value = parseFloat(value) || 0.0;

    let config = {};
    config[key] = value;

    Config.set(config);
});



/*
 * BACKGROUND COMMUNICATION
 */
var connectBtn = $("#connect");

connectBtn.click(function(e) {
    e.preventDefault();

    connectBtn.attr("data-text", connectBtn.text());

    connectBtn.html(`<img src="/assets/images/loader_dark.gif">`);

    let apiKey = $("input[name=apiKey]").val();
    let apiService = $("input[name=apiService]").val();

    background.postMessage({action: "login", apiKey, apiService});
});

var background = chrome.runtime.connect({name: "popup"});

background.onMessage.addListener(function(msg) {

    if (msg.action == "login") {
        if (msg.error === undefined) {
            alert(chrome.i18n.getMessage("accountSuccessfullyConnected"));
        } else {
            alert(msg.error);
        }

        connectBtn.text(connectBtn.attr("data-text"));
    }

});




/*
 * CUSTOM SELECT
 */
$(".custom-select-label").click(function(e) {
    let p = $(this).parent();
    $(".custom-select").not($(this).parent()[0]).removeClass("open");
    p.toggleClass("open");
});

$(".custom-select-dropdown-field button").click(function(e) {
    e.preventDefault();

    let p = $(this).closest(".custom-select");

    let v = $(this).parent().find("input").val();

    p.find(".custom-select-value").text(v);
    p.removeClass("open");
});

$(".custom-select-dropdown-value").click(function() {
    $(this).parent().find("input").val($(this).attr("data-value")).change();
    $(this).parent().find("button").click();
});



/*
 * AutoSubmit rules
 */
function displayAutoSubmitRules() {
    Config.get('autoSubmitRules').then(rules => {
        let rows = '';

        rules.forEach(function(rule, i) {
            rows += `
                <tr data-index="${i}">
                    <td style="text-align: left">${rule.url_pattern}</td>
                    <td>
                        <button class="edit-rule">${chrome.i18n.getMessage('edit')}</button>
                        <button class="delete-rule">${chrome.i18n.getMessage('delete')}</button>
                    </td>
                </tr>
            `;
        });

        $('#autosubmit-rules .content').html(`
            <table class="config">
                ${rows}
                <tr${rules.length ? ' class="d-none"' : ''}>
                    <td>${chrome.i18n.getMessage('autoSubmitNoRules')}</td>
                </tr>
                <tr>
                    <td>
                        <button class="default-btn new-rule">${chrome.i18n.getMessage('autoSubmitCreateNewRule')}</button>
                    </td>
                </tr>
            </table>
        `);
    });
}

displayAutoSubmitRules();

$('#autosubmit-rules').on('click', '.new-rule', function(e) {
    e.preventDefault();

    let noRulesRow = $(this).closest('tr').prev();

    if ($('#autosubmit-rules .rule-form').length) {
        return alert(chrome.i18n.getMessage('autoSubmitAlertFormOpened'));
    }

    noRulesRow.addClass('d-none');
    noRulesRow.before(renderRuleForm());
});

function renderRuleForm() {
    return `
        <tr class="rule-form">
            <td colspan="3">
                <input type="hidden" name="index" value="-1">
                <input type="text" name="url_pattern" style="margin-bottom: 8px;" placeholder="${chrome.i18n.getMessage('autoSubmitPlaceholderUrl')} site.com">
                <textarea name="code" rows="4" placeholder='${chrome.i18n.getMessage('autoSubmitPlaceholderCode')}&#10;{"type":"source","value":"document"}&#10;{"type":"method","value":"querySelector","args":["#submit-button"]}&#10;{"type":"method","value":"click"}'></textarea>
                <button class="default-btn save-rule" style="margin-right: 5px;">${chrome.i18n.getMessage('save')}</button>
                <button class="default-btn cancel">${chrome.i18n.getMessage('cancel')}</button>
            </td>
        </tr>
    `;
}

$('#autosubmit-rules').on('click', '.save-rule', function(e) {
    e.preventDefault();

    let form = $(this).closest('tr');

    let index = parseInt(form.find('input[name=index]').val());

    let rule = {
        url_pattern: form.find('input[name=url_pattern]').val(),
        code: form.find('textarea').val(),
    };

    if (!rule.url_pattern.length) return alert(chrome.i18n.getMessage('autoSubmitAlertUrlRequired'));
    try {new RegExp(rule.url_pattern)} catch (e) {return alert(chrome.i18n.getMessage('autoSubmitAlertUrlInvalid'))}
    if (!rule.code.length) return alert(chrome.i18n.getMessage('autoSubmitAlertCodeRequired'));

    Config.get('autoSubmitRules').then(rules => {
        if (!rules) rules = [];
        if (index == -1) {
            rules.push(rule);
        } else {
            rules[index] = rule;
        }
        Config.set({autoSubmitRules: rules}).then(displayAutoSubmitRules);
    });
});

$('#autosubmit-rules').on('click', '.cancel', function(e) {
    e.preventDefault();
    displayAutoSubmitRules();
});

$('#autosubmit-rules').on('click', '.edit-rule', function(e) {
    e.preventDefault();
    let index = parseInt($(this).closest('tr').attr('data-index'));

    if ($('#autosubmit-rules .rule-form').length) {
        return alert(chrome.i18n.getMessage('autoSubmitAlertFormOpened'));
    }

    $('#autosubmit-rules tr[data-index='+index+']').replaceWith(renderRuleForm());
    Config.get('autoSubmitRules').then(rules => {
        let form = $('#autosubmit-rules .rule-form');
        form.find('input[name=index]').val(index);
        form.find('input[name=url_pattern]').val(rules[index].url_pattern);
        form.find('textarea').val(rules[index].code);
    });
});

$('#autosubmit-rules').on('click', '.delete-rule', function(e) {
    e.preventDefault();
    let index = parseInt($(this).closest('tr').attr('data-index'));
    let url_pattern = $(this).closest('tr').children().first().text();
    if (!confirm(chrome.i18n.getMessage('autoSubmitConfirmDelete') + ' "' + url_pattern + '"?')) return;
    Config.get('autoSubmitRules').then(rules => {
        let newRules = [];
        for (let i = 0; i < rules.length; i++) {
            if (i != index) {
                newRules.push(rules[i]);
            }
        }
        Config.set({autoSubmitRules: newRules}).then(displayAutoSubmitRules);
    });
});

setTimeout(function() {
    if (location.href.endsWith('#autosubmit-rules')) {
        $([document.documentElement, document.body]).animate({
            scrollTop: $("#autosubmit-rules").offset().top
        }, 1000);
    }
}, 400);
