AwsWafIntegration.getToken = function () {
    return {
        captcha_voucher: document.querySelector("input[name=amazon_waf_captcha_voucher]").value,
        existing_token: document.querySelector("input[name=amazon_waf_existing_token]").value
    }
}

AwsWafIntegration.hasToken = function () {
    const captcha_voucher_input = document.querySelector("input[name=amazon_waf_captcha_voucher]");
    return !!(captcha_voucher_input && captcha_voucher_input.value);
}

if (window.ChallengeScript !== undefined) {
    ChallengeScript.submitCaptcha(document.querySelector("input[name=amazon_waf_captcha_voucher]").value);
}