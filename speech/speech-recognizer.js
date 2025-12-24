/**
 * 🎤 语音识别模块 - Google Speech API
 * 模块化设计，可独立使用或集成到其他项目
 */

class SpeechRecognizer {
    /**
     * 检查浏览器是否支持语音识别
     */
    static isSupported() {
        return 'webkitSpeechRecognition' in window || 'SpeechRecognition' in window;
    }

    /**
     * 检测是否为 Android 设备
     */
    static isAndroid() {
        return /Android/i.test(navigator.userAgent);
    }

    constructor(options = {}) {
        this.lang = options.lang || 'zh-CN';
        // Android 需要禁用 continuous 和 interimResults
        this.continuous = options.continuous ?? !SpeechRecognizer.isAndroid();
        this.interimResults = options.interimResults ?? !SpeechRecognizer.isAndroid();

        // 回调函数
        this.onResult = options.onResult || (() => { });
        this.onError = options.onError || (() => { });
        this.onStart = options.onStart || (() => { });
        this.onEnd = options.onEnd || (() => { });

        this.recognition = null;
        this.isListening = false;
        this._timeout = null;
        this._autoRestart = false;
    }

    /**
     * 初始化识别器
     */
    init() {
        if (!SpeechRecognizer.isSupported()) {
            throw new Error('浏览器不支持语音识别');
        }

        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        this.recognition = new SR();
        this.recognition.lang = this.lang;
        this.recognition.continuous = this.continuous;
        this.recognition.interimResults = this.interimResults;
        this.recognition.maxAlternatives = 1;

        this.recognition.onstart = () => {
            this.isListening = true;
            this.onStart();

            // Android 超时保护 (10秒)
            if (SpeechRecognizer.isAndroid()) {
                this._timeout = setTimeout(() => {
                    if (this.isListening) this.stop();
                }, 10000);
            }
        };

        this.recognition.onresult = (e) => {
            const result = e.results[e.results.length - 1];
            const text = result[0].transcript.trim();
            const isFinal = result.isFinal;

            if (text) {
                this.onResult(text, isFinal);
            }

            if (isFinal && SpeechRecognizer.isAndroid()) {
                clearTimeout(this._timeout);
            }
        };

        this.recognition.onerror = (e) => {
            // 忽略 no-speech 和 aborted 错误
            if (e.error !== 'no-speech' && e.error !== 'aborted') {
                this.onError(e.error);
            }
            clearTimeout(this._timeout);
        };

        this.recognition.onend = () => {
            this.isListening = false;
            clearTimeout(this._timeout);

            // 自动重启（仅桌面端连续模式）
            if (this._autoRestart && this.continuous) {
                setTimeout(() => this.start(), 100);
            } else {
                this.onEnd();
            }
        };

        return this;
    }

    /**
     * 开始识别
     */
    start(autoRestart = false) {
        if (!this.recognition) this.init();
        this._autoRestart = autoRestart;

        try {
            this.recognition.start();
        } catch (e) {
            // 可能已经在运行
            if (e.message.includes('already started')) {
                // 忽略
            } else {
                this.onError(e.message);
            }
        }
        return this;
    }

    /**
     * 停止识别
     */
    stop() {
        this._autoRestart = false;
        if (this.recognition && this.isListening) {
            try {
                this.recognition.stop();
            } catch (e) { }
        }
        clearTimeout(this._timeout);
        return this;
    }

    /**
     * 销毁识别器
     */
    destroy() {
        this.stop();
        if (this.recognition) {
            this.recognition.onstart = null;
            this.recognition.onresult = null;
            this.recognition.onerror = null;
            this.recognition.onend = null;
            this.recognition = null;
        }
    }
}

// ES Module 导出
export { SpeechRecognizer };

// 全局访问（兼容非模块环境）
if (typeof window !== 'undefined') {
    window.SpeechRecognizer = SpeechRecognizer;
}
