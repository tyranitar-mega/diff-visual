#!/usr/bin/env python3
"""
diff2html - Generate GitHub-style HTML diff from a git diff.

Usage:
  python diff2html.py [-o output.html]               diff current branch vs main/master
  python diff2html.py <commit-hash> [-o output.html]  diff a specific commit
  python diff2html.py <diff-file> [-o output.html]    render an existing diff file
  python diff2html.py --stdin [-o output.html]        read diff from stdin
"""

import argparse
import os
import re
import subprocess
import sys
from html import escape
from pathlib import Path

try:
    from pygments import lex
    from pygments.lexers import get_lexer_for_filename
    from pygments.lexers.special import TextLexer
    from pygments.token import Token
    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

class DiffFile:
    def __init__(self):
        self.old_path = ""
        self.new_path = ""
        self.status = "modified"  # added | deleted | modified | renamed | copied
        self.similarity = 100
        self.binary = False
        self.hunks: list[Hunk] = []


class Hunk:
    def __init__(self, old_start: int, old_count: int, new_start: int, new_count: int, header: str = ""):
        self.old_start = old_start
        self.old_count = old_count
        self.new_start = new_start
        self.new_count = new_count
        self.header = header
        self.lines: list[DiffLine] = []


class DiffLine:
    def __init__(self, kind: str, content: str, old_ln: int = 0, new_ln: int = 0):
        # kind: ' ', '-', '+', or '\' (for no newline messages)
        self.kind = kind
        self.content = content
        self.old_ln = old_ln
        self.new_ln = new_ln


def _finalize_hunk(f: DiffFile, h: Hunk | None) -> Hunk | None:
    """Append current hunk to file and return None."""
    if h is not None:
        f.hunks.append(h)
    return None


def _finalize_file(files: list[DiffFile], f: DiffFile | None, h: Hunk | None
                   ) -> tuple[DiffFile | None, Hunk | None]:
    """Finalize hunk and file, appending to list if it has changes."""
    if f is None:
        return None, None
    h = _finalize_hunk(f, h)
    if f.hunks or f.binary:
        files.append(f)
    return None, None


def parse_diff(text: str) -> list[DiffFile]:
    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk: Hunk | None = None
    old_ln = 0
    new_ln = 0

    for raw_line in text.splitlines():
        line = raw_line

        # --- File header ---
        if line.startswith("diff --git "):
            current_file, current_hunk = _finalize_file(files, current_file, current_hunk)
            current_file = DiffFile()
            in_git_diff = True
            parts = line.split()
            if len(parts) >= 4:
                current_file.old_path = parts[2][2:]
                current_file.new_path = parts[3][2:]
            continue

        if current_file is None:
            continue

        if line.startswith("index "):
            continue

        if line.startswith("new file mode "):
            current_file.status = "added"
            continue

        if line.startswith("deleted file mode "):
            current_file.status = "deleted"
            continue

        if line.startswith("rename from "):
            current_file.status = "renamed"
            current_file.old_path = line[len("rename from "):]
            continue
        if line.startswith("rename to "):
            current_file.new_path = line[len("rename to "):]
            continue

        m = re.match(r"similarity index (\d+)%", line)
        if m:
            current_file.similarity = int(m.group(1))
            continue

        if line.startswith("Binary files ") and "differ" in line:
            current_file.binary = True
            continue

        if line.startswith("--- "):
            path = line[4:]
            if path != "/dev/null":
                current_file.old_path = path[2:] if path.startswith("a/") else path
            continue
        if line.startswith("+++ "):
            path = line[4:]
            if path != "/dev/null":
                current_file.new_path = path[2:] if path.startswith("b/") else path
            continue

        # --- Hunk header ---
        m = re.match(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*)", line)
        if m:
            current_hunk = _finalize_hunk(current_file, current_hunk)
            old_s = int(m.group(1))
            old_c = int(m.group(2)) if m.group(2) else 1
            new_s = int(m.group(3))
            new_c = int(m.group(4)) if m.group(4) else 1
            current_hunk = Hunk(old_s, old_c, new_s, new_c, m.group(5).strip())
            old_ln = old_s
            new_ln = new_s
            continue

        if current_hunk is None:
            continue

        if line.startswith("\\ "):
            current_hunk.lines.append(DiffLine("\\", line[1:]))
            continue

        if line.startswith("+"):
            dl = DiffLine("+", line[1:], old_ln=0, new_ln=new_ln)
            current_hunk.lines.append(dl)
            new_ln += 1
        elif line.startswith("-"):
            dl = DiffLine("-", line[1:], old_ln=old_ln, new_ln=0)
            current_hunk.lines.append(dl)
            old_ln += 1
        elif line.startswith(" "):
            dl = DiffLine(" ", line[1:], old_ln=old_ln, new_ln=new_ln)
            current_hunk.lines.append(dl)
            old_ln += 1
            new_ln += 1

    # Finalize last file
    _finalize_file(files, current_file, current_hunk)

    return files


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

