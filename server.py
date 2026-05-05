#!/usr/bin/env python3
"""HTML 管理器 — 扫描本机所有 .html 文件并提供 Web UI 管理。

零依赖：仅使用 Python stdlib。
启动：python3 server.py
访问：http://localhost:8765
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------- 配置 ----------
PORT = 8765
HOME = Path.home()
DATA_DIR = HOME / ".html-manager"
DB_PATH = DATA_DIR / "index.sqlite"

DEFAULT_ROOTS = [str(HOME)]

# 扫描时跳过的目录名（任意层级匹配）
SKIP_DIR_NAMES = {
    "node_modules", ".git", ".svn", ".hg", "__pycache__", ".venv", "venv",
    "env", ".env", "dist", "build", ".next", ".nuxt", ".cache", ".npm",
    "Library", ".Trash", ".Trashes", ".DocumentRevisions-V100", ".Spotlight-V100",
    ".fseventsd", ".TemporaryItems", "Pods", "DerivedData", ".gradle",
    ".idea", ".vscode-server", "site-packages", "vendor", ".pnpm-store",
    ".yarn", ".rbenv", ".pyenv", ".rustup", ".cargo", ".docker", ".colima",
    "Photos Library.photoslibrary", "Music",
}

# 跳过这些前缀路径（相对 HOME）
SKIP_PATH_PREFIXES = {
    str(HOME / "Library"),
    str(HOME / ".Trash"),
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB 以上的 HTML 跳过
TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


# ---------- 数据库 ----------
def db_connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_db_lock = threading.Lock()
_db = db_connect()


def db_init() -> None:
    with _db_lock:
        # 第一阶段：建表（不含 sha1 索引，因为旧库可能没 sha1 列）
        _db.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            title TEXT,
            size INTEGER,
            mtime REAL,
            indexed_at REAL,
            favorite INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            sha1 TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);
        CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
        CREATE INDEX IF NOT EXISTS idx_files_favorite ON files(favorite);
        CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(deleted);

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS file_tags (
            file_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (file_id, tag_id),
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS scan_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        # 第二阶段：旧库迁移补 sha1 列
        cols = {r["name"] for r in _db.execute("PRAGMA table_info(files)").fetchall()}
        if "sha1" not in cols:
            _db.execute("ALTER TABLE files ADD COLUMN sha1 TEXT")
        # 第三阶段：建 sha1 索引（此时列一定存在）
        _db.execute("CREATE INDEX IF NOT EXISTS idx_files_sha1 ON files(sha1)")
        _db.commit()


def db_get_state(key: str, default: str = "") -> str:
    with _db_lock:
        row = _db.execute("SELECT value FROM scan_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def db_set_state(key: str, value: str) -> None:
    with _db_lock:
        _db.execute(
            "INSERT INTO scan_state(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        _db.commit()


# ---------- 扫描 ----------
def extract_title_and_hash(path: Path) -> tuple[str, str]:
    """读一遍文件，同时拿到 title 和 sha1。"""
    title = ""
    h = hashlib.sha1()
    head = b""
    try:
        with path.open("rb") as f:
            chunk = f.read(64 * 1024)
            head = chunk
            while chunk:
                h.update(chunk)
                chunk = f.read(256 * 1024)
    except OSError:
        return "", ""

    m = TITLE_RE.search(head)
    if m:
        raw = m.group(1).strip()
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                text = raw.decode(enc)
                title = re.sub(r"\s+", " ", text).strip()[:300]
                break
            except UnicodeDecodeError:
                continue
    return title, h.hexdigest()


def should_skip_dir(p: Path) -> bool:
    name = p.name
    if name in SKIP_DIR_NAMES:
        return True
    if name.startswith(".") and name not in (".",):
        # 隐藏目录默认跳过，除了 ~ 本身
        return True
    sp = str(p)
    for prefix in SKIP_PATH_PREFIXES:
        if sp == prefix or sp.startswith(prefix + os.sep):
            return True
    return False


def scan_roots(roots: list[str], progress_cb=None) -> dict:
    seen_paths: set[str] = set()
    new_count = 0
    update_count = 0
    skipped_count = 0
    started = time.time()

    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
            d = Path(dirpath)
            # 过滤子目录（in-place）
            dirnames[:] = [n for n in dirnames if not should_skip_dir(d / n)]
            for fname in filenames:
                if not (fname.lower().endswith(".html") or fname.lower().endswith(".htm")):
                    continue
                fpath = d / fname
                try:
                    st = fpath.stat()
                except OSError:
                    skipped_count += 1
                    continue
                if st.st_size > MAX_FILE_SIZE:
                    skipped_count += 1
                    continue
                spath = str(fpath)
                seen_paths.add(spath)

                with _db_lock:
                    row = _db.execute(
                        "SELECT id, mtime, size, sha1 FROM files WHERE path=?", (spath,)
                    ).fetchone()
                if row and abs(row["mtime"] - st.st_mtime) < 1 and row["size"] == st.st_size:
                    # 未变化：补 sha1（旧库回填）
                    if not row["sha1"]:
                        _, sha1 = extract_title_and_hash(fpath)
                        with _db_lock:
                            _db.execute(
                                "UPDATE files SET deleted=0, sha1=? WHERE id=?",
                                (sha1, row["id"]),
                            )
                            _db.commit()
                    else:
                        with _db_lock:
                            _db.execute("UPDATE files SET deleted=0 WHERE id=?", (row["id"],))
                            _db.commit()
                    if progress_cb:
                        progress_cb(new_count, update_count)
                    continue

                title, sha1 = extract_title_and_hash(fpath)
                with _db_lock:
                    if row:
                        _db.execute(
                            "UPDATE files SET name=?, title=?, size=?, mtime=?, "
                            "indexed_at=?, deleted=0, sha1=? WHERE id=?",
                            (fname, title, st.st_size, st.st_mtime, time.time(), sha1, row["id"]),
                        )
                        update_count += 1
                    else:
                        _db.execute(
                            "INSERT INTO files(path,name,title,size,mtime,indexed_at,sha1) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (spath, fname, title, st.st_size, st.st_mtime, time.time(), sha1),
                        )
                        new_count += 1
                    _db.commit()
                if progress_cb:
                    progress_cb(new_count, update_count)

    # 标记本次未见到的为 deleted
    with _db_lock:
        all_rows = _db.execute("SELECT id, path FROM files WHERE deleted=0").fetchall()
        missing_ids = [r["id"] for r in all_rows if r["path"] not in seen_paths]
        if missing_ids:
            _db.executemany(
                "UPDATE files SET deleted=1 WHERE id=?",
                [(i,) for i in missing_ids],
            )
            _db.commit()

    elapsed = time.time() - started
    return {
        "new": new_count,
        "updated": update_count,
        "skipped": skipped_count,
        "marked_deleted": len(missing_ids) if 'missing_ids' in locals() else 0,
        "elapsed_sec": round(elapsed, 1),
    }


# 扫描状态
_scan_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "new": 0,
    "updated": 0,
    "result": None,
    "roots": list(DEFAULT_ROOTS),
}
_scan_lock = threading.Lock()


def background_scan(roots: list[str]) -> None:
    with _scan_lock:
        if _scan_state["running"]:
            return
        _scan_state.update({
            "running": True,
            "started_at": time.time(),
            "finished_at": None,
            "new": 0,
            "updated": 0,
            "result": None,
            "roots": roots,
        })

    def progress(n, u):
        with _scan_lock:
            _scan_state["new"] = n
            _scan_state["updated"] = u

    def runner():
        try:
            result = scan_roots(roots, progress_cb=progress)
        except Exception as e:
            result = {"error": str(e)}
        with _scan_lock:
            _scan_state["running"] = False
            _scan_state["finished_at"] = time.time()
            _scan_state["result"] = result
        db_set_state("last_scan_at", str(time.time()))
        db_set_state("last_scan_result", json.dumps(result, ensure_ascii=False))

    threading.Thread(target=runner, daemon=True).start()


# ---------- API ----------
def query_files(
    q: str = "",
    favorite: bool = False,
    tag: str = "",
    sort: str = "mtime_desc",
    limit: int = 200,
    offset: int = 0,
    show_deleted: bool = False,
    dir_prefix: str = "",
) -> list[dict]:
    sql = "SELECT f.* FROM files f"
    params: list = []
    where = []

    if tag:
        sql += " JOIN file_tags ft ON ft.file_id=f.id JOIN tags t ON t.id=ft.tag_id"
        where.append("t.name=?")
        params.append(tag)

    if not show_deleted:
        where.append("f.deleted=0")
    if favorite:
        where.append("f.favorite=1")
    if q:
        where.append("(f.name LIKE ? OR f.title LIKE ? OR f.path LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if dir_prefix:
        where.append("(f.path = ? OR f.path LIKE ?)")
        params.extend([dir_prefix, dir_prefix.rstrip("/") + "/%"])

    if where:
        sql += " WHERE " + " AND ".join(where)

    order_map = {
        "mtime_desc": "f.mtime DESC",
        "mtime_asc": "f.mtime ASC",
        "name_asc": "f.name COLLATE NOCASE ASC",
        "name_desc": "f.name COLLATE NOCASE DESC",
        "size_desc": "f.size DESC",
        "size_asc": "f.size ASC",
    }
    sql += f" ORDER BY {order_map.get(sort, 'f.mtime DESC')} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _db_lock:
        rows = _db.execute(sql, params).fetchall()
        # tags
        results = []
        for r in rows:
            tag_rows = _db.execute(
                "SELECT t.name FROM tags t JOIN file_tags ft ON ft.tag_id=t.id "
                "WHERE ft.file_id=? ORDER BY t.name", (r["id"],)
            ).fetchall()
            d = dict(r)
            d["tags"] = [tr["name"] for tr in tag_rows]
            d["dir"] = str(Path(r["path"]).parent)
            d["folder"] = Path(r["path"]).parent.name
            results.append(d)
    return results


def count_files(q="", favorite=False, tag="", show_deleted=False, dir_prefix="") -> int:
    sql = "SELECT COUNT(DISTINCT f.id) c FROM files f"
    params: list = []
    where = []
    if tag:
        sql += " JOIN file_tags ft ON ft.file_id=f.id JOIN tags t ON t.id=ft.tag_id"
        where.append("t.name=?")
        params.append(tag)
    if not show_deleted:
        where.append("f.deleted=0")
    if favorite:
        where.append("f.favorite=1")
    if q:
        where.append("(f.name LIKE ? OR f.title LIKE ? OR f.path LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if dir_prefix:
        where.append("(f.path = ? OR f.path LIKE ?)")
        params.extend([dir_prefix, dir_prefix.rstrip("/") + "/%"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _db_lock:
        return _db.execute(sql, params).fetchone()["c"]


def folder_tree() -> list[dict]:
    """返回直接父目录聚合 + 完整树。返回每个目录及其文件数。"""
    with _db_lock:
        rows = _db.execute(
            "SELECT path FROM files WHERE deleted=0"
        ).fetchall()
    counter: dict[str, int] = {}
    for r in rows:
        d = str(Path(r["path"]).parent)
        # 累加自身和所有祖先目录
        p = Path(d)
        while True:
            sp = str(p)
            counter[sp] = counter.get(sp, 0) + 1
            if p.parent == p:
                break
            p = p.parent
    # 只保留至少含 1 个文件、且是 HOME 子目录的，限制深度
    home = str(HOME)
    out = []
    for path, cnt in counter.items():
        if not (path == home or path.startswith(home + os.sep)):
            continue
        depth = path.count(os.sep) - home.count(os.sep)
        if depth < 0 or depth > 6:
            continue
        out.append({
            "path": path,
            "name": Path(path).name or path,
            "depth": depth,
            "count": cnt,
            "parent": str(Path(path).parent) if depth > 0 else "",
        })
    out.sort(key=lambda x: x["path"])
    return out


def find_duplicates() -> list[dict]:
    """按 sha1 分组，返回有 2+ 个相同 hash 的文件组。"""
    with _db_lock:
        rows = _db.execute(
            "SELECT sha1, COUNT(*) c FROM files "
            "WHERE deleted=0 AND sha1 IS NOT NULL AND sha1 != '' "
            "GROUP BY sha1 HAVING c > 1 ORDER BY c DESC"
        ).fetchall()
        groups = []
        for r in rows:
            file_rows = _db.execute(
                "SELECT id, path, name, title, size, mtime, favorite, sha1 "
                "FROM files WHERE sha1=? AND deleted=0 ORDER BY mtime DESC",
                (r["sha1"],),
            ).fetchall()
            groups.append({
                "sha1": r["sha1"],
                "count": r["c"],
                "size": file_rows[0]["size"] if file_rows else 0,
                "files": [dict(fr) for fr in file_rows],
            })
    return groups


def batch_set_favorite(ids: list[int], fav: bool) -> int:
    if not ids:
        return 0
    with _db_lock:
        _db.executemany(
            "UPDATE files SET favorite=? WHERE id=?",
            [(1 if fav else 0, i) for i in ids],
        )
        _db.commit()
    return len(ids)


def batch_add_tag(ids: list[int], tag_name: str) -> int:
    tag_name = tag_name.strip()
    if not tag_name or not ids:
        return 0
    with _db_lock:
        _db.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag_name,))
        tag_id = _db.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()["id"]
        _db.executemany(
            "INSERT OR IGNORE INTO file_tags(file_id, tag_id) VALUES(?,?)",
            [(i, tag_id) for i in ids],
        )
        _db.commit()
    return len(ids)


def batch_trash(ids: list[int]) -> dict:
    ok = 0
    failed = []
    for i in ids:
        success, msg = trash_file(i)
        if success:
            ok += 1
        else:
            failed.append({"id": i, "msg": msg})
    return {"ok": ok, "failed": failed}


def all_tags() -> list[dict]:
    with _db_lock:
        rows = _db.execute(
            "SELECT t.name, COUNT(ft.file_id) cnt FROM tags t "
            "LEFT JOIN file_tags ft ON ft.tag_id=t.id "
            "LEFT JOIN files f ON f.id=ft.file_id AND f.deleted=0 "
            "GROUP BY t.id ORDER BY cnt DESC, t.name"
        ).fetchall()
    return [dict(r) for r in rows]


def add_tag(file_id: int, tag_name: str) -> None:
    tag_name = tag_name.strip()
    if not tag_name:
        return
    with _db_lock:
        _db.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag_name,))
        tag_id = _db.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()["id"]
        _db.execute(
            "INSERT OR IGNORE INTO file_tags(file_id, tag_id) VALUES(?,?)",
            (file_id, tag_id),
        )
        _db.commit()


def remove_tag(file_id: int, tag_name: str) -> None:
    with _db_lock:
        row = _db.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
        if not row:
            return
        _db.execute(
            "DELETE FROM file_tags WHERE file_id=? AND tag_id=?",
            (file_id, row["id"]),
        )
        _db.commit()


def set_favorite(file_id: int, fav: bool) -> None:
    with _db_lock:
        _db.execute("UPDATE files SET favorite=? WHERE id=?", (1 if fav else 0, file_id))
        _db.commit()


def set_note(file_id: int, note: str) -> None:
    with _db_lock:
        _db.execute("UPDATE files SET note=? WHERE id=?", (note, file_id))
        _db.commit()


def get_file(file_id: int) -> dict | None:
    with _db_lock:
        row = _db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        tag_rows = _db.execute(
            "SELECT t.name FROM tags t JOIN file_tags ft ON ft.tag_id=t.id "
            "WHERE ft.file_id=? ORDER BY t.name", (file_id,)
        ).fetchall()
        d["tags"] = [tr["name"] for tr in tag_rows]
        d["dir"] = str(Path(row["path"]).parent)
    return d


def trash_file(file_id: int) -> tuple[bool, str]:
    f = get_file(file_id)
    if not f:
        return False, "not found"
    src = Path(f["path"])
    if not src.exists():
        with _db_lock:
            _db.execute("UPDATE files SET deleted=1 WHERE id=?", (file_id,))
            _db.commit()
        return True, "already missing, marked deleted"
    # 用 macOS osascript 移到废纸篓（可还原）
    try:
        subprocess.run(
            [
                "osascript", "-e",
                f'tell app "Finder" to delete POSIX file "{src}"',
            ],
            check=True, capture_output=True, timeout=10,
        )
    except Exception as e:
        return False, f"trash failed: {e}"
    with _db_lock:
        _db.execute("UPDATE files SET deleted=1 WHERE id=?", (file_id,))
        _db.commit()
    return True, "moved to trash"


def open_in_browser(file_id: int) -> bool:
    f = get_file(file_id)
    if not f:
        return False
    subprocess.Popen(["open", f["path"]])
    return True


def reveal_in_finder(file_id: int) -> bool:
    f = get_file(file_id)
    if not f:
        return False
    subprocess.Popen(["open", "-R", f["path"]])
    return True


# ---------- HTTP 处理 ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默
        return

    # ---- helpers ----
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type="text/html; charset=utf-8"):
        if not path.exists():
            self._send_json({"error": "not found"}, 404)
            return
        try:
            data = path.read_bytes()
        except OSError as e:
            self._send_json({"error": str(e)}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # 允许在 iframe 嵌入
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    # ---- routes ----
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        qs = urllib.parse.parse_qs(url.query)

        if path == "/" or path == "/index.html":
            self._send_html(INDEX_HTML)
            return

        if path == "/api/files":
            q = qs.get("q", [""])[0]
            fav = qs.get("favorite", ["0"])[0] == "1"
            tag = qs.get("tag", [""])[0]
            sort = qs.get("sort", ["mtime_desc"])[0]
            try:
                limit = max(1, min(1000, int(qs.get("limit", ["200"])[0])))
                offset = max(0, int(qs.get("offset", ["0"])[0]))
            except ValueError:
                limit, offset = 200, 0
            show_deleted = qs.get("show_deleted", ["0"])[0] == "1"
            dir_prefix = qs.get("dir", [""])[0]
            files = query_files(q=q, favorite=fav, tag=tag, sort=sort,
                                limit=limit, offset=offset, show_deleted=show_deleted,
                                dir_prefix=dir_prefix)
            total = count_files(q=q, favorite=fav, tag=tag, show_deleted=show_deleted,
                                dir_prefix=dir_prefix)
            self._send_json({"files": files, "total": total})
            return

        if path == "/api/tags":
            self._send_json({"tags": all_tags()})
            return

        if path == "/api/folders":
            self._send_json({"folders": folder_tree()})
            return

        if path == "/api/duplicates":
            groups = find_duplicates()
            total_files = sum(g["count"] for g in groups)
            wasted = sum(g["size"] * (g["count"] - 1) for g in groups)
            self._send_json({"groups": groups, "group_count": len(groups),
                             "total_files": total_files, "wasted_bytes": wasted})
            return

        if path == "/api/scan/status":
            with _scan_lock:
                state = dict(_scan_state)
            with _db_lock:
                total = _db.execute("SELECT COUNT(*) c FROM files WHERE deleted=0").fetchone()["c"]
                trashed = _db.execute("SELECT COUNT(*) c FROM files WHERE deleted=1").fetchone()["c"]
            state["total_indexed"] = total
            state["trashed_count"] = trashed
            state["last_scan_at"] = db_get_state("last_scan_at", "")
            self._send_json(state)
            return

        if path.startswith("/api/file/") and path.endswith("/raw"):
            try:
                fid = int(path.split("/")[3])
            except (IndexError, ValueError):
                self._send_json({"error": "bad id"}, 400)
                return
            f = get_file(fid)
            if not f:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_file(Path(f["path"]))
            return

        if path.startswith("/api/file/"):
            try:
                fid = int(path.split("/")[3])
            except (IndexError, ValueError):
                self._send_json({"error": "bad id"}, 400)
                return
            f = get_file(fid)
            if not f:
                self._send_json({"error": "not found"}, 404)
                return
            self._send_json(f)
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        body = self._read_json()

        if path == "/api/scan":
            roots = body.get("roots") or DEFAULT_ROOTS
            background_scan(roots)
            self._send_json({"ok": True})
            return

        if path == "/api/file/favorite":
            set_favorite(int(body["id"]), bool(body.get("favorite")))
            self._send_json({"ok": True})
            return

        if path == "/api/file/note":
            set_note(int(body["id"]), str(body.get("note", "")))
            self._send_json({"ok": True})
            return

        if path == "/api/file/tag/add":
            add_tag(int(body["id"]), str(body.get("tag", "")))
            self._send_json({"ok": True})
            return

        if path == "/api/file/tag/remove":
            remove_tag(int(body["id"]), str(body.get("tag", "")))
            self._send_json({"ok": True})
            return

        if path == "/api/file/open":
            ok = open_in_browser(int(body["id"]))
            self._send_json({"ok": ok})
            return

        if path == "/api/file/reveal":
            ok = reveal_in_finder(int(body["id"]))
            self._send_json({"ok": ok})
            return

        if path == "/api/file/trash":
            ok, msg = trash_file(int(body["id"]))
            self._send_json({"ok": ok, "msg": msg})
            return

        if path == "/api/batch/favorite":
            ids = [int(x) for x in body.get("ids", [])]
            n = batch_set_favorite(ids, bool(body.get("favorite")))
            self._send_json({"ok": True, "count": n})
            return

        if path == "/api/batch/tag":
            ids = [int(x) for x in body.get("ids", [])]
            n = batch_add_tag(ids, str(body.get("tag", "")))
            self._send_json({"ok": True, "count": n})
            return

        if path == "/api/batch/trash":
            ids = [int(x) for x in body.get("ids", [])]
            r = batch_trash(ids)
            self._send_json({"ok": True, **r})
            return

        self._send_json({"error": "not found"}, 404)


# ---------- 前端 ----------
INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>HTML 管理器</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {
    --bg: #fafaf9;
    --panel: #ffffff;
    --border: #e7e5e4;
    --text: #1c1917;
    --muted: #78716c;
    --accent: #0ea5e9;
    --accent-soft: #e0f2fe;
    --danger: #dc2626;
    --warn: #d97706;
    --sb-w: 220px;
    --pv-w: 480px;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; height: 100%; background: var(--bg);
    color: var(--text);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
    overflow: hidden;
  }
  .app {
    display: flex;
    height: 100vh;
    width: 100vw;
  }
  .sidebar { width: var(--sb-w); flex-shrink: 0; }
  .main { flex: 1 1 0; min-width: 0; }
  .preview { width: var(--pv-w); flex-shrink: 0; }
  .dragger { width: 6px; flex-shrink: 0; }

  .app.sb-collapsed .sidebar,
  .app.sb-collapsed #dragSb { display: none; }
  .app.list-collapsed .main,
  .app.list-collapsed #dragPv { display: none; }
  .app.pv-collapsed .preview,
  .app.pv-collapsed #dragPv { display: none; }
  /* 当 main 折叠且 preview 也存在时，需要 sidebar 和 preview 之间的细分隔 */
  .app.list-collapsed:not(.pv-collapsed) .preview { flex: 1 1 0; width: auto; }

  /* dragger */
  .dragger {
    background: transparent; cursor: col-resize; position: relative;
    transition: background 0.15s;
  }
  .dragger:hover, .dragger.dragging { background: var(--accent); opacity: 0.4; }
  .dragger::before {
    content: ""; position: absolute; left: 2px; top: 0; width: 2px; height: 100%;
    background: var(--border);
  }

  /* sidebar */
  .sidebar {
    background: var(--panel); border-right: 0;
    display: flex; flex-direction: column; min-width: 0;
    overflow: hidden;
  }
  .app.sb-collapsed .sidebar { display: none; }

  .brand {
    padding: 14px 16px; font-weight: 600; font-size: 15px;
    border-bottom: 1px solid var(--border); display: flex; align-items: center;
    justify-content: space-between; gap: 8px;
  }
  .brand small { font-weight: 400; color: var(--muted); font-size: 12px; }
  .nav { padding: 8px; overflow-y: auto; flex: 1; }
  .nav-item {
    display: flex; align-items: center; padding: 7px 10px; border-radius: 6px;
    cursor: pointer; color: var(--text); justify-content: space-between;
    user-select: none; gap: 6px;
  }
  .nav-item:hover { background: #f5f5f4; }
  .nav-item.active { background: var(--accent-soft); color: #0369a1; font-weight: 500; }
  .nav-item .count { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  .nav-item.active .count { color: #0369a1; }
  .nav-item .label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
  .nav-section {
    padding: 12px 12px 4px; font-size: 11px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.5px;
    display: flex; justify-content: space-between; align-items: center;
  }
  .nav-section .toggle {
    cursor: pointer; user-select: none; color: var(--muted); font-size: 11px;
  }
  .folder-tree { font-size: 13px; }
  .folder-node {
    display: flex; align-items: center; padding: 4px 8px; border-radius: 4px;
    cursor: pointer; gap: 4px; user-select: none;
  }
  .folder-node:hover { background: #f5f5f4; }
  .folder-node.active { background: var(--accent-soft); color: #0369a1; }
  .folder-twisty {
    width: 14px; display: inline-block; text-align: center;
    color: var(--muted); font-size: 10px;
  }
  .folder-name { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .folder-cnt { color: var(--muted); font-size: 11px; }
  .folder-node.active .folder-cnt { color: #0369a1; }

  .scan-box {
    border-top: 1px solid var(--border); padding: 12px;
    font-size: 12px; color: var(--muted);
  }
  .scan-box button {
    width: 100%; margin-top: 6px; padding: 6px;
    border: 1px solid var(--border); background: white; border-radius: 6px;
    cursor: pointer; font-size: 12px;
  }
  .scan-box button:hover { background: #f5f5f4; }
  .scan-box button:disabled { opacity: 0.6; cursor: wait; }

  .icon-btn {
    border: 0; background: transparent; color: var(--muted); cursor: pointer;
    padding: 4px 6px; border-radius: 4px; font-size: 14px;
  }
  .icon-btn:hover { background: #f5f5f4; color: var(--text); }

  /* main */
  .main { display: flex; flex-direction: column; min-width: 0; background: var(--bg); overflow: hidden; }
  .toolbar {
    display: flex; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border);
    background: var(--panel); align-items: center;
  }
  .toolbar .icon-btn { font-size: 16px; padding: 6px 8px; }
  .search {
    flex: 1; padding: 7px 12px; border: 1px solid var(--border); border-radius: 6px;
    background: white; font-size: 14px; outline: none; min-width: 100px;
  }
  .search:focus { border-color: var(--accent); }
  .toolbar select {
    padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px;
    background: white; font-size: 13px; cursor: pointer;
  }
  .toolbar .view-tabs { display: flex; gap: 2px; }
  .view-tab {
    padding: 6px 10px; border: 1px solid var(--border); background: white;
    cursor: pointer; font-size: 12px; color: var(--muted);
  }
  .view-tab:first-child { border-radius: 6px 0 0 6px; }
  .view-tab:last-child { border-radius: 0 6px 6px 0; border-left: 0; }
  .view-tab.active { background: var(--accent-soft); color: #0369a1; border-color: var(--accent); position: relative; z-index: 1; }

  .totalbar {
    display: flex; justify-content: space-between; align-items: center;
    color: var(--muted); font-size: 12px; padding: 6px 16px;
    border-bottom: 1px solid var(--border); background: var(--panel);
  }
  .batch-bar {
    display: none; padding: 8px 12px; background: var(--accent-soft);
    border-bottom: 1px solid var(--accent); gap: 8px; align-items: center;
    flex-wrap: wrap;
  }
  .batch-bar.show { display: flex; }
  .batch-bar .count { font-weight: 500; color: #0369a1; }
  .batch-bar button {
    padding: 5px 10px; border: 1px solid #7dd3fc; background: white;
    border-radius: 5px; cursor: pointer; font-size: 12px; color: #0369a1;
  }
  .batch-bar button:hover { background: #f0f9ff; }
  .batch-bar button.danger { color: var(--danger); border-color: #fca5a5; }
  .batch-bar input {
    padding: 4px 8px; border: 1px solid #7dd3fc; border-radius: 5px;
    font-size: 12px; width: 120px;
  }

  .list { flex: 1; overflow-y: auto; padding: 0 16px 16px; }
  .row {
    display: grid; grid-template-columns: 24px 1fr auto auto auto;
    gap: 12px; align-items: center;
    padding: 10px 12px; border-bottom: 1px solid var(--border);
    cursor: pointer; background: var(--panel);
  }
  .row.checked { background: #fef9c3; }
  .row.checked:hover { background: #fef08a; }
  .row:first-child { border-top-left-radius: 8px; border-top-right-radius: 8px; }
  .row:last-child { border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; border-bottom: 0; }
  .row:hover { background: #f5f5f4; }
  .row.selected { background: var(--accent-soft); }
  .row.selected.checked { background: #fef08a; }
  .row .checkbox { cursor: pointer; }
  .row .meta { min-width: 0; }
  .row .name {
    font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .row .path {
    color: var(--muted); font-size: 12px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    margin-top: 2px;
  }
  .row .size, .row .date {
    color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums;
  }
  .row .star {
    cursor: pointer; padding: 4px; border-radius: 4px;
    color: var(--muted); font-size: 16px;
  }
  .row .star.on { color: #f59e0b; }
  .row .star:hover { background: rgba(0,0,0,0.05); }
  .tag-pills { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
  .tag-pill {
    background: var(--accent-soft); color: #0369a1;
    padding: 1px 7px; border-radius: 99px; font-size: 11px;
  }
  .empty {
    padding: 60px 20px; text-align: center; color: var(--muted);
  }

  /* duplicates view */
  .dup-group {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; margin-bottom: 12px; overflow: hidden;
  }
  .dup-header {
    padding: 10px 14px; background: #fef3c7; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: center;
    font-size: 13px;
  }
  .dup-header b { color: var(--warn); }
  .dup-files { padding: 0; }
  .dup-files .row { border-radius: 0; }
  .dup-files .row:last-child { border-bottom: 0; }

  /* preview */
  .preview { background: var(--panel); border-left: 0; display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
  .app.pv-collapsed .preview { display: none; }
  .preview.meta-collapsed .preview-header,
  .preview.meta-collapsed .preview-actions,
  .preview.meta-collapsed .preview-section { display: none !important; }
  .preview.meta-collapsed .meta-bar { background: #fef3c7; }
  .meta-bar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 4px 12px; border-bottom: 1px solid var(--border);
    background: var(--panel); font-size: 12px; color: var(--muted);
  }
  .meta-bar .meta-toggle {
    background: transparent; border: 0; cursor: pointer; color: var(--muted);
    padding: 4px 8px; border-radius: 4px; font-size: 12px;
  }
  .meta-bar .meta-toggle:hover { background: rgba(0,0,0,0.05); color: var(--text); }
  .meta-bar .meta-title-mini {
    flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-weight: 500; color: var(--text); margin-right: 8px;
  }
  .preview-header { padding: 14px 16px; border-bottom: 1px solid var(--border); }
  .preview-title { font-weight: 600; font-size: 15px; word-break: break-all; }
  .preview-path { color: var(--muted); font-size: 12px; word-break: break-all; margin-top: 4px; font-family: ui-monospace, Menlo, monospace; }
  .preview-actions { display: flex; gap: 6px; padding: 10px 16px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .preview-actions button {
    padding: 6px 12px; border: 1px solid var(--border); background: white;
    border-radius: 6px; cursor: pointer; font-size: 12px;
  }
  .preview-actions button:hover { background: #f5f5f4; }
  .preview-actions button.danger { color: var(--danger); border-color: #fecaca; }
  .preview-actions button.danger:hover { background: #fef2f2; }
  .preview-body { flex: 1; overflow: hidden; display: flex; flex-direction: column; min-height: 200px; }
  .preview-frame { flex: 1; border: 0; background: white; }
  .preview-section { padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .preview-section .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
  .tag-input { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
  .tag-pill.removable { cursor: pointer; }
  .tag-pill.removable:hover { background: #fee2e2; color: var(--danger); }
  .tag-add {
    border: 1px dashed var(--border); background: transparent; color: var(--muted);
    padding: 1px 7px; border-radius: 99px; font-size: 11px; cursor: pointer;
  }
  .tag-add input {
    border: 0; outline: 0; background: transparent; width: 80px; font: inherit;
  }
  .tag-suggest { display: flex; gap: 4px; flex-wrap: wrap; }
  .tag-suggest .tag-pill {
    cursor: pointer; background: #f5f5f4; color: var(--muted);
    border: 1px dashed transparent;
  }
  .tag-suggest .tag-pill:hover { background: var(--accent-soft); color: #0369a1; border-color: var(--accent); }
  .tag-suggest .tag-pill .cnt { opacity: 0.6; margin-left: 4px; }
  .note-area {
    width: 100%; min-height: 60px; border: 1px solid var(--border);
    border-radius: 6px; padding: 8px; font: inherit; resize: vertical;
    background: white;
  }
  .preview-empty { padding: 80px 20px; text-align: center; color: var(--muted); }

  /* floating reopen buttons */
  .reopen-sb, .reopen-pv, .reopen-list {
    position: fixed; top: 50%; transform: translateY(-50%);
    width: 24px; height: 50px; background: var(--panel);
    border: 1px solid var(--border); cursor: pointer;
    display: none; z-index: 50; align-items: center; justify-content: center;
    color: var(--muted); font-size: 12px;
  }
  .reopen-sb { border-left: 0; border-radius: 0 6px 6px 0; }
  .reopen-pv { right: 0; border-right: 0; border-radius: 6px 0 0 6px; }
  .reopen-list {
    top: 80px; transform: none; height: 32px; width: 32px;
    border-radius: 50%;
  }
  .reopen-sb:hover, .reopen-pv:hover, .reopen-list:hover { background: var(--accent-soft); color: #0369a1; }

  .ctx-menu {
    position: fixed; background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; box-shadow: 0 8px 24px rgba(0,0,0,0.12);
    padding: 4px 0; min-width: 180px; z-index: 200; font-size: 13px;
    user-select: none;
  }
  .ctx-item {
    padding: 7px 14px; cursor: pointer; display: flex; gap: 8px; align-items: center;
  }
  .ctx-item:hover { background: var(--accent-soft); color: #0369a1; }
  .ctx-item.danger { color: var(--danger); }
  .ctx-item.danger:hover { background: #fef2f2; }
  .ctx-sep { height: 1px; background: var(--border); margin: 4px 0; }
  .ctx-item .kbd { margin-left: auto; color: var(--muted); font-size: 11px; }

  .toast {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #1c1917; color: white; padding: 8px 16px; border-radius: 6px;
    font-size: 13px; opacity: 0; transition: opacity .2s; pointer-events: none;
    z-index: 100;
  }
  .toast.show { opacity: 0.95; }

  .scan-progress {
    height: 3px; background: var(--accent); width: 100%; transform-origin: left;
    animation: progress 1.5s ease-in-out infinite;
  }
  @keyframes progress {
    0% { transform: scaleX(0); }
    50% { transform: scaleX(0.7); }
    100% { transform: scaleX(1); }
  }
</style>
</head>
<body>
<div class="app" id="app">
  <aside class="sidebar">
    <div class="brand">
      <span>HTML 管理器 <small id="totalCount">—</small></span>
      <button class="icon-btn" id="collapseSb" title="折叠侧栏">‹</button>
    </div>
    <div class="nav" id="nav">
      <div class="nav-item active" data-filter='{"type":"all"}'>
        <span class="label">📄 全部文件</span><span class="count" id="cnt-all">—</span>
      </div>
      <div class="nav-item" data-filter='{"type":"favorite"}'>
        <span class="label">⭐ 收藏</span><span class="count" id="cnt-fav">—</span>
      </div>
      <div class="nav-item" data-filter='{"type":"duplicates"}'>
        <span class="label">🔁 重复文件</span><span class="count" id="cnt-dup">—</span>
      </div>
      <div class="nav-item" data-filter='{"type":"deleted"}'>
        <span class="label">🗑 已删除</span><span class="count" id="cnt-del">—</span>
      </div>

      <div class="nav-section">
        <span>文件夹</span>
        <span class="toggle" id="toggleFolders">收起</span>
      </div>
      <div id="folderTree" class="folder-tree"></div>

      <div class="nav-section">标签</div>
      <div id="tagList"></div>
    </div>
    <div class="scan-box">
      <div id="scanStatus">未扫描</div>
      <button id="scanBtn">开始扫描</button>
    </div>
  </aside>

  <div class="dragger" id="dragSb" data-target="sb"></div>

  <main class="main">
    <div class="toolbar">
      <input class="search" id="searchInput" placeholder="搜索文件名 / 标题 / 路径...">
      <select id="sortSelect">
        <option value="mtime_desc">最近修改</option>
        <option value="mtime_asc">最早修改</option>
        <option value="name_asc">名称 A→Z</option>
        <option value="name_desc">名称 Z→A</option>
        <option value="size_desc">尺寸大→小</option>
        <option value="size_asc">尺寸小→大</option>
      </select>
      <button class="icon-btn" id="collapseList" title="折叠列表">⇤</button>
      <button class="icon-btn" id="collapsePv" title="折叠预览">›</button>
    </div>
    <div class="totalbar">
      <span id="resultTotal">—</span>
      <span id="contextLabel" style="color: var(--accent);"></span>
    </div>
    <div class="batch-bar" id="batchBar">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;width:100%">
        <span class="count" id="batchCount">0 已选</span>
        <button id="batchSelectAll">全选当前页</button>
        <button id="batchClear">清空</button>
        <button id="batchFav">⭐ 加收藏</button>
        <input id="batchTagInput" placeholder="新标签名" maxlength="30">
        <button id="batchTag">+ 加新标签</button>
        <button id="batchTrash" class="danger">🗑 移到废纸篓</button>
      </div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;width:100%;font-size:12px;color:#0369a1">
        <span>快速贴已有标签：</span>
        <span id="batchTagSuggest" style="display:flex;gap:4px;flex-wrap:wrap"></span>
      </div>
    </div>
    <div class="list" id="list"></div>
  </main>

  <div class="dragger" id="dragPv" data-target="pv"></div>

  <aside class="preview" id="preview">
    <div class="preview-empty">选择一个文件查看详情</div>
  </aside>
</div>
<button class="reopen-sb" id="reopenSb" title="展开侧栏">›</button>
<button class="reopen-list" id="reopenList" title="展开列表">⇥</button>
<button class="reopen-pv" id="reopenPv" title="展开预览">‹</button>
<div class="toast" id="toast"></div>

<script>
const state = {
  filter: { type: "all" },
  q: "",
  sort: "mtime_desc",
  files: [],
  selected: null,
  total: 0,
  checked: new Set(),
  view: "list", // "list" | "duplicates"
  expandedFolders: new Set(),
  foldersHidden: false,
};

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 1800);
}

function fmtSize(b) {
  if (b == null) return "—";
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB";
  return (b / 1024 / 1024).toFixed(1) + " MB";
}

function fmtDate(t) {
  if (!t) return "—";
  const d = new Date(t * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + " 天前";
  return d.toISOString().slice(0, 10);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  return res.json();
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

async function loadFiles() {
  if (state.filter.type === "duplicates") {
    state.view = "duplicates";
    await loadDuplicates();
    return;
  }
  state.view = "list";
  const params = new URLSearchParams();
  params.set("sort", state.sort);
  if (state.q) params.set("q", state.q);
  if (state.filter.type === "favorite") params.set("favorite", "1");
  if (state.filter.type === "deleted") params.set("show_deleted", "1");
  if (state.filter.type === "tag") params.set("tag", state.filter.tag);
  if (state.filter.type === "folder") params.set("dir", state.filter.dir);
  params.set("limit", "500");

  const data = await api("/api/files?" + params.toString());
  state.files = data.files;
  state.total = data.total;
  renderList();
  document.getElementById("resultTotal").textContent =
    `${data.total} 个结果` + (data.files.length < data.total ? ` (显示前 ${data.files.length})` : "");
  updateContextLabel();
}

function updateContextLabel() {
  const el = document.getElementById("contextLabel");
  if (state.filter.type === "folder") {
    el.textContent = "📁 " + state.filter.dir.replace(/^.*\//, "") || state.filter.dir;
    el.title = state.filter.dir;
  } else if (state.filter.type === "tag") {
    el.textContent = "# " + state.filter.tag;
  } else if (state.filter.type === "duplicates") {
    el.textContent = "重复文件";
  } else {
    el.textContent = "";
  }
}

async function loadDuplicates() {
  const data = await api("/api/duplicates");
  const list = document.getElementById("list");
  document.getElementById("resultTotal").textContent =
    `${data.group_count} 组重复 · 共 ${data.total_files} 个文件 · 浪费 ${fmtSize(data.wasted_bytes)}`;
  updateContextLabel();
  if (!data.groups.length) {
    list.innerHTML = `<div class="empty">没有发现重复的 HTML 文件 🎉</div>`;
    return;
  }
  list.innerHTML = "";
  // 收集所有文件供 selectFile 等使用
  state.files = [];
  for (const g of data.groups) {
    for (const f of g.files) { f.tags = f.tags || []; state.files.push(f); }
    const grp = document.createElement("div");
    grp.className = "dup-group";
    grp.innerHTML = `
      <div class="dup-header">
        <span><b>${g.count} 个相同文件</b> · ${fmtSize(g.size)} · <code style="color:var(--muted);font-size:11px">${g.sha1.slice(0, 12)}</code></span>
        <span style="color:var(--muted)">浪费 ${fmtSize(g.size * (g.count - 1))}</span>
      </div>
      <div class="dup-files"></div>
    `;
    const filesEl = grp.querySelector(".dup-files");
    for (const f of g.files) {
      filesEl.appendChild(buildRow(f));
    }
    list.appendChild(grp);
  }
}

function buildRow(f) {
  const row = document.createElement("div");
  row.className = "row" +
    (state.selected === f.id ? " selected" : "") +
    (state.checked.has(f.id) ? " checked" : "");
  const isChecked = state.checked.has(f.id);
  row.innerHTML = `
    <input type="checkbox" class="checkbox" data-id="${f.id}" ${isChecked ? "checked" : ""}>
    <div class="meta">
      <div class="name">${escapeHtml(f.title || f.name)}</div>
      <div class="path">${escapeHtml(f.path)}</div>
      ${(f.tags && f.tags.length) ? `<div class="tag-pills">${f.tags.map(t => `<span class="tag-pill">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
    </div>
    <div class="size">${fmtSize(f.size)}</div>
    <div class="date">${fmtDate(f.mtime)}</div>
    <div class="star ${f.favorite ? "on" : ""}" data-id="${f.id}">${f.favorite ? "★" : "☆"}</div>
  `;
  row.addEventListener("click", (e) => {
    if (e.target.classList.contains("star") || e.target.classList.contains("checkbox")) return;
    selectFile(f);
  });
  row.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    showContextMenu(e.clientX, e.clientY, f);
  });
  row.querySelector(".checkbox").addEventListener("change", (e) => {
    if (e.target.checked) state.checked.add(f.id); else state.checked.delete(f.id);
    row.classList.toggle("checked", e.target.checked);
    updateBatchBar();
  });
  row.querySelector(".star").addEventListener("click", async (e) => {
    e.stopPropagation();
    const newFav = !f.favorite;
    await api("/api/file/favorite", { method: "POST", body: { id: f.id, favorite: newFav } });
    f.favorite = newFav;
    row.querySelector(".star").className = "star " + (newFav ? "on" : "");
    row.querySelector(".star").textContent = newFav ? "★" : "☆";
    loadCounts();
  });
  return row;
}

function renderList() {
  const list = document.getElementById("list");
  if (!state.files.length) {
    list.innerHTML = `<div class="empty">没有匹配的 HTML 文件<br><small>试试搜索其他关键词，或点左下角"开始扫描"</small></div>`;
    return;
  }
  list.innerHTML = "";
  for (const f of state.files) list.appendChild(buildRow(f));
}

function updateBatchBar() {
  const bar = document.getElementById("batchBar");
  const n = state.checked.size;
  bar.classList.toggle("show", n > 0);
  document.getElementById("batchCount").textContent = `${n} 已选`;
  if (n > 0) renderBatchTagSuggest();
}

async function renderBatchTagSuggest() {
  const wrap = document.getElementById("batchTagSuggest");
  if (!wrap) return;
  const data = await api("/api/tags");
  if (!data.tags.length) {
    wrap.innerHTML = `<span style="color:var(--muted)">（暂无）</span>`;
    return;
  }
  wrap.innerHTML = "";
  for (const t of data.tags) {
    const el = document.createElement("span");
    el.className = "tag-pill";
    el.style.cursor = "pointer";
    el.style.background = "white";
    el.style.border = "1px solid #7dd3fc";
    el.innerHTML = `+ ${escapeHtml(t.name)} <span style="opacity:0.6">${t.cnt}</span>`;
    el.addEventListener("click", async () => {
      const ids = [...state.checked];
      if (!ids.length) return;
      await api("/api/batch/tag", { method: "POST", body: { ids, tag: t.name } });
      toast(`已为 ${ids.length} 个文件加标签 #${t.name}`);
      await loadFiles();
      await loadTags();
      renderBatchTagSuggest();
    });
    wrap.appendChild(el);
  }
}

function closeContextMenu() {
  const m = document.getElementById("__ctxMenu");
  if (m) m.remove();
}

function showContextMenu(x, y, f) {
  closeContextMenu();
  const items = [
    { label: "📋 复制路径", action: async () => { await navigator.clipboard.writeText(f.path); toast("路径已复制"); } },
    { label: "📋 复制文件名", action: async () => { await navigator.clipboard.writeText(f.name); toast("文件名已复制"); } },
    { label: "📋 复制标题", action: async () => { await navigator.clipboard.writeText(f.title || f.name); toast("标题已复制"); }, hidden: !(f.title) },
    { sep: true },
    { label: "🌐 在浏览器打开", action: async () => { await api("/api/file/open", { method: "POST", body: { id: f.id } }); toast("已打开"); } },
    { label: "📁 在 Finder 显示", action: async () => { await api("/api/file/reveal", { method: "POST", body: { id: f.id } }); toast("已显示"); } },
    { sep: true },
    { label: f.favorite ? "★ 取消收藏" : "☆ 加入收藏", action: async () => {
        await api("/api/file/favorite", { method: "POST", body: { id: f.id, favorite: !f.favorite } });
        f.favorite = !f.favorite;
        loadFiles(); loadCounts();
    } },
    { label: "🗑 移到废纸篓", danger: true, action: async () => {
        if (!confirm(`确定将以下文件移到废纸篓？\n\n${f.path}`)) return;
        const r = await api("/api/file/trash", { method: "POST", body: { id: f.id } });
        if (r.ok) { toast("已移到废纸篓"); state.selected = null; await loadFiles(); await loadCounts(); await loadFolders(); renderPreview(null); }
        else alert("失败：" + r.msg);
    } },
  ].filter(it => !it.hidden);

  const menu = document.createElement("div");
  menu.className = "ctx-menu";
  menu.id = "__ctxMenu";
  for (const it of items) {
    if (it.sep) {
      const sep = document.createElement("div");
      sep.className = "ctx-sep";
      menu.appendChild(sep);
    } else {
      const el = document.createElement("div");
      el.className = "ctx-item" + (it.danger ? " danger" : "");
      el.textContent = it.label;
      el.addEventListener("click", () => { closeContextMenu(); it.action(); });
      menu.appendChild(el);
    }
  }
  document.body.appendChild(menu);
  // 位置裁切到视口
  const rect = menu.getBoundingClientRect();
  const px = Math.min(x, window.innerWidth - rect.width - 6);
  const py = Math.min(y, window.innerHeight - rect.height - 6);
  menu.style.left = px + "px";
  menu.style.top = py + "px";
}

window.addEventListener("click", closeContextMenu);
window.addEventListener("contextmenu", (e) => {
  // 只有在 row 上右键才保留菜单（其他位置原生）
  if (!e.target.closest(".row")) closeContextMenu();
});
window.addEventListener("scroll", closeContextMenu, true);
window.addEventListener("keydown", (e) => { if (e.key === "Escape") closeContextMenu(); });

async function selectFile(f) {
  state.selected = f.id;
  // 只更新选中样式
  document.querySelectorAll(".row").forEach(el => el.classList.remove("selected"));
  // re-render quick (avoid losing checkboxes via full re-render)
  renderList();
  const data = await api(`/api/file/${f.id}`);
  renderPreview(data);
}

function renderPreview(f) {
  const p = document.getElementById("preview");
  if (!f) {
    p.innerHTML = `<div class="preview-empty">选择一个文件查看详情</div>`;
    return;
  }
  p.innerHTML = `
    <div class="meta-bar">
      <div class="meta-title-mini" title="${escapeHtml(f.path)}">${escapeHtml(f.title || f.name)}</div>
      <button class="meta-toggle" id="metaToggle" title="折叠/展开操作">▾ 操作</button>
    </div>
    <div class="preview-header">
      <div class="preview-title">${escapeHtml(f.title || f.name)}</div>
      <div class="preview-path">${escapeHtml(f.path)}</div>
    </div>
    <div class="preview-actions">
      <button data-act="open">在浏览器打开</button>
      <button data-act="reveal">在 Finder 显示</button>
      <button data-act="copy">复制路径</button>
      <button data-act="trash" class="danger">移到废纸篓</button>
    </div>
    <div class="preview-section">
      <div class="label">标签</div>
      <div class="tag-input" id="tagBox">
        ${(f.tags||[]).map(t => `<span class="tag-pill removable" data-tag="${escapeHtml(t)}">${escapeHtml(t)} ✕</span>`).join("")}
        <span class="tag-add"><input id="tagInput" placeholder="+ 新建标签" maxlength="30"></span>
      </div>
      <div class="label" style="margin-top:10px">已有标签（点击添加）</div>
      <div class="tag-suggest" id="tagSuggest"><span style="color:var(--muted);font-size:12px">加载中...</span></div>
    </div>
    <div class="preview-section">
      <div class="label">备注</div>
      <textarea class="note-area" id="noteArea" placeholder="给这个文件加个备注...">${escapeHtml(f.note || "")}</textarea>
    </div>
    <div class="preview-section" style="display:flex;justify-content:space-between;font-size:12px;color:var(--muted)">
      <span>${fmtSize(f.size)}</span>
      <span>修改于 ${fmtDate(f.mtime)}</span>
    </div>
    <div class="preview-body">
      <iframe class="preview-frame" id="previewFrame" src="/api/file/${f.id}/raw" sandbox="allow-same-origin"></iframe>
    </div>
  `;

  p.querySelector('[data-act="open"]').onclick = async () => {
    await api("/api/file/open", { method: "POST", body: { id: f.id } });
    toast("已在浏览器打开");
  };
  p.querySelector('[data-act="reveal"]').onclick = async () => {
    await api("/api/file/reveal", { method: "POST", body: { id: f.id } });
    toast("已在 Finder 中显示");
  };
  p.querySelector('[data-act="copy"]').onclick = async () => {
    await navigator.clipboard.writeText(f.path);
    toast("路径已复制");
  };
  p.querySelector('[data-act="trash"]').onclick = async () => {
    if (!confirm(`确定将以下文件移到废纸篓？

${f.path}`)) return;
    const r = await api("/api/file/trash", { method: "POST", body: { id: f.id } });
    if (r.ok) {
      toast("已移到废纸篓");
      state.selected = null;
      await loadFiles();
      await loadCounts();
      await loadFolders();
      renderPreview(null);
    } else {
      alert("失败：" + r.msg);
    }
  };
  p.querySelectorAll(".tag-pill.removable").forEach(el => {
    el.onclick = async () => {
      const tag = el.dataset.tag;
      await api("/api/file/tag/remove", { method: "POST", body: { id: f.id, tag } });
      f.tags = f.tags.filter(t => t !== tag);
      renderPreview(f);
      loadTags();
      loadFiles();
    };
  });
  const tagInput = document.getElementById("tagInput");
  tagInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && tagInput.value.trim()) {
      const tag = tagInput.value.trim();
      await api("/api/file/tag/add", { method: "POST", body: { id: f.id, tag } });
      if (!f.tags.includes(tag)) f.tags.push(tag);
      tagInput.value = "";
      renderPreview(f);
      loadTags();
      loadFiles();
    }
  });
  const noteArea = document.getElementById("noteArea");
  let noteTimer;
  noteArea.addEventListener("input", () => {
    clearTimeout(noteTimer);
    noteTimer = setTimeout(async () => {
      await api("/api/file/note", { method: "POST", body: { id: f.id, note: noteArea.value } });
      toast("已保存备注");
    }, 600);
  });

  // 已有标签建议（一键添加）—— 先调用，避免被后续异常打断
  renderTagSuggest(f);

  // 元数据折叠
  if (localStorage.getItem("metaCollapsed") === "1") {
    p.classList.add("meta-collapsed");
  }
  const metaToggleBtn = document.getElementById("metaToggle");
  metaToggleBtn.textContent = p.classList.contains("meta-collapsed") ? "▸ 操作" : "▾ 操作";
  metaToggleBtn.addEventListener("click", () => {
    const collapsed = p.classList.toggle("meta-collapsed");
    localStorage.setItem("metaCollapsed", collapsed ? "1" : "0");
    metaToggleBtn.textContent = collapsed ? "▸ 操作" : "▾ 操作";
  });
}

async function renderTagSuggest(f) {
  const wrap = document.getElementById("tagSuggest");
  if (!wrap) return;
  const data = await api("/api/tags");
  const have = new Set(f.tags || []);
  const suggestions = data.tags.filter(t => !have.has(t.name));
  if (!suggestions.length) {
    wrap.innerHTML = `<span style="color:var(--muted);font-size:12px">暂无其他标签可用</span>`;
    return;
  }
  wrap.innerHTML = "";
  for (const t of suggestions) {
    const el = document.createElement("span");
    el.className = "tag-pill";
    el.innerHTML = `+ ${escapeHtml(t.name)}<span class="cnt">${t.cnt}</span>`;
    el.addEventListener("click", async () => {
      await api("/api/file/tag/add", { method: "POST", body: { id: f.id, tag: t.name } });
      f.tags = [...(f.tags || []), t.name];
      renderPreview(f);
      loadTags();
      loadFiles();
    });
    wrap.appendChild(el);
  }
}

async function loadTags() {
  const data = await api("/api/tags");
  const wrap = document.getElementById("tagList");
  wrap.innerHTML = "";
  if (!data.tags.length) {
    wrap.innerHTML = `<div style="padding:6px 10px;font-size:12px;color:var(--muted)">还没有标签</div>`;
    return;
  }
  for (const t of data.tags) {
    const el = document.createElement("div");
    el.className = "nav-item";
    el.dataset.filter = JSON.stringify({ type: "tag", tag: t.name });
    el.innerHTML = `<span class="label"># ${escapeHtml(t.name)}</span><span class="count">${t.cnt}</span>`;
    if (state.filter.type === "tag" && state.filter.tag === t.name) {
      el.classList.add("active");
    }
    el.addEventListener("click", () => setFilter(JSON.parse(el.dataset.filter)));
    wrap.appendChild(el);
  }
}

async function loadFolders() {
  const data = await api("/api/folders");
  const wrap = document.getElementById("folderTree");
  if (state.foldersHidden) { wrap.innerHTML = ""; return; }
  // 按 path 排序，构建 hierarchy
  const folders = data.folders;
  const byParent = {};
  for (const f of folders) {
    const k = f.parent || "__root__";
    (byParent[k] = byParent[k] || []).push(f);
  }
  // 找根节点（不在 folders 里有 parent 的，或 depth==0）
  const roots = folders.filter(f => f.depth === 0 || !folders.find(p => p.path === f.parent));
  wrap.innerHTML = "";
  function renderNode(node, container) {
    const div = document.createElement("div");
    div.className = "folder-node" +
      (state.filter.type === "folder" && state.filter.dir === node.path ? " active" : "");
    div.style.paddingLeft = (8 + node.depth * 12) + "px";
    const children = byParent[node.path] || [];
    const expanded = state.expandedFolders.has(node.path);
    const twisty = children.length ? (expanded ? "▼" : "▶") : "·";
    div.innerHTML = `
      <span class="folder-twisty">${twisty}</span>
      <span class="folder-name">📁 ${escapeHtml(node.name)}</span>
      <span class="folder-cnt">${node.count}</span>
    `;
    div.querySelector(".folder-twisty").addEventListener("click", (e) => {
      e.stopPropagation();
      if (expanded) state.expandedFolders.delete(node.path);
      else state.expandedFolders.add(node.path);
      loadFolders();
    });
    div.addEventListener("click", () => {
      setFilter({ type: "folder", dir: node.path });
    });
    container.appendChild(div);
    if (expanded) {
      for (const c of children) renderNode(c, container);
    }
  }
  for (const r of roots) renderNode(r, wrap);
}

async function loadCounts() {
  const [all, fav, del, dup] = await Promise.all([
    api("/api/files?limit=1"),
    api("/api/files?favorite=1&limit=1"),
    api("/api/files?show_deleted=1&limit=1"),
    api("/api/duplicates"),
  ]);
  document.getElementById("cnt-all").textContent = all.total;
  document.getElementById("cnt-fav").textContent = fav.total;
  document.getElementById("cnt-del").textContent = Math.max(0, del.total - all.total);
  document.getElementById("cnt-dup").textContent = dup.group_count;
  document.getElementById("totalCount").textContent = all.total + " 项";
}

function setFilter(f) {
  state.filter = f;
  state.checked.clear();
  updateBatchBar();
  document.querySelectorAll(".nav-item").forEach(el => {
    if (!el.dataset.filter) return;
    el.classList.toggle("active",
      JSON.stringify(JSON.parse(el.dataset.filter)) === JSON.stringify(f));
  });
  loadFolders();
  loadFiles();
}

// init
document.querySelectorAll(".nav-item[data-filter]").forEach(el => {
  el.addEventListener("click", () => setFilter(JSON.parse(el.dataset.filter)));
});
let searchTimer;
document.getElementById("searchInput").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.q = e.target.value.trim();
    loadFiles();
  }, 200);
});
document.getElementById("sortSelect").addEventListener("change", (e) => {
  state.sort = e.target.value;
  loadFiles();
});

document.getElementById("toggleFolders").addEventListener("click", () => {
  state.foldersHidden = !state.foldersHidden;
  document.getElementById("toggleFolders").textContent = state.foldersHidden ? "展开" : "收起";
  loadFolders();
});

// 折叠/展开
const appEl = document.getElementById("app");
function applyCollapse() {
  const sb = localStorage.getItem("sbCollapsed") === "1";
  const pv = localStorage.getItem("pvCollapsed") === "1";
  let list = localStorage.getItem("listCollapsed") === "1";
  // 不允许 list 和 preview 同时折叠（否则 main 没东西显示），自动恢复 list
  if (list && pv) { list = false; localStorage.removeItem("listCollapsed"); }
  appEl.classList.toggle("sb-collapsed", sb);
  appEl.classList.toggle("pv-collapsed", pv);
  appEl.classList.toggle("list-collapsed", list);

  const reopenSb = document.getElementById("reopenSb");
  const reopenList = document.getElementById("reopenList");
  const reopenPv = document.getElementById("reopenPv");
  reopenSb.style.display = sb ? "flex" : "none";
  reopenPv.style.display = pv ? "flex" : "none";
  reopenList.style.display = list ? "flex" : "none";
  // sidebar 折叠时 reopen-sb 贴 viewport 左侧；list 折叠时 reopen-list 紧贴 sidebar 右侧
  if (sb) { reopenSb.style.left = "0"; }
  if (list) {
    // 放到 sidebar 右边
    const sbWidth = sb ? 0 : (parseInt(getComputedStyle(document.documentElement).getPropertyValue("--sb-w")) || 220);
    reopenList.style.left = (sbWidth + 8) + "px";
  }
}
document.getElementById("collapseSb").addEventListener("click", () => {
  localStorage.setItem("sbCollapsed", "1");
  applyCollapse();
});
document.getElementById("collapsePv").addEventListener("click", () => {
  localStorage.setItem("pvCollapsed", "1");
  applyCollapse();
});
document.getElementById("collapseList").addEventListener("click", () => {
  if (localStorage.getItem("pvCollapsed") === "1") {
    toast("请先展开预览，否则没东西看了");
    return;
  }
  localStorage.setItem("listCollapsed", "1");
  applyCollapse();
});
document.getElementById("reopenSb").addEventListener("click", () => {
  localStorage.removeItem("sbCollapsed");
  applyCollapse();
});
document.getElementById("reopenPv").addEventListener("click", () => {
  localStorage.removeItem("pvCollapsed");
  applyCollapse();
});
document.getElementById("reopenList").addEventListener("click", () => {
  localStorage.removeItem("listCollapsed");
  applyCollapse();
});

// 拖拽改宽度
function setupDrag(id, target, minW, maxW) {
  const drag = document.getElementById(id);
  const cssVar = target === "sb" ? "--sb-w" : "--pv-w";
  const lsKey = target === "sb" ? "sbWidth" : "pvWidth";
  const saved = localStorage.getItem(lsKey);
  if (saved) document.documentElement.style.setProperty(cssVar, saved + "px");
  let dragging = false;
  drag.addEventListener("mousedown", (e) => {
    dragging = true;
    drag.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    // 关键：拖动期间让所有 iframe 不吃鼠标事件，否则鼠标进入 iframe 后 mousemove 不触发
    document.querySelectorAll("iframe").forEach(f => f.style.pointerEvents = "none");
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    let w;
    if (target === "sb") w = e.clientX;
    else w = window.innerWidth - e.clientX;
    w = Math.max(minW, Math.min(maxW, w));
    document.documentElement.style.setProperty(cssVar, w + "px");
    localStorage.setItem(lsKey, w);
  });
  window.addEventListener("mouseup", () => {
    if (dragging) {
      dragging = false; drag.classList.remove("dragging");
      document.body.style.cursor = ""; document.body.style.userSelect = "";
      document.querySelectorAll("iframe").forEach(f => f.style.pointerEvents = "");
    }
  });
}
setupDrag("dragSb", "sb", 160, 500);
setupDrag("dragPv", "pv", 280, 1000);
applyCollapse();

// 批量操作
document.getElementById("batchSelectAll").addEventListener("click", () => {
  for (const f of state.files) state.checked.add(f.id);
  renderList();
  updateBatchBar();
});
document.getElementById("batchClear").addEventListener("click", () => {
  state.checked.clear();
  renderList();
  updateBatchBar();
});
document.getElementById("batchFav").addEventListener("click", async () => {
  const ids = [...state.checked];
  await api("/api/batch/favorite", { method: "POST", body: { ids, favorite: true } });
  toast(`已收藏 ${ids.length} 个文件`);
  state.checked.clear();
  await loadFiles();
  await loadCounts();
});
document.getElementById("batchTag").addEventListener("click", async () => {
  const tag = document.getElementById("batchTagInput").value.trim();
  if (!tag) { toast("请输入标签名"); return; }
  const ids = [...state.checked];
  await api("/api/batch/tag", { method: "POST", body: { ids, tag } });
  toast(`已为 ${ids.length} 个文件加标签 #${tag}`);
  document.getElementById("batchTagInput").value = "";
  state.checked.clear();
  await loadFiles();
  await loadTags();
});
document.getElementById("batchTrash").addEventListener("click", async () => {
  const ids = [...state.checked];
  if (!confirm(`确定将选中的 ${ids.length} 个文件移到废纸篓？`)) return;
  const r = await api("/api/batch/trash", { method: "POST", body: { ids } });
  toast(`已移到废纸篓：${r.ok}/${ids.length}`);
  state.checked.clear();
  state.selected = null;
  await loadFiles();
  await loadCounts();
  await loadFolders();
  renderPreview(null);
});

async function pollScanStatus() {
  const s = await api("/api/scan/status");
  const el = document.getElementById("scanStatus");
  const btn = document.getElementById("scanBtn");
  if (s.running) {
    el.innerHTML = `扫描中... 新增 <b>${s.new}</b> / 更新 <b>${s.updated}</b><div class="scan-progress" style="margin-top:6px"></div>`;
    btn.disabled = true;
    btn.textContent = "扫描中...";
  } else {
    btn.disabled = false;
    btn.textContent = "重新扫描";
    if (s.last_scan_at) {
      const lastDate = new Date(parseFloat(s.last_scan_at) * 1000);
      el.innerHTML = `已索引 <b>${s.total_indexed}</b> 个 HTML<br>上次扫描：${lastDate.toLocaleString()}`;
    } else {
      el.textContent = "尚未扫描";
      btn.textContent = "开始扫描";
    }
    if (s.result && pollScanStatus._wasRunning) {
      toast(`扫描完成：新增 ${s.result.new}，更新 ${s.result.updated}，耗时 ${s.result.elapsed_sec}s`);
      loadFiles();
      loadCounts();
      loadTags();
      loadFolders();
    }
  }
  pollScanStatus._wasRunning = s.running;
}

document.getElementById("scanBtn").addEventListener("click", async () => {
  await api("/api/scan", { method: "POST", body: {} });
  pollScanStatus();
});

setInterval(pollScanStatus, 2000);
pollScanStatus();
loadCounts();
loadFiles();
loadTags();
loadFolders();
</script>
</body>
</html>
"""


# ---------- main ----------
def main():
    db_init()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n✨ HTML 管理器已启动")
    print(f"   {url}")
    print(f"   数据：{DB_PATH}")
    print(f"   按 Ctrl+C 停止\n")

    # 自动打开浏览器
    if "--no-open" not in sys.argv:
        try:
            subprocess.Popen(["open", url])
        except Exception:
            pass

    # 首次启动若库为空，自动开始一次后台扫描
    with _db_lock:
        n = _db.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
    if n == 0 and "--no-scan" not in sys.argv:
        print("   首次启动，开始扫描 ~/ ...\n")
        background_scan(DEFAULT_ROOTS)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n再见 👋")
        server.shutdown()


if __name__ == "__main__":
    main()
