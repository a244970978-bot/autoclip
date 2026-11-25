# AutoClip 严格本地版：首次技术核查

核查日期：2026-11-25（Asia/Shanghai）。**技术核查已完成；阶段 1 最小出片闭环未完成，不能交付为可运行版。**

## 简短结论

| 项目 | 结论 |
| --- | --- |
| 已有可复用能力 | 本地文件导入、faster-whisper / SenseVoice、本地 OpenAI 兼容模型接入、Studio 多镜头草稿、排序/区间编辑、逐段字幕映射、多段 FFmpeg 拼接、横竖版、安全区、取消/中断与成品校验代码。除下文隔离探针外，均属**源码确认，真实运行待验证**。 |
| 需要新增能力 | 严格本机/禁止外传运行配置；以字幕证据 ID 为基础的中文内容策略；保留条件/否定/对象上下文的复核；逐段原话审核及精确版本批准；字幕纠错修订历史；独立导出重组 SRT 与审核方案。 |
| 主要风险 | **诊断默认允许上传媒体片段**；“本地”判断包含局域网；外部字体/更新/模板/统计等联网入口；小模型结构化选材不稳定；词级时间不等于真实精确；依赖授权不是全 MIT；运行依赖缺失。 |
| 第一版范围 | 单个本地视频、连续/多区间两种模式、四种策略、审核后渲染、原画幅 + 9:16 fit/留边、MP4 + SRT + JSON 本地输出；先验证 MP4/H.264/AAC，不承诺尚未测试的格式。先不做账号、网页下载、直播、云服务、自动发布、可靠说话人识别或生成式旁白。 |

## 1. 来源与基线