CSS = """\
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif; background: #ffffff; color: #1f2328; overflow-x: hidden; font-weight: 500; }
.container { max-width: 1280px; margin: 0 auto; padding: 20px; }

/* Header */
.commit-header { background: #ffffff; border: 1px solid #d0d7de; border-radius: 6px; padding: 16px; margin-bottom: 16px; }
.commit-title { font-size: 16px; font-weight: 600; margin-bottom: 8px; }
.commit-meta { font-size: 12px; color: #656d76; }
.commit-meta span { margin-right: 16px; }

/* File list */
.file-list { background: #ffffff; border: 1px solid #d0d7de; border-radius: 6px; margin-bottom: 16px; overflow: hidden; }
.file-list-item { display: flex; align-items: center; padding: 8px 16px; border-bottom: 1px solid #d0d7de; text-decoration: none; color: #1f2328; font-size: 14px; font-weight: 500; }
.file-list-item:last-child { border-bottom: none; }
.file-list-item:hover { background: #e8eaed; }
.file-status { display: inline-block; width: 20px; height: 20px; border-radius: 3px; text-align: center; line-height: 20px; font-size: 11px; font-weight: 600; margin-right: 8px; flex-shrink: 0; }
.file-status-A { background: #2da44e; color: #fff; }
.file-status-M { background: #9a6700; color: #fff; }
.file-status-D { background: #cf222e; color: #fff; }
.file-status-R { background: #0969da; color: #fff; }
.file-path { flex: 1; }
.file-stats { font-size: 12px; }
.add-stat { color: #1a7f37; margin-right: 4px; }
.del-stat { color: #cf222e; }

/* Toggle all */
.toggle-all { margin-left: auto; font-size: 12px; }
.toggle-all a { color: #0969da; cursor: pointer; text-decoration: none; }
.toggle-all a:hover { text-decoration: underline; }
.toggle-all .sep { color: #d0d7de; margin: 0 6px; }

/* Collapsible file */
.diff-file { background: #ffffff; border: 1px solid #d0d7de; border-radius: 6px; margin-bottom: 16px; overflow: hidden; }
.diff-file[open] { border-radius: 6px; }
.diff-file-header { display: flex; align-items: center; padding: 8px 16px; background: #f6f8fa; font-size: 14px; font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace; cursor: pointer; user-select: none; font-weight: 500; }
.diff-file-header::-webkit-details-marker { display: none; }
.diff-file-header::before { content: "\\25B6"; color: #656d76; font-size: 10px; margin-right: 8px; transition: transform .15s; }
.diff-file[open] > .diff-file-header::before { transform: rotate(90deg); }
.diff-file-header .file-status { margin-right: 8px; }
.diff-file-header .file-path { flex: 1; }
.diff-file-header .file-stats { margin-left: 8px; }
.diff-file-header .copy-btn { background: none; border: 1px solid #d0d7de; color: #656d76; border-radius: 3px; cursor: pointer; font-size: 11px; padding: 1px 6px; margin-left: 8px; font-family: inherit; }
.diff-file-header .copy-btn:hover { background: #e8eaed; color: #1f2328; }
.diff-file-header .copy-btn.copied { border-color: #1a7f37; color: #1a7f37; }
.diff-binary { padding: 24px; text-align: center; color: #656d76; font-size: 14px; }

/* Unified diff table */
.diff-table-wrapper { overflow-x: auto; }
.diff-table { width: 100%; border-collapse: collapse; font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace; font-size: 12px; line-height: 20px; }
.diff-table td { padding: 0 8px; vertical-align: top; white-space: pre; font-weight: 500; }
.diff-table .ln { width: 50px; min-width: 50px; text-align: right; padding-right: 10px; color: #6e7781; -webkit-user-select: none; -moz-user-select: none; user-select: none; }
.diff-table .ln::before { content: attr(data-ln); }
.diff-table .kind::before { content: attr(data-kind); }
.diff-table .ln-old { border-right: 1px solid #d0d7de; }
.diff-table .ln-new { border-right: 1px solid #d0d7de; }
.diff-table .kind { width: 16px; min-width: 16px; text-align: center; padding: 0; -webkit-user-select: none; -moz-user-select: none; user-select: none; color: #6e7781; }
.diff-table .content { padding-left: 10px; padding-right: 10px; }

/* Line colours */
.line-add { background: #e6ffec; }
.line-add .ln-old { background: #ffffff; }
.line-add .ln-new { background: #e6ffec; }
.line-add .content { background: #e6ffec; }
.line-del { background: #ffebe9; }
.line-del .ln-old { background: #ffebe9; }
.line-del .ln-new { background: #ffffff; }
.line-del .content { background: #ffebe9; }
.line-ctx { background: #ffffff; }
.line-hunk-header { background: #f6f8fa; }
.line-hunk-header td { color: #656d76; padding: 4px 8px; font-weight: 500; }

/* File diff stats summary */
.stats-summary { display: flex; align-items: center; padding: 8px 16px; background: #f6f8fa; border-bottom: 1px solid #d0d7de; font-size: 12px; color: #656d76; flex-wrap: wrap; gap: 4px 16px; }
.stats-summary .additions { color: #1a7f37; }
.stats-summary .deletions { color: #cf222e; }

/* Syntax highlight token colours */
{token_css}

/* Sidebar */
.sidebar { position: fixed; top: 0; left: 0; width: 300px; height: 100vh; background: #f6f8fa; border-right: 1px solid #d0d7de; z-index: 100; transform: translateX(-100%); transition: transform .2s ease; display: flex; flex-direction: column; }
.sidebar.open { transform: translateX(0); }
.sidebar-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid #d0d7de; font-size: 14px; font-weight: 600; flex-shrink: 0; }
.sidebar-close { background: none; border: none; color: #656d76; cursor: pointer; font-size: 18px; padding: 0 4px; line-height: 1; }
.sidebar-close:hover { color: #1f2328; }
.sidebar-tree { padding: 8px 0; flex: 1; overflow-y: auto; min-height: 0; }

/* Sidebar toggle button */
.sidebar-toggle-btn { position: fixed; top: 12px; left: 12px; z-index: 99; background: #ffffff; border: 1px solid #d0d7de; color: #656d76; border-radius: 6px; cursor: pointer; font-size: 14px; padding: 4px 10px; line-height: 1.5; display: flex; align-items: center; gap: 6px; transition: opacity .2s; font-weight: 500; }
.sidebar-toggle-btn:hover { background: #e8eaed; color: #1f2328; }
.sidebar-toggle-btn.hidden { opacity: 0; pointer-events: none; }

/* Main content shift */
.main-content { transition: margin-left .2s ease; }
.main-content.shifted { margin-left: 300px; }

/* Tree folder */
.tree-folder { font-size: 13px; }
.tree-folder-header { display: flex; align-items: center; padding: 4px 16px; cursor: pointer; user-select: none; color: #1f2328; font-weight: 500; }
.tree-folder-header::-webkit-details-marker { display: none; }
.tree-folder-header:hover { background: #e8eaed; }
.tree-folder-icon { display: inline-block; width: 16px; font-size: 10px; color: #656d76; transition: transform .15s; flex-shrink: 0; text-align: center; }
.tree-folder[open] > .tree-folder-header .tree-folder-icon { transform: rotate(90deg); }
.tree-folder-name { margin-left: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-folder-contents { padding-left: 16px; }

/* Tree file */
.tree-file { display: flex; align-items: center; padding: 4px 16px; text-decoration: none; color: #1f2328; font-size: 13px; font-weight: 500; }
.tree-file:hover { background: #e8eaed; }
.tree-file .file-status { width: 16px; height: 16px; line-height: 16px; font-size: 9px; margin-right: 6px; flex-shrink: 0; }
.tree-file-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-file-stats { font-size: 11px; margin-left: auto; white-space: nowrap; }
.tree-file.active { background: #e8eaed; border-left: 2px solid #0969da; padding-left: 14px; }
.tree-file.active .tree-file-name { color: #0969da; }
"""


