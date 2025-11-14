// ==UserScript==
// @name         我的验证码识别脚本
// @namespace    http://tampermonkey.net/
// @version      0.1
// @author       You
// @connect      *
// @match        http://*/*
// @match        https://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// ==/UserScript==

(function () {
    'use strict';

    var element, input, imgIndex, canvasIndex, inputIndex, captchaType;
    var localRules = [];
    var baseUrl = "http://localhost:6688"
    var exist = false;
    var iscors = false;
    var inBlack = false;
    var firstin = true;
    // 用于跟踪验证码图像的上次修改时间
    var lastModified = 0;
    // 用于跟踪DOM变化的节流定时器
    var domChangeTimer = null;
    var imgSrc = "";
    var lastRequestedCode = "";

    // 添加菜单
    GM_registerMenuCommand('添加当前页面规则', addRule);
    GM_registerMenuCommand('清除当前页面规则', delRule);
    GM_registerMenuCommand('管理网页黑名单', manageBlackList);
    GM_registerMenuCommand('导入规则', importRules);
    GM_registerMenuCommand('导出规则', exportRules);

    GM_setValue("preCode", "");

    function importRules() {
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = 'application/json';
        input.click();
        input.onchange = function () {
            var file = input.files[0];
            var reader = new FileReader();
            reader.readAsText(file);
            reader.onload = function () {
                var rules = JSON.parse(reader.result);
                GM_setValue("captchaRules", rules);
                topNotice("导入规则成功");
                setTimeout(function () {
                    window.location.reload();
                }, 1000);
            }
        }
    }

    function exportRules() {
        var rules = GM_getValue("captchaRules", []);
        var data = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(rules));
        var a = document.createElement('a');
        a.href = data;
        a.download = 'captchaRules.json';
        a.click();
    }

    function escapeSelectorValue(value) {
        if (!value) return "";
        if (window.CSS && window.CSS.escape) {
            return window.CSS.escape(value);
        }
        return value.replace(/([ !"#$%&'()*+,.\/:;<=>?@[\]^`{|}~])/g, '\\$1');
    }

    function getElementSelector(target) {
        if (!target || !target.nodeType || target.nodeType !== 1) return "";
        if (target.id) {
            return "#" + escapeSelectorValue(target.id);
        }
        var segments = [];
        var elementRef = target;
        while (elementRef && elementRef.nodeType === 1 && elementRef !== document.body) {
            var tagName = elementRef.nodeName.toLowerCase();
            var index = 1;
            var sibling = elementRef;
            while (sibling = sibling.previousElementSibling) {
                if (sibling.nodeName === elementRef.nodeName) {
                    index++;
                }
            }
            segments.unshift(tagName + ":nth-of-type(" + index + ")");
            if (elementRef.parentElement && elementRef.parentElement.id) {
                segments.unshift("#" + escapeSelectorValue(elementRef.parentElement.id));
                break;
            }
            elementRef = elementRef.parentElement;
        }
        return segments.join(" > ");
    }

    function isElementVisible(elem) {
        if (!elem) return false;
        if (!document.body.contains(elem)) return false;
        var rect = elem.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return false;
        var computed = window.getComputedStyle(elem);
        if (!computed) return true;
        return computed.display !== "none" && computed.visibility !== "hidden" && parseFloat(computed.opacity) !== 0;
    }

    function resolveCaptchaElement(rule) {
        if (!rule) return null;
        var target = null;
        if (rule.imgSelector) {
            try {
                target = document.querySelector(rule.imgSelector);
            } catch (err) {
                console.log("【我的验证码识别】解析img选择器失败:", err);
            }
        }
        if (!target) {
            if (rule.type === "canvas") {
                var canvasList = document.getElementsByTagName('canvas');
                target = canvasList && typeof rule.img === "number" ? canvasList[rule.img] : null;
            } else {
                var imgList = document.getElementsByTagName('img');
                target = imgList && typeof rule.img === "number" ? imgList[rule.img] : null;
            }
        }
        if (target && !isCode.call({ element: target })) {
            return null;
        }
        if (target && !isElementVisible(target)) {
            return null;
        }
        return target || null;
    }

    function resolveInputElement(rule) {
        if (!rule) return null;
        var inputRef = null;
        if (rule.inputSelector) {
            try {
                inputRef = document.querySelector(rule.inputSelector);
            } catch (err) {
                console.log("【我的验证码识别】解析input选择器失败:", err);
            }
            if (inputRef && rule.inputType === "textarea" && inputRef.tagName !== "TEXTAREA") {
                inputRef = null;
            }
            if (inputRef && rule.inputType !== "textarea" && inputRef.tagName !== "INPUT") {
                inputRef = null;
            }
        }
        if (!inputRef) {
            if (rule.inputType === "textarea") {
                var textareaList = document.getElementsByTagName('textarea');
                inputRef = textareaList && typeof rule.input === "number" ? textareaList[rule.input] : null;
            } else {
                var inputList = document.getElementsByTagName('input');
                inputRef = inputList && typeof rule.input === "number" ? inputList[rule.input] : null;
                if (inputList && inputList[0] && (inputList[0].id == "_w_simile" || inputList[0].id == "black_node") && typeof rule.input === "number") {
                    inputRef = inputList[rule.input + 1];
                }
            }
        }
        return inputRef || null;
    }

    function isCode() {
        // 修复this上下文问题
        var elem = this && this.element ? this.element : element;
        if (elem && (elem.height >= 100 || elem.height == elem.width))
            return false;
        var attrList = ["id", "title", "alt", "name", "className", "src"];
        var strList = ["code", "Code", "CODE", "captcha", "Captcha", "CAPTCHA", "yzm", "Yzm", "YZM", "check", "Check", "CHECK", "random", "Random", "RANDOM", "veri", "Veri", "VERI", "验证码", "看不清", "换一张", "imgcode"];
        for (var i = 0; i < attrList.length; i++) {
            for (var j = 0; j < strList.length; j++) {
                if (elem && elem[attrList[i]]) {
                    var attr = elem[attrList[i]];
                    if (typeof attr === 'string' && attr.indexOf(strList[j]) != -1) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    function isInput() {
        var attrList = ["placeholder", "alt", "title", "id", "className", "name"];
        var strList = ["code", "Code", "CODE", "captcha", "Captcha", "CAPTCHA", "yzm", "Yzm", "YZM", "check", "Check", "CHECK", "random", "Random", "RANDOM", "veri", "Veri", "VERI", "验证码", "看不清", "换一张"];
        for (var i = 0; i < attrList.length; i++) {
            for (var j = 0; j < strList.length; j++) {
                if (input && input[attrList[i]]) {
                    var attr = input[attrList[i]];
                    if (typeof attr === 'string' && attr.indexOf(strList[j]) != -1) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    function addRule() {
        var ruleData = { "url": window.location.href.split("?")[0], "img": "", "imgSelector": "", "input": "", "inputSelector": "", "inputType": "", "type": "", "captchaType": "" };
        topNotice("请在验证码图片上点击鼠标 “右”👉 键");
        document.oncontextmenu = function (e) {
            e = e || window.event;
            e.preventDefault();

            if (e.target.tagName == "IMG" || e.target.tagName == "GIF") {
                var imgList = document.getElementsByTagName('img');
                for (var i = 0; i < imgList.length; i++) {
                    if (imgList[i] == e.target) {
                        var k = i;
                        ruleData.type = "img";
                    }
                }
            }
            else if (e.target.tagName == "CANVAS") {
                var imgList = document.getElementsByTagName('canvas');
                for (var i = 0; i < imgList.length; i++) {
                    if (imgList[i] == e.target) {
                        var k = i;
                        ruleData.type = "canvas";
                    }
                }
            }
            if (k == null) {
                topNotice("选择有误，请重新点击验证码图片");
                return;
            }
            ruleData.img = k;
            ruleData.imgSelector = getElementSelector(e.target);
            topNotice("请在验证码输入框上点击鼠标 “左”👈 键");
            document.onclick = function (e) {
                e = e || window.event;
                e.preventDefault();
                var inputList = document.getElementsByTagName('input');
                var textareaList = document.getElementsByTagName('textarea');
                if (e.target.tagName == "INPUT") {
                    ruleData.inputType = "input";
                    for (var i = 0; i < inputList.length; i++) {
                        if (inputList[i] == e.target) {
                            if (inputList[0] && (inputList[0].id == "_w_simile" || inputList[0].id == "black_node")) {
                                var k = i - 1;
                            }
                            else {
                                var k = i;
                            }
                        }
                    }
                }
                else if (e.target.tagName == "TEXTAREA") {
                    ruleData.inputType = "textarea";
                    for (var i = 0; i < textareaList.length; i++) {
                        if (textareaList[i] == e.target) {
                            var k = i;
                        }
                    }
                }
                if (k == null) {
                    topNotice("选择有误，请重新点击验证码输入框");
                    return;
                }
                ruleData.inputSelector = getElementSelector(e.target);
                ruleData.input = k;
                var r = confirm("选择验证码类型\n\n数/英验证码请点击“确定”，算术验证码请点击“取消”");
                if (r == true) {
                    ruleData.captchaType = "general";
                }
                else {
                    ruleData.captchaType = "math";
                }
                let rules = GM_getValue("captchaRules", []);
                rules.push(ruleData);
                GM_setValue("captchaRules", rules);
                topNotice("添加规则成功");
                setTimeout(function () {
                    window.location.reload();
                }, 1000);
            }
        }
    }

    function delRule() {
        var ruleData = { "url": window.location.href.split("?")[0] }
        let rules = GM_getValue("captchaRules", []);
        rules = rules.filter(rule => rule.url !== ruleData.url);
        GM_setValue("captchaRules", rules);
        topNotice("删除规则成功");
    }

    function codeByRule() {
        var code = "";
        var src = element.src;
        if (firstin) {
            firstin = false;
            if (src && src.indexOf('data:image') != -1) {
                code = src.split("base64,")[1];
                GM_setValue("tempCode", code);
                if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                    GM_setValue("preCode", code);
                    lastRequestedCode = code;
                    p1(code).then((ans) => {
                        if (ans != "")
                            writeIn1(ans, code);
                        else
                            codeByRule();
                    });
                }
            }
            else if (src && src.indexOf('blob') != -1) {
                const image = new Image()
                image.src = src;
                image.onload = () => {
                    const canvas = document.createElement('canvas')
                    canvas.width = image.width
                    canvas.height = image.height
                    const context = canvas.getContext('2d')
                    context.drawImage(image, 0, 0, image.width, image.height);
                    code = canvas.toDataURL().split("base64,")[1];
                    GM_setValue("tempCode", code);
                    if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                        GM_setValue("preCode", code);
                        lastRequestedCode = code;
                        p1(code).then((ans) => {
                        if (ans != "")
                            writeIn1(ans, code);
                        else
                            codeByRule();
                        });
                    }
                }
            }
            else {
                try {
                    var img = element;
                    if (img.src && img.width != 0 && img.height != 0) {
                        var canvas = document.createElement("canvas");
                        var ctx = canvas.getContext("2d");
                        canvas.width = img.width;
                        canvas.height = img.height;
                        ctx.drawImage(img, 0, 0, img.width, img.height);
                        code = canvas.toDataURL("image/png").split("base64,")[1];
                        GM_setValue("tempCode", code);
                        if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                            GM_setValue("preCode", code);
                            lastRequestedCode = code;
                            p1(code).then((ans) => {
                                if (ans != "")
                                    writeIn1(ans, code);
                                else
                                    codeByRule();
                            });
                        }
                    }
                    else {
                        codeByRule();
                    }
                }
                catch (err) {
                    return;
                }
            }
        }
        else {
            if (src && src.indexOf('data:image') != -1) {
                code = src.split("base64,")[1];
                GM_setValue("tempCode", code);
                if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                    GM_setValue("preCode", code);
                    lastRequestedCode = code;
                    p1(code).then((ans) => {
                        writeIn1(ans, code);
                    });
                }
            }
            else if (src && src.indexOf('blob') != -1) {
                const image = new Image()
                image.src = src;
                image.onload = () => {
                    const canvas = document.createElement('canvas')
                    canvas.width = image.width
                    canvas.height = image.height
                    const context = canvas.getContext('2d')
                    context.drawImage(image, 0, 0, image.width, image.height);
                    code = canvas.toDataURL().split("base64,")[1];
                    GM_setValue("tempCode", code);
                    if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                        GM_setValue("preCode", code);
                        lastRequestedCode = code;
                        p1(code).then((ans) => {
                            writeIn1(ans, code);
                        })
                    }
                }
            }
            else {
                var canvas = document.createElement("canvas");
                var ctx = canvas.getContext("2d");
                element.onload = function () {
                    canvas.width = element.width;
                    canvas.height = element.height;
                    ctx.drawImage(element, 0, 0, element.width, element.height);
                    code = canvas.toDataURL("image/png").split("base64,")[1];
                    GM_setValue("tempCode", code);
                    if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                        GM_setValue("preCode", code);
                        lastRequestedCode = code;
                        p1(code).then((ans) => {
                            writeIn1(ans, code);
                        });
                    }
                }
            }
        }
    }

    function canvasRule() {
        setTimeout(function () {
            try {
                var code = element.toDataURL("image/png").split("base64,")[1];
                GM_setValue("tempCode", code);
                if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                    GM_setValue("preCode", code);
                    lastRequestedCode = code;
                    p1(code).then((ans) => {
                        writeIn1(ans, code);
                    });
                }
            }
            catch (err) {
                canvasRule();
            }
        }, 100);
    }

    function findCode(k) {
        var code = '';
        var codeList = document.getElementsByTagName('img');
        for (var i = k; i < codeList.length; i++) {
            var src = codeList[i].src;
            element = codeList[i];
            if (!isElementVisible(element)) {
                continue;
            }
            if (src && src.indexOf('data:image') != -1) {
                if (isCode()) {
                    firstin = false;
                    code = src.split("base64,")[1];
                    GM_setValue("tempCode", code);
                    if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                        GM_setValue("preCode", code);
                        lastRequestedCode = code;
                        p(code, i).then((ans) => {
                            writeIn(ans, code);
                        });
                    }
                    break;
                }
            }
            else {
                if (isCode()) {
                    if (firstin) {
                        firstin = false;
                        var img = element;
                        if (img.src && img.width != 0 && img.height != 0) {
                            var canvas = document.createElement("canvas");
                            var ctx = canvas.getContext("2d");
                            canvas.width = img.width;
                            canvas.height = img.height;
                            ctx.drawImage(img, 0, 0, img.width, img.height);
                            code = canvas.toDataURL("image/png").split("base64,")[1];
                            try {
                                code = canvas.toDataURL("image/png").split("base64,")[1];
                            }
                            catch (err) {
                                findCode(i + 1);
                                return;
                            }
                            GM_setValue("tempCode", code);
                            if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                                iscors = isCORS();
                                GM_setValue("preCode", code);
                                lastRequestedCode = code;
                                p(code, i).then((ans) => {
                                    if (ans != "") {
                                        writeIn(ans, code);
                                    } else {
                                        findCode(i);
                                    }
                                });
                                return;
                            }
                        }
                        else {
                            findCode(i);
                            return;
                        }
                    }
                    else {
                        var canvas = document.createElement("canvas");
                        var ctx = canvas.getContext("2d");
                        element.onload = function () {
                            canvas.width = element.width;
                            canvas.height = element.height;
                            ctx.drawImage(element, 0, 0, element.width, element.height);
                            try {
                                code = canvas.toDataURL("image/png").split("base64,")[1];
                            }
                            catch (err) {
                                findCode(i + 1);
                                return;
                            }
                            GM_setValue("tempCode", code);
                            if (GM_getValue("tempCode") != GM_getValue("preCode")) {
                                iscors = isCORS();
                                GM_setValue("preCode", code);
                                lastRequestedCode = code;
                                p(code, i).then((ans) => {
                                    writeIn(ans, code);
                                });
                                return;
                            }
                        }
                        break;
                    }
                }
            }
        }
    }

    function findInput() {
        var inputList = document.getElementsByTagName('input');
        for (var i = 0; i < inputList.length; i++) {
            input = inputList[i];
            if (isInput()) {
                return true;
            }
        }
        return false;
    }

    function writeIn(ans, code) {
        if (!ans) {
            return;
        }
        if (code && GM_getValue("preCode", "") !== code) {
            return;
        }
        if (findInput()) {
            ans = ans.replace(/\s+/g, "");
            input.value = ans;
            if (typeof (InputEvent) !== "undefined") {
                input.value = ans;
                input.dispatchEvent(new InputEvent('input'));
                var eventList = ['input', 'change', 'focus', 'keypress', 'keyup', 'keydown', 'select'];
                for (var i = 0; i < eventList.length; i++) {
                    fire(input, eventList[i]);
                }
                input.value = ans;
            }
            else if (KeyboardEvent) {
                input.dispatchEvent(new KeyboardEvent("input"));
            }
        }
    }

    function p(code, i) {
        return new Promise((resolve) => {
            const datas = {
                "img_base64": String(code),
            }
            GM_xmlhttpRequest({
                method: "POST",
                url: baseUrl + "/api/ocr/image",
                data: JSON.stringify(datas),
                headers: {
                    "Content-Type": "application/json"
                },
                responseType: "json",
                onload: function (response) {
                    if (response.status == 200) {
                        try {
                            var result = response.response["result"];
                            return resolve(result);
                        }
                        catch (e) {
                            return resolve("");
                        }
                    }
                    else {
                        try {
                            if (response.response["result"] == null) {
                                findCode(i + 1);
                            }
                        }
                        catch (err) {
                        }
                        return resolve("");
                    }
                },
                onerror: function(error) {
                    return resolve("");
                }
            });
        });
    }

    function p1(code) {
        if (captchaType == "general" || captchaType == null) {
            return new Promise((resolve) => {
                const datas = {
                    "img_base64": String(code),
                }
                GM_xmlhttpRequest({
                    method: "POST",
                    url: baseUrl + "/api/ocr/image",
                    data: JSON.stringify(datas),
                    headers: {
                        "Content-Type": "application/json"
                    },
                    responseType: "json",
                    onload: function (response) {
                        if (response.status == 200) {
                            try {
                                var result = response.response["result"];
                                return resolve(result);
                            }
                            catch (e) {
                                return resolve("");
                            }
                        }
                        else {
                            return resolve("");
                        }
                    },
                    onerror: function(error) {
                        return resolve("");
                    }
                });
            });
        }
        else if (captchaType == "math") {
            // 使用本地算术验证码识别，不依赖云码
            return new Promise((resolve) => {
                const datas = {
                    "img_base64": String(code),
                }
                GM_xmlhttpRequest({
                    method: "POST",
                    url: baseUrl + "/api/ocr/compute",
                    data: JSON.stringify(datas),
                    headers: {
                        "Content-Type": "application/json"
                    },
                    responseType: "json",
                    onload: function (response) {
                        if (response.status == 200) {
                            try {
                                var result = response.response["result"];
                                return resolve(result);
                            }
                            catch (e) {
                                return resolve("");
                            }
                        }
                        else {
                            return resolve("");
                        }
                    },
                    onerror: function(error) {
                        return resolve("");
                    }
                });
            });
        }
    }

    function isCORS() {
        try {
            if (element.src && (element.src.indexOf('http') != -1 || element.src.indexOf('https') != -1)) {
                if (element.src.indexOf(window.location.host) == -1) {
                    console.log("检测到当前页面存在跨域问题");
                    return true;
                }
                return false;
            }
        }
        catch (err) {
            return;
        }
    }

    function p2() {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                url: element.src,
                method: "GET",
                headers: { 'Content-Type': 'application/json; charset=utf-8', 'path': window.location.href },
                responseType: "blob",
                onload: function (response) {
                    let blob = response.response;
                    let reader = new FileReader();
                    reader.onloadend = (e) => {
                        let data = e.target.result;
                        element.src = data;
                        return resolve(data);
                    }
                    reader.readAsDataURL(blob);
                }
            });
        });
    }

    function fire(element, eventName) {
        var event = document.createEvent("HTMLEvents");
        event.initEvent(eventName, true, true);
        element.dispatchEvent(event);
    }
    
    function FireForReact(element, eventName) {
        try {
            let env = new Event(eventName);
            element.dispatchEvent(env);
            var funName = Object.keys(element).find(p => Object.keys(element[p]).find(f => f.toLowerCase().endsWith(eventName)));
            if (funName != undefined) {
                element[funName].onChange(env)
            }
        }
        catch (e) { }
    }

    function writeIn1(ans, code) {
        if (!ans) {
            return;
        }
        if (code && GM_getValue("preCode", "") !== code) {
            return;
        }
        if (!input) {
            return;
        }
        ans = ans.replace(/\s+/g, "");
        if (input.tagName == "TEXTAREA") {
            input.innerHTML = ans;
        }
        else {
            input.value = ans;
            if (typeof (InputEvent) !== "undefined") {
                input.value = ans;
                input.dispatchEvent(new InputEvent('input'));
                var eventList = ['input', 'change', 'focus', 'keypress', 'keyup', 'keydown', 'select'];
                for (var i = 0; i < eventList.length; i++) {
                    fire(input, eventList[i]);
                }
                FireForReact(input, 'change');
                input.value = ans;
            }
            else if (KeyboardEvent) {
                input.dispatchEvent(new KeyboardEvent("input"));
            }
        }
    }

    function compareUrl() {
        return new Promise((resolve) => {
            let rules = GM_getValue("captchaRules", []);
            let currentUrl = window.location.href.split("?")[0];
            let matchedRule = rules.find(rule => rule.url === currentUrl);
            if (matchedRule) {
                localRules = matchedRule;
                resolve(true);
            } else {
                localRules = [];
                resolve(false);
            }
        });
    }

    function prepareRuleElements() {
        element = resolveCaptchaElement(localRules);
        input = resolveInputElement(localRules);
        if (!element || !input) {
            return false;
        }
        if (!isElementVisible(element)) {
            return false;
        }
        if (localRules["type"] === "canvas") {
            var canvases = document.getElementsByTagName('canvas');
            for (var cIdx = 0; cIdx < canvases.length; cIdx++) {
                if (canvases[cIdx] === element) {
                    canvasIndex = cIdx;
                    break;
                }
            }
        } else {
            var imgs = document.getElementsByTagName('img');
            for (var imgIdxTemp = 0; imgIdxTemp < imgs.length; imgIdxTemp++) {
                if (imgs[imgIdxTemp] === element) {
                    imgIndex = imgIdxTemp;
                    break;
                }
            }
        }
        if (localRules["inputType"] === "textarea") {
            var textareas = document.getElementsByTagName('textarea');
            for (var tIdx = 0; tIdx < textareas.length; tIdx++) {
                if (textareas[tIdx] === input) {
                    inputIndex = tIdx;
                    break;
                }
            }
        } else {
            var inputs = document.getElementsByTagName('input');
            for (var inIdx = 0; inIdx < inputs.length; inIdx++) {
                if (inputs[inIdx] === input) {
                    inputIndex = inIdx;
                    break;
                }
            }
        }
        return true;
    }

    function start() {
        compareUrl().then((isExist) => {
            if (isExist) {
                exist = true;
                captchaType = localRules["captchaType"] || "general";
                if (!prepareRuleElements()) {
                    exist = false;
                    firstin = true;
                    GM_setValue("preCode", "");
                    findCode(0);
                    return;
                }
                firstin = true;
                GM_setValue("preCode", "");
                lastRequestedCode = "";
                imgSrc = element && element.src ? element.src : "";
                iscors = isCORS();
                var runRule = function () {
                    if (localRules["type"] == "canvas") {
                        canvasRule();
                    } else {
                        codeByRule();
                    }
                };
                if (iscors) {
                    p2().then(() => {
                        runRule();
                    }).catch(() => {
                        runRule();
                    });
                }
                else {
                    runRule();
                }
            }
            else {
                exist = false;
                firstin = true;
                GM_setValue("preCode", "");
                lastRequestedCode = "";
                findCode(0);
            }
        });
    }

    function pageChange() {
        if (exist) {
            if (!prepareRuleElements()) {
                exist = false;
                firstin = true;
                GM_setValue("preCode", "");
                findCode(0);
                return;
            }
            firstin = true;
            GM_setValue("preCode", "");
            lastRequestedCode = "";
            imgSrc = element && element.src ? element.src : imgSrc;
            iscors = isCORS();
            var runRule = function () {
                if (localRules["type"] == "canvas") {
                    canvasRule();
                } else {
                    codeByRule();
                }
            };
            if (iscors) {
                p2().then(() => {
                    runRule();
                }).catch(() => {
                    runRule();
                });
            }
            else {
                runRule();
            }
        }
        else {
            firstin = true;
            GM_setValue("preCode", "");
            lastRequestedCode = "";
            findCode(0);
        }
    }

    function topNotice(msg) {
        var div = document.createElement('div');
        div.id = 'topNotice';
        div.style.cssText = 'position: fixed; top: 0; left: 0; width: 100%; height: 5%; z-index: 9999999999; background: rgba(117,140,148,1); display: flex; justify-content: center; align-items: center; color: #fff; font-family: "Microsoft YaHei"; text-align: center;';
        div.innerHTML = msg;
        div.style.fontSize = 'medium';
        document.body.appendChild(div);
        setTimeout(function () {
            document.body.removeChild(document.getElementById('topNotice'));
        }, 3500);
    }

    function manageBlackList() {
        var blackList = GM_getValue("blackList", []);
        var div = document.createElement("div");
        div.style.cssText = 'width: 700px; height: 350px; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background-color: white; border: 1px solid black; z-index: 9999999999; text-align: center; padding-top: 20px; padding-bottom: 20px; padding-left: 20px; padding-right: 20px; box-shadow: 0px 0px 10px 0px rgba(0,0,0,0.75); border-radius: 10px; overflow: auto;';
        div.innerHTML = "<h3 style='margin-bottom: 12px; font-weight: bold; font-size: 18px;'>黑名单</h3><button style='position: absolute; top: 10px; left: 10px; width: 50px; height: 30px; line-height: 30px; text-align: center; font-size: 13px; margin: 10px' id='add'>添加</button><table id='blackList' style='width:100%; border-collapse:collapse; border: 1px solid black;'><thead style='background-color: #f5f5f5;'><tr><th style='width: 80%; text-align: center; padding: 5px;'>字符串</th><th style='width: 20%; text-align: center; padding: 5px;'>操作</th></tr></thead><tbody></tbody></table><button style='position: absolute; top: 10px; right: 10px; width: 30px; height: 30px; line-height: 30px; text-align: center; font-size: 18px; font-weight: bold; color: #333; background-color: transparent; border: none; outline: none; cursor: pointer;' id='close'>×</button>";
        document.body.insertBefore(div, document.body.firstChild);
        var table = document.getElementById("blackList").getElementsByTagName('tbody')[0];
        for (var i = 0; i < blackList.length; i++) {
            var row = table.insertRow(i);
            row.insertCell(0).innerHTML = "<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>" + blackList[i] + "</div>";
            var removeBtn = document.createElement("button");
            removeBtn.className = "remove";
            removeBtn.style.cssText = 'background-color: transparent; color: blue; border: none; padding: 5px; font-size: 14px; border-radius: 5px;';
            removeBtn.innerText = "移除";
            row.insertCell(1).appendChild(removeBtn);
        }
        var close = document.getElementById("close");
        close.onclick = function () {
            div.remove();
        }
        var add = document.getElementById("add");
        add.onclick = function () {
            var zz = prompt("请输入一个字符串，任何URL中包含该字符串的网页都将被加入黑名单");
            if (zz == null) return;
            var blackList = GM_getValue("blackList", []);
            if (blackList.indexOf(zz) == -1) {
                blackList.push(zz);
                GM_setValue("blackList", blackList);
                var row = table.insertRow(table.rows.length);
                row.insertCell(0).innerHTML = "<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>" + zz + "</div>";
                var removeBtn = document.createElement("button");
                removeBtn.className = "remove";
                removeBtn.style.cssText = "background-color: transparent; color: blue; border: none; padding: 5px; font-size: 14px; border-radius: 5px; cursor: pointer; ";
                removeBtn.innerText = "移除";
                row.insertCell(1).appendChild(removeBtn);
                removeBtn.onclick = function () {
                    var index = this.parentNode.parentNode.rowIndex - 1;
                    blackList.splice(index, 1);
                    GM_setValue("blackList", blackList);
                    this.parentNode.parentNode.remove();
                }
                topNotice("添加黑名单成功，刷新页面生效")
            }
            else {
                topNotice("该网页已在黑名单中");
            }
        }
        var remove = document.getElementsByClassName("remove");
        for (var i = 0; i < remove.length; i++) {
            remove[i].onclick = function () {
                var index = this.parentNode.parentNode.rowIndex - 1;
                blackList.splice(index, 1);
                GM_setValue("blackList", blackList);
                this.parentNode.parentNode.remove();
                topNotice("移除黑名单成功，刷新页面生效");
            }
        }
    }

    console.log("【我的验证码识别】正在运行...");

    var url = window.location.href;
    var blackList = GM_getValue("blackList", []);
    var inBlack = blackList.some(function (blackItem) {
        return url.includes(blackItem);
    });
    if (inBlack) {
        console.log("【我的验证码识别】当前页面在黑名单中");
        return;
    } else {
        // 页面初始化时启动识别
        start();
    }

    // 页面加载完成后再次尝试识别验证码，确保元素已加载
    window.addEventListener('load', function() {
        if (!inBlack) {
            console.log("【我的验证码识别】页面加载完成，重新尝试识别");
            setTimeout(function() {
                start();
            }, 1000);
        }
    });

    // 页面可见性变化时也尝试重新识别
    document.addEventListener('visibilitychange', function() {
        if (!document.hidden && !inBlack) {
            console.log("【我的验证码识别】页面变为可见，重新尝试识别");
            setTimeout(function() {
                start();
            }, 500);
        }
    });

    // 页面DOM内容加载完成后尝试识别
    document.addEventListener('DOMContentLoaded', function() {
        if (!inBlack) {
            console.log("【我的验证码识别】DOM内容加载完成，尝试识别");
            setTimeout(function() {
                start();
            }, 500);
        }
    });

    // 恢复更多DOM监听功能，确保验证码识别正常工作
    setTimeout(function () {
        const targetNode = document.body;
        const config = { 
            attributes: true, 
            childList: true, 
            subtree: true,
            attributeFilter: ['src', 'class', 'id', 'style'] // 恢复更多属性监听
        };
        
        const callback = function (mutationsList) {
            if (inBlack) return;
            
            // 节流处理，避免频繁触发
            if (domChangeTimer) {
                clearTimeout(domChangeTimer);
            }
            
            domChangeTimer = setTimeout(function() {
                try {
                    let hasCaptchaChange = false;
                    
                    for (let mutation of mutationsList) {
                        // 检查是否有新节点添加
                        if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
                            for (let node of mutation.addedNodes) {
                                // 检查是否添加了验证码相关元素
                                if (node.nodeType === 1) { // 元素节点
                                    if ((node.tagName === 'IMG' || node.tagName === 'CANVAS') && isCode.call({element: node})) {
                                        hasCaptchaChange = true;
                                        element = node; // 更新element引用
                                        break;
                                    }
                                    // 检查子元素
                                    const captchaElements = node.querySelectorAll && 
                                        (node.querySelectorAll('img, canvas') || []);
                                    for (let elem of captchaElements) {
                                        if (isCode.call({element: elem})) {
                                            hasCaptchaChange = true;
                                            element = elem; // 更新element引用
                                            break;
                                        }
                                    }
                                }
                            }
                        } 
                        // 检查属性变化
                        else if (mutation.type === 'attributes') {
                            // 检查验证码元素的src变化
                            if (mutation.attributeName === 'src' && mutation.target.src) {
                                // 检查是否是验证码元素
                                if (isCode.call({element: mutation.target})) {
                                    const now = Date.now();
                                    // 确保不是短时间内重复触发，并且src确实发生了变化
                                    if (now - lastModified > 100 && mutation.target.src !== imgSrc) {
                                        lastModified = now;
                                        imgSrc = mutation.target.src;
                                        hasCaptchaChange = true;
                                        element = mutation.target;
                                    }
                                }
                            }
                            // 也检查其他可能的验证码相关属性变化
                            else if ((mutation.attributeName === 'id' || mutation.attributeName === 'class') && 
                                     mutation.target.tagName === 'IMG') {
                                // 检查元素是否变成了验证码元素
                                if (isCode.call({element: mutation.target})) {
                                    hasCaptchaChange = true;
                                    element = mutation.target;
                                }
                            }
                        }
                    }
                    
                    // 只有检测到验证码相关变化时才触发识别
                    if (hasCaptchaChange) {
                        firstin = true;
                        GM_setValue("preCode", "");
                        lastRequestedCode = "";
                        // 确保始终调用pageChange进行识别
                        pageChange();
                    }
                    
                    // 原有逻辑：检查现有element是否发生变化
                    if (exist && element) {
                        // 修复：确保在验证码元素src变化时正确处理
                        if (element.src && element.src !== imgSrc) {
                            imgSrc = element.src;
                            firstin = true;
                            GM_setValue("preCode", "");
                            lastRequestedCode = "";
                            pageChange();
                        } else if (!element.src && element.tagName === "CANVAS") {
                            // 对于Canvas元素的特殊处理
                            firstin = true;
                            GM_setValue("preCode", "");
                            lastRequestedCode = "";
                            pageChange();
                        }
                    }
                } catch (err) {
                    // 忽略错误，避免中断监听
                    return;
                }
            }, 50); // 减少节流延迟以提高响应速度
        }
        
        const observer = new MutationObserver(callback);
        observer.observe(targetNode, config);
    }, 1000);

    // 添加专门的验证码更新检测机制
    setTimeout(function () {
        if (inBlack) return;
        
        // 定时检查验证码元素是否发生变化
        setInterval(function() {
            if (exist && element) {
                // 对于基于规则的验证码元素
                if (element.tagName === "IMG" && element.src && element.src !== imgSrc) {
                    imgSrc = element.src;
                    firstin = true;
                    GM_setValue("preCode", "");
                    lastRequestedCode = "";
                    pageChange();
                } else if (element.tagName === "CANVAS") {
                    // 对于Canvas类型的验证码，检查内容是否变化
                    try {
                        const currentData = element.toDataURL();
                        if (currentData !== imgSrc) {
                            imgSrc = currentData;
                            firstin = true;
                            GM_setValue("preCode", "");
                            lastRequestedCode = "";
                            pageChange();
                        }
                    } catch (e) {
                        // 忽略Canvas访问错误
                    }
                }
            } else if (!exist && element) {
                // 对于自动识别的验证码元素
                if (element.tagName === "IMG" && element.src && element.src !== imgSrc) {
                    imgSrc = element.src;
                    firstin = true;
                    GM_setValue("preCode", "");
                    lastRequestedCode = "";
                    findCode(0);
                }
            }
        }, 300); // 每300ms检查一次
        
        // 特殊处理登录失败后刷新验证码的情况
        const loginFailObserver = new MutationObserver(function(mutations) {
            let loginFailDetected = false;
            
            for (let mutation of mutations) {
                // 检查新增节点
                if (mutation.type === 'childList') {
                    for (let node of mutation.addedNodes) {
                        if (node.nodeType === 1) { // 元素节点
                            const textContent = (node.textContent || '').toLowerCase();
                            // 检测常见的登录失败提示关键词
                            const failKeywords = ['失败', '错误', '不正确', '无效', 'error', 'fail', 'incorrect', 'wrong'];
                            
                            if (failKeywords.some(keyword => textContent.includes(keyword.toLowerCase()))) {
                                loginFailDetected = true;
                                break;
                            }
                            
                            // 检查子元素
                            if (node.querySelectorAll) {
                                const childTexts = Array.from(node.querySelectorAll('*')).map(el => el.textContent || '');
                                if (childTexts.some(text => 
                                    failKeywords.some(keyword => text.toLowerCase().includes(keyword)))) {
                                    loginFailDetected = true;
                                    break;
                                }
                            }
                        }
                    }
                }
                
                if (loginFailDetected) break;
            }
            
            // 如果检测到登录失败，等待验证码刷新后重新识别
            if (loginFailDetected) {
                console.log("【我的验证码识别】检测到登录失败，等待验证码刷新后重新识别");
                setTimeout(function() {
                    firstin = true;
                    GM_setValue("preCode", "");
                    lastRequestedCode = "";
                    imgSrc = ""; // 清除之前的src记录
                    
                    if (exist) {
                        pageChange();
                    } else {
                        findCode(0);
                    }
                }, 500); // 等待500ms确保验证码刷新完成
            }
        });
        
        // 观察整个文档的变化
        loginFailObserver.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: false
        });
    }, 1500);

    setTimeout(function () {
        if (inBlack) return;
        try {
            // 添加对element元素存在性的检查
            if (!element || element.tagName != "CANVAS") return;
        }
        catch (err) {
            return;
        }
        var canvasData1 = element.toDataURL();
        setInterval(function () {
            // 添加对element元素存在性的检查
            if (!element) return;
            var canvasData2 = element.toDataURL();
            if (canvasData1 != canvasData2) {
                canvasData1 = canvasData2;
                // 更新imgSrc以确保其他检测机制也能正常工作
                imgSrc = canvasData2;
                pageChange();
            }
        }, 300); // 降低检查频率但不过低
    }, 1000);

    setTimeout(function () {
        if (inBlack) return;
        var tempUrl = window.location.href;
        setInterval(function () {
            if (tempUrl != window.location.href) {
                tempUrl = window.location.href;
                start();
            }
        }, 500);
    }, 500)
})();