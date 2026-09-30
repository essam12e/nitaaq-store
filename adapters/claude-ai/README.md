# claude.ai packaging

`python3 tools/build.py` writes `dist/claude-ai/nitaaq-store.zip`: the shared
core with only Agent Skills spec frontmatter fields (`name`, `description`,
`license`, `compatibility`, `metadata`), which claude.ai skill upload accepts.

Upload it from the Skills area of claude.ai settings (Customize → Skills in
current apps). Skills must be enabled for the account/organization, and the
helpers need code execution to be available. See docs/ar/التثبيت.md.
