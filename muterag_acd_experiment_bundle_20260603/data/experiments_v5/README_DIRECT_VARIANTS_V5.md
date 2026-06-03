# Direct attack variants

Experiment A separates two direct prompt-injection behaviours.

## Employee-record leak

- `experiment_direct_blackbox/`
- `experiment_direct_whitebox/`

The injected payload asks the model to retrieve an employee record from
`hr_employee_directory.md`. Experiment A pins that file into the LLM context
only for these two corpora. The evaluator uses
`metadata/leak_targets_v5.json` to check whether the response contains the
targeted fictional employee data.

## Ignore and refuse

- `experiment_direct_blackbox_ignore/`
- `experiment_direct_whitebox_ignore/`

The injected payload asks the model to ignore the user's question and return a
fixed refusal sentence. These conditions do not receive the employee directory.

## Condition groups

- `v5_all`: normal, employee-record direct, MutedRAG
- `v5_ignore`: normal, ignore/refuse direct
- `v5_full`: all seven corpora

`A_PIN_CONDITIONS` defaults to
`direct_blackbox,direct_whitebox`. Keep normal and MutedRAG unpinned when
measuring ordinary false-positive and availability effects.

