# Test Results — Wake Assistant

## Commands

```bash
PYTHONPATH=.:/tmp/generic_tldv_wake_deps python3 -m unittest discover -s tests -q
```

Result:

```text
Ran 16 tests in 0.037s

OK
```

```bash
python3 -m compileall services/wake-orchestrator/app services/wake-orchestrator/tests
```

Result: pass.

```bash
WAKE_ORCHESTRATOR_CHECK_CONFIG=1 VEXA_API_KEY=vexa-key GROQ_API_KEY=groq-key AIVIS_API_KEY=aivis-key PYTHONPATH=.:/tmp/generic_tldv_wake_deps python3 -m app.main
```

Result:

```text
config ok
```

```bash
docker compose --env-file .env -f deploy/compose/docker-compose.yml --profile wake config
```

Result: pass; `wake-orchestrator` profile includes Kabosu wake defaults, Groq model, Aivis model UUID, `WAKE_AUTO_DISCOVER_BOTS=true`, and an optional empty fixed meeting id.

```bash
cd services/dashboard && npm test -- test_export_and_bot_defaults.test.ts
```

Result:

```text
Test Files  1 passed (1)
Tests  4 passed (4)
```

```bash
cd services/dashboard && VEXA_API_URL=http://localhost:8056 npm run build
```

Result: pass; Next.js compiled, TypeScript completed, and 60 static pages generated.

```bash
docker compose --env-file .env -f deploy/compose/docker-compose.yml --profile wake build wake-orchestrator
```

Result: pass; image `vexaai/wake-orchestrator:latest` built.

```bash
docker run --rm -e WAKE_ORCHESTRATOR_CHECK_CONFIG=1 -e VEXA_API_KEY=vexa-key -e GROQ_API_KEY=groq-key -e AIVIS_API_KEY=aivis-key vexaai/wake-orchestrator:latest
```

Result:

```text
config ok
```

```bash
make -C deploy/compose -n build-wake-orchestrator
```

Result: pass; dry run expands to a tagged Docker build for `vexaai/wake-orchestrator:<BUILD_TAG>`.

```bash
node .gitnexus/run.cjs detect-changes -r generic_tldv
```

Result: medium risk; affected process is `ZoomCallbackContent -> WithPostMeetingAutoStop`. This is expected because dashboard bot creation defaults now enable voice-agent playback for join, pending meeting, and Zoom callback flows.
