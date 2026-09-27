"""Minimal row builders for tests."""

from sqlalchemy.orm import Session

from app.db.models import BuilderOrg, Site


def org_with_site(db: Session, name: str = "Test Org") -> tuple[BuilderOrg, Site]:
    org = BuilderOrg(name=name, settings={})
    db.add(org)
    db.flush()
    site = Site(
        builder_org_id=org.id,
        name=f"{name} Site",
        address="Plot 1",
        area="Sector 62, Noida",
        pincode="201309",
        lat=28.62,
        lng=77.36,
    )
    db.add(site)
    db.commit()
    return org, site
