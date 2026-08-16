# Project agent instructions

## Autonomy

- For requests to change, build, fix, implement, run, test, or publish this project, continue autonomously through all safe in-scope steps until the requested outcome is complete.
- Read files, edit project files, install project-local dependencies, run builds and tests, initialize Git, commit changes, and perform non-destructive validation without asking for confirmation.
- Resolve ordinary implementation details with reasonable defaults. Record user-configurable assumptions in configuration files and documentation instead of pausing.
- The user's standing request authorizes creating and updating the public GitHub repository and GitHub Pages site for this project, provided personal Pokemon data, captures, databases, credentials, and local settings are excluded.
- Keep the user informed with short progress updates, but do not turn routine choices into blocking questions.

## Safety boundaries

- Never publish personal Pokemon box data, screenshots, videos, SQLite databases, local settings, credentials, tokens, usernames, home-directory paths, or generated reports derived from private data.
- Keep private inputs under ignored directories and publish only source code, examples using fictional/sample data, documentation, tests, and sanitized static demo output.
- Ask before destructive actions, purchases, irreversible external changes outside this project's GitHub repository, or material scope expansion.
- Preserve unrelated user changes and do not use destructive Git commands.

## Definition of done

- Implement the requested behavior, run relevant tests/builds, inspect the public diff for sensitive data, and update documentation.
- For publishing requests, push the sanitized repository and verify the GitHub Pages deployment when the available authenticated tools permit it.
