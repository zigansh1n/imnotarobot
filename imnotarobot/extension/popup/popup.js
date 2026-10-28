
var background = chrome.runtime.connect({name: "popup"});

background.onMessage.addListener(function(msg) {

    if (msg.action == "login") {
        let btn = $("#login-form button");
        btn.text(btn.attr("data-text"));

        if (msg.error === undefined) {
            $("#login-form").hide();
            displayAccountInfo(msg.response);
        } else {
            $("#login-form").find(".result").html(`<div class="error">${msg.error}</div>`);
        }
    }

    if (msg.action == "logout") {
        $("#account-info").hide();
        $("#login-form .result").text('');
        $("#login-form").show();
        $("#login-form").find("input").focus();
    }

    if (msg.action == "getAccountInfo") {
        $('.main-loader').hide();

        if (msg.error === undefined) {
            displayAccountInfo(msg.response);
        } else {
            $("#login-form").show().find("input").focus();
        }
    }

});


background.postMessage({action: "getAccountInfo"});


$("#login-form").submit(function(e) {
    e.preventDefault();

    let btn = $(this).find("button");

    btn
        .attr("data-text", btn.text())
        .css({width: btn.outerWidth()});

    btn.html(`<img src="/assets/images/loader_dark.gif">`);

    $(this).find(".result").text("");

    let apiKey = $(this).find("input[name=apiKey]").val();

    background.postMessage({action: "login", apiKey});
});

$("#account-info .logout").click(function(e) {
    e.preventDefault();
    background.postMessage({action: "logout"});
});

$("#settings-form").on("keyup change", "input,select", function() {
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


Config.getAll().then(config => {
   $("#settings-form").find("input,select").each(function() {
       let field = $(this);
       let value = config[field.attr("name")];

       if (field.attr("type") == "checkbox") {
           field.prop("checked", value);
       } else {
           field.val(value);
       }

       field.closest(".custom-select").find(".custom-select-value").text(value);
   });
});

function displayAccountInfo(info) {
    let block = $("#account-info");
    block.find('.email').text(info.email);
    block.find('.balance').text((info.valute == "USD" ? "$" : "₽") + " " + info.balance);
    block.show();
}



/*
 * CUSTOM SELECT
 */
$(".custom-select-label").click(function(e) {
    let p = $(this).parent();

    $(".custom-select").not($(this).parent()[0]).removeClass("open");

    p.find(".custom-select-dropdown").css({
        top: (p.offset().top + 22) + "px"
    });

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

$(".auto-submit-link").attr("href", "chrome-extension://"+chrome.runtime.id+"/options/options.html#autosubmit-rules");