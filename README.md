# diff2html

Generate GitHub-style HTML diff views from git diff output or commit hashes. Each file's diff is shown as a collapsible section with syntax highlighting, making it easy to navigate large changes.

## Usage

```bash
# Use latest commit in current directory, output to diff.html
python diff2html.py

# Specify a commit
python diff2html.py abc123def

# From a commit hash with custom output
python diff2html.py ~/my-project abc123def -o review.html

# From a diff file
python diff2html.py diff.patch

# From stdin
git diff HEAD~3 HEAD | python diff2html.py --stdin
```

### Options

| Argument | Description |
|----------|-------------|
| `repo` | Path to git repository (default: current directory) |
| `source` | Diff file path, commit hash, or leave empty for `HEAD` |
| `-o, --output` | Output HTML file (default: `diff.html`) |
| `--stdin` | Read diff from stdin |

## Features

- **File-based view** — each changed file is a collapsible `<details>` panel with a copy-path button
- **File list** — summary of all changed files with add/del counts; click to jump
- **Expand / collapse all** — toggle all files at once
- **Syntax highlighting** — source code in diffs is highlighted via `pygments` (auto-detected from file extension), with GitHub-dark-compatible token colors
- **GitHub-style dark theme** — inline CSS, no external dependencies
- **Commit metadata** — shows title, author, date when using a commit hash
- **No external runtime dependencies** — pure Python standard library; `pygments` is optional but recommended
- **WSL support** — auto-detects WSL2 and prints a `file://wsl$/...` URL for Windows browser

## Requirements

- Python 3.10+
- `pygments` (optional, for syntax highlighting) — `pip install pygments`
