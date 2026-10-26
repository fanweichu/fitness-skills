# AI installation instructions / AI 自动安装说明

These instructions apply when the user asks an AI assistant to install this repository. Follow the host's filesystem permissions. Installation copies skills only; it does not authorize reading personal records, initializing a training project, installing system packages or publishing anything.

以下步骤适用于用户要求 AI 安装本仓库。遵守宿主文件权限。安装只复制 Skill，不包含读取私人记录、创建训练项目、安装系统软件或发布内容的授权。

1. Obtain this repository using the URL supplied by the user, or use the current checkout. Do not guess an owner or repository URL. Read `README.md` and `install.py`.
   使用用户提供的仓库地址获取源码，或使用当前检出目录。不要猜测仓库地址。读取 `README.md` 和 `install.py`。
2. Use Python 3.10 or newer. From the repository root, run `python3 scripts/check_distribution.py`, then `python3 install.py --dry-run` and `python3 install.py`. These commands are noninteractive. The installer uses `$CODEX_HOME/skills`, falling back to `~/.codex/skills`. For another host, use its documented skill directory with `--dest`; do not assume host compatibility.
   使用 Python 3.10+，在仓库根目录依次执行上述检查、预览和安装命令。命令无需交互。其他宿主通过 `--dest` 指定其支持的 Skill 目录，不擅自假定兼容。
3. Check exit codes and JSON output. Both skill directories must contain `SKILL.md`. Identical installations report `unchanged`; a differing installation stops before copying either skill. Preserve it and report the conflict; do not silently delete or overwrite it.
   检查退出码和 JSON 结果。两个目录均须含 `SKILL.md`。相同版本可重复安装；有差异则在复制前停止，保留现有内容并说明冲突。
4. Report which skills were installed. In Codex they should be available on the next turn. A host may need to refresh its skill catalog. Installing files does not prove video dependencies or visual analysis work.
   说明安装结果。Codex 下一轮应可使用；其他宿主可能需要刷新技能目录。文件安装成功不等于视频依赖或视觉分析已验证。

## Runtime dependencies / 运行依赖

Text coaching uses Python's standard library. Video review also needs FFmpeg, FFprobe, Pillow and NumPy. Its decoder uses POSIX file locking: use macOS or Linux; native Windows is not supported by the current decoder. Windows users can use a compatible Linux environment such as WSL, but that path has not been tested here. Actual frame interpretation requires a vision-capable assistant.

文字训练管理只需要 Python 标准库。视频分析另需 FFmpeg、FFprobe、Pillow 和 NumPy；解码器使用 POSIX 文件锁，适用于 macOS/Linux，当前不支持原生 Windows。Windows 可使用 WSL 等 Linux 环境，但本仓库未验证该路径。判断动作还需要能查看图像的 AI。

After the user requests video setup, inspect installed dependencies:

用户要求配置视频功能后，使用已安装目录运行只读预检：

```bash
python3 SKILL_DIR/scripts/review_runtime.py preflight
```

Here `SKILL_DIR` is the resolved installed `analyze-workout-video` directory, not a literal path. If modules are missing, use an existing compatible environment or an isolated environment outside this repository, and install `Pillow` and `numpy` there under the host's normal permissions. Use that environment's Python for video scripts. FFmpeg/FFprobe must be on `PATH`. Page screenshot checks optionally need Node.js, Playwright and a browser. Do not silently install unrelated tools or change global configuration.

`SKILL_DIR` 须替换为实际视频 Skill 目录。缺失依赖时，优先使用已有环境，或在仓库外的独立环境中按宿主权限安装 `Pillow` 和 `numpy`，后续用该环境的 Python。FFmpeg/FFprobe 须位于 `PATH`。网页截图验收另需 Node.js、Playwright 和浏览器；不要默默安装无关工具或修改全局配置。

## Separate user workspace / 独立私人工作区

Only when requested, initialize a new user-selected directory **outside this repository** with the coach skill's `scripts/init_project.py --project PROJECT`. Existing projects must not be reinitialized. Keep profile, symptoms, goals, recordings and generated reports in that private workspace. None belongs in a public skill repository.

仅在用户要求时，使用教练 Skill 的 `scripts/init_project.py --project PROJECT` 初始化用户选定的**仓库外**目录。已有项目不得重新初始化。个人档案、不适、目标、录像和生成报告全部留在私人工作区，不进入公共 Skill 仓库。
