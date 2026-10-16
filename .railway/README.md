# .railway

`railway.ts` declares the wiki-curator-cog Railway service as Railway
Infrastructure as Code. It is a named partial (`wiki-curator-cog`): it
manages that service and nothing else in the `mini-app-polis ecosystem`
project, and only in production.

It is applied by CI, never from a workstation: a pull request into `main`
that changes `.railway/` gets the plan as a comment
(`.github/workflows/railway-plan.yml`), and merging it applies exactly that
plan (`railway-apply.yml`). Both call the shared workflows in
`mini-app-polis/.github`. Railway itself does not read this directory at
deploy time.

A variable the cog starts reading must be added to `SHARED` here, or the
next apply removes it.
