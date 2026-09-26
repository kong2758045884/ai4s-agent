"""Use imported Hyper events as discovery hints, never as team verification."""
from contextlib import closing
import re

from . import impact_store


def institution_seeds(domain_id, subdomain_name=""):
    connection = impact_store.connect()
    if connection is None:
        return []
    with closing(connection):
        rows = connection.execute("""SELECT DISTINCT e.id,e.name FROM impact_entity e
            JOIN impact_entity_direction ed ON ed.entity_id=e.id
            JOIN impact_direction d ON d.id=ed.direction_id
            WHERE d.ai4s_domain_id=? AND e.kind='institution' AND e.country='zn'
            ORDER BY e.name,e.id""", (domain_id,)).fetchall()
        result = []
        for row in rows:
            if re.search(r"香港|澳门|台湾|Hong Kong|Macau|Taiwan", row["name"], re.I):
                continue
            # Discovery screen only. Formal publication still requires team
            # identity, mainland affiliation, and official original sources.
            if not re.search(r"大学|学院|科学院|研究院|研究所|实验室", row["name"]):
                continue
            if subdomain_name:
                from .official_team_directory import _subdomain_score
                from .strategic_graph import _normalized_name
                events = connection.execute(
                    "SELECT title,summary FROM impact_event WHERE entity_id=?", (row["id"],))
                text = " ".join(row["name"] + " " + ev["title"] + " " + ev["summary"] for ev in events)
                if not _subdomain_score(_normalized_name(text), subdomain_name):
                    continue
            result.append(row["name"])
        return list(dict.fromkeys(result))
