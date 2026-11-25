# B 批 Python / 本地 ASR：安装与组件验证记录

日期：2026-11-25（Asia/Shanghai）。用户明确批准「B 批下载、核验后本地安装」。**批准的 127 项已下载、检查并在项目独立环境禁网安装；完整环境仍缺未批准的 chardet，阶段 1 视频闭环未完成。不是可运行成品或发布验收。**

## 1. 授权、固定输入和实际安装

- 起始代码提交：`9b7ef312f6af41c7a1093017585072f9af6e1af6`；业务源码仍为锁定的 AutoClip 1.6.1 基线。本批只增加包检查工具、合成归档测试和脱敏文档，没有修改原依赖 pin、业务功能或原始资源。
- 授权边界为 [原申请清单](PYTHON_DEPENDENCIES_PROPOSAL_2026-11-25.json) 的精确文件；该 JSON 保留申请时状态，文件内容未改，SHA256 为 `4646f1c1c3bf9eb9c595e019b0b5ff79e812f469bc26c66687c2346621847067`。当前批准/执行状态以本回执为准。
- 实得 **127 个文件、100,952,102 bytes**，126 个 wheel、1 个 sdist；全部大小/SHA256 与批准清单一致。只从固定的 HTTPS `files.pythonhosted.org` 下载，不登录、不使用环境代理、不换镜像。
- 首次下载有两个 socket 超时及一个过慢连接，保留部分文件；修正 Python 3.9 的 `socket.timeout` 捕获后按同一 URL 续传并复核，复用 124 个已验证文件后完成。首轮约 616 秒、续传约 7.46 秒，另有检查时间；没有把续传耗时当成整批耗时。
- 使用 A 批 CPython 3.13.13 创建 `.local/environments/autoclip-b-2026-11-25/`。离线安装先准备批准的 setuptools/wheel，再构建已审查的 pysrt，最后安装其余固定本地 wheel；总计核对到批准的 127 个已安装版本，另有解释器原带 pip。未改变系统 Python、全局 PATH/pip，未安装服务或开机项。
- pip 全程 `--no-index --no-deps --no-cache-dir --require-hashes`，sdist 构建另用 `--no-build-isolation`，不让解析器或构建后端补下载。安装报告的 125 个后续来源均为 `file://`，先装的两个构建包另有命令记录。

## 2. 包内检查、安装行为与许可缺口

`scripts/local_first_package_audit.py` 只读取固定归档并写入忽略的本地报告，**不联网、不安装、不执行归档代码**。本次检查路径穿越、重复条目、链接/特殊文件、解压体积、wheel CRC/外层 RECORD 哈希和大小、外层 METADATA/WHEEL 一致性，全部 127 个归档通过。126 个实际 wheel 的有效依赖/extra 约束及本机平台 tag 无额外缺项或版本冲突；sdist 必须另外审查，不能以 PyPI JSON 代替。

