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
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif; background: #0d1117; color: #e6edf3; padding: 20px; }
.container { max-width: 1280px; margin: 0 auto; }

/* Header */
.commit-header { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; margin-bottom: 16px; }
.commit-title { font-size: 16px; font-weight: 600; margin-bottom: 8px; }
.commit-meta { font-size: 12px; color: #8b949e; }
.commit-meta span { margin-right: 16px; }

/* File list */
.file-list { background: #161b22; border: 1px solid #30363d; border-radius: 6px; margin-bottom: 16px; overflow: hidden; }
.file-list-item { display: flex; align-items: center; padding: 8px 16px; border-bottom: 1px solid #21262d; text-decoration: none; color: #e6edf3; font-size: 14px; }
.file-list-item:last-child { border-bottom: none; }
.file-list-item:hover { background: #1c2128; }
.file-status { display: inline-block; width: 20px; height: 20px; border-radius: 3px; text-align: center; line-height: 20px; font-size: 11px; font-weight: 600; margin-right: 8px; flex-shrink: 0; }
.file-status-A { background: #238636; color: #fff; }
.file-status-M { background: #9e6a03; color: #fff; }
.file-status-D { background: #da3633; color: #fff; }
.file-status-R { background: #1f6feb; color: #fff; }
.file-path { flex: 1; }
.file-stats { font-size: 12px; }
.add-stat { color: #3fb950; margin-right: 4px; }
.del-stat { color: #f85149; }

/* Toggle all */
.toggle-all { margin-left: auto; font-size: 12px; }
.toggle-all a { color: #58a6ff; cursor: pointer; text-decoration: none; }
.toggle-all a:hover { text-decoration: underline; }
.toggle-all .sep { color: #30363d; margin: 0 6px; }

/* Collapsible file */
.diff-file { background: #161b22; border: 1px solid #30363d; border-radius: 6px; margin-bottom: 16px; overflow: hidden; }
.diff-file[open] { border-radius: 6px; }
.diff-file-header { display: flex; align-items: center; padding: 8px 16px; background: #1c2128; font-size: 14px; font-family: Consolas, "Courier New", monospace; cursor: pointer; user-select: none; }
.diff-file-header::-webkit-details-marker { display: none; }
.diff-file-header::before { content: "\\25B6"; color: #8b949e; font-size: 10px; margin-right: 8px; transition: transform .15s; }
.diff-file[open] > .diff-file-header::before { transform: rotate(90deg); }
.diff-file-header .file-status { margin-right: 8px; }
.diff-file-header .file-path { flex: 1; }
.diff-file-header .file-stats { margin-left: 8px; }
.diff-file-header .copy-btn { background: none; border: 1px solid #30363d; color: #8b949e; border-radius: 3px; cursor: pointer; font-size: 11px; padding: 1px 6px; margin-left: 8px; font-family: inherit; }
.diff-file-header .copy-btn:hover { background: #30363d; color: #e6edf3; }
.diff-file-header .copy-btn.copied { border-color: #3fb950; color: #3fb950; }
.diff-binary { padding: 24px; text-align: center; color: #8b949e; font-size: 14px; }

/* Unified diff table */
.diff-table { width: 100%; border-collapse: collapse; font-family: Consolas, "Courier New", monospace; font-size: 12px; line-height: 20px; }
.diff-table td { padding: 0 8px; vertical-align: top; white-space: pre; }
.diff-table .ln { width: 50px; text-align: right; padding-right: 8px; color: #484f58; user-select: none; }
.diff-table .ln-old { border-right: 1px solid #30363d; }
.diff-table .ln-new { border-right: 1px solid #30363d; }
.diff-table .kind { width: 16px; text-align: center; padding: 0; user-select: none; color: #484f58; }
.diff-table .content { padding-left: 8px; }

/* Line colours */
.line-add { background: #12261d; }
.line-add .ln-old { background: #0d1117; }
.line-add .ln-new { background: #12261d; }
.line-add .content { background: #12261d; }
.line-del { background: #28171a; }
.line-del .ln-old { background: #28171a; }
.line-del .ln-new { background: #0d1117; }
.line-del .content { background: #28171a; }
.line-ctx { background: #0d1117; }
.line-hunk-header { background: #0d1117; }
.line-hunk-header td { color: #8b949e; padding: 4px 8px; font-weight: 500; }

/* File diff stats summary */
.stats-summary { display: flex; align-items: center; padding: 8px 16px; background: #1c2128; border-bottom: 1px solid #30363d; font-size: 12px; color: #8b949e; flex-wrap: wrap; gap: 4px 16px; }
.stats-summary .additions { color: #3fb950; }
.stats-summary .deletions { color: #f85149; }

/* Syntax highlight token colours */
{token_css}
"""


# ---------------------------------------------------------------------------
# Syntax highlighting (via pygments)
# ---------------------------------------------------------------------------

TOKEN_COLORS: dict = {}
if HAS_PYGMENTS:
    TOKEN_COLORS = {
        Token.Keyword:            "#ff7b72",
        Token.Keyword.Constant:   "#ff7b72",
        Token.Keyword.Declaration: "#ff7b72",
        Token.Keyword.Namespace:  "#ff7b72",
        Token.Keyword.Pseudo:     "#ff7b72",
        Token.Keyword.Reserved:   "#ff7b72",
        Token.Keyword.Type:       "#ff7b72",
        Token.Name.Builtin:       "#ffa657",
        Token.Name.Builtin.Pseudo: "#ffa657",
        Token.Name.Class:         "#d2a8ff",
        Token.Name.Decorator:     "#d2a8ff",
        Token.Name.Entity:        "#d2a8ff",
        Token.Name.Exception:     "#ff7b72",
        Token.Name.Function:      "#d2a8ff",
        Token.Name.Label:         "#ff7b72",
        Token.Name.Namespace:     "#d2a8ff",
        Token.Name.Tag:           "#7ee787",
        Token.Name.Variable:      "#ffa657",
        Token.String:             "#a5d6ff",
        Token.String.Char:        "#a5d6ff",
        Token.String.Doc:         "#a5d6ff",
        Token.String.Double:      "#a5d6ff",
        Token.String.Escape:      "#a5d6ff",
        Token.String.Heredoc:     "#a5d6ff",
        Token.String.Interpol:    "#a5d6ff",
        Token.String.Other:       "#a5d6ff",
        Token.String.Single:      "#a5d6ff",
        Token.Number:             "#79c0ff",
        Token.Number.Bin:         "#79c0ff",
        Token.Number.Float:       "#79c0ff",
        Token.Number.Hex:         "#79c0ff",
        Token.Number.Integer:     "#79c0ff",
        Token.Number.Oct:         "#79c0ff",
        Token.Comment:            "#8b949e",
        Token.Comment.Hashbang:   "#8b949e",
        Token.Comment.Multiline:  "#8b949e",
        Token.Comment.Preproc:    "#8b949e",
        Token.Comment.Single:     "#8b949e",
        Token.Comment.Special:    "#8b949e",
        Token.Operator:           "#ff7b72",
        Token.Operator.Word:      "#ff7b72",
        Token.Punctuation:        "#e6edf3",
        Token.Generic.Deleted:    "#ffa198",
        Token.Generic.Inserted:   "#3fb950",
        Token.Generic.Emph:       "#e6edf3",
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
            f'<td class="ln ln-old">{old_ln_str}</td>'
            f'<td class="ln ln-new">{new_ln_str}</td>'
            f'<td class="kind">{kind_sym}</td>'
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
    parts.append('<table class="diff-table">')
    for hunk in df.hunks:
        parts.append(render_hunk(hunk, title))
    parts.append('</table>')
    parts.append('</details>')
    return "\n".join(parts)


JS = """\
function toggleAll(expand) {
  document.querySelectorAll('.diff-file').forEach(function(d) {
    if (expand) d.setAttribute('open', ''); else d.removeAttribute('open');
  });
}
document.addEventListener('click', function(e) {
  var link = e.target.closest('.file-list-item');
  if (!link) return;
  var href = link.getAttribute('href');
  if (!href || !href.startsWith('#')) return;
  var target = document.getElementById(href.slice(1));
  if (target && target.tagName === 'DETAILS') {
    target.setAttribute('open', '');
    setTimeout(function() { target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 50);
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
<div class="container">
{commit_html}
{file_list_html}
{diff_html}
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
        base = _find_base_branch(repo_dir)
        if base and current_branch != base:
            diff_text = subprocess.run(
                ["git", "diff", f"{base}...HEAD"],
                capture_output=True, text=True, cwd=repo_dir
            ).stdout
        else:
            diff_text = get_diff_from_commit(repo_dir, "HEAD")
        commit_info = get_commit_info(repo_dir, "HEAD")
    elif os.path.isfile(args.source):
        diff_text = read_diff_from_file(args.source)
    else:
        if not is_git:
            print(f"Error: not a git repository: {repo_dir}", file=sys.stderr)
            sys.exit(1)
        diff_text = get_diff_from_commit(repo_dir, args.source)
        commit_info = get_commit_info(repo_dir, args.source)

    if not diff_text or not diff_text.strip():
        print("Error: empty diff", file=sys.stderr)
        sys.exit(1)

    files = parse_diff(diff_text)

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
