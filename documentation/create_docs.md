You are creating developer-friendly documentation for a software project.

**Context:**
- Project Type: any (web app, CLI, library, desktop app, etc. — adapt to what you find)
- Target Audience: intermediate developers who are new to this specific codebase
- Goal: Enable future developers (and AI assistants) to quickly understand, build on, and contribute to this codebase

---

## Step 1 — Gather context first

Before writing anything, read whatever exists of the following as your primary source material. These are **input references only** — they shape your understanding of the project; they do not dictate the shape of the output. Do not invent details that are not supported by these files or by the code itself.

1. `README.md` — user-facing overview and run instructions
2. `BUILD.md` — build / dev setup, if present
3. `CLAUDE.md` — architecture notes, state model, design constraints
4. `.claude/napkin.md` — reusable patterns and gotchas discovered in prior sessions
5. Any other top-level `*.md` (e.g. `ALGORITHM.md`, `CONTEXT.md`, `PLAN.md`)
6. Manifest / config files that reveal stack and entry points (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, the main HTML/JS/source files, etc.)

If a key piece of information is missing from these sources, either skip that section or mark it `_TODO: confirm with maintainer_` rather than guessing.

---

## Step 2 — Verify load-bearing claims before writing

Spot-check anything a reader will *act on* against the actual source — install commands, run commands, entry-point file paths, dependency names and versions, port numbers, env var names. If the manifest or source disagrees with a reference doc, trust the source and flag the discrepancy in your summary. A confidently-wrong command is worse than no command.

---

## Step 3 — Handle an existing `documentation/` folder

Before writing, check if `documentation/` already exists.
- **Empty or missing:** create it and proceed.
- **Exists with content:** do not overwrite blindly. Read what's there. Update files in place where they're outdated, add new files for uncovered topics, and leave hand-written content alone unless it's clearly stale or contradicted by the code. If you're unsure whether to replace a file, ask the user.

---

## Step 4 — Produce the `documentation/` folder

Create a `documentation/` folder containing **whatever files best serve this project**. The output is not required to mirror the reference files in name, count, or structure — those are just where you got your information. Decide the file set based on what an intermediate developer actually needs to understand and contribute to this codebase.

Use whatever filenames make sense for the audience. A small CLI tool might only need `getting-started.md` and `architecture.md`; a desktop app might warrant `install.md`, `architecture.md`, `packaging.md`, and `data-model.md`; a library might need `usage.md` and `api.md`. Group, split, or rename freely.

**Prefer fewer dense files over many sparse ones.** A topic-driven prompt tends to spawn one thin file per bullet — resist that. If two topics are short and related, put them in the same file under separate headers.

**Don't duplicate strong root docs.** If the project already has a thorough top-level `README.md` or `BUILD.md`, link to it from the `documentation/` index rather than copying its contents. Two copies will drift. Reserve `documentation/` for the architecture / design / rationale layer the root files don't cover.

**Anti-rot rule.** For details that change often — line numbers, exact function names, specific config keys, dependency versions — point at the source (`path/to/file.js:123`) instead of transcribing the value. Transcribed details go stale silently; pointers don't.

**Diagrams: use Mermaid.** It renders on GitHub and most markdown viewers. Fall back to ASCII only if a Mermaid diagram type doesn't fit.

### Topics to cover (across however many files you choose)

Across the documentation set as a whole, make sure these topics are addressed where they apply. Drop any that don't.

- **Project overview** — one-sentence pitch, problem solved, 3–5 key features, project status (stable / beta / experimental)
- **Quick start** — prerequisites, install (3–4 commands), first run / how to launch
- **Dev setup & build** — language/tool versions, env vars, config files, dev server, production build / packaging
- **Testing** — only if tests exist; otherwise state "no automated tests" once and move on
- **Architecture** — folder/file map, main components, data flow, key dependencies and why
- **Coding conventions** — naming, file organization, recurring patterns
- **Design rationale** — non-obvious decisions, alternatives rejected, trade-offs, fragile areas
- **Data model** — main entities and how they relate (if applicable)
- **External integrations** — APIs, databases, services (if any)
- **Algorithms / domain logic** — if there is non-trivial logic worth specifying
- **Entry points** — where a new reader should start in the code
- **Future considerations** — known limitations, planned improvements

Cross-link between files (e.g. "see `architecture.md` for the data flow"). Keep an index — typically a `README.md` or `index.md` inside `documentation/` — that lists each file with a one-line description so a newcomer can navigate.

### Don't include

- Changelogs, version history, release notes — git already has these
- Contributor lists or credits
- Glossaries, unless terms are genuinely project-specific and unobvious
- Roadmaps or "future work" invented from thin air — only include if the source files explicitly state plans
- Generic boilerplate (e.g. a "What is Python?" intro) — assume reader-knowledge of the stack
- Marketing copy

---

## Output Requirements

- Clear markdown formatting (headers, lists, fenced code blocks)
- Real code / command examples lifted from the project, not invented placeholders
- Professional but friendly tone
- Assume reader has experience in the project's language but is new to this codebase
- Skimmable: headers, bold for key terms, whitespace
- Each file should be focused and scannable — split when a file starts covering unrelated concerns

**Output Format:** Write each file directly under `documentation/`. Do not paste the full contents back into chat — a short summary of what was created (file list with one-line purpose each, plus any TODOs left for the maintainer) is enough.
