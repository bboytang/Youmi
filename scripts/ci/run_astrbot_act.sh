#!/usr/bin/env bash
set -euo pipefail
youmi_root="$(git rev-parse --show-toplevel)"
youmi_results="${YOUMI_ACT_RESULTS:-${TMPDIR:-/tmp}/youmi-act-results}"
mkdir -p "$youmi_results"
act --version
for youmi_job in "${@:-backend}"; do
  case "$youmi_job" in backend|connector|manager) ;; *) echo "Unknown job: $youmi_job" >&2; exit 2 ;; esac
  if [[ "$youmi_job" == manager ]]; then
    docker compose -f "$youmi_root/scripts/ci/astrbot-test-services.yaml" down
    docker compose -f "$youmi_root/scripts/ci/astrbot-test-services.yaml" up -d --wait
    trap 'docker compose -f "$youmi_root/scripts/ci/astrbot-test-services.yaml" down' EXIT
  fi
  act workflow_dispatch -W "$youmi_root/.github/workflows/youmi-astrbot.yml" -j "$youmi_job" \
    -P ubuntu-latest=-self-hosted --pull=false --env-file /dev/null --secret-file /dev/null \
    --env "YOUMI_REPO_ROOT=$youmi_root" --env "PATH=$PATH" \
    --env "YOUMI_TEST_SOURCE_DIR=${YOUMI_TEST_SOURCE_DIR:-$youmi_results/sources}" \
    --env "YOUMI_MAVEN_REPO=${YOUMI_MAVEN_REPO:-$youmi_results/m2}" \
    --env "YOUMI_MAVEN_SETTINGS=${YOUMI_MAVEN_SETTINGS:-}" \
    2>&1 | tee "$youmi_results/$youmi_job.log"
done
