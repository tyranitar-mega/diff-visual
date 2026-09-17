# diff2html

Generate GitHub-style HTML diff views from git diff output or commit hashes. Each file's diff is shown as a collapsible section with syntax highlighting, making it easy to navigate large changes.

Example:

![](./assets/example.png)

What GitHub shows:

![](./assets/github.png)

## Usage

```bash
# Diff current branch against its gh stack parent, or main/master when not stacked
python diff2html.py

# Diff current branch against a specific branch
python diff2html.py -b main

# Specify a commit (compare commit..HEAD)
python diff2html.py abc123def

# From a diff file
python diff2html.py diff.patch

# From stdin
git diff HEAD~3 HEAD | python diff2html.py --stdin

# Custom output file
python diff2html.py -o review.html
```

### Options

| Argument | Description |
|----------|-------------|
| `source` | Commit hash, diff file path, or omit to diff current branch against its gh stack parent or main/master |
| `-b, --branch` | Compare current branch against the given branch (e.g. `-b main`) |
| `-o, --output` | Output HTML file (default: `diff.html`) |
| `--stdin` | Read diff from stdin |

## Features

- **File-based view** — each changed file is a collapsible `<details>` panel with a copy-path button
- **File list** — summary of all changed files with add/del counts; click to jump
- **Sidebar tree** — directory tree with file status and stats for quick navigation
- **Expand / collapse all** — toggle all files at once
- **Syntax highlighting** — source code in diffs is highlighted via `pygments` (auto-detected from file extension), with GitHub-style token colors
- **GitHub-style light theme** — inline CSS, no external dependencies
- **Commit metadata** — shows title, author, date when diffing against a branch or commit
- **gh stack support** — automatically compares a stacked branch with the layer immediately below it; explicit `-b/--branch` takes precedence
- **No external runtime dependencies** — pure Python standard library; `pygments` is optional but recommended
- **WSL support** — auto-detects WSL2 and prints a `file://wsl$/...` URL for Windows browser

## Requirements

- Python 3.10+
- `pygments` (optional, for syntax highlighting) — `pip install pygments`