# ---------------------------------------------------------------------------
# Syntax highlighting (via pygments)
# ---------------------------------------------------------------------------

TOKEN_COLORS: dict = {}
if HAS_PYGMENTS:
    TOKEN_COLORS = {
        Token.Keyword:            "#cf222e",
        Token.Keyword.Constant:   "#cf222e",
        Token.Keyword.Declaration: "#cf222e",
        Token.Keyword.Namespace:  "#cf222e",
        Token.Keyword.Pseudo:     "#cf222e",
        Token.Keyword.Reserved:   "#cf222e",
        Token.Keyword.Reserved:   "#cf222e",
        Token.Keyword.Type:       "#cf222e",
        Token.Name.Builtin:       "#953800",
        Token.Name.Builtin.Pseudo: "#953800",
        Token.Name.Class:         "#8250df",
        Token.Name.Decorator:     "#8250df",
        Token.Name.Entity:        "#8250df",
        Token.Name.Exception:     "#cf222e",
        Token.Name.Function:      "#8250df",
        Token.Name.Label:         "#cf222e",
        Token.Name.Namespace:     "#8250df",
        Token.Name.Tag:           "#116329",
        Token.Name.Variable:      "#953800",
        Token.String:             "#0550ae",
        Token.String.Char:        "#0550ae",
        Token.String.Doc:         "#0550ae",
        Token.String.Double:      "#0550ae",
        Token.String.Escape:      "#0550ae",
        Token.String.Heredoc:     "#0550ae",
        Token.String.Interpol:    "#0550ae",
        Token.String.Other:       "#0550ae",
        Token.String.Single:      "#0550ae",
        Token.Number:             "#0550ae",
        Token.Number.Bin:         "#0550ae",
        Token.Number.Float:       "#0550ae",
        Token.Number.Hex:         "#0550ae",
        Token.Number.Integer:     "#0550ae",
        Token.Number.Oct:         "#0550ae",
        Token.Comment:            "#6e7781",
        Token.Comment.Hashbang:   "#6e7781",
        Token.Comment.Multiline:  "#6e7781",
        Token.Comment.Preproc:    "#6e7781",
        Token.Comment.Single:     "#6e7781",
        Token.Comment.Special:    "#6e7781",
        Token.Operator:           "#cf222e",
        Token.Operator.Word:      "#cf222e",
        Token.Punctuation:        "#1f2328",
        Token.Generic.Deleted:    "#cf222e",
        Token.Generic.Inserted:   "#1a7f37",
        Token.Generic.Emph:       "#1f2328",
    }

    def _token_css() -> str:
        lines = []
        seen = set()
        for ttype, color in TOKEN_COLORS.items():
            cls = token_cls(ttype)
            if cls in seen:
                continue
            seen.add(cls)
            lines.append(f".{cls} {{ color: {color}; }}")
        return "\n".join(lines)

    def token_cls(ttype) -> str:
        return "tok-" + str(ttype).replace(".", "-").replace(" ", "").lower().lstrip("token")

    def get_lexer(path: str):
        try:
            return get_lexer_for_filename(path, stripnl=False, ensurenl=False)
        except Exception:
            return TextLexer(stripnl=False, ensurenl=False)

    _LEXER_CACHE: dict[str, object] = {}

    def get_cached_lexer(path: str):
        if path not in _LEXER_CACHE:
            _LEXER_CACHE[path] = get_lexer(path)
        return _LEXER_CACHE[path]

    def highlight_line(text: str, path: str) -> str:
        lexer = get_cached_lexer(path)
        parts = []
        for ttype, value in lex(text, lexer):
            cls = token_cls(ttype)
            if cls and cls in _TOKEN_CLASSES_SET:
                parts.append(f'<span class="{cls}">{escape(value)}</span>')
            else:
                parts.append(escape(value))
        return "".join(parts)

    # Pre-compute set of known token classes
    _TOKEN_CLASSES_SET: set[str] = set()
    for ttype in TOKEN_COLORS:
        _TOKEN_CLASSES_SET.add(token_cls(ttype))

    SYNTAX_CSS = _token_css()
