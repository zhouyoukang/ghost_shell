# 🎤 语音识别模块

简化版语音识别，仅支持 Google Speech API。

## 使用方法

```javascript
import { SpeechRecognizer } from './speech-recognizer.js';

const recognizer = new SpeechRecognizer({
    lang: 'zh-CN',
    onResult: (text, isFinal) => console.log(text),
    onError: (error) => console.error(error),
    onStart: () => console.log('开始'),
    onEnd: () => console.log('结束')
});

recognizer.start();
recognizer.stop();
```

## 配置选项

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `lang` | `'zh-CN'` | 识别语言 |
| `continuous` | 桌面 true, Android false | 连续识别 |
| `interimResults` | 桌面 true, Android false | 临时结果 |

## 文件结构

```
speech/
├── speech-recognizer.js  # 核心模块
├── index.html            # 测试页面
└── README.md
```

## 测试

访问 `https://localhost:4443/speech/index.html`
