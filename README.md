# HTML 管理器

> 把电脑里散落各处的 `.html` 文件，像管理文档一样集中起来。

一个 macOS 上的本地 HTML 文件管理器。一行命令启动，浏览器里像 Notion 一样浏览、搜索、收藏、加标签、查重、批量整理你电脑里的所有 HTML 文件。**零依赖，单文件 Python 脚本。**

<p align="center">
  <img src="assets/AppIcon.icns" width="128" alt="logo" />
</p>

---

## 这个工具适合谁

如果你属于以下任何一种人，它会让你少 40 分钟翻硬盘的时间：

- **AI 时代的 vibe coder**：让 Claude / Cursor / Copilot 生成过几十上百个 HTML（设计稿、原型、报告、教程、可视化…），现在散落在各个项目里，找一个之前做的页面比写新的还慢。
- **设计师 / PM**：经常收/导出 HTML 设计稿和原型，想集中一处快速预览。
- **数据分析师**：跑 Jupyter / pandas-profiling / Plotly / Pyecharts 生成了一堆 `report.html`，想按时间和主题归档。
- **任何"一年前那个 HTML 我放哪了"的人**。

---

## 主要功能

| 功能 | 说明 |
|---|---|
| 🔍 **递归扫描** | 自动索引 `~/` 下所有 `.html` / `.htm` 文件，跳过 `node_modules` / `Library` / `.git` 等噪声目录 |
| 🌳 **文件夹树** | 左侧栏可展开的目录树，每个目录显示包含的 HTML 数量 |
| 🔁 **重复文件检测** | 按 SHA1 hash 自动找出完全相同的文件，告诉你浪费了多少空间 |
| 🏷️ **标签 + 备注** | 给文件打多个标签、写备注，输入新标签自动收录到"已有标签"供以后一键复用 |
| ⭐ **收藏** | 一键星标常用文件 |
| ⚡ **批量操作** | 多选后批量打标签 / 加收藏 / 移到废纸篓 |
| 👁️ **实时预览** | 右侧 iframe 直接渲染 HTML，可一键在浏览器打开或在 Finder 中显示 |
| 🪟 **三栏布局** | 左侧栏 / 中间列表 / 右侧详情可独立拖宽、独立折叠，专心看预览时可全屏 |
| 🖱️ **右键菜单** | 列表行上右键复制路径 / 文件名 / 标题 / 直接打开 / 收藏 / 删除 |
| 🗑️ **安全删除** | 走 macOS Finder 移到废纸篓，可还原，从不直接 `rm` |
| 🔄 **增量扫描** | 后续扫描只重读变化的文件，毫秒级完成 |

---

## 安装

### 系统要求
- macOS 10.14+（用到了 `osascript` 移废纸篓和 `open` 命令）
- Python 3.9+（macOS 自带的 `/usr/bin/python3` 即可，**无需 pip install 任何东西**）

### 一键安装（推荐）

```bash
git clone https://github.com/jiaoziluanbu/html-manager.git
cd html-manager
./install.sh
```

完成后桌面会出现「**HTML 管理器.app**」，双击启动。

> 第一次启动 macOS 可能弹「无法验证开发者」提示。到 **系统设置 → 隐私与安全性 → 最下面点「仍要打开」**，之后双击就生效。这是因为我们没花 99 美刀买苹果开发者证书签名 —— 代码是开源的，你可以自己读。

### 安装到 `~/Applications` 而不是桌面

```bash
INSTALL_DIR=~/Applications ./install.sh
```

### 不想要 .app，命令行启动

```bash
python3 server.py
```

或者加个 alias 到 `~/.zshrc`：
```bash
echo "alias htmlm='python3 ~/path/to/html-manager/server.py'" >> ~/.zshrc
```

---

## 使用

启动后浏览器自动打开 `http://localhost:8765`。

### 第一次启动会做什么
1. 打开 UI（一开始空的）
2. 自动开始扫描你的 `~/` 目录
3. 几秒到几分钟之间完成（取决于硬盘里 HTML 的数量）
4. 文件出现在列表里，左侧栏显示目录树和"重复文件"统计

