# IndexTTS2 Natural Voice Studio

一个面向普通用户的 IndexTTS-2.5 本地网页工作台，重点解决“音色像，但说话仍像念稿”的问题。

项目来自真实的人耳 A/B 校准过程，保留了有效的部分：整段连贯生成、独立音色与情绪参考、64 段真人情绪库、语义匹配、逐段语速与停顿控制，以及听感反馈记录。

> 本项目没有训练或发布新的模型权重。这里的“训练”指人耳校准、情绪参考库建设和韵律策略迭代。模型能力仍来自官方 IndexTTS-2.5。

## 功能

- 简单的三步式中文网页界面
- 音色参考与情绪参考解耦
- 关闭随机采样，便于稳定比较
- 8 维手动情绪向量
- 文本自动情绪模式
- 按句识别陈述、转折、提醒、强调、收尾等表达
- 可编辑的语速、句前停顿和句后停顿
- 真人带读增强：用真人节奏和情绪驱动已授权音色
- 64 段真人情绪库录制与语义检索
- 自然度 A/B 校准和本地偏好记录
- 串行推理，避免四条任务并行挤占显存
- 仅监听 `127.0.0.1`，上传音频保留在本机

## 当前适用环境

- Apple Silicon Mac
- macOS
- IndexTTS-2.5
- PyTorch MPS

应用会拒绝自动回退到 CPU。其他平台尚未验证。

当前公开版已在 IndexTTS `v2.5.0-3-gee40fa7` 上完成测试；上游后续接口变化可能需要适配。

## 不包含的内容

为保护授权人与减少仓库体积，本仓库不包含：

- IndexTTS-2.5 模型权重
- 任何真人音色或情绪录音
- 作者本地生成的音频
- 缓存、虚拟环境和测试历史
- 私人偏好评分数据

你必须使用自己有权使用的参考音频。

## 安装

1. 克隆本项目：

```bash
git clone https://github.com/tbohu/indextts2-natural-voice-studio.git
cd indextts2-natural-voice-studio
```

2. 按 [IndexTTS 官方仓库](https://github.com/index-tts/index-tts) 的当前说明安装 IndexTTS-2.5，并取得官方模型文件。推荐目录结构：

```text
indextts2-natural-voice-studio/
├── vendor/index-tts/             # 官方代码与其虚拟环境
└── models/IndexTTS-2.5/          # 官方模型，必须包含 config.yaml
```

3. 如果你的目录不同，复制并修改本地配置：

```bash
cp .env.example .env.local
```

4. 在 IndexTTS 的虚拟环境中补齐网页工作台需要的轻量依赖：

```bash
vendor/index-tts/.venv/bin/python3 -m pip install -r requirements-ui.txt
```

5. 启动：

```bash
chmod +x scripts/start_macos.command
./scripts/start_macos.command
```

浏览器将打开 `http://127.0.0.1:7865/`。

## 最短使用流程

1. 输入文案。
2. 上传 8–15 秒、单人、稳定、干净的已授权音色参考。
3. 选择情绪方式。
4. 顺序生成并试听。

建议先从“自然连贯”开始。需要更强的语义韵律时，再录制页面顶部的 64 段真人情绪库，并使用 `0.60` 左右的情绪强度作为起点。

## 参考音频建议

音色参考用于回答“谁在说话”：

- 8–15 秒
- 单人、自然说话
- 音色稳定
- 背景干净
- 情绪不过强

情绪参考用于回答“怎么说”：

- 与音色参考可以是不同片段
- 有自然停顿、重音、快慢和高低音变化
- 不要降噪、EQ、压缩、变速或修改音高

## 真人情绪库

仓库包含录音稿：

[`assets/emotion_library/IndexTTS财经口播情绪库录音稿.docx`](assets/emotion_library/IndexTTS财经口播情绪库录音稿.docx)

网页会逐条显示 64 个表演单元。每条录音只保存在本地的 `assets/emotion_library/recordings/`，该目录已被 Git 忽略，不会自动提交到仓库。

情绪库主要面向中文财经、商业和产业新闻口播；其他题材可以工作，但语义标签可能不够完整。

## 配置

| 变量 | 作用 | 默认位置 |
| --- | --- | --- |
| `INDEXTTS_REPO` | 官方 IndexTTS 代码目录 | `vendor/index-tts` |
| `INDEXTTS_MODEL_DIR` | IndexTTS-2.5 模型目录 | `models/IndexTTS-2.5` |
| `INDEXTTS_PYTHON` | 启动使用的 Python | `vendor/index-tts/.venv/bin/python3` |
| `INDEXTTS_DEFAULT_VOICE` | 可选默认音色 | 无 |
| `INDEXTTS_DEFAULT_EMOTION` | 可选默认情绪参考 | 无 |
| `INDEXTTS_BASELINE_AUDIO` | 可选人耳对比基线 | 无 |

默认音频没有配置时，直接在网页上传即可。

## 测试

```bash
PYTHONPATH=. vendor/index-tts/.venv/bin/python3 -m unittest discover -s tests -v
```

单元测试不需要加载模型，也不会使用真实声音。

## 已知限制

- “像真人”必须由人耳确认，声学指标只能排查异常，不能代替听感。
- 逐句生成可能增强控制，但也可能产生拼接感或音色漂移。
- 真人带读和情绪库依赖录音质量与表达一致性。
- 当前路径和资源监控针对 macOS 优化。
- 本项目不能保证任何文本、声音或用途具有合规授权。

## 安全与授权

- 仅克隆你本人或已取得明确授权的声音。
- 不得用于冒充、欺诈、误导、诽谤或规避身份验证。
- 生成内容应在适用场景中明确标注为 AI 合成。
- 金融、医疗、法律等高风险内容需要独立事实核验与人工审核。

## 许可证

本仓库自行编写的网页工作台代码使用 [MIT License](LICENSE)。

IndexTTS-2.5 代码、模型和衍生使用仍受上游的 [bilibili Model Use License Agreement](LICENSE-INDEXTTS) 与 [免责声明](DISCLAIMER-INDEXTTS) 约束。请特别阅读其商业规模门槛、下游分发、合规和高风险用途条款。

本项目不是 Bilibili 或 IndexTTS 官方项目，也未获得其背书。完整第三方说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

