"""Create demo accounts with sample data: `python -m aureus.seed`.

    owner@demo.in / demo-pass-123  (business owner, Shree Ganesh Auto Components)
    ca@demo.in    / demo-pass-123  (CA, Mehta & Associates, with two clients)
"""

from . import db, sample, services
from .ingestion import parse_books, parse_gstr2b

PASSWORD = "demo-pass-123"


def _user(email, name, role, firm=None):
    try:
        return services.create_user(email, name, PASSWORD, role, firm)
    except services.EmailTaken:
        return services.login(email, PASSWORD)[1]


def main():
    db.init()
    owner = _user("owner@demo.in", "Rajesh Kulkarni", "msme")
    ca = _user("ca@demo.in", "CA Priya Mehta", "ca", "Mehta & Associates")
    if services.list_orgs(owner):
        print("Demo data already present.")
        return
    org = services.create_org(owner, dict(sample.BUYER))
    for purpose in services.PURPOSES:
        services.grant_consent(owner, org["id"], purpose)
    services.store_dataset(org["id"], owner["id"], "books", "tally", parse_books("tally", sample.tally_xml()))
    services.store_dataset(org["id"], owner["id"], "gstr2b", "gstr2b", parse_gstr2b(sample.gstr2b_json()))
    services.add_member(owner, org["id"], ca["email"])
    services.create_org(ca, {"name": "Vardhman Polymers", "gstin": sample.gstin("27", "AAECV4411N"), "district": "Nashik",
                             "industry": "Injection-moulded plastic parts", "annual_turnover": 9e7, "investment_pm": 2e7})
    print(__doc__)


if __name__ == "__main__":
    main()
