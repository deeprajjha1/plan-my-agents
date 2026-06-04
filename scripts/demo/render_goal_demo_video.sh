#!/usr/bin/env bash
# Record a ~1-minute goal demo and save to canvases/goal_demo_recording.webm
#
# Prerequisites:
#   - FastAPI on PLANMYAGENTS_API_BASE_URL (default http://127.0.0.1:8000)
#   - Next.js on PLANMYAGENTS_WEB_BASE_URL (default http://localhost:3000)
#     OR let Playwright start `npm run dev` automatically.
#   - cd apps/web && npm run e2e:install  (once)
#
# Edit goal text + mocked API result before recording:
#   apps/web/tests/e2e/demo-goal-fixture.ts
#
# Usage:
#   ./scripts/demo/render_goal_demo_video.sh
#   ./scripts/demo/render_goal_demo_video.sh --output canvases/yc_demo_recording.webm

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WEB="$ROOT/apps/web"
OUTPUT="${ROOT}/canvases/goal_demo_recording.webm"
WORK="$ROOT/.planmyagents_runs/demo_video"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$WORK" "$(dirname "$OUTPUT")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required (brew install ffmpeg)" >&2
  exit 1
fi

API_BASE="${PLANMYAGENTS_API_BASE_URL:-http://127.0.0.1:8000}"
WEB_BASE="${PLANMYAGENTS_WEB_BASE_URL:-http://localhost:3000}"

echo "Checking API at $API_BASE ..."
if ! curl -sf "$API_BASE/health" >/dev/null 2>&1; then
  echo "FastAPI not reachable. Start with:" >&2
  echo "  PYTHONPATH=apps/api .venv/bin/uvicorn planmyagents_api.web.app:app --host 127.0.0.1 --port 8000" >&2
  exit 1
fi

# Only pin WEB_BASE_URL when something is already listening — otherwise
# Playwright starts `npm run dev` via playwright.config.ts webServer.
USE_EXISTING_WEB=0
if curl -sf -o /dev/null "$WEB_BASE" 2>/dev/null; then
  echo "Using existing web server at $WEB_BASE"
  USE_EXISTING_WEB=1
else
  echo "No web server at $WEB_BASE — Playwright will start npm run dev"
fi

echo "Recording Playwright demo (mocked /goal — no planning wait) ..."
cd "$WEB"
rm -rf test-results
if [[ "$USE_EXISTING_WEB" -eq 1 ]]; then
  PLANMYAGENTS_API_BASE_URL="$API_BASE" \
  PLANMYAGENTS_WEB_BASE_URL="$WEB_BASE" \
    npx playwright test tests/e2e/demo.spec.ts --project=chromium
else
  PLANMYAGENTS_API_BASE_URL="$API_BASE" \
    npx playwright test tests/e2e/demo.spec.ts --project=chromium
fi

RAW="$(find test-results -name video.webm -print -quit)"
if [[ -z "$RAW" || ! -f "$RAW" ]]; then
  echo "No Playwright video found under apps/web/test-results" >&2
  exit 1
fi

echo "Post-processing → $OUTPUT"
ffmpeg -y -i "$RAW" \
  -vf "scale=800:500:force_original_aspect_ratio=decrease,pad=800:500:(ow-iw)/2:(oh-ih)/2,setsar=1" \
  -t 62 \
  -c:v libvpx-vp9 -b:v 1.5M -crf 32 \
  "$OUTPUT"

DUR="$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$OUTPUT")"
SIZE="$(du -h "$OUTPUT" | cut -f1)"
echo ""
echo "Done: $OUTPUT"
echo "  Duration: ${DUR}s  Size: $SIZE"
echo ""
echo "To change the goal question or final result, edit:"
echo "  apps/web/tests/e2e/demo-goal-fixture.ts"
