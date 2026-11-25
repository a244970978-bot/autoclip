# chardet 单项补充：已安装，依赖缺口已解除

2026-11-25（Asia/Shanghai）。用户明确批准「补充这一项」。**chardet 5.2.0 已核验并禁网安装；`pip check` 通过。不是完整应用、出片或隐私验收通过。**

## 1. 实际取得与安装

- 起始源码提交 `e93a0bfc85ff05e6a292d70a3f3c2253aa80e0ab`；仍为 AutoClip 1.6.1 的独立开发分支。本轮未修改业务源码、第三方代码、原依赖 pin 或原始素材。
- 只有 [单项固定清单](CHARDET_SUPPLEMENT_2026-11-25.json) 中的 `chardet-5.2.0-py3-none-any.whl`，**199,385 bytes**，来自 PyPI/files.pythonhosted.org。SHA256 为 `e1cf59446890a00105fe7b7912492ea04b6e6f06d4b742b2c788469e34c82970`，下载及测试结束复核均相同。原 B 批 127 文件清单保持不变；现在共核对到 **128 个批准的 Python 分发版本**（另有解释器原带 pip）。
- 复用已验证的包检查工具：56 个归档文件、路径/链接/CRC/RECORD、元数据检查通过。无额外依赖、原生库、启动 `.pth` 或独立模型资产；有 `chardetect` 控制台入口，安装不等于自动执行该入口。
- 包内保留完整 LGPL 2.1 正文；Python 源码声明 LGPL 2.1 或更高版本。PyPI 泛称 LGPL，不能将此包说成 MIT。原样留存声明，最终打包的再分发义务仍另行检查。[上游固定版本元数据](https://pypi.org/project/chardet/5.2.0/)
- 检查入口/import 及定向的联网/进程/凭据读取模式，未发现额外安装脚本或构建后端需要执行；这不是完整恶意代码或法律审计。
- 只装入原项目独立 venv：`.local/environments/autoclip-b-2026-11-25/`。使用精确本地 wheel、`--no-index --no-deps --no-cache-dir --no-compile --require-hashes`，pip 报告仅有一个 `file://` 来源。没有全局安装、修改系统 PATH、登录、模型下载或常驻服务。

## 2. 实际验证与必须纠正的功能描述

| 检查 | 观察结果 |
| --- | --- |
| 补装前 `pip check` | 退出 1：pysrt 1.1.2 requires chardet, which is not installed |
| 下载与完整性 | 固定文件大小/SHA256 一致；下载命令约 8.062 秒 |
| 包检查与禁网安装 | 均退出 0；包检查约 0.151 秒，安装约 0.430 秒 |
| 补装后 `pip check` | **退出 0：No broken requirements found.** 约 0.347 秒 |
| 128 个批准分发版本 | 全部与 B 清单 + 本单项版本一致，没有隐式升级其他包 |
| 原版 CLI help | 退出 0，约 0.625 秒；不是视频处理成功 |
| contracts 局部组 | 在起始提交干净工作区实际运行 5 个检查、203 个工具/合同测试，通过；命令约 12.104 秒。不是完整质量门禁/桌面验收 |
| 原版关键源文件 | 基线的 27 个关键 SHA256 保持不变 |

**修正此前“用于 pysrt 识别字幕编码”的笼统描述**：pysrt 1.1.2 虽在构建元数据声明 chardet 依赖，但其 `SubRipFile._detect_encoding()` 实际只检查 BOM，随后使用默认 UTF-8，chardet 接入仍是 TODO。本次补装解除的是依赖声明缺口，**没有自动新增各入口的编码识别功能**。

使用自写、非敏感、三条字幕的四份 SRT（不是用户转写、视频或 ASR 输出）运行原函数：

| 夹具编码 | chardet 本样本判断 | `pysrt.open` 默认 | 原 `TextProcessor.parse_srt` | Studio `inspect_subtitles` |
| --- | --- | --- | --- | --- |
| UTF-8 无 BOM | UTF-8 / 0.99 | 正确 | 正确 | available |
| UTF-8 BOM | UTF-8-SIG / 1.0 | 正确 | 正确 | available |
| UTF-16 BOM | UTF-16 / 1.0 | 正确 | 正确 | available |
| GB18030（字符为 GB2312 子集） | GB2312 / 0.99 | **UnicodeDecodeError** | 原有 gb18030 回退正确 | **invalid** |

- 显式提供原编码，以及将本样本 detector 结果**显式传入** pysrt 时，四份均保持原文字和三段毫秒时间；另存 UTF-8 后回读一致，原四份 SHA256 不变。
- 原 TextProcessor 已有编码回退，Studio 的这条有限读取路径没有相同回退；这是原版入口差异，不是本次新实现。不将单一函数的通过推广为“所有字幕格式/中文编码都支持”。GB18030 四字节扩展字符未测，检测置信度也不等于准确率。
- Studio 证据函数只借用一个**不存在的视频路径**定位旁边的 SRT；没有创建占位视频、导入或导出成片。以上是字幕组件检查，不是语音时间精度、完整视频或语义验收。
- 后续最小可行路线：保留原文件，明确编码选择/复核后生成 UTF-8 副本并记录来源；或在统一导入边界实现经过验证的解码与失败提示。当前未改这些产品行为，也没有静默吞字/覆盖原件。

## 3. 隔离、原始证据和恢复

构建/安装/组件验证使用已核实的 OS 禁网策略、空继承环境和新隔离 HOME，不读取其他应用账号。字幕测试进程内对非回环 IPv4/IPv6 的连接均实际得到 EPERM；contracts 的本机 mock 单独用受限回环策略。不能用这些探针替代全应用抓包/真正断网闭环。

- 原 wheel：`.local/dependencies/chardet-5.2.0/originals/`。
- 授权绑定、完整包/许可/入口检查、pip 报告、四份合成 SRT、原函数结果、128 版本核对及恢复记录：`.local/preparation/chardet-2026-11-25/`。
- 实际命令、输入对应脚本、stdout/stderr、退出状态、耗时：`.local/audit/2026-11-25/chardet-*`，全部不入 GitHub。
- 已在**额外的独立环境**离线安装同一 wheel、执行 `python -m pip --isolated uninstall -y chardet`、确认该副本无法再找到 chardet；原工作环境另检查仍为 5.2.0。没有为恢复测试卸载最终产物。
- 若以后撤销本补充，只在上述专用工作 venv、沿用安装时的 sandbox/隔离环境执行同类卸载；会恢复 pysrt 的缺依赖状态，不应称为应用可用。重新安装可直接复用已校验 wheel 与本地哈希清单，无须联网。不要操作系统 Python、原素材、其他项目或钥匙串。

## 4. 下一步仍受下载与验收边界约束

**没有下载/执行独立 FFmpeg 或 ffprobe。** 先前 Python 请求 403/连接失败的问题保留；本轮同一固定地址使用 curl HEAD 实得 200，取得大小，并 GET 了小型公开 SHA256/版本文本，两份发布者哈希与原项目 pin 一致。未 GET ZIP 二进制；HEAD 成功不等于实际下载、签名、许可、功能或出片通过。

下一项 [两文件冻结提案](FFMPEG_DOWNLOAD_PROPOSAL_2026-11-25.json)：仍是原版固定的 **9.0.2 arm64**，不自动换成现站的 9.1 或旧 GitHub 备选。ffmpeg ZIP 28,395,699 bytes，ffprobe ZIP 28,317,701 bytes，共 **56,713,400 bytes（约 56.71 MB）**，来自原项目指定的第三方构建站，不冒称 FFmpeg 官方二进制。发布者 [该固定构建的配置](https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/versions.txt) 启用 GPL/version3，按 GPLv3 及第三方声明核验；批准后还须实际检查二进制许可、签名/架构、编译项、字幕滤镜、解码/编码和资源占用。[FFmpeg 官方说明其只提供源码](https://ffmpeg.org/download.html)，[许可随编译项改变](https://ffmpeg.org/legal.html)。

本提案已另行请求确认，**仍未获批**，不顺带安装 PKG、其他工具链或模型。还需授权非敏感测试视频；真实闭环、三段重组、字幕/音画、严格本地功能、异常恢复、网络观测及人工观看均未完成。先前 ASR/LLM 组件结果保留原版本与时间，没有写成本轮重新完成全流程。