else:
    SYNTAX_CSS = ""

    def highlight_line(text: str, path: str) -> str:
        return escape(text)


def status_char(s: str) -> str:
    return {"added": "A", "deleted": "D", "modified": "M", "renamed": "R", "copied": "C"}.get(s, "?")


def _build_file_tree(files: list[DiffFile]) -> dict:
    """Build a nested tree dict from file list."""
    tree: dict = {"__files__": [], "__dirs__": {}}
    for df in files:
        path = df.new_path if df.new_path else df.old_path
        parts = path.replace("\\", "/").split("/")
        add_c = sum(1 for h in df.hunks for l in h.lines if l.kind == "+")
        del_c = sum(1 for h in df.hunks for l in h.lines if l.kind == "-")
        status = status_char(df.status)
        anchor = path.replace("\\", "/")
        node = tree
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                node["__files__"].append({
                    "name": part,
                    "anchor": anchor,
                    "status": status,
                    "add": add_c,
                    "del": del_c,
                })
            else:
                if part not in node["__dirs__"]:
                    node["__dirs__"][part] = {"__files__": [], "__dirs__": {}}
                node = node["__dirs__"][part]
    return tree


def _flatten_tree(node: dict) -> None:
    """Collapse single-child directory chains into compound names.

    e.g. a/ → b/ → c/ becomes a/b/c/ when each has only that one child.
    """
    for dirname in list(node["__dirs__"]):
        child = node["__dirs__"][dirname]
        _flatten_tree(child)
        while len(child["__dirs__"]) == 1 and not child["__files__"]:
            sub_name = next(iter(child["__dirs__"]))
            sub_child = child["__dirs__"][sub_name]
            merged = dirname + "/" + sub_name
            node["__dirs__"][merged] = sub_child
            del node["__dirs__"][dirname]
            dirname = merged
            child = sub_child


