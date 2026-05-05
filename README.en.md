<div align="right">

[简体中文](README.md) · **English**

</div>

# HTML Manager

> Manage every `.html` file scattered across your Mac like documents in Notion.

A local HTML file manager for macOS. One command to start, then browse, search, favorite, tag, dedupe, and bulk-organize every HTML file on your machine — all in your browser. **Zero dependencies, single Python file.**

<p align="center">
  <img src="assets/AppIcon.icns" width="128" alt="logo" />
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/platform-macOS-lightgrey.svg" alt="macOS">
  <img src="https://img.shields.io/badge/dependencies-zero-brightgreen.svg" alt="Zero deps">
</p>

---

## Who is this for

If you're any of the following, this tool will save you 40 minutes of digging through your hard drive every time:

- **The vibe coder of the AI era**: Claude / Cursor / Copilot has generated dozens of HTML files for you (mockups, prototypes, reports, tutorials, dataviz...). They're scattered across projects, and finding an old one takes longer than building a new one.
- **Designers / PMs**: You constantly receive or export HTML mockups and prototypes and want one place to preview them all.
- **Data analysts**: Your `pandas-profiling` / Plotly / Pyecharts pipelines spit out a flood of `report.html` files that need archiving by topic and date.
- **Anyone who's ever asked "where did I put that HTML from a year ago?"**.

---

## Features

| | |
|---|---|
| 🔍 **Recursive scan** | Auto-indexes every `.html` / `.htm` under `~/`, skipping noise like `node_modules` / `Library` / `.git` |
| 🌳 **Folder tree** | Collapsible directory tree in the sidebar, with file counts |
| 🔁 **Duplicate detection** | Groups identical files by SHA1 hash and shows wasted disk space |
| 🏷️ **Tags + notes** | Add tags and freeform notes; new tags auto-join the suggestions list for one-click reuse |
| ⭐ **Favorites** | One-click star for files you reach for often |
| ⚡ **Bulk actions** | Multi-select to bulk tag / favorite / move to trash |
| 👁️ **Live preview** | Right pane renders the HTML in an iframe; one click to open in your default browser or reveal in Finder |
| 🪟 **Three-pane layout** | Sidebar / list / detail panes are independently resizable and collapsible — go full-screen on a single preview when you want to read |
| 🖱️ **Right-click menu** | Copy path / filename / title, open, favorite, or trash any row |
| 🗑️ **Safe delete** | Goes through Finder's "Move to Trash" — fully recoverable, never `rm` |
| 🔄 **Incremental scan** | Subsequent scans only re-read changed files. Milliseconds. |

---

## Install

### Requirements
- macOS 10.14+ (uses `osascript` for trash and `open` for browser/Finder)
- Python 3.9+ (`/usr/bin/python3` shipped with macOS works — **no `pip install` needed**)

### One-liner

```bash
git clone https://github.com/jiaoziluanbu/html-manager.git
cd html-manager
./install.sh
```

A clickable «**HTML 管理器.app**» appears on your Desktop. Double-click to launch.

> First launch may show *"cannot verify developer"*. Go to **System Settings → Privacy & Security → scroll down → click "Open Anyway"**. Required because we don't pay Apple's $99/year signing fee — the source is open, read it yourself.

### Install to `~/Applications` instead of Desktop

```bash
INSTALL_DIR=~/Applications ./install.sh
```

### CLI-only (no .app)

```bash
python3 server.py
```

Or shell alias:
```bash
echo "alias htmlm='python3 ~/path/to/html-manager/server.py'" >> ~/.zshrc
```

---

## Usage

After launch, your browser opens to `http://localhost:8765`.

### First run
1. UI opens (empty)
2. A scan of `~/` kicks off automatically
3. Takes seconds to a couple of minutes (depending on how many HTMLs you have)
4. Files populate the list; the sidebar shows the folder tree and duplicate count

