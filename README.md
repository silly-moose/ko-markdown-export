# KnowledgeOwl → Markdown export

A small Python script that pulls every article from your KnowledgeOwl knowledge base and writes them out as a folder of Markdown files, organized by category. Upload that folder to Claude Projects, ChatGPT, or any other tool that accepts a folder of Markdown files as a knowledge source.

Re-run any time you want a fresh snapshot.

---

## What you'll need

- **Python 3.9 or newer.** Check with `python3 --version`. If you don't have it, install from [python.org](https://www.python.org/downloads/).
- **A KnowledgeOwl API key** with Read permission on Article and Category. (Steps below.)
- **Your knowledge base ID.** (Steps below.)
- A terminal (Terminal on Mac, PowerShell on Windows).

---

## One-time setup

### 1. Download this folder

Save the folder containing `export.py`, `requirements.txt`, `.env.example`, and this `README.md` somewhere on your computer. For example: `~/ko-markdown-export/`.

### 2. Open a terminal in that folder

On Mac: right-click the folder in Finder → "New Terminal at Folder".
On Windows: Shift + right-click the folder → "Open in Terminal".

### 3. Install the Python dependencies

```
python3 -m pip install -r requirements.txt
```

If `python3` is not recognized on Windows, try `python` instead.

### 4. Get your KnowledgeOwl API key

Only authors with **Full Admin** permissions can create API keys.

1. Sign in to KnowledgeOwl.
2. Go to **Account → API**.
3. Select **+ Add new API key**.
4. Enter a **Key purpose** like "Markdown export".
5. Under **Knowledge base access**, select the knowledge base you want to export.
6. Check **Read** for **Article** and **Category**. Leave every other box unchecked, including everything under **Account-level access**.
7. Select **Create**, then **Copy**. Save the key somewhere safe: KnowledgeOwl only shows it once.

