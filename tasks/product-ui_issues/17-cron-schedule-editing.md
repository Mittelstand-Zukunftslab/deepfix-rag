# 17 — Change the cron schedule

**What to build:** An admin changes when the nightly optimization job runs, or pauses it, from the Optimization screen. The new schedule is stored in the product database and survives a restart. An invalid expression is rejected and the current schedule stays in place. Pausing the job stops scheduled runs, and Run now still works while the job is paused.

**Blocked by:**

- 16 — Admin Optimization screen and Run now

**Status:** ready-for-agent

- [ ] The Optimization screen shows the cron expression in an editable field, an Enabled toggle, and a Save button.
- [ ] Updating the schedule accepts a five-field cron expression and an enabled flag, and updates the in-process scheduler. The new schedule is stored in the product database and survives a restart.
- [ ] An invalid cron expression returns 400 and leaves the schedule unchanged. The field shows the error below it.
- [ ] Disabling the job stops scheduled runs. Run now still works while the job is disabled.
- [ ] After saving, the screen shows the new expression and the next run time computed by the scheduler.
- [ ] Typecheck passes.
- [ ] Verify in the browser.
