# 最小环境下载提案与批准状态

首次核查时（2026-11-25）只访问源码、文档、GitHub/Hugging Face/PyPI 元数据与 HEAD，没有安装运行依赖或下载权重。随后用户明确回复「批准这四项下载」：**下列 A 四项已下载并通过大小与哈希校验**；Python/Ollama 在项目私有目录解压并完成隔离测试，未全局安装。当前实际结果见 [准备与验证记录](PREPARATION_RECEIPT_2026-11-25.md)。源码 checkout 自带的公开资源不等于新增安装或已授权测试素材。

## A. 已获明确批准并已准备的四项

来源为官方项目/模型发布者；下载会向其托管站及对应资产 CDN 发出文件请求，暴露常规 IP/请求元数据；不发送素材、字幕、抽样画面、提示词或现有应用凭据。下载使用独立本地目录，不登录账号，不运行 `curl | sh`，不添加系统后台服务。

| 项目 / 固定版本 | 大小（bytes） | 授权与用途 |
| --- | ---: | --- |
| [portable CPython 3.13.13 / 20260510 arm64](https://github.com/astral-sh/python-build-standalone/releases/tag/20260510) | 25,186,753（压缩包） | Python PSF 许可及包内第三方声明，需保留实际包内 notices；独立解释器，不替换系统 Python。版本和 SHA 已与原项目 pin、发布元数据核对。 |
| [Ollama v0.40.18 CLI darwin](https://github.com/ollama/ollama/releases/tag/v0.40.18) | 188,838,885（tgz） | 主项目 MIT，分发组件另核 notices；仅开回环本地推理，不安装桌面自动更新程序、不登录、不使用 cloud。 |
| [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) | 486,212,372（四个核心文件） | MIT；本地中文转写，模型本体 483,546,902 bytes，另含 tokenizer/vocabulary/config。 |
| [Qwen3-8B 官方 Q4_K_M GGUF](https://huggingface.co/Qwen/Qwen3-8B-GGUF) | 5,027,783,488 | Apache-2.0；本地中文内容分析。导入下载的本地文件，不通过模型名称另行自动 pull。不保证小模型选材/结构化质量。 |

四项已知核心文件合计 **5,728,021,498 bytes，约 5.73 GB / 5.33 GiB**，另有极小许可证文件/传输开销；解压、导入去重/副本、缓存会增加磁盘占用。建议首批准备留出至少 15 GiB 安装/模型预算，另留视频原件、临时片段与导出空间；这不是耗时或最终安装体积保证。

固定来源与校验：

- Python 文件 `cpython-3.13.13+20260510-aarch64-apple-darwin-install_only.tar.gz`；SHA256 `1ad1ed518447005d4b6dfa16d4f847d45790e17e94e30164a0a6e6c79a99730f`。
- Ollama 文件 `ollama-darwin.tgz`；发布元数据已核对版本、体积和 digest，SHA256 `015261f434d31d7f0781f8ba6b59cc8e1f70ecb562a4e6db44dfdd5e02f7e562`；下载后必须实际比对，不改用未知站点。
- Whisper revision `536b0662742c02347bc0e980a01041f333bce120`；`model.bin` SHA256 `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`；其他小文件以固定 revision 与取得后哈希记录。
- Qwen revision `7c41481f57cb95916b40956ab2f0b139b296d974`；`Qwen3-8B-Q4_K_M.gguf` SHA256 `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`。
- 只允许上述官方托管/资产 CDN；不自动使用 hf-mirror、ghproxy、清华或其他镜像。不可达/来源改变则记录并再确认。

**A 已准备不意味着应用已能运行。** 它只准备解释器/模型服务/权重；下列 B 缺项仍未获下载/安装批准，不能自动扩大安装范围。Qwen 的自制文本本机接口试跑已通过，但没有完成 AutoClip 的真实转写、素材分析或出片。

## B. 尚不能直接安装的缺项

| 项目 | 现有证据 / 下一步 |
| --- | --- |
| Python 运行依赖 + faster-whisper 运行时 | 已完成元数据级候选预览：127 个文件、100,952,102 bytes，均有声明许可和 SHA256，来源 PyPI / files.pythonhosted.org；[B 批提案](PYTHON_DEPENDENCIES_PROPOSAL_2026-11-25.md) 与逐文件 JSON 已列出。**未批准、未下载、未安装**；不是实际 pip/原生库兼容性证明。注意 `pysrt==1.1.2` 为 GPLv3 且仅 sdist，另含 GPL/MPL 传递依赖；先核包内许可/源码，再禁网构建，不自动下载额外构建依赖。 |
| FFmpeg + ffprobe arm64 | 原项目 pin 9.0.2 第三方站 `ffmpeg.martin-riedl.de` 的两个固定地址 HEAD 返回 403，大小和构建授权未核实，不在 A 内。可核对用户已有有权使用的二进制；否则对替代 arm64 发布物重新核对来源/大小/哈希/编译选项/许可证后申请下载。HEAD 失败不等于 GET 必然失败；本次没有下载来绕过确认。 |
| Node/npm/前端依赖 | UI 开发前再准备；源码 desktop pin 为 Node 20.20.2，而诊断测试另用 22.22.0，不能混为同一套环境。lock 479 项，含缺授权记录及 FSL Sentry CLI；无须为了首条本地 CLI 测试立即安装整个 UI/诊断服务工具链。 |
| Rust/Tauri | 当前阶段不构建桌面发行包，不下载。现有 Xcode CLT 不等于 Rust 已安装。 |

暂不下载 SenseVoice/PyTorch、多个 ASR 大模型、4B/14B LLM、视觉模型、whisper.cpp 工具链、浏览器自动化、Redis/Docker 或任何云组件。现有 Python 项目含 SDK 依赖不代表授权调用其云服务。

## C. 使用时约束与恢复

- 模型/依赖准备完成后才对应用及模型服务进行完整出站限制测试；不能用环境开关或供应商隐私声明替代抓包/断网验证。[Ollama 禁云说明](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)
- 不上传用户真实视频供 Codex 分析；后续如确有需要，必须另说清将发给模型的具体材料并获准。开发者可先用自制合成文字做协议测试，但它不替代真实 ASR/成片验收。
- 全部下载/运行数据留在忽略目录，记录来源、哈希、耗时、失败原因；失败不静默换云/换源。未批准的部分继续只核查。
- 本次只在 `.local/prepared/` 解压已批准的 Python/Ollama，并临时运行隔离测试；没有系统安装、全局 PATH/pip 修改、登录或后台服务注册。临时模型服务已停止；清理边界和实际测试记录见准备记录，不删除原素材、其他项目环境、已有模型或钥匙串。
- 不购买订阅、不注册付费 API/云资源；无云调用费不等于没有本机硬件、电力、空间和维护成本。