An older (legacy) key with GET permission also works, but a new key limited to Read on Article and Category is safer. For more on API keys, see [API keys](https://support.knowledgeowl.com/help/api-keys) in the KnowledgeOwl support docs.

### 5. Get your knowledge base ID

1. In KnowledgeOwl, go to **Articles**.
2. Look at the URL in your browser. It will look like:
   ```
   https://app.knowledgeowl.com/kb/articles/id/11abc2d3e45fg678h9012345
   ```
3. The long string at the end (`11abc2d3e45fg678h9012345` in the example) is your knowledge base ID.

### 6. Create your `.env` file

In the folder, make a copy of `.env.example` and name it `.env`. Then open `.env` in any text editor and fill in your values:

```
KO_API_KEY=paste-your-api-key-here
KO_PROJECT_ID=paste-your-knowledge-base-id-here
KO_KB_URL=https://your-kb.knowledgeowl.com
KO_OUTPUT_DIR=./export
```

- `KO_KB_URL` is optional. If you set it, each article's frontmatter will include a link back to the live article in your KB.
- `KO_OUTPUT_DIR` is optional. It defaults to `./export` (a folder next to the script).

---

## Run the export

From inside the folder, run:

```
python3 export.py
```

(On Windows, try `python export.py` if `python3` is not recognized.)

You'll see progress in the terminal:

```
Fetching categories...
  42 categories
Fetching articles (status: published or review)...
  318 articles
Writing Markdown files...
  [1/318] Getting-Started/welcome.md
  [2/318] Getting-Started/account-setup.md
  ...
Writing category index files...
  Getting-Started/_index.md
  ...
Done. Output: /Users/you/ko-markdown-export/export
Articles written:   318
Category indexes:   12
Images downloaded:  124
```

When it finishes, the `export` folder contains your knowledge base as Markdown files, organized by category. Any images referenced in articles are downloaded to an `images/` subfolder and linked from the Markdown files.

Categories that carry their own content — a description, or a body for Topic Display / Custom Content categories — get an `_index.md` file inside their folder. Regular articles live alongside that `_index.md`, in the same folder.

### What each file looks like

Every article becomes a `.md` file with YAML frontmatter at the top:

```markdown
---
title: "How to reset your password"
category: "Account / Security"
url_hash: "reset-your-password"
url: "https://your-kb.knowledgeowl.com/help/reset-your-password"
date_created: "01/15/2024 10:30 am EST"
date_modified: "03/02/2025 2:15 pm EST"
summary: "Step-by-step instructions for resetting your account password."
id: "abc123..."
---

# How to reset your password

[article body in Markdown...]
```

The frontmatter gives Claude/ChatGPT useful context (title, category, source URL) when answering questions from the folder.

---

## Upload to Claude or ChatGPT

### Claude (Projects)

1. Go to [claude.ai](https://claude.ai) and create (or open) a Project.
2. In the Project, open **Project knowledge**.
3. Drag and drop the contents of your `export` folder in.
4. Chat in that Project. Claude will use the articles as knowledge when answering.

### ChatGPT (Custom GPT or Project)

1. Go to [chat.openai.com](https://chat.openai.com).
2. Create a **Project** (or a custom GPT if you're on a plan that supports them).
3. Upload the contents of your `export` folder as files.
4. Chat in that Project/GPT — it'll reference the articles as knowledge.

---

## Re-running

The export is a static snapshot. When you add, edit, or remove articles in KnowledgeOwl, re-run the script to refresh the export:

```
python3 export.py
```

The script clears and rewrites the output folder on each run, so you always get a clean snapshot. Then re-upload to Claude/ChatGPT.

How often to re-run is up to you. A weekly or monthly cadence works well for most teams. If your KB changes rarely, re-run when you know something significant changed.

---

## What's included in the export

- Every article that's live in the knowledge base — the ones readers can actually see (API statuses `published` and `review`).
- **Category-level content** — category descriptions, plus the body of any Topic Display or Custom Content category — written as `_index.md` inside each category's folder.
- Folder structure mirroring your category hierarchy.
- Article body converted from HTML to Markdown.
- Images downloaded to a local `images/` folder and linked from the articles. Image downloads never include your API key.
- Frontmatter with title, category path, KB article URL, created/modified dates, meta description, and article ID.

## What's not included (by design)

- Drafts, rejected articles, archived articles, and deleted articles are skipped.
- Snippets, glossary, tags, readers, and other KO objects (articles and categories only).
- Internal links between articles are left as KO URLs rather than rewritten to local `.md` paths. They stay clickable and still work when the content is used as AI knowledge.

---

## Troubleshooting

**"KnowledgeOwl doesn't recognize this API key"**: The key in `KO_API_KEY` is wrong or was deleted. Copy it again, or create a new one (step 4).

**"This API key isn't allowed to read..."**: The key is missing a permission, or it's limited to a different knowledge base. In KnowledgeOwl, go to **Account → API**, edit the key, and give it Read on Article and Category for the knowledge base in `KO_PROJECT_ID`.

**"KnowledgeOwl doesn't recognize KO_PROJECT_ID"**: The knowledge base ID is wrong. See step 5.

**Script can't find `python3`**: On Windows, try `python` instead. If neither works, install Python from [python.org](https://www.python.org/downloads/).

**"No module named requests" (or similar)**: You skipped step 3. Run `python3 -m pip install -r requirements.txt`.

**Images missing from the export**: The script downloads every `<img>` src it finds, but only saves the file if the server responds with an actual image (not an HTML error page, sign-in page, etc.). Skipped images produce a warning in the terminal, and the image's original URL is preserved as a clickable link in the Markdown. A KB with lots of broken or legacy image references will show many skip warnings. This is normal and reflects stale references in the source articles, not a script problem.

---

## Looking ahead

A native KnowledgeOwl MCP server is on KnowledgeOwl's roadmap. When it ships, you'll be able to connect KnowledgeOwl to Claude directly, with always-fresh data and no export step. Until then, this Markdown export is the simple, low-maintenance way to give Claude and ChatGPT access to your KB.
