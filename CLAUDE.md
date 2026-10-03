# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## Git identity (mandatory)

Every commit in this repository must be authored **and** committed as the
repository owner. Before doing any other work in a new session or clone:

```sh
git config user.name "rafay-ah"
git config user.email "54492363+rafay-ah@users.noreply.github.com"
```

Rules:

- Commits must be authored and committed as
  `rafay-ah <54492363+rafay-ah@users.noreply.github.com>`.
- Do **not** add `Co-Authored-By` trailers, `Claude-Session` trailers, or any
  "Generated with Claude Code" lines to commit messages, pull request titles,
  or pull request bodies. `.claude/settings.json` disables the default
  attribution text for this reason; keep it that way.
- Before every push, verify the history:

  ```sh
  git log --format='%an <%ae> | %cn <%ce>'
  ```

  Every line must read
  `rafay-ah <54492363+rafay-ah@users.noreply.github.com> | rafay-ah <54492363+rafay-ah@users.noreply.github.com>`.
  Fix any commit that is not fully the owner's before pushing, e.g.
  `git commit --amend --reset-author --no-edit` for the last commit, or
  `git rebase -r <base> --exec 'git commit --amend --reset-author --no-edit'`
  for older unpushed commits. Never rewrite history that is already pushed
  without the owner's permission.
- Commit often, with clear, descriptive messages (imperative subject line,
  wrapped body explaining the why).