- 原仓库：[zhouxiaoka/autoclip](https://github.com/zhouxiaoka/autoclip)。本地开始时为空工作区、未出生的 `main`，无远程、无已有提交；未发现 submodule 或其他 worktree。
- 锁定 `main`：`2f511907a72cbfbb8d86639f6441967b826acb6a`，提交时间 2026-11-25 14:33:28 +08:00。代码版本 `1.6.1`。
- 正式标签 `v1.6.1` 解引用为 `55f39be5cc9780e5ac57521fb71ee12f3e008ded`，不是上述提交；二者源码 tree 均为 `80a3d9964bbe8cf0ae5c34b2ea0c4e5b1bdd8afe`。**未下载、安装或测试正式安装包。**
- 工作分支 `codex/local-first-audit-reviewed`；另保留本地只读用途的基线分支 `codex/upstream-baseline-2f51190`。未打发布标签、未修改上游。
- 采用真正的 [个人 fork](https://github.com/a244970978-bot/autoclip)，GitHub 元数据已核对 `fork=true`、parent/source 为原仓库；不是另建“原创项目”。公开 fork 只同步审阅后的源码和脱敏开发文档，素材/原始证据不入仓。实际同步结果看根目录 `GITHUB_SYNC_STATUS.md`。
- [MIT 原许可证](https://github.com/zhouxiaoka/autoclip/blob/2f511907a72cbfbb8d86639f6441967b826acb6a/LICENSE) 保持不变，版权为 AutoClip Team。许可证 SHA256 为 `dac7c1396ebc3de77b3d2d9dafe396f6b6d03752ea9b74a270cb81ceea911ade`。
- 原始 2,179 个已跟踪文件已建立本地 SHA256 清单；公开基线清单见 `upstream-baseline.json`。原始源代码、内置素材没有功能性改动。本批只增加核查工具/文档、输入排除规则与执行约束。

## 2. 必须区分的两条剪辑路径

### 传统流水线：主要仍是连续区间

`backend/pipeline/step6_video.py` 按每条 clip 的起止时间生成成片。`studio/jobs.py:862–929` 的 `_drafts_from_content_clips` 为每条内容候选创建 `scenes=[scene]`。`subtitle_promo.py:25–64` 基于已有连续候选生成标题/开头文字，明确不改变镜头起止范围。**这不等于按论点重组原声。**

### Studio：已经具备多区间编辑与渲染骨架

- `models.py:41–64,200–225`：Scene 起止区间、Draft 的 1–30 个 scenes 与修订号。
- `backend/api/v1/studio.py:949–970`：按 `clip_ids` 的请求顺序建立多个 Scene，校验片段属于该项目。
- `StudioEditor.tsx:192–204,228–255`：保存、原片预览、修改起止、替换、上移/下移、删除、显式导出按钮。
- `studio/render.py:325–338,510–529`：按草稿顺序逐段裁切，逐段截取字幕，再 concat；音频使用 PCM 中间段，最后统一编码 AAC。不是只改提示词。
- `packaging_render.py:49–56,179–199`：按渲染器 30fps 对齐后的时长累计输出 offset，字幕从原片时间转到段内/成片时间。
- `render.py:549–590`：非空/尺寸/时长检查、全片解码验证、完成后原子替换、finally 清理 partial/ready；存在取消与重启恢复实现。**这些保障尚未在本机实测。**

9 项隔离探针中的三段乱序夹具为 `[40,44] → [2,8] → [20,24]`，输出 offset 实测为 `0 → 4 → 10` 秒，总计 14 秒。它只证明原函数的时间计算，**没有生成视频，也不证明音画/字幕质量合格**。

## 3. 转写、本地模型与字幕复用

| 能力 | 源码证据与限制 |
| --- | --- |
| faster-whisper | `whisper_runtime.py:40–48` 固定 faster-whisper 1.2.1；`speech_recognizer.py:659–775` 请求 `word_timestamps=True`，写 SRT 和词级 sidecar。默认 CPU/int8；不是 Apple Metal ASR 路径。模型缺失时可能由 manager 发起下载，严格版必须改为批准后的准备步骤，运行时缺模型就报错。 |
| SenseVoiceSmall | `sensevoice_worker.py:44–76`：离线环境开关、已准备模型本地路径、CPU、禁用更新、`trust_remote_code=False`、请求时间戳；`sensevoice_alignment.py` 严格核对 CTC 文本/时间，不完整就拒绝。模型授权为自定义模型协议，不能称为 Apache-2.0；本机未安装/实测，第一批不选。 |
| 时间精度 | 有时间字段不等于真实精确。词边界、句尾、中文专名、中英混说、长停顿、否定与限定词仍需带人工参考的偏差测试。探针确认无时间/倒退/NaN 的合成结果会被拒绝，不代表真实识别误差已测。 |
| 字幕纠错 | `word_timing.py` 用 SRT 哈希绑定词级 sidecar；探针证实修改字幕后旧词级数据失效。`models.py:10–18,55–58,80–83` 明确丢弃一批已撤回的字幕纠错字段。旧 `subtitle_editor.py` 主要支持按字幕删除；`subtitle_processor.py:119–125` 有**均分词时间**逻辑，不能拿它当可靠词级识别。完整纠错/原时间戳/修订日志需要新增。 |
| 说话人 | 设置字段或包装姓名牌不是可靠 diarization。本地 Whisper/SenseVoice 路线未核实可靠说话人身份识别，第一版明确不宣称支持。 |
| 本地 LLM | `local_presets.py` 将 Ollama/LM Studio 转为 OpenAI 兼容 endpoint。属于可复用接入，而非本机已可用：未发现对应应用/模型缓存，已查端口 11434/1234/8080 无监听。本机真实中文分析、JSON 合规、上下文容量、速度均待测。 |
| 小模型风险 | `weak_local_timeline.py` 专门提示不超过 8B 的已知弱本地时间线模型；不能因为模型能聊天就认为能可靠选材。优先让模型返回已有字幕/句子 ID，由程序查表生成时间，不允许模型自由编造时间戳。 |

## 4. 联网、上传与付费面核查

下表是已发现的生产入口，不声称已穷尽所有传递依赖。**本次没有运行带真实素材的应用，没有实际上传，也没有抓包验收。**

| 路径 | 行为 / 严格本地版处理要求 |
| --- | --- |
| `services/diagnostics/consent.py:55–57` | 未设置选择时 `diagnostics=true, media=true, full_original=false`。隔离探针实际确认。`observe.py:23–75` 对失败/部分完成任务排队诊断；`upload.py:48,107–117` 有远端默认接收站及媒体角色检查。**必须停用入队、唤醒与上传，不能只藏设置按钮。** |
| `frontend/src/analytics/posthog.ts:385–447` | 配置采集 key 且未 opt-out 时启用统计/远端 flags；不是严格默认关闭。禁用初始化/传输并本地固定功能配置。 |
| `frontend/src/desktop/sentry.ts`、`backend/core/sentry_setup.py`、Rust telemetry | 条件启用错误报告；已有脱敏不等于不联网。严格版不配置采集信息且禁止传输，覆盖 worker/子进程与升级旧配置。 |
| `frontend/src/index.css:2` | `@import` Google Fonts；断网可能回退字体但仍会尝试外联。改为已授权本地字体/系统字体，不在启动时下载。 |
| `desktop/updater.ts`、`UpdatePrompt.tsx`、`src-tauri/tauri.conf.json` | 上游版本检查/更新；严格版关闭自动检查，不指向原版更新通道。 |
| `studio/templates/catalog.py:43,179`、`sponsor_promo_client.py` | 远程模板目录/推广配置；后者有启用条件。使用本地固定资源，不在核心流程后台请求。 |
| `llm_providers.py` / `llm_manager.py` / `studio/intelligence.py` | 按配置可向云模型发送字幕、分析输入或抽样帧；接口可能计费。只接受经验证的本机服务，云路由在入口和传输层双重拒绝。 |
| `cloud_transcription.py`、`core/image_providers.py` | 云 ASR / 生图路径，可能上传音频/内容并计费；第一版完全禁用。 |
| `utils/speech_recognizer.py:1128–1182` | 显式选择本地 Whisper 时默认禁 fallback；但遗留 `auto` 分支仍会在可用服务中选云 ASR。未发现“明确本地选择失败后必然转云”的证据，也不能因此认为所有入口都安全。严格版统一拒绝云路由及自动选择云。 |
| `whisper_model_manager.py` / `pip_install.py` / `sensevoice_runtime.py` / framing / packaging | PyPI、Hugging Face、镜像与可选运行时下载入口。转写缺模型也可能触发准备；必须与离线运行分离，来源与体积改变不能静默切换。 |
| `upload_post_publisher.py`、任务发布队列、下载器、Twitch/OBS | 投稿会上传视频；链接下载、浏览器 cookies、直播后台维护也有外联/凭据风险。第一版禁用这些入口和后台服务，不读取其他应用账号。 |

### “localhost”不能作为隐私验收

`llm_providers.py:329–349` 的 `is_local_url()` 为绕开代理而设计，接受 LAN/private/link-local IP、`0.0.0.0`、`host.docker.internal` 和 `.local` 名称。`local_presets.py` 也允许覆盖预设 base URL。这不是严格本机安全策略。

需要独立策略：仅批准的数值回环地址/端口，拒绝 URL 用户信息、远端/局域网、重定向、环境代理和未知模型；核对监听进程及模型文件来源，并限制该模型进程外联（防止本地代理转云）。现有 `LocalOriginGuard` 主要保护后端**入站**来源，不能代替出站限制。

## 5. 本机与候选模型

实测：Apple M5，10 核 CPU / 10 核 GPU，24 GiB 统一内存，macOS 26.6 arm64，Metal 4；当前卷可用约 87 GiB。只确认硬件能力，没有运行 GPU 推理或编码性能测试。

- PATH 中 Python 3.9.6，低于项目 `>=3.10`；未找到 node/npm/ffmpeg/ffprobe/ollama/uv/cargo/rustc/cmake。额外检查常见命令目录无命中；不等于穷尽全盘所有工具。
- 系统 Python 元数据未发现所需的 FastAPI/SQLAlchemy/Pydantic/ASR 等抽查包；常见 Ollama/HF/AutoClip ASR 缓存目录不存在；Xcode Command Line Tools 已有。
- 原版隔离 `python3 -B -m backend.cli --help` 实际退出 **1**：`ModuleNotFoundError: No module named 'sqlalchemy'`。未启动成功，更未完成导入/转写/分析/导出。

候选大小已查询官方发布者文件元数据，未下载。内存为工程估算，不是本机测量值；不得将其他硬件基准当成本机速度。

| 候选 | 权重体积（十进制） | 授权 / 预计限制 |
| --- | --- | --- |
| faster-whisper small | 核心文件约 486 MB | MIT；复用成本最低，CPU/int8，预计约 1–3 GiB 运行占用；中文准确率/速度/词边界待测，优先作最小闭环。 |
| faster-whisper medium | 核心文件约 1.53 GB | MIT；CPU 代价更高，预计 3–6 GiB，只有 small 实测不达标再考虑。 |
| SenseVoiceSmall + VAD | 约 936 MB + 1.72 MB，另有 PyTorch/FunASR | SenseVoice 自定义模型协议、VAD Apache-2.0；不是把 FunASR 代码许可套给权重。准备体积更大，暂缓。 |
| Qwen3-8B Q4_K_M | 5,027,783,488 bytes | 官方 GGUF，Apache-2.0；4K–8K 上下文、串行分块下预计约 7–11 GiB，需实测结构化选材，不能保证质量。首选验证候选。 |
| Qwen3-4B Q4_K_M | 2,497,280,256 bytes | Apache-2.0；内存较低，约 4–7 GiB，但语义/结构化风险更高，不作为无提示的替代。 |
| Qwen3-14B Q4_K_M | 9,001,752,960 bytes | Apache-2.0；预计约 12–18 GiB，24 GiB 机器并行 ASR/渲染易有压力；不是默认下载项，升级也不保证解决问题。 |

复用 Ollama 本机 OpenAI 兼容接口；若选它，应使用独立 CLI 服务、关闭 cloud/web-search、绑定回环、导入已校验的 GGUF，不登录、不依赖桌面自动更新。官方提供 `OLLAMA_NO_CLOUD=1`，**仍要实测网络隔离**。Apple Silicon 的 whisper.cpp/Metal 可作后续加速替代，但本基线没有该 ASR 接口，需增加适配与时间精度验收，不能说已支持。

## 6. 授权与供应链缺口

- 已核查 `requirements.txt` 32 个直接固定版本的 PyPI 授权元数据（一次 pydantic 查询超时，重试已成功）。其中 **pysrt 1.1.2 为 GPLv3**；其余包括 MIT、BSD、Apache-2.0、Pillow MIT-CMU 等。未安装、未审计全部传递依赖/实际包内 LICENSE；不将本工具所有组成部分宣称为 MIT。[PyPI](https://pypi.org/project/pysrt/1.1.2/)
- 前端 lock 有 479 条包记录，472 条带 license，7 条缺失；9 条 Sentry CLI 平台包标记 `FSL-1.1-MIT`，不能笼统称为宽松开源依赖。严格版无需 sourcemap 上传/该 CLI，后续删除对应构建链应重建 lock 并测试，当前没有擅改。
- 已见 Noto/Anton/Barlow/PressStart 的 OFL、YuNet MIT、词典 MIT 声明。内置演示视频不自动视为已获本次测试/再分发授权；本次未分析其字幕/音视频内容。优先使用明确授权或自制非敏感夹具。
- FFmpeg 上游许可取决于构建选项，不是 MIT。原项目固定的 arm64 FFmpeg/ffprobe 第三方下载地址 **HEAD 均返回 403**；未 GET 二进制，不能推断安装一定失败，也不能核实大小/最终构建许可。替代：先使用用户确认有权使用的现有二进制，或核实新的 arm64 构建来源、大小/哈希/许可证后再申请下载，不盲目装整套工具链。[FFmpeg 说明](https://ffmpeg.org/legal.html)
- 上游已有 `backend/dump.rdb`（88 bytes）。最小格式检查确认只有 5 条 AUX 元数据与 EOF、没有 keyspace 记录；本批不添加任何数据库备份。只浅取基线，未对上游完整历史作秘密审计；公开 fork 继承历史不代表历史全部安全。当前已跟踪文件的高置信凭据模式扫描无命中，不等于完整秘密审计。

## 7. 本次实际验证及仍待验收

本地原始命令、stdout、stderr、退出状态、耗时与哈希存放 `.local/audit/2026-11-25/`，**不入 GitHub**。前期只读浏览与检查由对话工具记录；持久命令日志从隔离环境/CLI 测试开始，不声称日志覆盖所有前期命令。

| 检查 | 实际结果 |
| --- | --- |
| 原版 CLI help，隔离 HOME/data/env，OS 禁网 | 退出 1，缺 SQLAlchemy；约 0.105 秒。Python 版本也不满足项目要求。 |
| `scripts/local_first_audit_probe.py`，OS 禁网 | 9 项通过；测试主体约 0.020 秒、命令约 0.249 秒。通过表示确认原函数行为，包含不安全的默认值；**不是安全门禁已通过**。 |
| OS 禁网试验架 | 对 TEST-NET 地址 socket connect 得 EPERM；退出 0，约 0.032 秒。只验证该测试子进程的禁网机制，不证明应用/模型已离线通过。 |
| 原版源文件 | 基线哈希已保存；业务代码与原始资源未改。没有用户测试视频，不能声称用户素材哈希验收已通过。 |
| 启动/真实 ASR/LLM/三段成片/字幕精度/音画/人工观看 | **全部待验证**。本次没有成片，也没有占位成片。 |
| 上游完整质量门禁、前端构建、桌面安装包 | **未运行**；没有为跑门禁而绕过下载确认安装依赖，不宣称候选源码/发布合格。 |
| 抓包/完整断网闭环/异常恢复 | **全部待验证**；没有将源码检索、空闲无流量或 sandbox 小测试当作素材不外传的证明。 |

本批无需停止应用：没有启动常驻应用/模型服务。恢复原版只需使用保留的基线分支/提交，不应在当前目录直接 `reset --hard` 或 `clean`。如需原版独立副本，先按宿主 worktree 规则创建并核对基线；保留本分支和本地证据。后续安装、启动/停止、卸载与进程清理在真实最小闭环后记录，现在不编造这些命令已可用。

## 8. 下一步与来源

下载边界和来源/体积见 [下载提案](DOWNLOAD_PROPOSAL.md)；程序级实施及逐阶段验收见 [实施与验收合同](IMPLEMENTATION_AND_ACCEPTANCE.md)。在获准准备最小环境前，保持不下载依赖/权重、不启动带素材的联网应用。

主要外部依据（2026-11-25 查询，均为原项目/发布者）：[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[Whisper small](https://huggingface.co/Systran/faster-whisper-small)、[Qwen 8B](https://huggingface.co/Qwen/Qwen3-8B-GGUF)、[Qwen 4B](https://huggingface.co/Qwen/Qwen3-4B-GGUF)、[Qwen 14B](https://huggingface.co/Qwen/Qwen3-14B-GGUF)、[SenseVoice 模型页](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)、[其链接的模型协议](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)、[Ollama 禁云配置](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)、[whisper.cpp Apple Silicon 路线](https://github.com/ggml-org/whisper.cpp)。许可证记录是技术尽调，不替代最终分发义务复核。
