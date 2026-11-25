# GitHub 同步状态

更新日期：2026-11-25（Asia/Shanghai）。

- 最新批次：**FFmpeg 准备、网络排障、素材组件验证回执已本地完成，待提交/推送核验**。上批 HEAD `c22e9cb01d6be0cb1c7c1cc00fcbe893e990f175` 已同步；本批不能在远程确认前称为同步完成。
- 仓库：https://github.com/a244970978-bot/autoclip 。已重新核实账号 `a244970978-bot`、public fork 的 parent/source 均为 `zhouxiaoka/autoclip`，未将第三方项目另建“原创仓库”。
- 分支：`codex/local-first-audit-reviewed`；上游基线：`2f511907a72cbfbb8d86639f6441967b826acb6a`。一个 worktree、无 submodule、修改前干净且与远程一致，保留其他本地分支，不强推/改历史/删分支。
- 本批改动：新增脱敏的 FFmpeg/网络/素材组件回执，更新 AGENTS、技术核查、批准状态和阶段门槛。本批没有修改上游业务源码或依赖 pin，没有发布应用，也没有渲染未经审核的真实素材。
- 实测：两个批准的 9.0.2 ZIP 均匹配原 pin，arm64/签名完整性/GPL-3.0-or-later 与禁网探针通过；原版 Studio 对自制色卡的三段乱序及横/竖版 fit 实际导出通过机器检查。授权素材完成真实本地 ASR、全音视频解码和本机 Qwen/ClipFinder 分析，原件/副本哈希保持不变；两个连续候选只在私有目录等待人工审核，不是已通过的重组成片。词级数据本次不可用，人工精度/语义/观看、完整断网应用/恢复与发行许可仍待验证。
- 局部源码合同：首次全禁网络使本机 mock server bind 得 EPERM，整体退出 1，失败证据保留；改用仅回环 profile 后，5 项检查/203 个测试通过，约 8.649 秒。报告绑定 `c22e9cb`、`tracked_changes=true`；27 个关键原版文件哈希不变。不是完整源码门禁/应用发布验收。提交后 clean-tree 复验尚待执行。
- 推送安全：本轮远程刷新成功；已重新读取 Actions permissions，仍明确 `enabled=false`，分支保护为未配置、rulesets/environments/deployments/hooks 均空，Pages 未启用。只推上述 codex 分支，不推 main/标签、不运行发布/部署。API 和 Git 均有间歇 TLS 失败，采用有界重试，没有禁用证书验证、改全局代理或切换来源。
- 排除：只同步六个审阅后的开发文档/状态文件；不加入原素材及其名称/路径/哈希、转写/分析/候选方案原文、账号凭据、权重、venv、缓存、数据库、安装包、测试媒体或原始日志。忽略规则已核对。fork 继承的上游公开历史未进行穷尽审计，本次保存不是媒体/业务/整机备份。
- 后续：正常提交、推送并核对远程 SHA 和六个文件 blob；失败则在此保留“待推送”及原因，下次优先补办。成功后本文件可引用已同步的前一内容提交避免自指，最终 HEAD 另核对。

之前 A/B/chardet 的回执与同步记录保留在 Git 历史及 `docs/local-first/`。本地初稿因自动作者元数据含机器信息而未推送，该分支完整保留；当前分支使用已核实账号的 noreply 作者身份。
