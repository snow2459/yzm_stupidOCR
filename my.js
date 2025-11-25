// ==UserScript==
// @name         验证码自动识别脚本
// @namespace    http://tampermonkey.net/
// @version      0.2
// @author       You
// @connect      *
// @match        http://*/*
// @match        https://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// ==/UserScript==

/**
 * 验证码自动识别脚本
 * 功能模块：
 * 1. 配置管理（Token、规则、黑名单）
 * 2. 限流管理（10秒50次）
 * 3. OCR 请求处理
 * 4. 验证码识别逻辑
 * 5. DOM 监听和自动填写
 */

(function () {
    'use strict';

    // ==================== 配置和常量 ====================
    var baseUrl = "http://localhost:6688";
    var RETRY_DELAY_MS = 1000; // 请求失败后的重试间隔，避免高频重试
    
    // 限流配置
    var RATE_LIMIT_WINDOW = 10000; // 10秒
    var RATE_LIMIT_MAX_REQUESTS = 50; // 最大50次
    var requestHistory = []; // 请求历史记录

    // ==================== 全局状态变量 ====================
    var element, input; // 当前验证码元素和输入框
    var imgIndex, canvasIndex, inputIndex; // 元素索引
    var captchaType; // 验证码类型：general 或 math
    var localRules = []; // 当前页面的规则
    var exist = false; // 是否存在匹配的规则
    var iscors = false; // 是否存在跨域问题
    var inBlack = false; // 是否在黑名单中
    var firstin = true; // 是否首次识别
    var lastModified = 0; // 验证码图像的上次修改时间
    var domChangeTimer = null; // DOM变化的节流定时器
    var imgSrc = ""; // 当前验证码图片的 src
    var lastRequestedCode = ""; // 最后一次请求的验证码 code

    // 初始化
    GM_setValue("preCode", "");

    // ==================== 限流管理 ====================
    function checkRateLimit() {
        var now = Date.now();
        // 清理过期记录
        requestHistory = requestHistory.filter(function(timestamp) {
            return now - timestamp < RATE_LIMIT_WINDOW;
        });
        
        // 检查是否超过限制
        if (requestHistory.length >= RATE_LIMIT_MAX_REQUESTS) {
            var oldestRequest = requestHistory[0];
            var waitTime = RATE_LIMIT_WINDOW - (now - oldestRequest);
            return {
                allowed: false,
                waitTime: Math.ceil(waitTime / 1000) // 转换为秒
            };
        }
        
        // 记录本次请求
        requestHistory.push(now);
        return { allowed: true };
    }

    // ==================== Token 管理 ====================
    
    /**
     * 配置 Token
     */
    function configureToken() {
        var currentToken = GM_getValue("ocrToken", "");
        var div = document.createElement("div");
        div.style.cssText = 'width: 500px; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background-color: white; border: 2px solid #667eea; z-index: 9999999999; text-align: center; padding: 30px; box-shadow: 0px 0px 20px 0px rgba(0,0,0,0.5); border-radius: 10px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;';
        div.innerHTML = '<h3 style="margin-bottom: 20px; color: #333; font-size: 18px;">🔐 配置 OCR Token</h3>' +
            '<p style="color: #666; font-size: 13px; margin-bottom: 15px; text-align: left;">请在管理界面 (http://localhost:6688/admin) 配置 Token 后，将 Token 粘贴到下方：</p>' +
            '<input type="text" id="tokenInput" placeholder="请输入 Token" style="width: 100%; padding: 10px; border: 2px solid #e0e0e0; border-radius: 6px; font-size: 14px; margin-bottom: 15px; box-sizing: border-box;" value="' + (currentToken ? '••••••••' : '') + '">' +
            '<div style="display: flex; gap: 10px;">' +
            '<button id="saveToken" style="flex: 1; padding: 10px; background: #667eea; color: white; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 500;">保存</button>' +
            '<button id="clearToken" style="flex: 1; padding: 10px; background: #dc3545; color: white; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 500;">清除</button>' +
            '<button id="closeToken" style="flex: 1; padding: 10px; background: #6c757d; color: white; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 500;">关闭</button>' +
            '</div>' +
            '<p style="color: #999; font-size: 12px; margin-top: 15px; text-align: left;">提示：Token 用于验证 API 访问权限，未配置或错误的 Token 将导致识别失败</p>';
        document.body.appendChild(div);

        var tokenInput = document.getElementById("tokenInput");
        var saveBtn = document.getElementById("saveToken");
        var clearBtn = document.getElementById("clearToken");
        var closeBtn = document.getElementById("closeToken");

        // 如果已有 token，点击输入框显示真实值
        if (currentToken) {
            tokenInput.addEventListener('focus', function() {
                if (this.value === '••••••••') {
                    this.value = currentToken;
                }
            });
        }

        saveBtn.onclick = function() {
            var token = tokenInput.value.trim();
            if (!token) {
                topNotice("Token 不能为空", "error");
                return;
            }
            GM_setValue("ocrToken", token);
            topNotice("Token 保存成功", "success");
            setTimeout(function() {
                div.remove();
            }, 1000);
        };

        clearBtn.onclick = function() {
            if (confirm("确定要清除 Token 吗？")) {
                GM_setValue("ocrToken", "");
                topNotice("Token 已清除", "success");
                setTimeout(function() {
                    div.remove();
                }, 1000);
            }
        };

        closeBtn.onclick = function() {
            div.remove();
        };
    }

    // ==================== 规则管理 ====================
    
    /**
     * 导入规则
     */
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

    /**
     * 导出规则
     */
    function exportRules() {
        var rules = GM_getValue("captchaRules", []);
        var data = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(rules));
        var a = document.createElement('a');
        a.href = data;
        a.download = 'captchaRules.json';
        a.click();
    }

    // ==================== DOM 工具函数 ====================
    
    /**
     * 将图片元素转换为 base64 字符串
     * @param {HTMLElement} imgElement - 图片元素（img 或 canvas）
     * @returns {string} base64 编码的图片数据
     */
    function imageToBase64(imgElement) {
        try {
            if (imgElement.tagName === "CANVAS") {
                return imgElement.toDataURL("image/png").split("base64,")[1];
            } else if (imgElement.tagName === "IMG") {
                var canvas = document.createElement("canvas");
                var ctx = canvas.getContext("2d");
                canvas.width = imgElement.width;
                canvas.height = imgElement.height;
                ctx.drawImage(imgElement, 0, 0, imgElement.width, imgElement.height);
                return canvas.toDataURL("image/png").split("base64,")[1];
            }
        } catch (err) {
            console.log("【我的验证码识别】图片转换失败:", err);
        }
        return null;
    }
    
    /**
     * 将 Blob URL 图片转换为 base64
     * @param {string} blobUrl - Blob URL
     * @returns {Promise<string>} base64 编码的图片数据
     */
    function blobUrlToBase64(blobUrl) {
        return new Promise(function(resolve) {
            const image = new Image();
            image.src = blobUrl;
            image.onload = function() {
                const canvas = document.createElement('canvas');
                canvas.width = image.width;
                canvas.height = image.height;
                const context = canvas.getContext('2d');
                context.drawImage(image, 0, 0, image.width, image.height);
                resolve(canvas.toDataURL().split("base64,")[1]);
            };
            image.onerror = function() {
                resolve(null);
            };
        });
    }
    
    /**
     * 转义 CSS 选择器值
     */
    function escapeSelectorValue(value) {
        if (!value) return "";
        if (window.CSS && window.CSS.escape) {
            return window.CSS.escape(value);
        }
        return value.replace(/([ !"#$%&'()*+,.\/:;<=>?@[\]^`{|}~])/g, '\\$1');
    }

    /**
     * 获取元素的 CSS 选择器
     */
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

    /**
     * 检查元素是否可见
     */
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

    // ==================== 结果写入 ====================
    
    /**
     * 将识别结果写入输入框（自动识别模式）
     * @param {string} ans - 识别结果
     * @param {string} code - 验证码的 base64 code
     */
    function writeIn(ans, code) {
        // 检查结果是否有效
        if (!ans || (typeof ans !== 'string' && typeof ans !== 'number')) {
            console.log("【我的验证码识别】writeIn: 结果无效", ans);
            return;
        }
        // 转换为字符串并清理
        ans = String(ans).replace(/\s+/g, "");
        if (!ans || ans.length === 0) {
            console.log("【我的验证码识别】writeIn: 结果为空");
            return;
        }
        
        // 检查验证码是否匹配：使用 lastRequestedCode 而不是 preCode
        // 如果 code 存在且与 lastRequestedCode 不一致，说明验证码已更新
        if (code && lastRequestedCode && lastRequestedCode !== code) {
            console.log("【我的验证码识别】writeIn: 验证码已更新，跳过写入 (code:", code, "lastRequestedCode:", lastRequestedCode, ")");
            return;
        }
        
        if (findInput()) {
            // 如果输入框为空，或者当前值不等于识别结果，则允许写入
            var currentValue = (input.value || "").trim();
            if (currentValue === "" || currentValue !== ans) {
                console.log("【我的验证码识别】writeIn: 写入结果", ans, "(当前值:", currentValue, ")");
                triggerInputEvents(input, ans);
            } else {
                console.log("【我的验证码识别】writeIn: 输入框已有相同值，跳过写入");
            }
        } else {
            console.log("【我的验证码识别】writeIn: 未找到输入框");
        }
    }

    // ==================== OCR 请求处理 ====================
    
    /**
     * 通用 OCR 请求函数
     * 包含 token 验证和限流检查
     * @param {string} url - API 路径
     * @param {object} data - 请求数据
     * @param {function} onSuccess - 成功回调
     * @param {function} onError - 错误回调
     */
    function makeOCRRequest(url, data, onSuccess, onError) {
        // 检查限流
        var rateLimitCheck = checkRateLimit();
        if (!rateLimitCheck.allowed) {
            topNotice("请求过于频繁，请等待 " + rateLimitCheck.waitTime + " 秒后再试", "warning");
            if (onError) onError("rate_limit");
            return;
        }

        // 获取 token
        var token = GM_getValue("ocrToken", "");
        if (!token) {
            topNotice("未配置 Token，请通过菜单配置 Token", "error");
            if (onError) onError("no_token");
            return;
        }

        var headers = {
            "Content-Type": "application/json",
            "X-Token": token
        };

        function handleErrorWithDelay(code) {
            if (typeof onError === "function") {
                setTimeout(function() { onError(code); }, RETRY_DELAY_MS);
            }
        }

        GM_xmlhttpRequest({
            method: "POST",
            url: baseUrl + url,
            data: JSON.stringify(data),
            headers: headers,
            responseType: "json",
            onload: function (response) {
                if (response.status == 200) {
                    try {
                        var result = response.response["result"];
                        // 确保结果不为空且是有效字符串
                        if (result && typeof result === 'string' && result.trim().length > 0) {
                            if (onSuccess) onSuccess(result);
                        } else {
                            console.log("【我的验证码识别】识别结果为空或无效:", result);
                            handleErrorWithDelay("empty_result");
                        }
                    }
                    catch (e) {
                        console.log("【我的验证码识别】解析响应失败:", e);
                        handleErrorWithDelay("parse_error");
                    }
                }
                else if (response.status == 403) {
                    // Token 验证失败
                    topNotice("Token 验证失败，请检查 Token 配置", "error");
                    handleErrorWithDelay("token_invalid");
                }
                else {
                    console.log("【我的验证码识别】请求失败，状态码:", response.status);
                    handleErrorWithDelay("request_failed");
                }
            },
            onerror: function(error) {
                topNotice("请求失败，请检查服务是否正常运行", "error");
                handleErrorWithDelay("network_error");
            }
        });
    }

    /**
     * 验证识别结果是否有效
     */
    function isValidResult(result) {
        if (result === null || result === undefined || result === "") {
            return false;
        }
        return typeof result === 'string' || typeof result === 'number';
    }

    /**
     * 通用 OCR 识别函数（自动识别模式）
     * @param {string} code - 验证码的 base64 code
     * @param {number} i - 图片索引（用于失败时尝试下一个）
     */
    function p(code, i) {
        return new Promise((resolve) => {
            const datas = { "img_base64": String(code) };
            makeOCRRequest("/api/ocr/image", datas, 
                function(result) {
                    if (isValidResult(result)) {
                        console.log("【我的验证码识别】p: 识别成功", result);
                        resolve(String(result));
                    } else {
                        console.log("【我的验证码识别】p: 识别结果无效", result);
                        resolve("");
                    }
                },
                function(error) {
                    console.log("【我的验证码识别】p: 请求失败", error);
                    if (error === "token_invalid" || error === "no_token") {
                        resolve("");
                    } else {
                        // 其他错误，尝试下一个
                        try {
                            if (i !== undefined) {
                                findCode(i + 1);
                            }
                        } catch (err) {
                        }
                        resolve("");
                    }
                }
            );
        });
    }

    /**
     * OCR 识别函数（规则模式）
     * @param {string} code - 验证码的 base64 code
     */
    function p1(code) {
        var apiUrl = "/api/ocr/image";
        if (captchaType == "math") {
            apiUrl = "/api/ocr/compute";
        }
        
        return new Promise((resolve) => {
            const datas = { "img_base64": String(code) };
            makeOCRRequest(apiUrl, datas,
                function(result) {
                    if (isValidResult(result)) {
                        console.log("【我的验证码识别】p1: 识别成功", result);
                        resolve(String(result));
                    } else {
                        console.log("【我的验证码识别】p1: 识别结果无效", result);
                        resolve("");
                    }
                },
                function(error) {
                    console.log("【我的验证码识别】p1: 请求失败", error);
                    resolve("");
                }
            );
        });
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

    /**
     * 触发 DOM 事件
     */
    function fire(element, eventName) {
        var event = document.createEvent("HTMLEvents");
        event.initEvent(eventName, true, true);
        element.dispatchEvent(event);
    }
    
    /**
     * 触发 React 组件事件
     */
    function FireForReact(element, eventName) {
        try {
            let env = new Event(eventName);
            element.dispatchEvent(env);
            var funName = Object.keys(element).find(p => 
                Object.keys(element[p]).find(f => f.toLowerCase().endsWith(eventName))
            );
            if (funName != undefined) {
                element[funName].onChange(env);
            }
        }
        catch (e) { }
    }
    
    /**
     * 触发输入框的所有事件（确保表单框架能捕获到变化）
     */
    function triggerInputEvents(inputElement, value) {
        inputElement.value = value;
        if (typeof (InputEvent) !== "undefined") {
            inputElement.dispatchEvent(new InputEvent('input'));
            var eventList = ['input', 'change', 'focus', 'keypress', 'keyup', 'keydown', 'select'];
            for (var i = 0; i < eventList.length; i++) {
                fire(inputElement, eventList[i]);
            }
            inputElement.value = value;
        }
        else if (KeyboardEvent) {
            inputElement.dispatchEvent(new KeyboardEvent("input"));
        }
    }

    /**
     * 将识别结果写入输入框（规则模式）
     * @param {string} ans - 识别结果
     * @param {string} code - 验证码的 base64 code
     */
    function writeIn1(ans, code) {
        // 检查结果是否有效
        if (!ans || (typeof ans !== 'string' && typeof ans !== 'number')) {
            console.log("【我的验证码识别】writeIn1: 结果无效", ans);
            return;
        }
        // 转换为字符串并清理
        ans = String(ans).replace(/\s+/g, "");
        if (!ans || ans.length === 0) {
            console.log("【我的验证码识别】writeIn1: 结果为空");
            return;
        }
        
        // 检查验证码是否匹配：使用 lastRequestedCode 而不是 preCode
        // 如果 code 存在且与 lastRequestedCode 不一致，说明验证码已更新
        if (code && lastRequestedCode && lastRequestedCode !== code) {
            console.log("【我的验证码识别】writeIn1: 验证码已更新，跳过写入 (code:", code, "lastRequestedCode:", lastRequestedCode, ")");
            return;
        }
        
        if (!input) {
            console.log("【我的验证码识别】writeIn1: 输入框未定义");
            return;
        }
        
        // 如果输入框为空，或者当前值不等于识别结果，则允许写入
        var currentValue = "";
        if (input.tagName == "TEXTAREA") {
            currentValue = (input.innerHTML || "").trim();
        } else {
            currentValue = (input.value || "").trim();
        }
        
        if (currentValue === "" || currentValue !== ans) {
            console.log("【我的验证码识别】writeIn1: 写入结果", ans, "(当前值:", currentValue, ")");
            if (input.tagName == "TEXTAREA") {
                input.innerHTML = ans;
            }
            else {
                triggerInputEvents(input, ans);
                FireForReact(input, 'change');
            }
        } else {
            console.log("【我的验证码识别】writeIn1: 输入框已有相同值，跳过写入");
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

    // ==================== UI 工具函数 ====================
    
    /**
     * 显示顶部通知
     * @param {string} msg - 消息内容
     * @param {string} type - 消息类型：success/error/warning
     */
    function topNotice(msg, type) {
        var div = document.createElement('div');
        div.id = 'topNotice';
        var bgColor = 'rgba(117,140,148,1)'; // 默认蓝色
        if (type === 'error') {
            bgColor = 'rgba(220,53,69,0.95)'; // 红色
        } else if (type === 'success') {
            bgColor = 'rgba(40,167,69,0.95)'; // 绿色
        } else if (type === 'warning') {
            bgColor = 'rgba(255,193,7,0.95)'; // 黄色
        }
        div.style.cssText = 'position: fixed; top: 0; left: 0; width: 100%; min-height: 50px; z-index: 9999999999; background: ' + bgColor + '; display: flex; justify-content: center; align-items: center; color: #fff; font-family: "Microsoft YaHei"; text-align: center; padding: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.2);';
        div.innerHTML = '<div style="font-size: 16px; font-weight: 500;">' + msg + '</div>';
        document.body.appendChild(div);
        var duration = type === 'error' ? 5000 : 3500; // 错误消息显示更久
        setTimeout(function () {
            var notice = document.getElementById('topNotice');
            if (notice) {
                document.body.removeChild(notice);
            }
        }, duration);
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

    // ==================== 初始化 ====================
    
    /**
     * 初始化脚本
     */
    function init() {
        console.log("【我的验证码识别】正在运行...");
        
        // 检查黑名单
        var url = window.location.href;
        var blackList = GM_getValue("blackList", []);
        inBlack = blackList.some(function (blackItem) {
            return url.includes(blackItem);
        });
        
        if (inBlack) {
            console.log("【我的验证码识别】当前页面在黑名单中");
            return;
        }
        
        // 注册菜单
        GM_registerMenuCommand('添加当前页面规则', addRule);
        GM_registerMenuCommand('清除当前页面规则', delRule);
        GM_registerMenuCommand('管理网页黑名单', manageBlackList);
        GM_registerMenuCommand('导入规则', importRules);
        GM_registerMenuCommand('导出规则', exportRules);
        GM_registerMenuCommand('配置 Token', configureToken);
        
        // 启动识别
        start();
    }
    
    // 执行初始化
    init();

    // ==================== 事件监听 ====================
    
    // 页面加载完成后再次尝试识别验证码
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
