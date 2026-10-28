class TwoCaptcha {

    constructor(options) {
        if (typeof options == "string") {
            options = { apiKey: options }
        }

        let defaultOptions = {
            apiKey: "",
            service: "http://127.0.0.1:8765",
            softId: 0,
            defaultTimeout: 120,
            recaptchaTimeout: 600,
            pollingInterval: 10,
        }

        for (let key in defaultOptions) {
            this[key] = options[key] === undefined ? defaultOptions[key] : options[key];
        }
    }

    normal(captcha) {
        captcha.method = "base64";

        return this.solve(captcha, { timeout: this.defaultTimeout });
    }

    recaptcha(captcha) {
        captcha.method = "userrecaptcha";

        return this.solve(captcha, { timeout: this.recaptchaTimeout });
    }

    geetest(captcha) {
        captcha.method = 'geetest';

        return this.solve(captcha);
    }

    geetest_v4(captcha) {
        captcha.method = 'geetest_v4';

        return this.solve(captcha);
    }

    hcaptcha(captcha) {
        captcha.method = 'hcaptcha';

        return this.solve(captcha);
    }

    keycaptcha(captcha) {
        captcha.method = "keycaptcha";

        return this.solve(captcha);
    }

    arkoselabs(captcha) {
        captcha.method = "funcaptcha";

        return this.solve(captcha);
    }

    lemin(captcha) {
        captcha.method = "lemin";

        return this.solve(captcha);
    }

    yandex(captcha) {
        captcha.method = "yandex";

        return this.solve(captcha);
    }

    capy(captcha) {
        captcha.method = "capy";

        return this.solve(captcha);
    }

    amazon_waf(captcha) {
        captcha.method = "amazon_waf";
        return this.solve(captcha);
    }

    turnstile(captcha) {
        captcha.method = "turnstile";

        return this.solve(captcha);
    }

    async solve(captcha, waitOptions) {
        let result = {};

        result.captchaId = await this.send(captcha);

        result.code = await this.waitForResult(result.captchaId, waitOptions);

        return result;
    }

    async send(captcha) {
        this.sendAttachDefaultParams(captcha);

        let files = this.extractFiles(captcha);

        this.mapParams(captcha, captcha.method);
        this.mapParams(files, captcha.method);

        return await this.in(captcha, files);
    }

    async waitForResult(id, waitOptions) {
        if (!waitOptions) {
            waitOptions = {
                timeout: this.defaultTimeout,
                pollingInterval: this.pollingInterval,
            }
        }

        let startedAt = this.getTime();

        let timeout = waitOptions.timeout === undefined ? this.defaultTimeout : waitOptions.timeout;
        let pollingInterval = waitOptions.pollingInterval === undefined ? this.pollingInterval : waitOptions.pollingInterval;

        while (true) {
            if (this.getTime() - startedAt < timeout) {
                await new Promise(resolve => setTimeout(resolve, pollingInterval * 1000));
            } else {
                break;
            }

            try {
                let code = await this.getResult(id);
                if (code) return code;
            } catch (e) {
                throw e;
            }
        }

        throw new Error('Timeout ' + timeout + ' seconds reached');
    }

    getTime() {
        return parseInt(Date.now() / 1000);
    }

    async getResult(id) {
        try {
            return await this.res({
                action: "get",
                id: id,
            });
        } catch (e) {
            if (e.message == "CAPCHA_NOT_READY") {
                return null;
            }

            throw e;
        }
    }

    async balance() {
        let balance = await this.res({ action: "getbalance" });

        return parseFloat(balance);
    }

    async userInfo() {
        return await this.res({ action: "userinfo" });
    }

    async report(id, isCorrect) {
        let action = isCorrect ? "reportgood" : "reportbad";

        return await this.res({ action, id });
    }

    sendAttachDefaultParams(captcha) {
        if (this.softId > 0) {
            captcha.softId = this.softId;
        }
    }

    extractFiles(captcha) {
        let files = {};

        let fileKeys = ['file', 'hintImg'];

        for (let i = 1; i < 10; i++) {
            fileKeys.push('file_' + i);
        }

        fileKeys.forEach(function (key) {
            if (captcha[key] !== undefined /* && is_file($captcha[$key]) */) {
                files[key] = captcha[key];
                delete captcha[key];
            }
        });

        return files;
    }

    mapParams(params, method) {
        let map = this.getParamsMap(method);

        for (let k in map) {
            let newName = k;
            let oldName = map[k];

            if (params[newName] !== undefined) {
                params[oldName] = params[newName];
                delete params[newName];
            }
        }

        if (params.proxy !== undefined) {
            params.proxytype = params.proxy.type;
            params.proxy = params.proxy.uri;
        }
    }

    getParamsMap(method) {
        let commonMap = {
            base64: "body",
            caseSensitive: "regsense",
            minLen: "min_len",
            maxLen: "max_len",
            hintText: "textinstructions",
            hintImg: "imginstructions",
            url: "pageurl",
            score: "min_score",
            text: "textcaptcha",
            rows: "recaptcharows",
            cols: "recaptchacols",
            previousId: "previousID",
            canSkip: "can_no_answer",
            apiServer: "api_server",
            softId: "soft_id",
            captchaId: "captcha_id",
            divId: "div_id",
            callback: "pingback",
        };

        let methodMap = {
            userrecaptcha: {
                sitekey: "googlekey",
            },
            funcaptcha: {
                sitekey: "publickey",
            },
            capy: {
                sitekey: "captchakey",
            },
        };

        if (methodMap[method] !== undefined) {
            for (let key in methodMap[method]) {
                commonMap[key] = methodMap[method][key];
            }
        }

        return commonMap;
    }

    async in(captcha, files) {
        // TODO: prepare files

        return await this.request('POST', '/in.php', captcha, files);
    }

    async res(data) {
        return await this.request('GET', '/res.php', data);
    }

    async request(method, path, data, files) {
        data.key = this.apiKey;
        data.header_acao = 1;
        data.json = 1;

        // "service" is either a host of a cloud solver or the full address of the local one
        let baseUrl = String(this.service).match(/^https?:\/\//) ? this.service : "https://" + this.service;

        let url = baseUrl.replace(/\/+$/, "") + path;

        let options = {
            method: method,
        };

        if (method === 'GET') {
            let kv = [];

            for (let key in data) {
                kv.push(key + '=' + encodeURIComponent(data[key]));
            }

            url += '?' + kv.join('&');
        } else {
            let formData = new FormData();

            for (let key in data) {
                if (typeof data[key] == 'object') {
                    for (let ok in data[key]) {
                        formData.append(key + "[" + ok + "]", data[key][ok]);
                    }
                } else {
                    formData.append(key, data[key]);
                }
            }

            options.body = formData;
        }

        let response;

        try {
            response = await fetch(url, options);
        } catch (e) {
            throw new Error("API_CONNECTION_ERROR");
        }

        if (!response.ok) {
            throw new Error('API_HTTP_CODE_' + response.status);
        }

        let json;

        try {
            json = await response.json();
        } catch (e) {
            throw new Error("API_INCORRECT_RESPONSE");
        }

        if (json.status === 0) {
            throw new Error(json.request);
        }

        return json.request || json;
    }

}
