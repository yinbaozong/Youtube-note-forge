# YouTube Note Forge

把 YouTube、Bilibili 等长视频整理成适合 Obsidian 长期学习的中文笔记，并保留 SRT、封面和与正文对应的关键截图。

当前版本：`4.0.5`

### 4.0.5 重复字幕面板修复

真实失败数据包含两份完全相同的 370 条字幕（共 740 条），时间在两份之间从 895 秒退回 0 秒，被旧校验误报为字幕不完整。浏览器与 Python 接收端现在均按时间排序、去除时间及文本完全一致的记录，保留同一时刻的不同句子。已用该视频保存的实际输入验证：370 条、0–895 秒、视频长 898 秒，通过完整性校验。此验证针对素材接收，不等同于后续抽帧和文章生成的端到端验收。

### 4.0.4 字幕与校验修复

Chrome 点击生成时先在当前视频页读取完整字幕，最多等待约 15 秒；优先使用原生文字稿或播放器字幕接口，必要时打开 YouTube 原生文字稿面板。不读取屏幕上的单句字幕或第三方翻译面板。文本与视频 ID、时长校验通过后交给现有 Skill 生成 SRT；读取失败仍使用既有 yt-dlp 路径，并区分“探测失败”和“接口未返回字幕”。需要重新加载 Chrome 扩展以应用新增的 scripting 权限。

SRT 链接由程序绑定真实文件，中文比例不再统计图片和字幕路径中的英文。正文先写入 `YouTube video/.reader-drafts/<视频ID>/`，通过校验后才发布正式笔记；失败显示具体校验项与草稿位置，点击继续可复用写作检查点。质量校验与最多一次正文修正继续保留。

验证范围：自动化回归与本地安装校验；未完成当前登录态下的真实视频端到端验收，页面结构变化或字幕接口限流仍可能导致读取失败。

## v4 架构

完整阅读器只由两个用户可见组件组成：

```text
Chrome 当前视频页
  -> YouTube 阅读器扩展（当前 URL + youtube.com Cookie）
  -> Obsidian YouTube Note Reader 插件（127.0.0.1:32191）
  -> 现有 youtube-transcript Skill 脚本
  -> 模型 API
  -> 当前 Vault/YouTube video
```

- Chrome 扩展只读取当前视频地址和 YouTube Cookie，并显示任务状态。
- Obsidian 插件拥有固定状态机、模型设置、任务恢复、脚本调用和文件写入。
- `youtube-transcript` 是唯一视频处理流程；没有第二套字幕、抽帧或写作实现。
- 完整安装不需要 OpenCode，也没有单独启动的桌面伴侣。
- Obsidian 必须在任务期间保持打开。关闭 Chrome 弹窗或视频页不会中止任务。
- OpenCode 仅保留为“只装 Skill”用户的可选入口。

这种结构让浏览器处理最擅长的登录态，Obsidian 管理 Vault、模型和本地任务，现有 Skill 处理视频。Cookie、截图和笔记都留在用户电脑上，且不会因为更换聊天模型而改变流程。

更完整的技术边界见 [docs/architecture-v4.md](docs/architecture-v4.md)。

## 一次任务的阶段

| 阶段 | 进度 | 工作内容 | 失败行为 |
| --- | ---: | --- | --- |
| Cookie | 0–5% | Chrome 读取当前登录态，Obsidian 插件保存 Cookie | 缺失或被拒绝时停止 |
| 素材 | 5–25% | 提取元数据、字幕、SRT 和封面 | 返回准确错误码，不自动改用 ASR |
| 规划 | 25–35% | 模型根据字幕生成文章大纲和截图计划 | 固定模型，不自动切换 |
| 截图 | 35–70% | 按计划从最高 720p 流定点抽帧 | 失败立即说明时间点和原因 |
| 写作 | 70–90% | 生成中文笔记并把截图放在对应段落附近 | 复用已成功素材 |
| 校验 | 90–98% | 检查标题、章节、中文比例、截图、SRT 和链接 | 只允许修改笔记一次 |
| 完成 | 100% | 返回笔记路径、照片目录、截图数和总耗时 | 可复制路径或在 Obsidian 打开 |

端到端硬限制为 8 分钟。任务状态保存在 Obsidian 插件中；重新打开 Chrome 弹窗会显示同一个任务。Obsidian 意外退出后，任务会标记为“已中断”，重新打开 Obsidian 后可从已有素材继续。

## 两种分享方式

| 方式 | 发布包 | 适合人群 | 依赖 |
| --- | --- | --- | --- |
| 完整阅读器 | `youtube-reader-chrome-obsidian-v4.0.5.zip` | 追求稳定的一键操作 | Chrome、Obsidian、Python、FFmpeg、Node.js、模型 API Key |
| 仅 Skill | `youtube-transcript-skill-v4.0.5.zip` | 已经使用 OpenCode，愿意自行调用命令 | OpenCode、Python、FFmpeg、Node.js |

