# diff2html

Generate GitHub-style HTML diff views from git diff output or commit hashes. Each file's diff is shown as a collapsible section, making it easy to navigate large changes.

## Usage

```bash
# From a commit hash
python diff2html.py <repo-dir> <commit> -o output.html

# From a diff file
python diff2html.py <repo-dir> diff.patch -o output.html

# From stdin
git diff HEAD~3 HEAD | python diff2html.py <repo-dir> --stdin -o output.html
```

### Options

| Argument | Description |
|----------|-------------|
| `repo` | Path to git repository |
| `source` | Diff file path, commit hash, or `--stdin` |
| `-o, --output` | Output HTML file (default: `diff.html`) |
| `--stdin` | Read diff from stdin |

## Features

- **File-based view** — each changed file is a collapsible `<details>` panel
- **File list** — summary of all changed files with add/del counts; click to jump
- **Expand / collapse all** — toggle all files at once
- **GitHub-style dark theme** — inline CSS, no external dependencies
- **Commit metadata** — when using a commit hash, shows title, author, date
- **No external dependencies** — pure Python standard library

## Example

```bash
python diff2html.py ~/my-project abc123def -o review.html
# Generates review.html with commit abc123def's changes
```