def _render_tree_node(node: dict) -> str:
    """Recursively render tree node to HTML."""
    items = []
    for dirname in sorted(node["__dirs__"]):
        items.append(("dir", dirname, node["__dirs__"][dirname]))
    for f in sorted(node["__files__"], key=lambda x: x["name"]):
        items.append(("file", f["name"], f))
    parts = []
    for typ, name, data in items:
        if typ == "dir":
            child = _render_tree_node(data)
            display_name = name + "/" if "/" in name else name
            parts.append(
                f'<details class="tree-folder" open>'
                f'<summary class="tree-folder-header">'
                f'<span class="tree-folder-icon">▾</span>'
                f'<span class="tree-folder-name">{escape(display_name)}</span>'
                f'</summary>'
                f'<div class="tree-folder-contents">'
                f'{child}'
                f'</div>'
                f'</details>'
            )
        else:
            stats = ""
            if data["add"] or data["del"]:
                sp = []
                if data["add"]:
                    sp.append(f'<span class="add-stat">+{data["add"]}</span>')
                if data["del"]:
                    sp.append(f'<span class="del-stat">−{data["del"]}</span>')
                stats = " " + " ".join(sp)
            parts.append(
                f'<a class="tree-file" href="#{escape(data["anchor"])}" data-file="{escape(data["anchor"])}">'
                f'<span class="file-status file-status-{data["status"]}">{data["status"]}</span>'
                f'<span class="tree-file-name">{escape(data["name"])}</span>'
                f'<span class="tree-file-stats">{stats}</span>'
                f'</a>'
            )
    return "\n".join(parts)