朋友使用完整阅读器时不需要安装 OpenCode，但需要使用自己的模型 API Key。

## 完整阅读器安装

### 1. 准备依赖

- Windows 10/11
- Chrome
- Obsidian 桌面版 1.11.4 或更高版本
- Python 3.10 或更高版本
- FFmpeg
- Node.js（发布包已包含编译好的 Obsidian 插件，普通安装不需要 npm）

确认下列命令可在 PowerShell 中运行：

```powershell
python --version
ffmpeg -version
node --version
```

### 2. 安装到 Vault

解压完整发布包，在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Vault "C:\Users\win11\Documents\Obsidian Vault"
```

如果需要在“没有可用字幕”时手动允许本地语音识别，安装时增加：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Vault "C:\Users\win11\Documents\Obsidian Vault" -InstallAsr
```

默认不安装 ASR，也不会下载完整视频。只有点击扩展中的“允许 ASR 并重试”后才会下载音频。

### 3. 启用 Obsidian 插件

1. 打开目标 Vault。
2. 重载或完全退出后重启一次 Obsidian；正在运行的 Obsidian 不会自动加载刚复制进去的 `main.js`。
3. 进入“设置 -> 第三方插件”，确认 `YouTube Note Reader` 已启用；若未启用，关闭“安全模式”后手动打开开关。
4. 在左侧设置列表进入 `YouTube Note Reader`。

插件目录为：

```text
<Vault>\.obsidian\plugins\youtube-note-reader
```

Skill 目录为：

```text
<Vault>\.obsidian\skills\youtube-transcript
```

### 4. 配置模型

所有模型设置都在 Obsidian 的 `YouTube Note Reader` 设置页：

- API Base：OpenAI-compatible 地址，例如 `https://api.openai.com/v1`。
- Model：服务商要求的完整模型名。
- API Key：保存到 Obsidian SecretStorage，不写入 Chrome 扩展或发布包。
- 验证 API Key：保存后点击一次，插件会向当前 API Base 和模型发送最小请求并显示成功或具体错误。
- Python：通常填写 `python`。
- Skill 目录：默认指向当前 Vault 内安装的 Skill。
- 输出目录：默认 `YouTube video`。
- 完成后自动打开：按需启用。

任务开始后会锁定本次模型，不自动降级或切换。

### 5. 加载 Chrome 扩展

1. 打开 `chrome://extensions`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择发布包中的 `extension` 文件夹。
5. 固定工具栏中的“YouTube 阅读器”图标。

Chrome 扩展通过 `http://127.0.0.1:32191` 与当前已打开的 Obsidian 插件通信，不再使用 Native Messaging。

打开扩展的“连接与设置”页后：绿色表示已连接当前 Obsidian Vault，红色表示未连接并显示处理建议。该页面也会显示 API Key 是否已保存，并可直接点击“验证 API Key”；验证请求由 Obsidian 发出，Chrome 无法读取 Key。

## 使用方法

1. 打开 Obsidian，并保持目标 Vault 和 `YouTube Note Reader` 插件处于启用状态。
2. 在 Chrome 登录 YouTube，打开视频页。
3. 点击“YouTube 阅读器”。
4. 点击“生成学习笔记”。
5. 弹窗可关闭；任务继续在 Obsidian 内运行。再次点击扩展会恢复进度。
6. 完成后弹窗显示笔记路径、截图目录、截图数量和总耗时。
7. 点击“在 Obsidian 打开”，或复制路径自行定位。

默认输出结构：

```text
<Vault>\YouTube video\中文标题 - English Title.md
<Vault>\YouTube video\transcripts\中文标题 - English Title.srt
<Vault>\YouTube video\assets\<video-id>\cover.jpg
<Vault>\YouTube video\assets\<video-id>\frame_*.jpg
```

运行中可点击“强制停止”。任务失败后不会自动换下载器、开浏览器或无限重试。

## 字幕错误与 ASR

字幕阶段使用三个不同错误码：

- `SUBTITLE_UNAVAILABLE`：视频元数据中确实没有可用字幕。
- `SUBTITLE_DOWNLOAD_FAILED`：发现了字幕轨道，但网络、限流、Cookie 或超时导致下载失败。
- `SUBTITLE_PARSE_FAILED`：字幕已下载，但内容无法解析。

旧版本把后两类也显示为“没有可用字幕”，因此看起来经常误报。v4 会显示真实原因，并在三类错误上提供“允许 ASR 并重试”。ASR 永远不会自动开启，因为它会额外下载音频、增加时间和本地计算量。

### 浏览器指纹依赖

