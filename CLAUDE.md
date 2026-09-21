# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Backend (Maven multi-module, Java 21, Spring Boot 3.3.5)
cd backend && mvn -o test          # all modules (offline mode, ~12s)
# Run a single test class:
mvn -o -pl his-generator -Dtest=DataGeneratorServiceTest test
mvn -o -pl his-server -Dtest=TimelineServiceTest test
# Run a single test method:
mvn -o -pl his-server -Dtest=DashboardServiceTest#admissionRatio_returnsCorrectFormula test
# Package server (skip tests):
mvn -pl his-server -am package -DskipTests

# Run generator JAR (generates all test data):
cd backend && mvn -q -pl his-generator -am install -DskipTests && java -jar his-generator/target/his-generator-1.0.0.jar

# Frontend (Vue 3 + Vite + Element Plus)
cd frontend && npm run dev          # dev server (port 5173, proxies /api → :8080)
cd frontend && npm run build        # production build
```

## Architecture Overview

```
backend/                            # Spring Boot 3.3.5, Java 21, Maven multi-module
├── his-common/                     # Entities, Mappers (MyBatis-Plus), DTOs, shared types
├── his-server/                     # REST API (port 8080), services, validators
└── his-generator/                  # Batch data generator (standalone JAR)

frontend/                           # Vue 3 + Vite + Element Plus + ECharts
├── src/api/                        # Axios wrappers (baseURL: /api)
├── src/views/                      # 6 routed views (lazy-loaded via import.meta.glob)
├── src/components/                 # Chart.vue, Timeline.vue
└── src/router/                     # createWebHistory routing, no guards
```

## Key Constraints (binding, from thesis requirements)

- **No auth/session/JWT** — the demo has no login; user identity is a frontend-only dropdown.
- **No simulated data (patient/doctor/pharmacist/nurse) may use 「王冠杰」** (the author's real name).
- **Unified envelope**: every response is `Result{code, message, data}`. `code=0` is success. `Result.ok()` and `Result.fail()` are the only constructors used.

## Frontend Patterns

- **API responses**: `frontend/src/api/request.js` axios interceptor returns `res.data` (the `Result` envelope). In components, `await someApi()` gives you `Result`, so `res.data` is the payload (`res.data.data` for nested DTOs like `TimelineVO`).
- **Lazy routes**: `src/router/index.js` uses `import.meta.glob('../views/*.vue')` — just add a `.vue` file to `views/`, no router edit needed. Placeholder rendering for missing views.
- **Vite dev proxy**: `/api` → `http://localhost:8080`. Production deployment must replicate this (nginx reverse proxy or same-origin JAR serving static files).
- **No TypeScript** — all frontend code is plain JS with `.vue` SFCs.

## Database

- MySQL 8.0, database `his_demo`. MyBatis-Plus auto-maps camelCase entity fields to snake_case columns (`patientId` ↔ `patient_id`).
- `schema.sql` creates all 15 tables. `seed-extension.sql` adds rational-rule + clinical-pathway seed rows.
- **Dictionary tables** (never truncated by generator): `drug`, `rule_config`, `doctor`, `rational_rule`, `clinical_pathway`.
- **Data tables** (truncated before each generator run): `patient`, `visit`, `diagnosis`, `prescription`, `prescription_item`, `lab_test`, `charge`, `dispense`, `admission`, `medical_order`, `vital_sign`.

## Redis Caching

- Redis is **optional/pure optimization**. All cache keys have a TTL (30 min default). Every `GET` path has a fallback: on Redis exception, log a warning and query DB directly — never return 500 because Redis is down.
- Cache keys: `timeline:{patientId}`, `timeline:admission:{admissionId}`.
- `CacheInvalidator` evicts patient-timeline keys on data writes; admission-timeline keys on ORDER/VITAL_SIGN/LAB_TEST writes.

## Data Generator (`his-generator`)

- `DataGeneratorService.generate(patientCount)` generates the full demo dataset.
- `clearBeforeRun` defaults to `true` — truncates 11 data tables, preserves 5 dictionary tables.
- Per patient: 5–20 outpatient visits (1–3 year history span), 80% of visits to a "home department".
- 20% of patients (`INPATIENT_RATIO`) get 1–3 admissions (3–15 day stay each, fixed 16-day gap between admissions).
- ~3% of prescriptions get a deliberately-injected violation (interaction/dose/indication) so the validation engine has something to detect.
- The `verify-final.sql` script checks referential integrity, visit counts, and item counts after generation.

## Testing

- Controller tests: `@WebMvcTest` + `@MockBean` for all injected services.
- Service/generator tests: plain Mockito (no Spring context).
- Generator tests initialize about 40 admissions from 200 patients (20% × 200 = 40) — mock-based, no real MySQL.
- 2 tests in `PerformanceTest.java` are gated behind `@EnabledIfSystemProperty` (skipped by default).