def render_hunk(hunk: Hunk, path: str = "") -> str:
    rows = []
    header_text = f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
    if hunk.header:
        header_text += f" {escape(hunk.header)}"
    rows.append(
        f'<tr class="line-hunk-header"><td class="ln ln-old"></td>'
        f'<td class="ln ln-new"></td>'
        f'<td class="kind"></td>'
        f'<td class="content" colspan="2">{escape(header_text)}</td></tr>'
    )

    for dl in hunk.lines:
        cls = {" ": "line-ctx", "+": "line-add", "-": "line-del", "\\": "line-ctx"}.get(dl.kind, "line-ctx")
        old_ln_str = str(dl.old_ln) if dl.old_ln > 0 else ""
        new_ln_str = str(dl.new_ln) if dl.new_ln > 0 else ""
        kind_sym = dl.kind if dl.kind in ("+", "-", " ") else " "
        if HAS_PYGMENTS and dl.kind in ("+", "-", " "):
            content = highlight_line(dl.content, path)
        else:
            content = escape(dl.content)
        rows.append(
            f'<tr class="{cls}">'
            f'<td class="ln ln-old" data-ln="{old_ln_str}"></td>'
            f'<td class="ln ln-new" data-ln="{new_ln_str}"></td>'
            f'<td class="kind" data-kind="{kind_sym}"></td>'
            f'<td class="content">{content}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def render_file(df: DiffFile, repo_dir: str, open_by_default: bool = False) -> str:
    title = df.new_path if df.new_path else df.old_path
    status = status_char(df.status)
    anchor = title.replace("\\", "/")

    parts = []
    if df.binary:
        parts.append(f'<details class="diff-file" id="{escape(anchor)}"{" open" if open_by_default else ""}>')
        parts.append(
            f'<summary class="diff-file-header">'
            f'<span class="file-status file-status-{status}">{status}</span>'
            f'<span class="file-path">{escape(title)}</span>'
            f'<button class="copy-btn" data-path="{escape(title)}" onclick="event.stopPropagation();copyPath(this)">copy</button>'
            f'</summary>'
            f'<div class="diff-binary">Binary file not shown.</div>'
            f'</details>'
        )
        return "\n".join(parts)

    add_count = sum(1 for h in df.hunks for l in h.lines if l.kind == "+")
    del_count = sum(1 for h in df.hunks for l in h.lines if l.kind == "-")

    stats = ""
    if add_count or del_count:
        stats_parts = []
        if add_count:
            stats_parts.append(f'<span class="add-stat">+{add_count}</span>')
        if del_count:
            stats_parts.append(f'<span class="del-stat">−{del_count}</span>')
        stats = " ".join(stats_parts)

    path_label = title
    if df.status == "renamed":
        path_label = f"{df.old_path} → {df.new_path}"

    parts.append(f'<details class="diff-file" id="{escape(anchor)}"{" open" if open_by_default else ""}>')
    parts.append(
        f'<summary class="diff-file-header">'
        f'<span class="file-status file-status-{status}">{status}</span>'
        f'<span class="file-path">{escape(path_label)}</span>'
        f'<span class="file-stats">{stats}</span>'
        f'<button class="copy-btn" data-path="{escape(title)}" onclick="event.stopPropagation();copyPath(this)">copy</button>'
        f'</summary>'
    )
    parts.append('<div class="diff-table-wrapper"><table class="diff-table">')
    for hunk in df.hunks:
        parts.append(render_hunk(hunk, title))
    parts.append('</table></div>')
    parts.append('</details>')
    return "\n".join(parts)


JS = """\
function toggleAll(expand) {
  document.querySelectorAll('.diff-file').forEach(function(d) {
    if (expand) d.setAttribute('open', ''); else d.removeAttribute('open');
  });
}
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('main-content').classList.toggle('shifted');
  document.getElementById('sidebar-toggle-btn').classList.toggle('hidden');
}
document.addEventListener('click', function(e) {
  var link = e.target.closest('.file-list-item, .tree-file');
  if (!link) return;
  var href = link.getAttribute('href');
  if (!href || !href.startsWith('#')) return;
  var target = document.getElementById(href.slice(1));
  if (target && target.tagName === 'DETAILS') {
    target.setAttribute('open', '');
    setTimeout(function() { target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 50);
  }
  if (link.classList.contains('tree-file')) {
    document.querySelectorAll('.tree-file.active').forEach(function(el) { el.classList.remove('active'); });
    link.classList.add('active');
  }
});
function copyPath(btn) {
  var path = btn.getAttribute('data-path');
  navigator.clipboard.writeText(path).then(function() {
    btn.classList.add('copied');
    btn.textContent = 'copied!';
    setTimeout(function() {
      btn.classList.remove('copied');
      btn.textContent = 'copy';
    }, 1500);
  }).catch(function() {
    // fallback
    var ta = document.createElement('textarea');
    ta.value = path;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    btn.classList.add('copied');
    btn.textContent = 'copied!';
    setTimeout(function() {
      btn.classList.remove('copied');
      btn.textContent = 'copy';
    }, 1500);
  });
}
"""

def generate_html(files: list[DiffFile], repo_dir: str = "", commit_info: dict | None = None) -> str:
    total_add = sum(1 for f in files for h in f.hunks for l in h.lines if l.kind == "+")
    total_del = sum(1 for f in files for h in f.hunks for l in h.lines if l.kind == "-")

    # Build file list
    file_list_rows = []
    for df in files:
        status = status_char(df.status)
        path = df.new_path if df.new_path else df.old_path
        anchor = path.replace("\\", "/")
        label = path
        if df.status == "renamed":
            label = f"{df.old_path} → {df.new_path}"
        add_c = sum(1 for h in df.hunks for l in h.lines if l.kind == "+")
        del_c = sum(1 for h in df.hunks for l in h.lines if l.kind == "-")
        stats = ""
        if add_c or del_c:
            sp = []
            if add_c:
                sp.append(f'<span class="add-stat">+{add_c}</span>')
            if del_c:
                sp.append(f'<span class="del-stat">-{del_c}</span>')
            stats = " ".join(sp)
        file_list_rows.append(
            f'<a class="file-list-item" href="#{escape(anchor)}">'
            f'<span class="file-status file-status-{status}">{status}</span>'
            f'<span class="file-path">{escape(label)}</span>'
            f'<span class="file-stats">{stats}</span>'
            f'</a>'
        )

    # Commit header
    commit_html = ""
    if commit_info:
        commit_html = f"""\
<div class="commit-header">
  <div class="commit-title">{escape(commit_info.get("title", ""))}</div>
  <div class="commit-meta">
    <span>commit {commit_info.get("hash", "")}</span>
    <span>author: {escape(commit_info.get("author", ""))}</span>
    <span>date: {commit_info.get("date", "")}</span>
  </div>
</div>"""

    toggle_links = '<span class="toggle-all"><a onclick="toggleAll(true)">expand all</a><span class="sep">|</span><a onclick="toggleAll(false)">collapse all</a></span>'

    stats_line = ""
    if len(files) or total_add or total_del:
        sp = []
        sp.append(f'<span class="changed-files">{len(files)} file{"s" if len(files) != 1 else ""} changed</span>')
        if total_add:
            sp.append(f'<span class="additions">+{total_add} additions</span>')
        if total_del:
            sp.append(f'<span class="deletions">-{total_del} deletions</span>')
        stats_line = f'<div class="stats-summary">{" &nbsp;|&nbsp; ".join(sp)}{toggle_links}</div>'

    file_list_html = ""
    if file_list_rows:
        file_list_html = f"""\
<div class="file-list">
  {stats_line}
  {''.join(file_list_rows)}
</div>"""

    diff_html = "\n".join(render_file(df, repo_dir) for df in files)

    # Build sidebar tree
    tree = _build_file_tree(files)
    _flatten_tree(tree)
    tree_html = _render_tree_node(tree)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(os.path.basename(repo_dir or "diff"))} — Diff</title>
