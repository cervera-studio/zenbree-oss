"""Which people are at a company (#229): domain to domain when both have one, else name to name (case-folded)."""

from collections import defaultdict

from app.lists.rows import norm_host


class PeopleIndex:
    """Built once from the people, then asked once per company: no pass over every person for each company."""

    def __init__(self) -> None:
        self._by_domain: defaultdict[str, list] = defaultdict(list)
        self._by_name_with_domain: defaultdict[str, list] = defaultdict(list)
        self._by_name_only: defaultdict[str, list] = defaultdict(list)

    def add(self, person, *, domain: str | None, company_name: str | None) -> None:
        name = (company_name or '').strip().lower()
        if host := norm_host(domain):
            self._by_domain[host].append(person)
            self._by_name_with_domain[name].append(person)
        else:
            self._by_name_only[name].append(person)

    def at(self, company: dict) -> list:
        """The people at a company row (its ``cells``)."""
        domain = norm_host(company.get('domain') or company.get('website'))
        name = str(company.get('name') or '').strip().lower()
        same_name = (self._by_name_only[name] + ([] if domain else self._by_name_with_domain[name])) if name else []
        return (self._by_domain[domain] if domain else []) + same_name
