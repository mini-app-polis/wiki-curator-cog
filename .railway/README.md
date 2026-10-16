# .railway

`railway.ts` declares the wiki-curator-cog Railway service as Railway
Infrastructure as Code. It is a named partial (`wiki-curator-cog`): it
manages that service and nothing else in the `mini-app-polis ecosystem`
project, and only in production.

It is applied by CI, never from a workstation, as mini-app-polis/infra
applies Terraform: a pull request into `main` that changes `.railway/` is
planned, with the plan in the job summary (`.github/workflows/railway-plan.yml`),
and merging it plans again and applies (`railway-apply.yml`). Removing a
service or a variable is destructive and applies only if the pull request is
labelled **`allow-destroy`**. Actions → Railway apply → Run workflow
re-applies `main` by hand. Both call the shared workflows in
`mini-app-polis/.github`. Railway itself does not read this directory at
deploy time.

A variable the cog starts reading must be added to `SHARED` here, or the
next apply removes it.
