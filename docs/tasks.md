# Personal checklist

Type `/todo` in web chat to see the commands. No model key is needed.

- `/todo add Buy notebooks` saves a task and returns its ID.
- `/todo list` shows open and completed tasks with IDs.
- `/todo done <id>` marks a task done.
- `/todo reopen <id>` marks it open again.
- `/todo delete <id>` deletes it.

This is a checklist, not a reminder or an autonomous worker. There are no dates,
notifications, background checks, calendar changes, or email actions. Commands
must be typed explicitly. A model reply cannot create or change tasks.
Task replies are not saved as chat history; use `/todo list` after reopening chat.
They can appear in an export or share of the current screen, so review it first.
Send task commands without attachments.

## Storage and identity

Uses the existing database configuration and requires `SECRET_KEY`. There is no
new service, API key or paid dependency. The `cw_tasks` table is created lazily.
Titles are encrypted with a separate key derived from `SECRET_KEY`. Do not rotate
that key without an appropriate migration: old task titles become unreadable.
No task titles are loaded into model context or sent to model providers.

Anonymous tasks belong to the signed browser identity. Keep that browser cookie:
clearing it can make guest tasks inaccessible. Google sign-in transfers guest
tasks to the existing linked identity. Sign-out is not erasure. New chat keeps
tasks. Forget me deletes the current identity's saved chats and tasks, but does
not revoke connected accounts or remove old share links.

The response labels Postgres or temporary SQLite storage. SQLite on an ephemeral
host can be lost on restart. This change does not change any database URL or make
free hosting always-on. Tasks older than 90 days are removed on the next task
command, not by a background deletion job. Each identity can add up to 200 tasks;
merging pre-existing guest checklists can exceed that limit, and all merged tasks
remain visible. Delete some before adding more.

## API safety

`POST /api/chat` with `X-Requested-With: crayon`, raw `/todo` text, and a
16-80 character alphanumeric/underscore/hyphen `request_id` for adds. Reusing the
same request ID and text deduplicates the add. Reusing it with different text is
rejected. The web UI generates a new ID for each submission. After a lost
response, use `/todo list` before manually resubmitting: manual submission is a
new request, not an automatic retry. IDs must be copied exactly for updates.

Results are marked `task_result` in SSE and rendered as plain text. They never
enter the browser tool execution loop. Storage, encryption or erasure failures
are reported as unverified rather than successful. Postgres count/add is guarded
with a per-identity transaction advisory lock; SQLite uses BEGIN IMMEDIATE.

Tests use isolated synthetic SQLite data. Production Postgres behavior and live
Render deployment require separate verification; this PR is not a deployment.
