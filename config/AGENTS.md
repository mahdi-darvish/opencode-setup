# Global instructions

## Reporting
- If a tool fails and you use another method, say so in one line, and state clearly whether the task succeeded in the end.

## Reading and writing documents
Tools are installed via Homebrew (`/opt/homebrew/bin`) and in `~/.local/bin`. If a command is not found, use the full path.

| Task | Use first | Fallback |
|---|---|---|
| Any PDF, Word, Excel, PowerPoint, HTML or CSV file → text | `markitdown <file>` (keeps tables and every Excel sheet) | the specific tools below |
| PDF text with layout | `pdftotext -layout <file> -` | `markitdown`, or Python `pdfplumber` for tables |
| PDF info, pages to images | `pdfinfo`, `pdftoppm -png -r 150 -f N -l N <file> <prefix>` | |
| Split, merge, repair, decrypt PDFs | `qpdf` | |
| Scanned PDF or photo (no text layer) | Render with `pdftoppm -r 200`, then look at the image yourself | |
| Excel: formulas, dates, editing, creating | `docpython` with `openpyxl` (formulas) or `pandas` (data analysis) | `markitdown` for a quick read |
| Old `.xls` | `docpython` with `pandas` + `xlrd` | |
| Word/PowerPoint: editing, creating | `docpython` with `python-docx` / `python-pptx` | |
| Converting between formats (Markdown, Word, HTML) | `pandoc` | `textutil` (built into macOS) |
| GitHub | `gh` (log in once with `gh auth login`) | |

- `docpython` is Python 3.13 with `openpyxl`, `pandas`, `pypdf`, `pdfplumber`, `python-docx`, `python-pptx`, `xlrd`, `pillow`, `tabulate`. Use it instead of the system `python3` (3.9, no libraries) for document work.
- Note: `markitdown` shows Excel formula cells as `NaN`, because it reads cached values. Use `openpyxl` when formulas or totals matter.
- No office suite (LibreOffice, Microsoft Office) is installed. Do not install software without asking.