YouTube 的字幕接口要求请求带浏览器 TLS 指纹，yt-dlp 通过 `curl_cffi` 实现。缺少它时字幕下载会间歇性收到 403 或 429，表现就是“明明有字幕却提取失败”。`requirements.txt` 已声明 `yt-dlp[default,curl-cffi]`，全新安装会自动装上。从旧版本升级需要重新安装依赖：

```
python -m pip install -r requirements.txt
```

确认指纹可用（每行都不应显示 `unavailable`）：

```
yt-dlp --list-impersonate-targets
```

### 明明有字幕却报错的三种成因

- **缺少浏览器指纹**：见上一节，装好 `curl_cffi` 即可。
- **限流或连接中断**：字幕下载遇到 403、429、超时和连接重置时会自动重试一次，不再一次失败就判定无字幕。
- **播放器客户端降级**：YouTube 只对部分播放器客户端返回字幕轨道。主客户端被限流时 yt-dlp 会降级到不返回字幕的客户端，导致元数据里字幕列表为空，看起来和真的没字幕一样。现在在判定“没有可用字幕”之前会换一个支持字幕的客户端重探一次，只有重探仍然为空才报 `SUBTITLE_UNAVAILABLE`。

字幕失败的报错会直接显示 yt-dlp 的 `ERROR` 行。此前失败详情按原始输出截断到 300 字符，而 Cookie 路径和无害的 WARNING 会先占满额度，把真正的原因挤掉。

## 常见问题

### 扩展提示无法连接 Obsidian

确认：

1. Obsidian 正在运行。
2. 当前 Vault 已启用 `YouTube Note Reader`。
3. 本机端口 `32191` 没有被其他程序占用。
4. Chrome 扩展是当前 `extension` 目录，且已在扩展页点击“重新加载”。

`4.0.0` 曾把 Chrome 扩展 ID 写死，`4.0.2` 又只修正了来源解析和旧错误清理，但 Chrome 后台的部分 GET 请求可能不携带 `Origin`，因此连接页会假报绿色，而点击生成仍在 `/active` 返回 403。`4.0.3` 为每个扩展请求增加包含扩展 ID 与版本的客户端标识，并让 CORS 预检、实际请求和连接检测使用同一套规则；普通网页仍无法调用本地 RPC。升级后必须同时重载 Obsidian 插件和 Chrome 扩展。

独立桌面伴侣已废弃，不需要运行 `restart_companion.ps1`。

### Cookie 失效

保持 YouTube 登录状态，再次点击生成或重试。扩展每次任务都会读取当前 Cookie，只有 YouTube 实际拒绝时才要求重新登录。

### 截图慢或失败

Skill 优先定点读取最高 720p 视频流，不下载完整视频。远程拉帧失败时只允许下载目标时间附近的短片段，成功后立即删除。达到阶段时限后会停止并报告，不会一直卡住。

### 关闭弹窗后任务是否继续

继续。任务由 Obsidian 插件持有，Chrome 弹窗只是状态窗口。关闭 Obsidian 会中断任务；重新打开后可继续上次任务。

## 验证、更新与卸载

验证安装：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_install.ps1 -Vault "C:\Users\win11\Documents\Obsidian Vault"
```

更新时解压新版本并重新运行 `install.ps1`，然后重载 Obsidian 和 Chrome 扩展。

卸载：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1 -Vault "C:\Users\win11\Documents\Obsidian Vault"
```

卸载不会删除已经生成的笔记、字幕和图片。

## 仅 Skill 安装

仅 Skill 发布包保留 OpenCode 兼容入口。将 `youtube-transcript` 文件夹安装到 OpenCode Skill 目录，并安装包内 `opencode/agent` 与 `opencode/command` 后使用：

```text
/video-note <一个或多个视频链接>
```

此方式不包含 Chrome 扩展的自动 Cookie 同步、Obsidian 任务恢复和进度界面。

## 开发与测试

```powershell
python -m unittest discover -s tests -p "test_*.py"

Push-Location .\obsidian-plugin
npm test
npm run typecheck
npm run build
Pop-Location

node --check .\extension\protocol.js
node --check .\extension\service_worker.js
node --check .\extension\popup.js
```

打包：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package_release.ps1
```

## 安全边界

- HTTP 服务只绑定 `127.0.0.1`，不会监听局域网地址。
- 接口只接受扩展来源或本机允许的请求。
- Cookie 仅写入本机现有凭据路径，不进入 Chrome storage、日志或发布包。
- API Key 使用 Obsidian SecretStorage。
- 正式笔记只有通过校验后才覆盖；失败草稿不冒充完成结果。
- 安装与卸载脚本不会关闭 Chrome，也不会删除生成的学习资料。

## License

See [LICENSE](LICENSE).
