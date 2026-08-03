"""Milestone 0 placeholder tests wiring the CI test job.

Real test suites arrive with their milestone: shared backend contract tests
(Milestone 2, §18), engine property tests (Milestone 3, ACC-01), official-SDK
MCP protocol tests (Milestone 4, §18), API golden fixtures (Milestone 5,
NFR-06), and the §6 benchmark/chaos suite (Milestone 8, NFR-00).
"""


def test_app_imports_and_boots() -> None:
    # app.main is the boot orchestrator (CFG-15); run() is the entrypoint.
    import app.main as main

    assert callable(main.run)
    assert main.build_storage is not None


def test_healthcheck_module_is_invocable() -> None:
    # DEL-01 fixes the module paths `app.main` and `app.healthcheck`.
    import app.healthcheck as hc

    assert callable(hc.main)
    hc.main()  # must not raise