<style>{CSS.replace("{token_css}", SYNTAX_CSS)}</style>
</head>
<body>
<aside class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <span>Files changed ({len(files)})</span>
    <button class="sidebar-close" onclick="toggleSidebar()">✕</button>
  </div>
  <div class="sidebar-tree">
    {tree_html}
  </div>
</aside>
<button class="sidebar-toggle-btn" id="sidebar-toggle-btn" onclick="toggleSidebar()">☰ Files</button>
<div class="main-content" id="main-content">
  <div class="container">
{commit_html}
{file_list_html}
{diff_html}
  </div>
</div>
<script>{JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def get_diff_from_commit(repo_dir: str, commit: str) -> str:
    """Get diff for a commit (compared to parent)."""
    # Check if it's a commit hash or ref
    try:
        # For merge commits, show diff against first parent
        result = subprocess.run(
            ["git", "diff", f"{commit}^..{commit}"],
            capture_output=True, text=True, cwd=repo_dir
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout

        # Fallback: maybe it's the initial commit
        result = subprocess.run(
            ["git", "show", "--format=format:%b", commit],
            capture_output=True, text=True, cwd=repo_dir
        )
        return result.stdout
    except Exception as e:
        print(f"Error getting diff: {e}", file=sys.stderr)
        sys.exit(1)


def get_commit_info(repo_dir: str, commit: str) -> dict:
    """Get commit metadata."""
    info = {}
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H|%an|%ai|%s", commit],
            capture_output=True, text=True, cwd=repo_dir
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split("|", 3)
            if len(parts) >= 4:
                info["hash"] = parts[0]
                info["author"] = parts[1]
                info["date"] = parts[2]
                info["title"] = parts[3]
    except Exception:
        pass
    return info


def read_diff_from_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _get_current_branch(repo_dir: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=repo_dir
    )
    return result.stdout.strip()


def _find_base_branch(repo_dir: str) -> str | None:
    for branch in ["main", "master"]:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            capture_output=True, text=True, cwd=repo_dir
        )
        if result.returncode == 0:
            return branch
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Generate GitHub-style HTML diff."
    )
    parser.add_argument("source", nargs="?", default=None,
                        help="Commit hash, diff file path, or omit to diff current branch against main/master")
    parser.add_argument("-b", "--branch", default=None,
                        help="Compare current branch against the given branch (e.g. -b main)")
    parser.add_argument("-o", "--output", default="diff.html",
                        help="Output HTML file (default: diff.html)")
    parser.add_argument("--stdin", action="store_true",
                        help="Read diff from stdin (use instead of source)")

    args = parser.parse_args()
    repo_dir = os.path.abspath(".")

    git_dir = os.path.join(repo_dir, ".git")
    is_git = os.path.isdir(git_dir) or os.path.isfile(git_dir)

    commit_info = None
    diff_text = None

    if args.stdin:
        diff_text = sys.stdin.read()
    elif args.source is None:
        if not is_git:
            print("Error: not a git repository", file=sys.stderr)
            sys.exit(1)
        current_branch = _get_current_branch(repo_dir)
        if args.branch:
            base = args.branch
        else:
            base = _find_base_branch(repo_dir)
        if base and current_branch != base:
            diff_text = subprocess.run(
                ["git", "diff", f"{base}..HEAD"],
                capture_output=True, text=True, cwd=repo_dir
            ).stdout
        else:
            diff_text = get_diff_from_commit(repo_dir, "HEAD")
        commit_info = get_commit_info(repo_dir, "HEAD")
        if args.branch and commit_info:
            commit_info["title"] = f"{current_branch} vs {args.branch} " + commit_info.get("title", "")
    elif os.path.isfile(args.source):
        diff_text = read_diff_from_file(args.source)
    else:
        if not is_git:
            print(f"Error: not a git repository: {repo_dir}", file=sys.stderr)
            sys.exit(1)
        diff_text = subprocess.run(
            ["git", "diff", f"{args.source}..HEAD"],
            capture_output=True, text=True, cwd=repo_dir
        ).stdout
        commit_info = get_commit_info(repo_dir, "HEAD")
        if commit_info:
            commit_info["title"] = f"{args.source[:8]}..HEAD " + commit_info.get("title", "")

    if not diff_text or not diff_text.strip():
        print("Error: empty diff", file=sys.stderr)
        sys.exit(1)

    files = parse_diff(diff_text)
    files.sort(key=lambda f: (not "/" in (f.new_path if f.new_path else f.old_path), f.new_path if f.new_path else f.old_path))

    if not files:
        print("Warning: no file changes found in diff", file=sys.stderr)

    html = generate_html(files, repo_dir, commit_info)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    out_path = os.path.abspath(args.output)
    print(f"Diff HTML written to {out_path}")

    # WSL2: print Windows-accessible file:// URL for browser
    if os.path.exists("/proc/version"):
        with open("/proc/version") as f:
            version_data = f.read().lower()
        if "microsoft" in version_data or "wsl" in version_data:
            distro = ""
            try:
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("WSL_DISTRO_NAME="):
                            distro = line.strip().split("=", 1)[1].strip('"')
                            break
            except Exception:
                pass
            if not distro:
                distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
            url = f"file://wsl$/{distro}/{out_path.lstrip('/')}"
            print(f"  Paste in Windows browser: {url}")

    print(f"  Files changed: {len(files)}")
    total_add = sum(1 for f in files for h in f.hunks for l in h.lines if l.kind == "+")
    total_del = sum(1 for f in files for h in f.hunks for l in h.lines if l.kind == "-")
    print(f"  {total_add} additions, {total_del} deletions")


if __name__ == "__main__":
    main()
