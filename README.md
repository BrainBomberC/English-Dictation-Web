# English Listening Repeater

英语听力复读练习网站。上传英文视频，自动按完整句子分割音频，逐句重复播放，集成 AI 辅助学习（解释句子、关键词、语法分析）。

![1785515165173](images/README/1785515165173.png)

<p align="center">图1 — 项目原理图</p>

![1785513699917](images/README/1785513699917.png)

<p align="center">图2 — 网页图1</p>

![1785515137589](images/README/1785515137589.png)

## 快速启动

```bash
cd D:\EnglishWeb
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

浏览器访问 `http://127.0.0.1:8001`。局域网内其他设备访问 `http://<本机IP>:8001`。

## 项目结构

```text
EnglishWeb/
├── app/                        # 后端
│   ├── main.py                 # FastAPI 入口，路由注册
│   ├── config.py               # 配置：视频源目录、服务端口
│   └── api/
│       └── routes.py           # 全部 API + 句子重分段算法
├── static/
│   └── js/
│       └── app.js              # 前端：播放器、复读逻辑、AI 聊天
├── templates/
│   └── index.html              # ChatGPT 风格 UI 模板
├── data/
│   └── projects/               # 视频缓存数据
├── downloads/input/            # 视频源文件存放目录
├── assets/                     # README 图片
├── .venv/                      # Python 虚拟环境
├── requirements.txt            # Python 依赖
├── .env                        # 环境变量
└── README.md
```

### 核心文件说明


| 文件                   | 作用                                                          |
| ---------------------- | ------------------------------------------------------------- |
| `app/main.py`          | FastAPI 应用入口，挂载静态文件、注册路由、配置中间件          |
| `app/config.py`        | 所有可配置参数：视频源目录列表、服务地址、端口                |
| `app/api/routes.py`    | **核心后端** — 视频扫描、句子重分段、视频/字幕服务、API 配置 |
| `static/js/app.js`     | **核心前端** — 播放器控制、句子列表渲染、重复逻辑、AI 对话   |
| `templates/index.html` | 全站唯一页面，ChatGPT 风格三栏布局（视频列表\|播放器\|AI）    |

---

## 添加视频

将处理好的文件放入一个目录，文件命名必须一致：

```text
YourDir/
├── VideoName.mp4      # 视频文件（必需）
├── VideoName.json     # Whisper 转录 JSON
├── VideoName.txt      # 纯文本（按句号正确断句）
└── VideoName.srt      # 字幕（可选）
```

然后在 `app/config.py` 的 `DEFAULT_SOURCES` 中添加该目录路径，刷新网页即可。

---

## 视频处理流程

```text
MP4 视频
  │
  ├─ ffmpeg 提取音频 → 16kHz WAV
  │
  ▼
Whisper large-v3 (HuggingFace: ggml-large-v3.bin)
  │  输出: result.json (transcription 数组 + offsets 毫秒时间戳)
  │        result.srt  (字幕文件)
  │
  ├─ 人工整理: result.txt (按句号正确断句)
  │
  ▼
网站后端 resegment_sentences() (app/api/routes.py)
  │  合并相邻 Whisper 段 → 完整句子
  │  时间戳 = 首段.from ~ 末段.to
  │
  ▼
前端播放器 (static/js/app.js)
  │  video.currentTime = segment.start
  │  timeupdate → currentTime >= segment.end → pause → repeat
  │
  ▼
逐句复读 + AI 辅助学习
```

### Whisper 输出格式

Whisper 将音频按停顿切分为原子段（通常 200~400 段），每段包含文本和毫秒时间戳：

```json
{
  "transcription": [
    {
      "text": " All right, we're going to introduce you guys...",
      "offsets": { "from": 0,     "to": 4560 },
      "timestamps": { "from": "00:00:00,000", "to": "00:00:04,560" }
    },
    {
      "text": " So for a lot of you, this will be the first time...",
      "offsets": { "from": 4800,  "to": 8460 }
    }
  ]
}
```

`offsets.from` / `offsets.to` 是毫秒级音频时间戳，由 Whisper 直接对齐音频得出，**精确可靠**。

### 核心算法：句子重分段

Whisper 按音频停顿切段，不按语义。例如：

```text
段4: [11.8s-16.2s] "And so there's a couple of important concepts
                     that you'll want to kind of understand"
段5: [16.2s-22.3s] "and some sort of basic terminology..."
```

这两个段实际是一个完整句子，需要合并。

算法 (`app/api/routes.py` → `resegment_sentences()`)：

```python
# 1. 加载 Whisper 原子段（不可拆分的最小单位）
segments = data["transcription"]

# 2. 加载目标句子（来自 .txt）
targets = re.split(r'(?<=[.!?])\s+', txt)

# 3. 对每个目标句子，搜索最佳原子段范围
for sent in targets:
    for i in range(pos-1, pos+8):      # 搜索窗口
        for j in range(i, i+5):         # 尝试合并 1~5 个连续段
            acc = seg[i] + ... + seg[j]  # 拼接文本
            overlap = 匹配词数 / 目标词数
            if overlap > best_score:
                best_s, best_e = i, j

    # 4. 时间戳 = 首段.from ~ 末段.to（精确，不估算）
    start = segments[best_s]["offsets"]["from"] / 1000
    end   = segments[best_e]["offsets"]["to"]   / 1000
```

### 设计原则：只合并，不拆分

**每个 Whisper 原子段有且仅有 `from`/`to` 两个时间戳，不存在段内每个词的单独时间戳。**

- ✅ **合并**：选段 4+5 → `start = 段4.from, end = 段5.to` → 时间戳精确
- ❌ **拆分**：把段 4 切成两半 → 分割点时间只能估算 → 音频必然错位

因此算法**永远只合并相邻原子段，绝不分拆**。时间戳的精度完全来自 Whisper 原始输出。

### 已知局限

1. 词重叠匹配对长句可能遗漏后半段（搜索窗口 `±8` 不够）
2. 依赖 `.txt` 的断句质量
3. 纯文本匹配无语义判断

---

## AI 助手

右侧面板集成 AI 辅助学习，兼容任意 OpenAI 格式 API：

- **Explain** — 用中文解释当前句子含义
- **Words** — 提取关键词和短语的中文释义
- **Grammar** — 分析语法结构

配置方式：

- **API URL**：填入兼容 OpenAI 格式的地址（如 `http://localhost:8000/v1`、`https://api.deepseek.com/v1`）
- **API Key**：可选，云端 API 需填入，本地 API 留空

---

## 技术栈


| 层       | 技术                                 |
| -------- | ------------------------------------ |
| 后端框架 | FastAPI + uvicorn                    |
| 前端     | 原生 JavaScript（无框架）            |
| 样式     | CSS 变量 + 深色/浅色主题             |
| 视频处理 | Whisper large-v3 (HuggingFace)       |
| AI 接口  | OpenAI-compatible`/chat/completions` |
| 部署     | 单文件 Python 进程，局域网即可用     |
