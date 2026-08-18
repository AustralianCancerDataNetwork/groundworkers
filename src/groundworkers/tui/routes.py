from groundskeeping.contracts import PageRoute

SETUP_ROUTE = PageRoute(
    key="setup",
    label="Setup",
    purpose="Configure and verify the services Groundworkers uses.",
)


__all__ = ["SETUP_ROUTE"]
