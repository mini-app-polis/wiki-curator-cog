// wiki-curator-cog as Railway Infrastructure as Code, applied by CI: a pull
// request into main that changes .railway/ gets a plan comment, and merging
// it applies exactly that plan (.github/workflows/railway-plan.yml,
// railway-apply.yml). Nothing is applied from a workstation, and Railway
// itself does not read this file when it deploys. Replaces railway.json,
// which Railway stops reading on 2026-12-01. MINI-69.
//
// A named partial: the ecosystem project holds services from several
// repositories, so this file manages wiki-curator-cog and nothing else.
// Removing the service from this file would delete it on the next apply.
//
// Production only. Planned against any other environment it declares
// nothing, and a partial that owns nothing there changes nothing; the
// development service keeps the settings its dashboard holds.
//
// One run per deploy, then exit (ADR-004): never restarted, because a
// failed run is not retried. Built by Railpack from railpack.json (which
// installs git) until the move to Lambda retires this service — the CD-021
// deferral in evaluator.yaml.
//
// Variables are references to the environment's shared variables, which
// Doppler prd keeps current (`ctx.shared.NAME` is `${{shared.NAME}}`); no
// value lives in this file. Only the names the cog reads are declared.

import { defineRailway, github, project, service } from "railway/iac";

export const partial = "wiki-curator-cog";

const REGION = "us-east4-eqdc4a";

const SHARED = [
  "ENVIRONMENT",
  "GH_TOKEN",
  "KAIANO_API_BASE_URL",
  "LOGGING_LEVEL",
  "SENTRY_DSN_COGS",
  "WIKI_BRANCH",
  "WIKI_CURATOR_COG_API_KEY",
];

export default defineRailway((ctx) => {
  const name = ctx.projectName ?? "mini-app-polis ecosystem";
  if (ctx.environment !== "production") {
    return project(name, { resources: [] });
  }

  const cog = service("wiki-curator-cog", {
    source: github("mini-app-polis/wiki-curator-cog", { branch: "main" }),
    build: { builder: "RAILPACK" as const },
    replicas: { [REGION]: 1 },
    deploy: {
      startCommand: "python -m wiki_curator_cog.main",
      restartPolicyType: "NEVER" as const,
    },
    env: Object.fromEntries(SHARED.map((n) => [n, ctx.shared[n]])),
  });

  return project(name, { resources: [cog] });
});
