# swe-mux

[English](README.md) | [简体中文](README.zh-CN.md)

**在同一个工作区运行所有编程智能体。无论坐在电脑前还是拿着手机，都能知道哪些会话需要你处理。**

swe-mux 是面向编程智能体的终端复用器。
它在你自己的计算机上，通过真实终端运行 Claude Code、Codex、pi、oh-my-pi、opencode 或任意 Shell，显示哪个会话需要你处理以及原因，并通过你自己的 Tailscale 网络将整个工作区带到手机上。
项目采用 Apache-2.0 开源许可证，无需 swe-mux 账号，不使用中继，也不收集使用遥测。
Windows 提供原生桌面应用，Linux 和 macOS 使用浏览器，手机端可安装为 Web 应用。

[![swe-mux 工作区中的智能体和预览窗格](site/img/showcase-panes.webp?v=e8ebca109a98)](https://swemux.dev/demo/)

[试用交互式演示](https://swemux.dev/demo/)，或[阅读安装指南（英文）](https://swemux.dev/docs/install/)。
演示使用真实界面和模拟智能体活动，无需连接模型提供商。

手机端：答复等待中的智能体，然后打开它正在构建的应用。视频长 50 秒，录制自演示。

https://github.com/user-attachments/assets/87d1c12c-8cfc-4632-a3bf-8d8c11f364a5

桌面与手机协同、智能体请求其他智能体，以及合入队列。视频长 85 秒，录制自演示。

https://github.com/user-attachments/assets/1f964770-bee4-4823-a341-4c123b40605b

## 功能

- **知道哪个智能体需要你，以及原因。**
  所有 CLI 共用一套状态：工作中、空闲、等待你处理。
  状态根据智能体钩子、对话记录、终端和进程树判断，不只依赖屏幕内容。
  等待状态会说明是审批、问题、限流还是登录。
  主智能体空闲但子智能体或后台任务仍在运行时，会与工作已完成的会话区分显示。
  提醒和推送通知包含会话名称及原因，可送达锁屏手机，并遵守免打扰时段。
- **从手机工作。**
  手机运行同一个工作区，包含实时终端、触控按键栏、审批、Git 差异审查、文件、笔记、提示词队列和推送。
  可从浏览器安装为 Web 应用，通过你自己的 Tailscale 网络访问计算机，无需应用商店或中继。
- **在手机上打开开发服务器。**
  主机 `127.0.0.1` 上的服务器可作为桌面或手机的预览窗格打开。
  swe-mux 代理热重载和 WebSocket，无需暴露额外端口。
  点击智能体输出的 URL 即可打开。
- **在同一位置运行各种智能体 CLI。**
  支持识别 Claude Code、Codex、pi、oh-my-pi 和 opencode；其他 CLI 或 Shell 也能在真实终端中运行。
  在任意窗格输入 `claude` 或 `codex`，即可原地转为受跟踪的会话。
  可以恢复会话、从任意消息处分叉 Claude 对话、重启智能体并继续同一对话，以及一键切换已保存的 Claude 和 Codex 登录。
- **查看智能体加载和执行了什么。**
  智能体标签页列出 Claude Code 或 Codex 会话可见的工具、技能、MCP 服务器、插件和钩子，并标记启动后发生的配置变化。
  可集中阅读 CLAUDE.md、AGENTS.md 和 Claude 记忆，跟踪对话记录，并同时搜索所有智能体的历史对话。
- **了解配额用在了哪里。**
  每个已保存的 Claude 和 Codex 账号都显示 5 小时及每周配额窗口。
  用量区分为 swe-mux 会话使用、会话之外的账号使用，以及其他设备使用，并提供历史记录和重置提醒。
- **找出智能体遗留的进程。**
  每个进程和监听端口都关联到启动它的会话。
  会标记会话结束后仍在运行、脱离会话进程树，或超过 90% CPU / 2 GiB 内存的进程，并提供终止进程树操作。
  不会自动终止任何进程。
  侧边栏显示活动会话、CPU 和内存。
- **让会话协调工作。**
  在运行中的轮次后排入提示词，让 Codex 会话向 Claude 会话提问，并在你批准的检查通过后合入已完成分支。
- **重启和更新时保留会话。**
  独立监督进程持有每个终端，swe-mux 重启期间仍可输入。
- **保留熟悉的操作习惯。**
  各智能体输入框使用相同编辑按键，提供 tmux、VS Code、Vim 和 Emacs 工作区键位预设，以及图像粘贴、设备端听写和朗读。

## 界面语言

同一个应用和安装程序同时包含英文与简体中文。
首次设置时，或在 **设置 → 常规 → 语言** 中，选择 **English** 或 **简体中文**。
尚未保存选择时，界面跟随受支持的浏览器语言；无法匹配时使用英文。
保存的选择同时适用于桌面和手机，不会重启会话或丢失草稿。

语言切换涵盖 swe-mux 控件、帮助、状态标签和应用消息。
终端输出、智能体回复、你自己的内容、命令语法和语音命令短语保持原来的语言。

## 运行位置与数据

智能体 CLI 在真实终端中运行，使用你现有的订阅。
数据保存在你自己的计算机上，无需 swe-mux 账号，没有供应商运营的后端，也没有使用遥测。
智能体提供商、可选集成和更新检查仍会访问网络。
助手使用你配置的模型端点和预算；本地语音识别是独立功能。

独立监督进程在守护进程重启和应用更新期间保留会话。
监督进程终止或断电会结束活动进程；冷恢复可还原可阅读、可继续的记录。
自动化和智能体控制需要明确的项目权限。
剪贴板历史记录应用内的文本复制，默认仅保存在内存中，启用持久化后才会落盘。

## 安装

swe-mux 已发布到 PyPI。
wheel 包是纯 Python 包，包含构建好的前端，因此安装无需 Node，也无需检出源码。

```sh
# 推荐：隔离环境，并将所有命令加入全局 PATH。
uv tool install swe-mux
swe-mux  # Windows：打开桌面应用和托盘

# 不使用 uv 时，可用同样隔离且加入 PATH 的安装方式。
pipx install swe-mux
swe-mux  # Windows：打开桌面应用和托盘
```

Windows 上完成以上步骤即可：运行 `swe-mux`，会打开原生窗口并显示托盘图标，无需控制台或额外安装。
引导设置提供开始菜单、桌面及登录时启动快捷方式，并检测已配置的项目。
设置、界面导览和可选入门步骤始终可从侧边栏「用量」上方的「入门指南」访问。

其他平台，或希望只使用浏览器时，运行 `swemux start`。
它会将守护进程放入后台，并在 <http://127.0.0.1:8765> 开始提供服务后返回。
关闭终端不会停止它；`swemuxd --shutdown` 会停止守护进程及其会话。
如果希望在前台查看日志，可直接运行 `swemuxd`。
`swemux doctor` 提供只读健康报告，涵盖守护进程、监督进程、前端构建、检测到的智能体 CLI、tailnet 监听和后台循环。

每次安装提供三个命令，分别对应一个程序：**`swemux`**（CLI）、**`swemuxd`**（守护进程）和 **`swe-mux`**（桌面窗口及托盘）。
项目有意不提供短别名：至少有一个无关工具也使用 `mux`，提供同名启动器会制造冲突。

从 v0.1.2 起，[发布页面](https://github.com/jatoran/swe-mux/releases)提供 **Windows 安装程序**和便携压缩包。
安装程序**没有代码签名**，首次运行时 SmartScreen 会警告；通过 PyPI 安装则不会出现该提示。

**任何 Python 安装方式都不会自动创建桌面快捷方式。**
wheel 没有安装后钩子，`uv` 只会显示自身的安装摘要。
在 Windows 上启动 `swe-mux`，或运行 `swemuxd` 使用浏览器界面，即可开始设置。
`swemux install-shortcut --startup` 也可直接创建开始菜单、桌面及登录时启动快捷方式。
使用「帮助 → 重新开始设置」或 `swemux setup --restart`，可选择保留设置，或先备份再重新设置全局偏好。
若要隔离测试首次使用流程，`swemuxd --new-user-profile test-one` 会使用独立数据目录和独立本地端口。

从源码运行、升级、PATH 故障排查及卸载：参见[运行与维护指南（英文）](.docs/development/OPERATOR_LIFECYCLE.md)。

### 要求

- Python 3.12+，以及用于安装的 uv 或 pipx。
- 至少一个已安装并登录的智能体 CLI。swe-mux 不负责安装、管理或代理它们。
- 可选 Tailscale，用于从手机访问守护进程。
- **只有从源码构建前端时**才需要 Node 22.6+；发布的 wheel 已包含前端。

设备端语音是可选功能，只有你明确点击后才会获取。
相关库和模型下载一次，并根据固定哈希校验；你提出请求前不会下载。
**两种支持的语音转文本配置都在你自己的计算机上解码**：faster-whisper 或 Windows 语音识别。
没有云端语音路径，也不会回退到浏览器；缺少引擎时，转录会返回带类型的错误，不会将音频发送到其他位置。
文本转语音同样如此，唯一例外是明确标为实验性的 Edge TTS 提供商，它会访问外部服务，并要求先确认披露说明。

## 首次使用

引导设置依次为 **使用模式 → 智能体 → 项目 → 键位**。
选择智能体后，近期项目文件夹会在后台出现。
可以筛选并选择这些文件夹，也可以手动添加。
点击「开始工作」会启动选定的 Shell 或智能体；权限、模型、语音、桌面、手机和界面导览等可选步骤仍可在「入门指南」中访问。

创建项目并指向现有文件夹。
`Ctrl+Shift+Enter` 在项目根目录打开终端，`Ctrl+Shift+P` 或 `F1` 打开命令面板。
只有你提出请求后才会启动进程。

然后照常输入 `claude`、`codex` 或其他 CLI 命令。
swe-mux 将自己的启动器放在该终端 PATH 的最前面，因此普通命令会将当前终端原地提升为智能体会话。
窗格和回滚缓冲保持不变，同时增加对话记录、状态、队列和上下文用量。

「运行」菜单可启动智能体、Shell、工作树会话或导入的任务。
导入任务包括 VS Code 任务、`package.json` 脚本和 `.swe-mux/actions.toml`。
只有其确切的当前内容受信任后才可运行。
参见[项目操作（英文）](.docs/design/features/project-actions.md)。

## 平台支持

每次推送时，CI 都会在 Windows、Linux 和 macOS 上构建、验证 wheel 并进行安装冒烟检查，还会从源码检出启动真实守护进程，验证 Shell 服务和正常退出。
目前没有 CI 作业从**已发布的产物**启动守护进程，验证范围到此为止。

- **Windows 10/11 是主要验证平台。**
  完整检查在此运行，包括真实 ConPTY 集成和 Playwright 渲染器测试；原生桌面应用目前也仅在此平台发布。
- **Linux 使用无界面守护进程和浏览器**，有必需的 CI 检查。
  原生 Linux 桌面应用和安装程序仍在规划中，当前界面通过浏览器使用。
- **macOS 使用守护进程和浏览器客户端**，有必需的 CI 检查，包括源码守护进程测试。

各项支持声明的依据见[跨平台验证记录（英文）](.docs/development/CROSS_PLATFORM_FINDINGS.md)。

## 远程访问

守护进程监听 localhost 和此计算机检测到的 Tailscale IPv4 地址；`swemuxd --local-only` 可限制为本地访问。
**Tailscale 策略就是访问边界**。
swe-mux 没有单独的远程登录，因此你的策略允许接入的 tailnet 对等设备拥有终端和代码执行权限。

浏览器会限制纯 HTTP 下的剪贴板和麦克风。
如需这些功能，可在前面配置 Tailscale Serve：`tailscale serve --bg http://127.0.0.1:8765`。
不支持 `0.0.0.0`、直接绑定局域网、Funnel、端口转发或公网入口。
参见[远程访问说明（英文）](.docs/design/features/remote-access.md)。

## 配置与数据

配置文件是数据目录中的 `config.toml`。
Windows 默认目录为 `~/.mux`；Linux 为 `$XDG_DATA_HOME/swe-mux`，未设置时使用 `~/.local/share/swe-mux`；macOS 为 `~/Library/Application Support/swe-mux`。
已有的 `~/.mux` 始终优先，`MUX_DATA_DIR` 可覆盖所有默认选择。

## 提供商账号

「设置 → 账号」可保存 Claude 和 Codex 登录，跟踪各账号的订阅窗口，并一键切换系统级登录。
只复制身份验证信息。

此功能用于**一个人在自己拥有并付费的账号之间切换**，代替提供商 CLI 原本要求的退出和登录流程。
它不是账号池，也不是绕过用量限制的方法。
账号不会在人与人之间共享，凭据保存在本地，仅发送到提供商自身的端点；会话不会进行负载均衡，切换始终由用户明确发起。
参见[范围与条款（英文）](.docs/design/features/provider-accounts.md)。

## 文档

已发布的英文文档：<https://swemux.dev/docs/>。
维护中的设计约定从[系统概览](.docs/design/00_OVERVIEW.md)开始，[文档路由](.docs/CLAUDE.md)将各子系统指向负责说明它的文档。

## 参与贡献

欢迎贡献。
功能建议请提交到 [Discussions 的 Ideas 分类](https://github.com/jatoran/swe-mux/discussions/categories/ideas)，点赞即投票，得票最高的开放建议会显示在[路线图](https://swemux.dev/roadmap/)中。
[Issues](https://github.com/jatoran/swe-mux/issues) 用于报告错误。
请描述问题，而非只提出修复方案。

[`CONTRIBUTING.md`](CONTRIBUTING.md) 说明变更需要满足的要求；[`CLAUDE.md`](CLAUDE.md) 说明开发规则；安全漏洞请按 [`SECURITY.md`](SECURITY.md) 报告。

## 许可证

swe-mux 使用 [Apache License 2.0](LICENSE)。
署名信息见 [`NOTICE`](NOTICE)，许可证中商标保留条款允许和不允许的行为见 [`TRADEMARK.md`](TRADEMARK.md)。

贡献通过 [DCO 签署](CONTRIBUTING.md) 提交，即 `git commit -s`，不要求 CLA。
CLA 会允许项目日后将你的贡献改用其他许可证；DCO 不会，这是有意作出的选择。

随 swe-mux 再分发的第三方软件列在 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)。
该文件由 `packaging/license_audit.py` 根据锁文件生成，以避免偏差。
swe-mux 不分发 GPL 或 AGPL 代码；`pystray` 以 LGPL 许可、可替换源码的形式包含在程序包中。

### 与智能体供应商无隶属关系

swe-mux 启动和观察其他供应商发布的编程智能体 CLI，包括 Anthropic 的 Claude Code 和 OpenAI 的 Codex CLI。
它**不隶属于 Anthropic、OpenAI 或其他相关供应商，也未获得其认可、赞助或认证**。
使用这些名称仅为说明某项功能适用于哪个工具。
你使用自己的账号，并遵守与各供应商的协议运行这些 CLI；可选的 OpenRouter 和 Hugging Face 集成也一样。

可选的 Edge TTS 集成有所不同：上游客户端使用 Microsoft Edge 面向消费者的朗读端点，无需 API 密钥，也没有公开的第三方服务合同，因此选择它前必须明确确认披露说明。
该客户端使用 LGPL，仅在独立的受管理 Python 或操作者提供的 Python 中运行，不包含在冻结应用包中。
这一软件边界本身并不能解决 Microsoft 服务条款方面的问题。