### Daily flow
- **Find**: type into the top search box (matches filename / `<title>` / path), or click the folder tree
- **Open**: click any row → live preview on the right; click "Open in Browser" for full-screen view
- **Organize**: right-click → copy path; multi-select → bulk tag (e.g. `#mockup` `#weekly-report` `#legacy`)
- **Reclaim space**: sidebar → "Duplicates" shows identical copies you can delete
- **Focus mode**: collapse sidebar + list, let the preview pane fill the whole window

### Re-scan
Bottom-left "Re-scan" button. Incremental — only changed files are re-read.

---

## Data Storage

| Location | Contents |
|---|---|
| `~/.html-manager/index.sqlite` | Index DB (path, title, size, SHA1, tags, notes, favorites) |
| `/tmp/html-manager.log` | Server logs |

**Your HTML files are never copied** — only metadata is indexed. Delete `~/.html-manager/` to reset everything (you'll lose your tags and notes).

---

## Privacy

- ✅ 100% local. **Zero outbound network requests.**
- ✅ Server only listens on `127.0.0.1:8765`. External access blocked.
- ✅ File contents are read only when you click preview.
- ✅ Trash goes through the system API — recoverable from Finder.

---

## Uninstall

```bash
# Remove desktop shortcut
rm -rf ~/Desktop/HTML\ 管理器.app

# Remove index data
rm -rf ~/.html-manager

# Remove the project itself
rm -rf /path/to/html-manager
```

---

## FAQ

**Q: How long does the first scan take?**
A typical Mac (50k–100k regular files with a few hundred HTMLs) finishes in 30 seconds to 2 minutes. Subsequent incremental scans usually take 1–3 seconds.

**Q: How much disk space?**
The index DB is roughly 200–500 KB per 1000 HTML files (metadata only).

**Q: Will it scan `node_modules`?**
No. Default exclusions: `node_modules` / `Library` / `.Trash` / `.git` / `__pycache__` / `venv` / `dist` / `build` / `.cache` / `Photos Library.photoslibrary` / all dotfile dirs. Edit `SKIP_DIR_NAMES` in `server.py` to adjust.

**Q: Files over 50 MB are skipped?**
Yes (to avoid choking on huge exports). Edit `MAX_FILE_SIZE` in `server.py` to change.

**Q: Linux / Windows?**
Indexing and UI both work (pure Python stdlib + HTML), but "Reveal in Finder" and "Move to Trash" are macOS-specific. Linux can use `xdg-open` + `gio trash`, Windows can use `explorer /select,` + a PowerShell recycle-bin script. PRs welcome.

**Q: Port 8765 is taken?**
Edit `PORT = 8765` at the top of `server.py`. Don't forget to update the URL in the launcher template inside `install.sh`.

**Q: My HTMLs aren't showing up after scan?**
They might be in an excluded directory (e.g. `~/Library/...`, inside `Photos Library.photoslibrary`). Try editing `SKIP_DIR_NAMES`, or run:
```bash
find ~ -name "*.html" -not -path "*/node_modules/*" -not -path "*/Library/*" 2>/dev/null | head -20
```

---

## Architecture

- **Backend**: Python stdlib `http.server.ThreadingHTTPServer` + `sqlite3` + `threading`. Zero external dependencies.
- **Frontend**: Single HTML file (embedded as a string in `server.py`), vanilla JS + CSS, no build step.
- **Total LOC**: ~1700 (700 HTML/CSS/JS, 500 Python, rest is templates and comments).
- **Startup**: < 200 ms.
- **First-scan throughput**: ~1000 files/sec (disk-bound).

The single-file design is intentional — fork it, edit one place, see one effect.

---

## Credits

UI inspired by Apple Notes / Things 3's three-pane layout. The icon is procedurally generated by Pillow (see `assets/make_icon.py`).

Built collaboratively by [Claude Code](https://claude.com/claude-code) (Opus 4.7) and the author.

---

## License

MIT