- 留存 **199 个 LICENSE/NOTICE/版权/作者相关文件**及元数据、入口和启动钩子。它是技术尽调，不是安全、法律或最终再分发合格证明。
- `coverage` 的 `.pth` 仅在 coverage 专用环境变量存在时启动覆盖率；本轮空环境没有这些变量。`setuptools` 的 `.pth` 安装其 distutils shim。检查到的 wheel 没有 `.data/scripts/` 文件；控制台入口由 pip 在独立环境内生成，并不等于运行了这些命令。
- 唯一 sdist `pysrt==1.1.2`：检查 `setup.py/setup.cfg/MANIFEST.in/PKG-INFO`，构建脚本只导入 sys/setuptools、读取 README 并调用 setup，未见自定义联网/外部命令。使用批准的工具禁网构建出的 wheel 为 13,502 bytes，SHA256 `33dd64d7d4bf6256bd91060a46e94bb459e83c02cd0e10ab8adf3cb6cb6b7753`，元数据声明 GPLv3 和运行依赖 `chardet`。
- **四项没有独立包内许可正文**：CTranslate2、flatbuffers、tokenizers、pysrt。前三项分别与对应版本上游的 [MIT](https://github.com/OpenNMT/CTranslate2/blob/v4.9.1/LICENSE)、[Apache-2.0](https://github.com/google/flatbuffers/blob/v25.12.19/LICENSE)、[Apache-2.0](https://github.com/huggingface/tokenizers/blob/v0.23.3/LICENSE) 声明核对；pysrt 的源码/元数据声明 [GPLv3](https://github.com/byroot/pysrt/blob/master/setup.py)。缺失正文、原生/静态第三方 notices、对应源码和最终分发义务仍须在打包前补齐，不能称为全部 MIT 或全部授权审核完成。
- PyAV 的 BSD 声明不覆盖所有内含组件。实际 wheel 带 libav、libx264/libx265 等；libav 二进制许可字符串为 LGPL version 3 or later，完整原生构建许可/源码对应关系仍需补核。其 [上游也明确说明 wheel 内含 FFmpeg 库](https://github.com/PyAV-Org/PyAV/blob/v18.1.1/docs/overview/installation.rst)。**这些库不是独立 ffmpeg/ffprobe 可执行程序**，没有借此宣称原版渲染依赖已备齐。
- 发现已批准 wheel 内含 `faster_whisper/assets/silero_vad_v6.onnx`（1,245,151 bytes）及 ONNXRuntime 三个示例模型。它们随固定包取得，没有另外下载。本轮不启用 VAD 或这些示例模型；内含权重的逐版本来源/补充 notices 仍属打包检查项。
- 原版云 SDK 和 Sentry 仅作为原依赖存在，不授权云调用/遥测/凭据读取；严格本地产品路由尚未实现，不能直接解除隔离运行原版。

## 3. 实际验证结果

以下命令、输入脚本、stdout/stderr、退出码、耗时、归档和运行时哈希均保存在本机忽略目录；没有把原始日志、模型输出或音频同步 GitHub。

| 检查 | 本次实际结果 |
| --- | --- |
| 127 个原文件大小/SHA256、归档与 RECORD 检查 | 通过；全量归档检查命令约 0.824 秒，退出 0 |
| 包检查工具合成回归 | 7 项通过。真实 setuptools 带嵌套 vendor dist-info，初版误判；先复现测试失败，再改为只识别外层元数据且继续验证全部 payload 的 RECORD，修复后通过 |
| 项目独立 venv、离线构建/安装 | 均退出 0；pysrt 构建命令约 0.850 秒，后续固定 wheel 安装约 2.209 秒 |
| `python -m pip --isolated check` | **退出 1**：`pysrt 1.1.2 requires chardet, which is not installed.` 没有忽略此失败或自动补包 |
| 原版 `python -B -m backend.cli --help` | **退出 0**，约 4.272 秒；此前缺 SQLAlchemy 退出 1 的阻塞解除。不是导入/出片成功 |
| 原生/核心组件导入 | NumPy、PyAV、CTranslate2、ONNXRuntime、faster-whisper、pysrt、FastAPI、SQLAlchemy、WebRTC VAD、Pillow 共 10 项实际导入通过；冷启动约 37.96 秒（其中 PyAV 首次约 32.61 秒）。不代表每个 SDK 的全部 API 已兼容 |
| 原源码隔离观察探针 | 9 项再次通过；仍观察到诊断默认允许媒体、原本地 URL 判断过宽等风险，不是安全修复已通过 |
| 统一质量脚本的 contracts 局部组 | `quality_gate.py --group contracts` 的 5 个检查通过，其中 203 个工具/合同测试（已包含新增 7 项）；命令约 8.893 秒，退出 0。首次 deny-all 策略拦下本机 mock 监听，退出 1；改用已验证的回环策略重跑通过，仍禁止非回环。测试中的模拟云失败/缺命令提示属于负例，不是实际云调用或全套前端检查 |
| 原版 OpenAI 兼容适配器 → 本机 Ollama/Qwen | 实际调用未修改的 `OpenAIProvider.call`，只有一个本机 `/v1/chat/completions` 请求；合成四句 ID 返回通过，结论前置、条件 ID 保留，约 5.914 秒。不是完整分析流程或内容质量验收 |
| 原版关键文件 | 公开基线清单的 27 个关键文件 SHA256 均不变；没有用户素材，不能写成用户素材哈希验收通过 |
| 完整质量门禁/前端/桌面/视频/抓包与人工观看 | **未完成**；未为此安装清单外 Ruff、Node、FFmpeg 等依赖。不宣称候选源码、发布或应用离线验收通过 |

工具测试绑定本批脚本内容：包检查工具 SHA256 `0a1b2452dbda14a5798bce1a54dc602c11f076f09b64858a3e94fc9044f789de`，合成测试 SHA256 `c69b5d72c9fbd59a23288d15d5e5ad7b2c702c18da28ca32d38ec87b6ec24356`。contracts 报告绑定起始提交及 `tracked_changes=true`；这不是对后来文档提交或整个产品做全门禁背书。

### 真实本地 ASR，但输入是自制合成语音

确认系统已有 Li-MuCompact 1.2.0 声音资源后，使用本机 `say` 从自写非敏感中文文字生成 AIFF，没有下载声音模型或使用私人录音。以已批准 Whisper small 的本地目录、`local_files_only=True`、CPU/int8、2 线程运行实际转写，`word_timestamps=True`、`vad_filter=False`，并使 VAD 加载函数一旦被调用就失败，确认本次没有启用内含 VAD。

- 音频 765,950 bytes、17.275601 秒；SHA256 `a061027db090a599235a36fa9731d692ec98130434fa68532dd273ac7fc9d972`。
- 模型加载约 **0.909 秒**、实际推理约 **2.952 秒**；整条命令约 5.218 秒，峰值进程 RSS **1,072,332,800 bytes**（约 1.0 GiB）。不是长课程、并行渲染或所有机器的速度保证。
- 本样本 67 个参考字符，去标点/空白后的字错率 **0.0**；输出 1 个 segment、61 个模型词单位，时间非负、不越界、不倒退。合成语音很干净，**不能推断真实访谈/口播也准确**。
- 未取得人工标注的句/词边界，**时间精度未测量**；单个长 segment 不能冒充优质句级字幕。普通词单位也不是可靠说话人识别。
- 音频和模型 SHA256 前后相同。原始 cues/词概率仅在本地；真实素材质量、不确定词复核、字幕修订历史、整条应用的转写路径仍待验收。

测试结束另复核 A 的原始 Qwen GGUF 和 Whisper model.bin，均仍与批准的原始 SHA256 一致，未用缓存副本的哈希冒充原件复核。

## 4. 隔离、失败恢复和停止

- 构建、安装、导入、ASR 均由 `sandbox-exec` 的 deny-network 策略限制，配合全新 HOME、`env -i`、HF 离线/禁隐式 token/禁遥测选项。原生导入进程内，对非回环 IPv4/IPv6 测试地址的 socket 均实际返回 EPERM。
- 文件策略禁止读取/写入项目外的真实 HOME、钥匙串文件及 securityd；用本任务自建的无敏感内容文件验证项目读写允许、HOME 外部文件及指向它的项目符号链接读取拒绝、外部文件写入拒绝。未通过读取真实账号资料“测试”。
- 模型兼容测试使用单独的回环策略，核对 `127.0.0.1:19783` 的监听 PID 为本次 Ollama，关闭 cloud、禁重定向，HTTP 请求钩子核验端口/路径，客户端超时 30 秒且 SDK 重试 0。复用 A 的已校验模型缓存，不 pull。
- 初次复用缓存时 `/api/tags` 为空，尝试本地注册实际报 `lstat` HOME 祖先路径 EPERM；不是模型或云服务失效。只增加三个祖先目录的**元数据读取**，不开放目录列举、账号文件内容或网络，再通过外部文件/符号链接拒绝测试；重试原缓存后成功，无须重新下载/注册。诊断脚本曾有一次语法错误，修正后重跑；所有失败退出记录保留。
- 原适配器导入还实际尝试本机 Redis 6379，连接拒绝后使用本地 SQLite 进度库。这是本地进度存储降级，**不是云模型回退**；未安装 Redis，严格版后续应明确固定本地进度后端，避免访问既有服务。
- 临时模型服务退出 0，端口已无监听；下载、pip、ASR 测试进程均已结束。未留下本任务常驻应用或模型服务，不停止无关系统/用户服务。

这只是本次受控进程的限制和组件测试。**尚未完成全应用网络抓包、物理断网完整闭环、旧配置迁移、异常恢复和人工观看成片**；不能将环境开关、EPERM 探针或代码扫描说成“整个软件绝不外传”。

## 5. 本机位置、使用边界与后续

| 忽略目录 | 内容 |
| --- | --- |
| `.local/dependencies/b-2026-11-25/originals/` | 127 个原始下载文件，未修改；约 108 MiB 的磁盘占用 |
| `.local/environments/autoclip-b-2026-11-25/` | 仅本项目 Python 环境，约 351 MiB；不建议解除隔离运行带私人素材的原版 |
| `.local/preparation/b-2026-11-25/` | 授权绑定、完整包/许可清单、构建 wheel、pip 报告、网络/账号拒绝检查、合成音频/ASR、适配器输入输出、隔离数据和失败记录 |
| `.local/audit/2026-11-25/` | 本批 `b-*` / `approved-b-*` 命令、stdout/stderr、退出状态、耗时 |

检查工具可以在项目根目录用已准备解释器运行（不执行包代码）；原始报告只能放 `.local`：

```sh
.local/environments/autoclip-b-2026-11-25/bin/python -I -S -B \
  scripts/local_first_package_audit.py \
  --manifest docs/local-first/PYTHON_DEPENDENCIES_PROPOSAL_2026-11-25.json \
  --archives .local/dependencies/b-2026-11-25/originals \
  --report-dir .local/preparation/b-2026-11-25/recheck
```

无需修改或回滚原版业务代码。本环境可单独清理，先确认没有该环境的运行进程，再只移除本任务的独立 venv；重建可复用原始 wheel、本地构建 wheel 及哈希清单，仍须禁网/无依赖解析。不要 `git clean`、删原模型/原素材、卸载系统 Python 或清理钥匙串。原版基线分支继续保留。

**下一项明确缺口**：源码/构建后元数据都要求 `chardet`，原提案漏列；本次 `pip check` 已真实确认。已提出单独确认 [chardet 5.2.0](https://pypi.org/project/chardet/5.2.0/) 的纯 Python wheel：199,385 bytes、LGPL 声明、来源 PyPI/files.pythonhosted.org、SHA256 `e1cf59446890a00105fe7b7912492ea04b6e6f06d4b742b2c788469e34c82970`，仅用于字幕编码检测，元数据没有额外依赖。**尚未获批、未下载/安装**；通过该项后仍要重新 `pip check` 与实际字幕解码验证，不能预写通过。

独立 FFmpeg/ffprobe、授权真实视频、阶段 1 导入→本地转写→本地分析→导出、三段重组与字幕精度、审核界面/安全边界、断网/中断恢复及人工观看仍待完成。现有组件足以继续做隔离开发，不足以交付成品。GitHub 仅保存本批审阅后的源码/测试/脱敏文档，分支/提交/远程核对见根目录 `GITHUB_SYNC_STATUS.md`。
