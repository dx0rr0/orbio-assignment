"""The demo policies, each covering one scenario. Dates are relative to `today` so the
scenarios hold whenever the demo runs.

Policy numbers avoid long runs of the same digit: the STT model drops digits from them
("AUT-00000001" came back as "AUT0000001" in the spike).
"""

from datetime import date, timedelta

from claims_agent.domain import Policy


def seed_policies(today: date) -> tuple[Policy, ...]:
    year = timedelta(days=365)
    return (
        # Happy path.
        Policy(
            number="AUT-48213907",
            holder_name="María López Fernández",
            plate="4721KLM",
            start_date=today - timedelta(days=200),
            end_date=today - timedelta(days=200) + year,
        ),
        # The driver is not the policyholder.
        Policy(
            number="AUT-51730284",
            holder_name="Javier Ruiz Martín",
            plate="8305HJT",
            start_date=today - timedelta(days=90),
            end_date=today - timedelta(days=90) + year,
        ),
        # Expired last month.
        Policy(
            number="AUT-62947105",
            holder_name="Lucía Navarro Gil",
            plate="2196FDS",
            start_date=today - timedelta(days=30) - year,
            end_date=today - timedelta(days=30),
        ),
        # Started three days ago: a week-old loss falls outside it.
        Policy(
            number="AUT-73058416",
            holder_name="Carlos Moreno Díaz",
            plate="6614LPR",
            start_date=today - timedelta(days=3),
            end_date=today - timedelta(days=3) + year,
        ),
        # Declared with a plate that is not the one on the policy.
        Policy(
            number="AUT-84169527",
            holder_name="Elena Sánchez Ortiz",
            plate="3087GBN",
            start_date=today - timedelta(days=150),
            end_date=today - timedelta(days=150) + year,
        ),
    )
