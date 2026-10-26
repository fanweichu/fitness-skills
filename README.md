# Fitness Skills · 健身 Skills

两个可在本地使用的 AI 健身技能：制定居家训练方案、持续记录训练，以及用源视频证据复核动作与次数。**分享的是通用代码和方法，不包含任何真实用户档案、训练记录或视频。**

Two local AI fitness skills for home workout planning, durable training records and source-video exercise review. **This repository distributes reusable code and instructions, without real user profiles, training records or recordings.**

| Skill | 中文 | English |
| --- | --- | --- |
| `home-fitness-coach` | 训练方案、练后反馈、更正记录、离线训练页 | Plans, feedback, persistent corrections and offline workout pages |
| `analyze-workout-video` | 动作识别、逐组计数、左右侧证据、局部技术复核 | Exercise identification, per-set counts, working-side evidence and focused technique review |

## 安装 / Installation

**让 AI 安装：** 将本仓库链接交给支持 Skills 的 AI，并发送：

> 请读取这个仓库的 README.md 和 INSTALL.md，按说明安装两个 Skill。已有版本不同则保留并告知我。不要读取我的训练数据，也不要创建训练项目。

**Ask an AI to install:** Give a skill-capable assistant this repository's URL and send:

> Read README.md and INSTALL.md in this repository and install both skills. Preserve any differing existing installation and report the conflict. Do not access my training data or create a workout project.

**手动或自动化安装 / Manual or automated installation**

先下载或克隆本仓库，在仓库根目录运行。需要 Python 3.10+；安装器仅使用标准库，不联网、不安装额外软件、不覆盖已有修改。

Download or clone this repository, then run from its root. Requires Python 3.10+. The installer uses only the standard library, makes no network requests, installs no extra software and preserves existing changes.

```bash
python3 scripts/check_distribution.py
python3 install.py --dry-run
python3 install.py
```

默认安装到 `$CODEX_HOME/skills`，未设置时为 `~/.codex/skills`。相同内容重复安装会返回 `unchanged`。其他支持相同 Skill 格式的宿主可通过 `--dest` 指定安装目录；其他宿主未在此验证。

The default destination is `$CODEX_HOME/skills`, or `~/.codex/skills` when unset. Identical installations return `unchanged`. Use `--dest` for another host that supports this skill format; other hosts have not been verified here.

```bash
python3 install.py --dest /path/to/host/skills
```

Codex 用户也可以让内置 skill-installer 从本仓库安装以下两个相对路径：

Codex users can also ask the built-in skill-installer to install these two repository paths:

```text
skills/home-fitness-coach
skills/analyze-workout-video
```

完整的 AI 安装流程及依赖说明见 [INSTALL.md](INSTALL.md)。文字功能只需 Python；视频另需 FFmpeg/FFprobe、Pillow、NumPy，以及能查看图像的 AI。视频解码适用于 macOS/Linux；原生 Windows 不支持。核心页面可直接离线打开，YouTube 教学链接需联网。

See [INSTALL.md](INSTALL.md) for AI installation steps and dependencies. Text workflows need only Python. Video workflows also require FFmpeg/FFprobe, Pillow, NumPy and a vision-capable assistant. Video decoding targets macOS/Linux; native Windows is unsupported. Core pages open offline; YouTube tutorial links require an internet connection.

## 使用 / Usage

安装后，可直接向 AI 提出以下请求。实际目标、器械、时间和身体情况由使用者提供，没有预设个人画像。

After installation, ask the assistant naturally. Goals, equipment, available time and relevant physical constraints come from each user; no personal profile is bundled.

| 中文示例 | English example |
| --- | --- |
| 用 home-fitness-coach 帮我安排今天的居家训练，记录到我指定的私人目录。 | Use home-fitness-coach to plan today's home workout in my chosen private workspace. |
| 更新这次训练的实际完成情况，保留原方案。 | Record what I actually completed while preserving the original plan. |
| 用 analyze-workout-video 分析我提供的视频，核实每组次数和动作。 | Use analyze-workout-video to review my supplied video and verify each set. |
| 只复核这段动作的支撑是否稳定。 | Review only whether the support is stable in this interval. |

新建训练项目时使用仓库外的私人目录。方案与实际完成分开保存；结论更正会保留旧版本并同步记录。视频定位信号不作为次数证据；看不清的部分保留不确定性。工具负责组织与校验证据，不能替代 AI 对源帧的观察，也不提供医疗诊断。

Create training projects outside this repository. Planned and completed work remain separate; corrections preserve history and update records. Motion signals help locate movement but do not establish repetition counts. Unclear evidence remains unresolved. Scripts organize and validate evidence; they do not replace the assistant's source-frame inspection or provide medical diagnosis.

## 隐私与发布 / Privacy and publishing

- 仓库只收录通用 Skill、脚本、文档和合成测试；不收录个人信息、原片、截图、训练报告、聊天记录或本机权限配置。
- 运行产物可能包含敏感信息，必须留在独立私人目录。`.gitignore` 是防误提交措施，不是隐私保证。
- 发布前运行分发检查，并检查 `git diff --cached`。检查器按 `release-files.txt` 限制发布文件并扫描常见敏感信息模式；它不能证明任意新增内容都已脱敏。
- 如果创建 Git 提交，作者姓名和邮箱会成为公开元数据；请自行选择公开身份和 GitHub 提供的 noreply 邮箱。不要提交任何旧的私人仓库历史。

- Only reusable skills, scripts, documentation and synthetic tests are included. No personal information, source media, screenshots, training reports, conversations or machine permission settings are included.
- Generated artifacts can contain sensitive information and belong in a separate private directory. `.gitignore` reduces mistakes; it is not a privacy guarantee.
- Before publishing, run the distribution check and inspect `git diff --cached`. The checker restricts release files using `release-files.txt` and scans common sensitive patterns; it cannot establish that arbitrary new content is anonymized.
- Git commits expose author names and emails. Choose your public identity and GitHub-provided noreply email before committing. Never import private repository history.

准备发布时，只把这个独立目录推送到新建的空仓库，不要推送装有训练数据的父目录。先在 GitHub 创建空仓库并设置本仓库使用的公开提交身份，再执行以下命令。`REPOSITORY_URL` 替换为你自己的空仓库地址。

When publishing, push only this standalone directory to a new empty repository, never the parent training workspace. Create an empty GitHub repository and configure your public commit identity for this repository before committing. Replace `REPOSITORY_URL` with your empty repository URL.

```bash
git init -b main
python3 -B scripts/check_distribution.py
git add .
git diff --cached
git commit -m "Add portable fitness skills"
git remote add origin REPOSITORY_URL
git push -u origin main
```

分发包不含 `.git` 历史，也不会自动创建远端、提交或推送。

The distribution contains no `.git` history and does not create a remote, commit or push automatically.

## 维护与验证 / Maintenance and checks

```bash
python3 scripts/check_distribution.py
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s skills/home-fitness-coach/tests -v
```

修改视频模块时，在具备视频依赖的环境中运行相应模块测试。测试采用合成数据与短合成视频，不需要私人录像：

For video module changes, run the relevant tests in an environment with video dependencies. Tests use synthetic data and short synthetic videos, without private recordings:

```bash
python3 -B -m unittest discover -s skills/analyze-workout-video/tests -v
```

发布文件清单为 [release-files.txt](release-files.txt)。提交新增公共源码时同时更新清单；不要把训练输出加入清单。GitHub Actions 只运行分发检查和安装模块测试。

The release inventory is [release-files.txt](release-files.txt). Update it when adding public source files; never add training outputs. GitHub Actions runs only distribution and installer module checks.
