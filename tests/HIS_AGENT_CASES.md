# Three synthetic HIS/Agent integration cases

Dataset: `tests/fixtures/his_agent_cases.json`. Every record is synthetic. Patients
2001/7101 reuse the repository's recorded contract examples; patient 2002 is a
new fixture, not a claim about rows present in a deployed database.

The HIS generator is configured for 10,000 synthetic patients. The SQL seed file
contains dictionaries/rules; running it alone does not create those patients.
The backend must be started with its database populated to use real HIS HTTP.

## Run without deploying MySQL

```bash
python tests/mock_his_server.py --port 18080
```

This mock exposes only the selected read-only patient/timeline routes and a
minimal patient list for discovering its two IDs. It does not simulate the rest
of HIS. POST, PUT and DELETE return 405; no database is used or modified.

Start the integrated Agent with HIS_BASE_URL=http://127.0.0.1:18080, plus the
existing Qwen and MiniCheck settings. Keep the mock running in a separate terminal.
Use the UI patient selector and cutoff date for each turn in the JSON. Start a
new thread for each case, but keep the two turns of a case in the same UI thread.

1. **Normal joint evidence**: patient 2001 at 2025-12-31. Read outpatient and
   admission data, then retrieve hypertension evidence. BP is a dated 158/96
   record; potassium is 3.4 then 3.9 mmol/L. The 2026 creatinine is hidden. The
   second question asks for provenance and exercises citation opening/reuse.
2. **Historical cutoff**: patient 2001 at 2025-03-02, then 2025-03-03. The latest
   visible potassium changes from 3.4 to 3.9. Changing cutoff must rotate the
   Agent memory/citation scope. Both dates exclude the later discharge event and
   the 2026 laboratory event. This case deliberately exposes the field-level
   admission history gap described below.
3. **Patient switch / missing data**: first read 2001, then select 2002 at the same
   cutoff. Patient 2002 has a diabetes diagnosis and a metformin prescription
   without dose/frequency, no labs and no BP. Missing values stay missing. Neither
   literature nor 2001's facts may fill them. A negative variant selects 999999;
   the mock returns data=null, and the Agent must report the missing patient.

External PubMed retrieval is live and nondeterministic. Do not assert a fixed PMID
list or require every answer to pass on a specific revision number. Check citation
scope, actual returned evidence, audit outcome and explicit failure messages.

## Known gap, not a passed acceptance criterion

The recorded 2025-03-01 ADMISSION entity already includes later dischargeTime,
dischargeDiagnosis and status fields. Current RAG as_of filtering removes future
events, but does not reconstruct historical values inside an earlier entity.
Consequently, a March 2 snapshot can still expose March 8 discharge data in that
entity. The dataset must not hide this defect. Its corresponding assertion is
marked expectedFailure; that does not mean the temporal requirement is satisfied.
A follow-up fix requires field-level visibility/version semantics coordinated
between HIS and the snapshot contract. These fixtures do not implement that fix.

## Check the dataset and mock transport

```bash
python -m unittest tests.test_his_agent_cases
```

These checks validate HTTP, mapping, event cutoff and the missing-data fixtures.
They do not call Qwen/MiniCheck or validate a medical answer. Manual Agent runs
must inspect his_snapshot, evidence_search, answer_audit and final_response events
in agent/logs/evidence_audit.jsonl, including patient_scope and retrieval IDs.
