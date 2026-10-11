# 16 — Admin Optimization screen and Run now

**What to build:** An admin opens Optimization and sees the nightly schedule, the next run time, the current champion and how it differs from the previous one, and the 20 most recent runs. Run now starts the same in-process job as the nightly schedule. Members who open the page see a single sentence and no controls. If Hermes is not set up on this server, the schedule and history still show and Run now is disabled. This screen replaces the mock Admin metrics screen.

**Blocked by:**

- 15 — Promotion gate and holdout check

**Status:** ready-for-agent

- [ ] The Optimization screen is an admin-only route that replaces the mock Admin metrics screen and its mock service. Members who open the URL see "This page is only available to admins." and no controls.
- [ ] On API startup, the process starts an in-process scheduler for the job `deepfix-optimize-workspace`. The default schedule is `0 2 * * *` in Europe/Berlin (02:00 every day). The scheduled callback is the same function as Run now.
- [ ] The optimization status returns the cron expression, timezone, enabled flag, and next run time computed by that scheduler, plus the 20 most recent runs: status (`running`, `skipped`, `rejected`, `promoted`, `succeeded`, `failed`), trigger (`cron` or `manual`), start and end times, reason, primary metric, dev and holdout deltas, holdout wins and losses, and MLflow link.
- [ ] If the Hermes interpreter is not configured, cannot import the agent library, or the LLM settings are missing, the response still returns the schedule and run history, with the agent marked unavailable. The screen shows "Hermes is not set up on this server, so a run cannot start. The schedule and history are still shown." and disables Run now.
- [ ] The screen shows the current champion config path and a diff against the previous champion, rendered from the config-diff JSON.
- [ ] Run now starts the same job as the scheduler, with trigger manual. It does not submit a different prompt and does not call the Hermes gateway.
- [ ] The button label is "Run now". It is disabled while any run is running, with the note "A run is already in progress."
- [ ] Members receive 403. An admin request while a run is running receives 409. If Hermes is not set up, the route returns 503.
- [ ] A manual run that is skipped shows the reason with the actual counts, for example "Skipped: 14 labeled questions in dev, 50 needed."
- [ ] The screen polls the optimization status every 5 seconds while a run is running.
- [ ] Typecheck passes.
- [ ] Verify in the browser.