### 日常使用流程
- **找文件**：顶部搜索框输入关键词（匹配文件名 / `<title>` / 路径）；或左侧点目录树定位
- **打开文件**：点列表里任意一行 → 右侧实时预览；点「在浏览器打开」用默认浏览器全屏看
- **整理**：右键复制路径粘到任意地方；多选后批量打标签（如 `#设计稿` `#周报` `#旧项目`）
- **清理空间**：左侧栏「重复文件」一键查看完全相同的副本，移除冗余
- **专注阅读**：折叠左侧栏 + 中间列表，预览区可占满整个窗口当文档读

### 重新扫描
左下角「重新扫描」按钮 — 增量扫描，新增/修改的文件秒级更新。

---

## 数据存储

| 位置 | 内容 |
|---|---|
| `~/.html-manager/index.sqlite` | 索引数据库（路径、标题、大小、SHA1、标签、备注、收藏） |
| `/tmp/html-manager.log` | 服务运行日志 |

**完全不复制你的 HTML 文件**，只索引元数据。删除 `~/.html-manager/` 目录就可以重置全部状态（你打的标签和备注都会丢）。

---

## 隐私

- ✅ 100% 本地运行，**没有任何外网请求**
- ✅ 服务只监听 `127.0.0.1:8765`，外部无法访问
- ✅ 文件内容只在你点击预览时被读取
- ✅ 移到废纸篓走系统 API，可在 Finder 里还原

---

## 卸载

```bash
# 删除桌面快捷方式
rm -rf ~/Desktop/HTML\ 管理器.app

# 删除索引数据
rm -rf ~/.html-manager

# 删除项目本身
rm -rf /path/to/html-manager
```

---

## FAQ

**Q：扫描多久能完成？**
A：一般家用 Mac（5-10 万个普通文件中含几百个 HTML）首次扫描 30 秒到 2 分钟。后续增量扫描通常 1-3 秒。

**Q：占多少空间？**
A：索引库每 1000 个 HTML 大约 200-500 KB（不存内容，只存元数据）。

**Q：会读 `node_modules` 里的 HTML 吗？**
A：不会。默认排除：`node_modules` / `Library` / `.Trash` / `.git` / `__pycache__` / `venv` / `dist` / `build` / `.cache` / `Photos Library.photoslibrary` / 所有 `.` 开头的隐藏目录等。如需调整看 `server.py` 顶部的 `SKIP_DIR_NAMES`。

**Q：超过 50 MB 的 HTML 会被跳过？**
A：是的（避免读巨型导出文件卡住扫描）。如需调整改 `server.py` 里的 `MAX_FILE_SIZE`。

**Q：能在 Linux / Windows 用吗？**
A：核心索引和 UI 都能跑（纯 Python stdlib + HTML），但「在 Finder 中显示」「移到废纸篓」依赖 macOS 命令，需要替换。Linux 可改用 `xdg-open` + `gio trash`，Windows 改用 `explorer /select,` + 移到回收站的 PowerShell 命令。欢迎 PR。

**Q：服务在跑但端口被占了？**
A：改 `server.py` 顶部的 `PORT = 8765` 为别的端口，记得同步改 `install.sh` 里 launcher 模板的 URL。

**Q：扫描后没看到我的 HTML？**
A：可能它在被排除的目录里（如 `~/Library/...`、`Photos Library.photoslibrary` 内部）。可以临时改 `SKIP_DIR_NAMES` 测试，或运行：
```bash
find ~ -name "*.html" -not -path "*/node_modules/*" -not -path "*/Library/*" 2>/dev/null | head -20
```

---

## 技术架构

- **后端**：Python stdlib `http.server.ThreadingHTTPServer` + `sqlite3` + `threading`，零外部依赖
- **前端**：单文件 HTML（嵌在 `server.py` 字符串里），原生 JS + CSS，无打包步骤
- **总代码量**：约 1700 行（含 700 行 HTML/CSS/JS、500 行 Python、其余为模板和注释）
- **启动时间**：< 200ms
- **首次扫描速度**：~ 1000 文件 / 秒（取决于磁盘）

代码风格刻意保持单文件，方便他人 fork 修改 — 改一处看一处。

---

## 致谢

UI 灵感来自 Apple Notes / Things 3 的三栏布局；图标用 Pillow 程序化生成（见 `assets/make_icon.py`）。

由 Claude Code (Opus 4.7) 与作者协作完成。

---

## License

MIT
